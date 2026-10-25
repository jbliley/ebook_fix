"""
ebook_fix.modules.reader_settings_repair

Removes book-wide styling that a reading app already has a setting for, so
the app's own choices are not overwritten. Jacob's rule (2026-10-22): anything
the reader can change should not be hard-coded in the book. One-off styling
that emphasizes a piece of text (italics, bold, a centered title, a small-caps
run, a block quote) is never touched; only settings that apply to the whole
body of the book.

What is removed, and where:

On the book's body-text paragraph class(es) (and the plain `p` selector, and
inline styles on plain body paragraphs):
- line-height              (the app's line spacing setting)
- text-indent              (the app's paragraph indent setting)
- margin-top / margin-bottom, the gap between paragraphs (the app's
  paragraph spacing setting). The space under a chapter header is kept: a
  rule gives the first paragraph after a heading or chapter title its
  original top margin back, as Paragraph Spacing does.
- text-align when it is justify, left or start (the app's justification
  setting); a centered or right-aligned paragraph is a one-off and stays
- letter-spacing, word-spacing
- side margins and padding, font-family, font-size, color and background
  (the app's font, text size, margin and theme settings)

On the page itself (`body`, `html`, the class on the <body> element, and
`@page`):
- side and top/bottom margins and padding (the app's page margin setting)
- line-height, and justify / left alignment
- font-family, font-size, color and background (the app's font, size and theme)

On any rule at all:
- a font size of 1em, 100% or inherit, which changes nothing but pins the
  size in some apps
- a non-zero indent on centered or right-aligned text, which only pushes a title
  off center
- rules left with nothing in them after the cleanup
- hyphenation switches (hyphens, -webkit-hyphens, adobe-hyphenate, the
  hyphenate limits), which override the app's hyphenation setting
- converter leftovers that do nothing in a reader (-aw-*,
  -webkit-line-box-contain)

Left alone: headings and chapter titles, centered or right-aligned
classes, block quotes and other classes that are not the body class, tables,
images, font size / family / color (Text Size, Font Strip and Color Strip
handle those), a `margin` shorthand on a body-text class (left as it is
rather than guessed at), and elements ebook_fix itself styled (header gaps,
scene markers).

Works on external stylesheets, embedded <style> blocks and inline styles.
Re-running finds nothing to remove and does nothing.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from ebook_fix.class_map import build_class_profiles
from ebook_fix.config import ReaderSettingsConfig
from ebook_fix.css import read_book_css
from ebook_fix.modules.paragraph_spacing_repair import _HEADING_SELECTORS, _to_em
from ebook_fix.report import Report

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_DECL = re.compile(r"^\s*([-\w]+)\s*:\s*(.+?)\s*$", re.DOTALL)
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")

BODY_TEXT_PROPS = frozenset({
    "line-height", "text-indent", "letter-spacing", "word-spacing", "margin-top", "margin-bottom",
    "margin-left", "margin-right", "padding-left", "padding-right",
    "font-family", "font-size", "color", "background", "background-color",
})
PAGE_PROPS = frozenset({
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "line-height",
    "font-family", "font-size", "color", "background", "background-color",
})
FLEXIBLE_ALIGN = frozenset({"justify", "left", "start"})
HYPHEN_PROPS = frozenset({
    "hyphens", "-webkit-hyphens", "-moz-hyphens", "-epub-hyphens", "-ms-hyphens", "adobe-hyphenate",
    "-webkit-hyphenate-limit-before", "-webkit-hyphenate-limit-after", "-webkit-hyphenate-limit-lines",
    "hyphenate-limit-chars", "hyphenate-limit-lines", "hyphenate-limit-last",
})
JUNK_PREFIXES = ("-aw-",)
JUNK_PROPS = frozenset({"-webkit-line-box-contain"})

# A zero indent on a centered title is harmless (and Class Standardize writes one),
# so only a real indent is removed there.
_ZERO_LENGTHS = frozenset({"0", "0px", "0em", "0pt", "0%", "0rem"})
NOOP_FONT_SIZES = frozenset({"1em", "100%", "inherit", "1.0em"})
LABEL_NOOP = "redundant font size (1em)"
LABEL_CENTER_INDENT = "indent on centered text"
LABEL_EMPTY = "empty rules"
LABEL_OTHER = "hyphenation and converter leftovers"
LABEL_PAGE = "page margins and padding"
LABEL_INLINE = "inline styles on body text"


def _is_any_rule_prop(name: str) -> bool:
    return name in HYPHEN_PROPS or name in JUNK_PROPS or name.startswith(JUNK_PREFIXES)


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _strip(body: str, should_remove):
    """(new_body, [(name, value)]) with every declaration for which
    should_remove(name, value) is true gone. The text around the other
    declarations, including the whitespace before a closing brace, is kept."""
    chunks = body.split(";")
    out, removed = [], []
    last = len(chunks) - 1
    for idx, chunk in enumerate(chunks):
        m = _DECL.match(chunk)
        if m:
            name, value = m.group(1).lower(), " ".join(m.group(2).split())
            if should_remove(name, value):
                removed.append((name, value))
                if idx == last:
                    out.append(chunk[len(chunk.rstrip()):])
                continue
        out.append(chunk)
        if idx != last:
            out.append(";")
    return "".join(out), removed


def find_body_classes(book, analysis=None):
    """(body_text_classes, body_element_classes)."""
    chapters = getattr(analysis, "chapters", None) if analysis is not None else None
    frontmatter = getattr(analysis, "frontmatter", None) if analysis is not None else None
    profiles = build_class_profiles(book, chapter_summary=chapters, frontmatter_summary=frontmatter)
    body_text = {p.class_name for p in profiles.values() if p.likely_role == "body-text" and p.role_confidence == "high"}
    body_el = set()
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        for el in doc.iter("{*}body", "{*}html"):
            body_el.update((el.get("class") or "").split())
    return body_text, body_el


def _heading_classes(book) -> set:
    found = set()
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        for h in doc.iter(*[f"{{*}}{t}" for t in _HEADINGS]):
            for el in h.iter():
                if isinstance(el.tag, str):
                    found.update((el.get("class") or "").split())
    return found


def _rewrite_css(css_text: str, body_text: set, body_el: set, skip_classes: set):
    """(new_text, Counter of what was removed). Strips what the module
    documents, then appends rules restoring each body class's original top
    margin right after a heading or chapter title."""
    counts = Counter()
    restore = {}          # class -> original margin-top
    text_sels = {f".{c}" for c in body_text} | {f"p.{c}" for c in body_text} | {"p"}
    page_sels = {"body", "html"} | {f".{c}" for c in body_el} | {f"body.{c}" for c in body_el}

    def sub(match):
        raw_sel, body = match.group(1), match.group(2)
        sel_text = _COMMENT.sub("", raw_sel).strip()
        is_page_at = sel_text.lower() == "@page"
        if sel_text.startswith("@") and not is_page_at:
            return match.group(0)
        comments = "".join(re.findall(r"/\*.*?\*/", raw_sel, re.DOTALL))
        if not body.strip():
            counts[LABEL_EMPTY] += 1
            return comments
        selectors = [s.strip() for s in sel_text.split(",") if s.strip()]
        kind = None
        if is_page_at or (selectors and all(s in page_sels for s in selectors)):
            kind = "page"
        elif selectors and all(s in text_sels for s in selectors):
            kind = "body-text"
        # a rule that also names a heading class is never rewritten by kind
        if kind and any(c in skip_classes for s in selectors for c in re.findall(r"\.([\w-]+)", s)):
            kind = None
        centered = bool(re.search(r"text-align\s*:\s*(?:center|right)\b", body, re.IGNORECASE))
        original_top = {}

        def should_remove(name, value):
            if _is_any_rule_prop(name):
                return True
            if name == "font-size" and value.lower() in NOOP_FONT_SIZES:
                return True
            if name == "text-indent" and centered and value.strip().lower() not in _ZERO_LENGTHS:
                return True
            if kind == "page":
                return name in PAGE_PROPS or (name == "text-align" and value.lower() in FLEXIBLE_ALIGN)
            if kind == "body-text":
                if name in BODY_TEXT_PROPS:
                    if name == "margin-top":
                        original_top["v"] = value
                    return True
                return name == "text-align" and value.lower() in FLEXIBLE_ALIGN
            return False

        new_body, removed = _strip(body, should_remove)
        if not removed:
            return match.group(0)
        for name, value in removed:
            if _is_any_rule_prop(name):
                counts[LABEL_OTHER] += 1
            elif name == "font-size" and value.lower() in NOOP_FONT_SIZES:
                counts[LABEL_NOOP] += 1
            elif name == "text-indent" and centered and kind is None and value.strip().lower() not in _ZERO_LENGTHS:
                counts[LABEL_CENTER_INDENT] += 1
            elif kind == "page" and name not in ("line-height", "text-align"):
                counts[LABEL_PAGE] += 1
            else:
                counts[name] += 1
        top = original_top.get("v")
        if kind == "body-text" and top and _to_em(top):
            for s in selectors:
                cls = re.sub(r"^p?\.", "", s)
                if cls != "p":
                    restore.setdefault(cls, top)
        if not new_body.strip():
            counts[LABEL_EMPTY] += 1
            return comments
        return f"{raw_sel}{{{new_body}}}"

    new_text = _RULE.sub(sub, css_text)
    if restore and counts:
        lines = ["", "/* ebook_fix: the gap between body paragraphs is left to the reading app; the space",
                 "   under chapter headers and above chapter titles is kept as it was. */"]
        for cls, top in restore.items():
            sel = f".{cls}"
            lines.append(f"{_HEADING_SELECTORS.format(sel=sel)}, [data-ebookfix-chapter] + {sel}, {sel}[data-ebookfix-chapter] {{ margin-top: {top}; }}")
        new_text = new_text.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    return new_text, counts


def _is_ebookfix_element(el) -> bool:
    if any(k.startswith("data-ebookfix") for k in el.attrib):
        return True
    return "ebookfix" in (el.get("class") or "")


def _remove_in_paragraph(name, value):
    return (name in BODY_TEXT_PROPS or _is_any_rule_prop(name)
            or (name == "font-size" and value.lower() in NOOP_FONT_SIZES)
            or (name == "text-align" and value.lower() in FLEXIBLE_ALIGN))


def _remove_in_body(name, value):
    return (name in PAGE_PROPS or _is_any_rule_prop(name)
            or (name == "text-align" and value.lower() in FLEXIBLE_ALIGN))


class ReaderSettingsRepair:
    name = "Reader Settings"

    def __init__(self, config: ReaderSettingsConfig | None = None):
        self.config = config or ReaderSettingsConfig()

    def _scan(self, book, analysis, write: bool) -> Counter:
        body_text, body_el = find_body_classes(book, analysis)
        skip = _heading_classes(book)
        counts = Counter()
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent

        contents = read_book_css(book)
        new_files = getattr(book, "new_files", None)
        for res in getattr(book, "css", []) or []:
            zpath = str(base / res.href)
            text = new_files[zpath].decode("utf-8", "replace") if new_files and zpath in new_files else contents.get(res.href)
            if not text:
                continue
            new_text, c = _rewrite_css(text, body_text, body_el, skip)
            if c:
                counts.update(c)
                if write:
                    book.new_files[zpath] = new_text.encode("utf-8")

        for chapter in book.chapters:
            doc = chapter.document
            if doc is None:
                continue
            touched = False
            for style in doc.iter("{*}style"):
                if style.text:
                    new_text, c = _rewrite_css(style.text, body_text, body_el, skip)
                    if c:
                        counts.update(c)
                        touched = True
                        if write:
                            style.text = new_text
            # inline styles on plain body paragraphs and on the page body itself
            for el in doc.iter("{*}p", "{*}body"):
                style_attr = el.get("style")
                if not style_attr or _is_ebookfix_element(el):
                    continue
                if _local(el) == "p":
                    classes = set((el.get("class") or "").split())
                    if classes and not classes <= body_text:
                        continue
                    if any(_local(a) in _HEADINGS for a in el.iterancestors()):
                        continue
                    new_style, removed = _strip(style_attr, _remove_in_paragraph)
                else:
                    new_style, removed = _strip(style_attr, _remove_in_body)
                if removed:
                    counts[LABEL_INLINE] += len(removed)
                    touched = True
                    if write:
                        new_style = new_style.strip().strip(";").strip()
                        if new_style:
                            el.set("style", new_style)
                        else:
                            del el.attrib["style"]
            if touched and write:
                chapter.modified = True
        return counts

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        self._report(report, self._scan(book, analysis, write=False), will=True)
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        counts = self._scan(book, analysis, write=True)
        self._report(report, counts, will=False)
        if counts and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report

    @staticmethod
    def _report(report, counts, will):
        verb = "will be" if will else "were"
        for label, n in sorted(counts.items()):
            if label == LABEL_EMPTY:
                detail = f"{n} stylesheet rule(s) left with nothing in them {verb} removed"
            elif label in (LABEL_OTHER, LABEL_PAGE, LABEL_INLINE, LABEL_NOOP, LABEL_CENTER_INDENT):
                detail = f"{label}: {n} declaration(s) {verb} removed so the reading app's own setting applies"
            else:
                detail = f"{n} '{label}' setting(s) on body text {verb} removed so the reading app's own setting applies"
            report.add("stylesheet", "Book-wide style the reading app handles", detail)
