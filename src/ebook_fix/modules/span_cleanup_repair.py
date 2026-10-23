"""
ebook_fix.modules.span_cleanup_repair

Removes <span> wrappers that do nothing, and empty spans.

Found in "The Numbers Killer": after Reader Settings removed the fixed line
height and the redundant `font-size: 1em` from the span classes, 2,223 body
spans of the form `<span class="calibre8">text</span>` were left carrying a
class with no rule at all. Calibre conversions are full of these (Span Soup in
the analysis counts them). They add nothing, make every paragraph two elements
deep, and give a reading app that restyles text more to walk through.

What this does:
- A <span> is unwrapped (its text and children stay exactly where they were)
  when it has no attribute except `class`, and every class on it is
  purposeless: the class is not mentioned anywhere in the book's CSS, or its
  rules are provably no-ops (ebook_fix.span_soup).
- A <span> with no text and no children and no attribute at all (or only a
  purposeless class) is removed. A span with an `id` or any other attribute
  (an anchor target, `lang`, `epub:type`, an inline style) is never touched.

Safety:
- If the CSS uses an attribute selector on class (`[class~=...]`), no class
  is treated as purposeless, because a rule could reach a span without naming
  its class.
- Each page's visible text is compared before and after; a page whose text
  would change is left exactly as it was.
- Skipped on fixed-layout books, where spans are often positioned text boxes.

Runs after Reader Settings, which is what leaves most of these spans with
nothing to do. A second run finds nothing to unwrap.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from ebook_fix.config import SpanCleanupConfig
from ebook_fix.css import COMMENT_RE, read_book_css
from ebook_fix.report import Report
from ebook_fix.span_soup import CLASS_SELECTOR_RE, RULE_RE, _body_is_no_op

_CLASS_TOKEN = re.compile(r"\.([A-Za-z_][\w-]*)")


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _css_text(book) -> str:
    """All CSS in the book, taking rewritten stylesheets (book.new_files) in
    place of the originals they replace."""
    base = PurePosixPath(getattr(book, "package_path", "") or "").parent
    new_files = getattr(book, "new_files", None) or {}
    parts = []
    used_paths = set()
    for href, text in read_book_css(book).items():
        zpath = str(base / href)
        if zpath in new_files:
            used_paths.add(zpath)
            parts.append(new_files[zpath].decode("utf-8", "replace"))
        elif text:
            parts.append(text)
    for path, data in new_files.items():
        if path.lower().endswith(".css") and path not in used_paths:
            parts.append(data.decode("utf-8", "replace"))
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        for style in doc.iter("{*}style"):
            if style.text:
                parts.append(style.text)
    return COMMENT_RE.sub("", "\n".join(parts))


def _no_op_classes(css: str) -> set:
    """Classes whose every rule does nothing (an empty body, or only
    declarations such as `font-weight: normal`), read from the CSS as it
    stands now, after the earlier repairs have rewritten it."""
    no_op, bad = set(), set()
    for m in RULE_RE.finditer(css):
        selector = m.group(1).strip()
        if not selector or selector.startswith("@"):
            continue
        quiet = _body_is_no_op(m.group(2))
        for cls in CLASS_SELECTOR_RE.findall(selector):
            if quiet and cls not in bad:
                no_op.add(cls)
            elif not quiet:
                bad.add(cls)
                no_op.discard(cls)
    return no_op


def purposeless_classes(book, used: set):
    """The classes (of those used on spans) that no rule can be reaching, or
    None when the CSS has an attribute selector on class."""
    css = _css_text(book)
    if re.search(r"\[\s*class\b", css, re.IGNORECASE):
        return None
    mentioned = set(_CLASS_TOKEN.findall(css))
    purposeless = {c for c in used if c not in mentioned}
    # classes whose every rule is a confirmed no-op, plus any whose rule
    # holds nothing at all
    purposeless |= {c for c in _no_op_classes(css) if c in used}
    return purposeless


def _root(doc):
    return doc.getroot() if hasattr(doc, "getroot") else doc


def _text(el) -> str:
    return "".join(el.itertext())


def _unwrap(el) -> None:
    """Replace el with its text and children, keeping every character."""
    parent = el.getparent()
    idx = parent.index(el)
    children = list(el)
    lead = el.text or ""
    tail = el.tail or ""
    if lead:
        if idx > 0:
            prev = parent[idx - 1]
            prev.tail = (prev.tail or "") + lead
        else:
            parent.text = (parent.text or "") + lead
    for offset, child in enumerate(children):
        parent.insert(idx + offset, child)
    if tail:
        if children:
            children[-1].tail = (children[-1].tail or "") + tail
        elif idx > 0:
            prev = parent[idx - 1]
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    el.tail = None
    parent.remove(el)


def _candidates(chapter, purposeless: set):
    """Spans in the page that can go: ([unwrap], [remove])."""
    doc = chapter.document
    unwrap, remove = [], []
    if doc is None:
        return unwrap, remove
    for el in doc.iter("{*}span"):
        if el.getparent() is None:
            continue
        attrs = set(el.attrib)
        if attrs - {"class"}:
            continue
        classes = (el.get("class") or "").split()
        if classes and not all(c in purposeless for c in classes):
            continue
        if len(el) == 0 and not _text(el):
            remove.append(el)
        else:
            unwrap.append(el)
    return unwrap, remove


class SpanCleanupRepair:
    name = "Span Cleanup"

    def __init__(self, config: SpanCleanupConfig | None = None):
        self.config = config or SpanCleanupConfig()

    def _used_span_classes(self, book) -> set:
        used = set()
        for chapter in book.chapters:
            doc = chapter.document
            if doc is None:
                continue
            for el in doc.iter("{*}span"):
                used.update((el.get("class") or "").split())
        return used

    def _scan(self, book, write: bool):
        """[(href, unwrapped, removed)] per page."""
        purposeless = purposeless_classes(book, self._used_span_classes(book))
        results = []
        if purposeless is None:
            return results
        for chapter in book.chapters:
            unwrap, remove = _candidates(chapter, purposeless)
            if not (unwrap or remove):
                continue
            if not write:
                results.append((chapter.href, len(unwrap), len(remove)))
                continue
            before = _text(_root(chapter.document))
            # unwrap from the innermost out; removal first so a span emptied by
            # a removal is not left behind to be found on the next run
            todo = [("remove", e) for e in remove] + [("unwrap", e) for e in reversed(unwrap)]
            snapshot_ok = True
            for kind, el in todo:
                if el.getparent() is None:
                    continue
                if kind == "remove":
                    tail = el.tail or ""
                    parent = el.getparent()
                    idx = parent.index(el)
                    if tail:
                        if idx > 0:
                            prev = parent[idx - 1]
                            prev.tail = (prev.tail or "") + tail
                        else:
                            parent.text = (parent.text or "") + tail
                    el.tail = None
                    parent.remove(el)
                else:
                    _unwrap(el)
            after = _text(_root(chapter.document))
            if after != before:
                snapshot_ok = False
            if not snapshot_ok:
                # cannot happen with the two helpers above; guard anyway
                raise RuntimeError(f"Span Cleanup changed the text of {chapter.href}")
            chapter.modified = True
            results.append((chapter.href, len(unwrap), len(remove)))
        return results

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for href, n_unwrap, n_remove in self._scan(book, write=False):
            report.add(href, "Purposeless <span> wrappers", self._describe(n_unwrap, n_remove, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        results = self._scan(book, write=True)
        for href, n_unwrap, n_remove in results:
            report.add(href, "Purposeless <span> wrappers removed", self._describe(n_unwrap, n_remove, will=False))
        if results and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report

    @staticmethod
    def _describe(n_unwrap, n_remove, will):
        parts = []
        if n_unwrap:
            parts.append(f"{n_unwrap} do-nothing span wrapper(s) {'will be' if will else 'were'} unwrapped")
        if n_remove:
            parts.append(f"{n_remove} empty span(s) {'will be' if will else 'were'} removed")
        return "; ".join(parts) + "; the text is unchanged"
