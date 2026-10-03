"""
ebook_fix.modules.toc_cleanup

Cleans up a book's existing table of contents and removes empty pages
from the reading order. Both problems were found together in "Sandman
Slim" (Richard Kadrey), where an empty first file and a messy contents
list turned out to be the same damage:

1. Empty pages. A file in the reading order that holds nothing at all
   (no text and no elements, not even an empty wrapper) is dropped. In
   the Sandman Slim file the very first page is an empty file, and the
   contents list has a "Cover" entry pointing into it. When that
   happens, the book's real cover page (the one the OPF guide names as
   its cover, sitting outside the reading order) takes the empty page's
   place at the front, and the "Cover" contents entry is pointed at it.
   Without such a cover page, the entries for the dropped file are
   simply removed. A file is only dropped when nothing else in the book
   links to it, it is not the navigation document, and it is not the
   book's declared cover. Anything blocked is left in place and the
   reason is reported. OPF guide references to a dropped file (a "text"
   entry pointing at the empty page, say) are removed too.

2. Contents list clean-up. Applied to the NCX, the EPUB 3 navigation
   document, and the in-memory contents list:
   - A duplicate sub-entry: an entry nested under another entry with
     the same label pointing at the same file ("Contents" inside
     "Contents"). The nested copy is removed; anything nested under it
     moves up to the parent.
   - A blank label. The label is taken from the first heading near the
     top of the file when it is the first entry for that file and the
     heading has text. Otherwise an entry under a labeled parent reads
     "Parent (Part 2)", "Parent (Part 3)" and so on (the parent's own
     file counts as part 1), and a top-level entry with nothing to go on
     reads "Section N". This stops readers and the generated EPUB 3
     navigation document from showing a raw file path.

Entries that are merely questionable are left alone: only an exact
duplicate, a blank label, or an entry for a file this module dropped is
changed. An entry pointing at a file that doesn't exist is not touched
here (the analysis already reports those).

Runs after Chapter Title Cleanup and before EPUB 3 Upgrade, because the
navigation document that module generates is built from the contents
list this one corrects. Re-running it on a repaired book finds nothing
to do.
"""

from __future__ import annotations

import copy
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from lxml import etree

from ebook_fix.config import TocCleanupConfig
from ebook_fix.parser import EPUBParser
from ebook_fix.report import Report
from ebook_fix.split_fragments import _drop_file, _local

OPF_NS = "http://www.idpf.org/2007/opf"
NCX_NS_URI = "http://www.daisy.org/z3986/2005/ncx/"
EPUB_OPS_NS = "http://www.idpf.org/2007/ops"
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:")
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
IMAGE_TAGS = {"img", "image", "svg"}
COVER_LABELS = {"cover", "front cover", "cover page"}
MAX_TEXT_BEFORE_HEADING = 150


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------


def _clean(text) -> str:
    """Whitespace-collapsed text, with non-breaking and zero-width
    spaces treated as nothing."""
    text = (text or "").replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    return " ".join(text.split())


def _key(text) -> str:
    return _clean(text).casefold()


def _path_of(href: str) -> str:
    return (href or "").partition("#")[0]


def _resolve(source_dir: str, href: str) -> str:
    """OPF-relative path of an href found inside a file in source_dir
    (fragment dropped)."""
    path = _path_of(href)
    if not path:
        return ""
    return posixpath.normpath(posixpath.join(source_dir, path))


def _body(doc):
    if doc is None:
        return None
    return doc.find(".//{*}body")


def _body_text(body) -> str:
    texts = []
    for el in body.iter():
        if not isinstance(el.tag, str):
            if el is not body and el.tail:
                texts.append(el.tail)
            continue
        texts.append(el.text or "")
        if el is not body:
            texts.append(el.tail or "")
    return _clean("".join(texts))


def _has_image(body) -> bool:
    for el in body.iter():
        if isinstance(el.tag, str) and _local(el) in IMAGE_TAGS:
            return True
    return False


def _raw_file_is_empty(book, chapter) -> bool:
    """True only if the file in the archive holds nothing but an XML
    declaration and whitespace. If the file can't be read back, it is
    assumed NOT to be empty."""
    source = getattr(book, "source", None)
    if not source:
        return False
    full_path = str(PurePosixPath(book.package_path).parent / chapter.href)
    try:
        with zipfile.ZipFile(source) as archive:
            data = archive.read(full_path)
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    text = data.decode("utf-8", "ignore").replace("\ufeff", "")
    text = re.sub(r"<\?xml[^>]*\?>", "", text)
    return not text.strip()


def _is_empty_page(book, chapter) -> bool:
    doc = chapter.document
    if doc is None:
        # The parser found no document in the file. Only treat it as
        # empty if the file itself really holds nothing.
        return _raw_file_is_empty(book, chapter)
    body = _body(doc)
    if body is None:
        return False
    if _body_text(body):
        return False
    # Even an empty <div> counts as something: a page whose picture or
    # text another repair just removed still has its wrapper shell, and
    # that is not this module's call to make. Only a body with no
    # elements at all is empty.
    return not any(isinstance(el.tag, str) for el in body.iterdescendants())


def _is_nav_document(doc) -> bool:
    if doc is None:
        return False
    for nav in doc.iter("{*}nav"):
        if nav.get(f"{{{EPUB_OPS_NS}}}type") == "toc":
            return True
    return False


def _flatten(entries):
    for entry in entries:
        yield entry
        if entry.children:
            yield from _flatten(entry.children)


def _first_heading_label(chapter) -> str:
    """Text of the first heading with real text, if it sits near the top
    of the file. Empty string if there isn't one."""
    body = _body(chapter.document)
    if body is None:
        return ""
    for el in body.iter():
        if not isinstance(el.tag, str) or _local(el) not in HEADING_TAGS:
            continue
        text = _clean("".join(el.itertext()))
        if not text:
            continue
        before = _clean("".join(el.xpath("preceding::text()")))
        if len(before) > MAX_TEXT_BEFORE_HEADING:
            return ""
        return text
    return ""


# ---------------------------------------------------------------------
# A uniform view of the NCX and the nav document
# ---------------------------------------------------------------------


@dataclass(eq=False)
class _Node:
    el: object
    label: str
    path: str
    fragment: str
    parent: "_Node | None" = None
    children: list = field(default_factory=list)


class _TocTree:
    """One navigation tree (the NCX or the nav document) seen through
    the same small set of operations, so the clean-up rules are written
    once. `root` is the live <ncx> element or the nav document's root;
    `base_dir` is the directory the tree's own hrefs are relative to
    (OPF-relative)."""

    def __init__(self, kind: str, root, base_dir: str, name: str):
        self.kind = kind
        self.root = root
        self.base_dir = base_dir
        self.name = name

    # -- reading --------------------------------------------------------

    def build(self) -> list:
        if self.kind == "ncx":
            nav_map = self.root.find(f"{{{NCX_NS_URI}}}navMap")
            if nav_map is None:
                return []
            return [
                self._ncx_node(p, None)
                for p in nav_map.findall(f"{{{NCX_NS_URI}}}navPoint")
            ]
        nav = self._toc_nav()
        if nav is None:
            return []
        ol = nav.find("{*}ol")
        if ol is None:
            return []
        return self._nav_nodes(ol, None)

    def _toc_nav(self):
        for nav in self.root.iter("{*}nav"):
            if nav.get(f"{{{EPUB_OPS_NS}}}type") == "toc":
                return nav
        return None

    def _split(self, href: str):
        path, _, fragment = (href or "").partition("#")
        if not path:
            return "", fragment
        return posixpath.normpath(posixpath.join(self.base_dir, path)), fragment

    def _ncx_node(self, point, parent):
        text_el = point.find(f"{{{NCX_NS_URI}}}navLabel/{{{NCX_NS_URI}}}text")
        label = _clean("".join(text_el.itertext())) if text_el is not None else ""
        content = point.find(f"{{{NCX_NS_URI}}}content")
        path, fragment = self._split(content.get("src", "") if content is not None else "")
        node = _Node(point, label, path, fragment, parent)
        node.children = [
            self._ncx_node(c, node)
            for c in point.findall(f"{{{NCX_NS_URI}}}navPoint")
        ]
        return node

    def _nav_nodes(self, ol, parent):
        nodes = []
        for li in ol.findall("{*}li"):
            a = li.find("{*}a")
            span = li.find("{*}span")
            label_el = a if a is not None else span
            label = _clean("".join(label_el.itertext())) if label_el is not None else ""
            path, fragment = ("", "")
            if a is not None:
                path, fragment = self._split(a.get("href", ""))
            node = _Node(li, label, path, fragment, parent)
            child_ol = li.find("{*}ol")
            if child_ol is not None:
                node.children = self._nav_nodes(child_ol, node)
            nodes.append(node)
        return nodes

    # -- editing --------------------------------------------------------

    def set_label(self, node, text: str) -> bool:
        if self.kind == "ncx":
            point = node.el
            label = point.find(f"{{{NCX_NS_URI}}}navLabel")
            if label is None:
                label = etree.Element(f"{{{NCX_NS_URI}}}navLabel")
                point.insert(0, label)
            text_el = label.find(f"{{{NCX_NS_URI}}}text")
            if text_el is None:
                text_el = etree.SubElement(label, f"{{{NCX_NS_URI}}}text")
            text_el.text = text
            return True
        li = node.el
        target = li.find("{*}a")
        if target is None:
            target = li.find("{*}span")
        if target is None:
            return False
        for child in list(target):
            target.remove(child)
        target.text = text
        return True

    def retarget(self, node, new_path: str) -> None:
        rel = posixpath.relpath(new_path, self.base_dir or ".")
        if self.kind == "ncx":
            content = node.el.find(f"{{{NCX_NS_URI}}}content")
            if content is not None:
                content.set("src", rel)
            return
        a = node.el.find("{*}a")
        if a is not None:
            a.set("href", rel)

    def remove(self, node) -> None:
        """Takes the entry out, moving anything nested under it up to
        where it was."""
        el = node.el
        parent = el.getparent()
        if parent is None:
            return
        if self.kind == "ncx":
            kids = el.findall(f"{{{NCX_NS_URI}}}navPoint")
        else:
            child_ol = el.find("{*}ol")
            kids = child_ol.findall("{*}li") if child_ol is not None else []
        index = parent.index(el)
        for offset, kid in enumerate(kids):
            parent.insert(index + offset, kid)
        previous = el.getprevious()
        tail = el.tail
        parent.remove(el)
        if previous is not None:
            previous.tail = tail
        else:
            parent.text = tail
        if self.kind == "nav" and parent.find("{*}li") is None:
            grandparent = parent.getparent()
            if grandparent is not None and _local(grandparent) == "li":
                grandparent.remove(parent)

    def finish(self) -> None:
        """NCX only: resequence playOrder if the file used it."""
        if self.kind != "ncx":
            return
        points = list(self.root.iter(f"{{{NCX_NS_URI}}}navPoint"))
        if any(p.get("playOrder") for p in points):
            for n, p in enumerate(points, start=1):
                p.set("playOrder", str(n))


def _flat_nodes(nodes):
    for node in nodes:
        yield node
        yield from _flat_nodes(node.children)


# ---------------------------------------------------------------------
# The plan: what to do about empty pages
# ---------------------------------------------------------------------


@dataclass
class _EmptyPage:
    chapter: object
    spine_index: int
    blocker: str | None = None


@dataclass
class _Plan:
    pages: list = field(default_factory=list)  # every empty page found
    cover: object = None  # Chapter that takes the first empty page's place
    cover_for: str = ""  # href of the empty page it replaces

    @property
    def removable(self):
        return [p for p in self.pages if p.blocker is None]


class TocCleanupRepair:
    name = "TOC Cleanup"

    def __init__(self, config: TocCleanupConfig | None = None):
        self.config = config or TocCleanupConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        plan = self._make_plan(book)
        self._report_pages(report, plan)
        # Dry run on copies, so nothing in the book is touched.
        for _label, tree in self._trees(book, copy_trees=True):
            for category, description in self._tidy(book, tree, plan):
                report.add(tree.name, category, description)
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        report = Report(self.name)
        plan = self._make_plan(book)
        self._report_pages(report, plan)

        changed_trees = []
        for _label, tree in self._trees(book, copy_trees=False):
            log = self._tidy(book, tree, plan)
            for category, description in log:
                report.add(tree.name, category, description)
            if log:
                tree.finish()
                changed_trees.append(tree)

        for tree in changed_trees:
            self._mark_tree_modified(book, tree)
        if changed_trees:
            self._rebuild_toc(book)

        if self.config.remove_empty_pages:
            self._remove_pages(book, plan, report)

        if changed_trees:
            book.mark_modified()
        return report

    # -----------------------------------------------------
    # Planning
    # -----------------------------------------------------

    def _make_plan(self, book) -> _Plan:
        plan = _Plan()
        if not self.config.remove_empty_pages:
            return plan

        by_id = {m.id: m for m in book.manifest}
        chapters_by_id = {c.id: c for c in book.chapters}
        for index, idref in enumerate(book.spine):
            chapter = chapters_by_id.get(idref)
            item = by_id.get(idref)
            if chapter is None or item is None:
                continue
            if "nav" in (item.properties or "").split():
                continue
            if not _is_empty_page(book, chapter):
                continue
            plan.pages.append(_EmptyPage(chapter, index, self._blocker(book, chapter)))

        removable = plan.removable
        if removable and removable[0].spine_index == 0:
            first = removable[0]
            cover = self._guide_cover_chapter(book)
            if cover is not None and self._has_cover_entry(book, first.chapter.href):
                plan.cover = cover
                plan.cover_for = first.chapter.href
        return plan

    def _blocker(self, book, chapter):
        href = chapter.href
        cover_ref = self._guide_cover_href(book)
        if cover_ref == href:
            return "it is the book's declared cover page"
        for other in book.chapters:
            if other is chapter or other.document is None or _is_nav_document(other.document):
                continue
            other_dir = posixpath.dirname(other.href)
            for el in other.document.iter():
                if not isinstance(el.tag, str) or _local(el) != "a":
                    continue
                link = el.get("href") or ""
                if link.startswith(EXTERNAL_PREFIXES):
                    continue
                if _resolve(other_dir, link) == href:
                    return f"{other.href} links to it"
        return None

    def _guide_references(self, book):
        opf = getattr(book, "opf_document", None)
        if opf is None:
            return []
        guide = opf.find(f"{{{OPF_NS}}}guide")
        if guide is None:
            return []
        return guide.findall(f"{{{OPF_NS}}}reference")

    def _guide_cover_href(self, book):
        for ref in self._guide_references(book):
            if (ref.get("type") or "").lower() == "cover":
                return _path_of(ref.get("href") or "")
        return None

    def _guide_cover_chapter(self, book):
        """The guide's cover page, but only if it is a real page with a
        picture on it that sits outside the reading order."""
        href = self._guide_cover_href(book)
        if not href:
            return None
        item = next((m for m in book.manifest if m.href == href), None)
        if item is None or item.id in book.spine:
            return None
        chapter = next((c for c in book.chapters if c.href == href), None)
        if chapter is None or chapter.document is None:
            return None
        body = _body(chapter.document)
        if body is None or not _has_image(body):
            return None
        return chapter

    def _has_cover_entry(self, book, href) -> bool:
        for entry in _flatten(book.toc):
            if _path_of(entry.href) == href and _key(entry.label) in COVER_LABELS:
                return True
        return False

    def _report_pages(self, report, plan):
        for page in plan.pages:
            href = page.chapter.href
            if page.blocker is not None:
                report.add(
                    href,
                    "Empty page kept",
                    f"{href} is empty but was left in place because {page.blocker}",
                )
                continue
            if plan.cover is not None and href == plan.cover_for:
                report.add(
                    href,
                    "Empty page",
                    f"{href} has no content at all; it is dropped and the cover page "
                    f"{plan.cover.href} takes its place at the front of the book",
                )
            else:
                report.add(
                    href,
                    "Empty page",
                    f"{href} has no content at all and nothing links to it; it is dropped",
                )

    # -----------------------------------------------------
    # Contents trees
    # -----------------------------------------------------

    def _trees(self, book, copy_trees):
        """(label, _TocTree) for the NCX and the nav document, if the
        book has them. With copy_trees, the trees are deep copies (for
        the analysis dry run)."""
        trees = []
        if book.ncx_document is not None:
            root = copy.deepcopy(book.ncx_document) if copy_trees else book.ncx_document
            ncx_dir = posixpath.dirname(book.ncx_href or "")
            trees.append(("ncx", _TocTree("ncx", root, ncx_dir, book.ncx_href or "toc.ncx")))
        nav = book.nav_chapter
        if nav is not None and _is_nav_document(nav.document):
            root = copy.deepcopy(nav.document) if copy_trees else nav.document
            trees.append(("nav", _TocTree("nav", root, posixpath.dirname(nav.href), nav.href)))
        return trees

    def _mark_tree_modified(self, book, tree):
        if tree.kind == "ncx":
            book.ncx_modified = True
        else:
            nav = book.nav_chapter
            if nav is not None:
                nav.modified = True

    def _tidy(self, book, tree, plan):
        """Applies every contents fix to `tree`, one at a time against a
        fresh read of it, and returns [(category, description)]."""
        log = []
        removed_paths = {p.chapter.href for p in plan.removable} if self.config.remove_empty_pages else set()
        chapters_by_href = {c.href: c for c in book.chapters}

        # Entries a fix could not be applied to are remembered (by the
        # live element, never id()) and skipped from then on, so one
        # stubborn entry can never stall the rest. The loop cap is a
        # second guard; a real contents list never needs anywhere near
        # that many edits.
        skipped = set()
        for _ in range(5000):
            nodes = tree.build()
            action = self._next_action(nodes, removed_paths, plan, chapters_by_href, skipped)
            if action is None:
                break
            kind, node, value, category, description = action
            if kind == "remove":
                tree.remove(node)
            elif kind == "retarget":
                tree.retarget(node, value)
            elif kind == "label":
                if not tree.set_label(node, value):
                    skipped.add(node.el)
                    continue
            log.append((category, description))
        return log

    def _next_action(self, nodes, removed_paths, plan, chapters_by_href, skipped):
        flat = list(_flat_nodes(nodes))

        # 1. Entries for a page that is being dropped.
        for node in flat:
            if node.path and node.path in removed_paths:
                if (
                    plan.cover is not None
                    and node.path == plan.cover_for
                    and _key(node.label) in COVER_LABELS
                ):
                    return (
                        "retarget", node, plan.cover.href,
                        "Contents entry repointed",
                        f"{node.label!r} now points at the cover page {plan.cover.href} "
                        f"instead of the empty {node.path}",
                    )
                return (
                    "remove", node, None,
                    "Contents entry removed",
                    f"{node.label or node.path!r} pointed at the empty page {node.path}",
                )

        # 2. A nested entry that repeats its parent.
        if self.config.collapse_duplicate_entries:
            for node in flat:
                parent = node.parent
                if parent is None or not node.path:
                    continue
                if (
                    node.path == parent.path
                    and _key(node.label)
                    and _key(node.label) == _key(parent.label)
                ):
                    return (
                        "remove", node, None,
                        "Duplicate contents entry",
                        f"{node.label!r} is nested under an identical entry for {node.path}",
                    )

        # 3. A blank label.
        if self.config.fill_blank_labels:
            first_for_path = {}
            for node in flat:
                if node.path:
                    first_for_path.setdefault(node.path, node)
            for node in flat:
                # Only a linked entry gets a label: one with no target
                # and no label is an empty shell, not a missing name.
                if node.label or not node.path or node.el in skipped:
                    continue
                label = self._label_for(node, nodes, first_for_path, chapters_by_href)
                if label:
                    return (
                        "label", node, label,
                        "Blank contents label",
                        f"The entry for {node.path or 'no file'} had no label; now {label!r}",
                    )
        return None

    def _label_for(self, node, top_nodes, first_for_path, chapters_by_href) -> str:
        if node.path and first_for_path.get(node.path) is node:
            chapter = chapters_by_href.get(node.path)
            if chapter is not None and chapter.document is not None:
                text = _first_heading_label(chapter)
                if text:
                    return text

        parent = node.parent
        siblings = parent.children if parent is not None else top_nodes
        if parent is not None and parent.label and node.path != parent.path:
            others = [s for s in siblings if s.path != parent.path]
            part = 1 + (others.index(node) + 1 if node in others else len(others) + 1)
            return f"{parent.label} (Part {part})"
        number = siblings.index(node) + 1 if node in siblings else len(siblings) + 1
        return f"Section {number}"

    # -----------------------------------------------------
    # Rebuilding the in-memory contents list
    # -----------------------------------------------------

    def _rebuild_toc(self, book):
        parser = EPUBParser()
        entries = None
        if book.toc_source == "ncx" and book.ncx_document is not None:
            ncx_dir = posixpath.dirname(book.ncx_href or "")
            entries = parser._parse_ncx(book.ncx_document, ncx_dir)
        elif book.toc_source == "nav":
            nav = book.nav_chapter
            if nav is not None and _is_nav_document(nav.document):
                toc_nav = None
                for candidate in nav.document.iter("{*}nav"):
                    if candidate.get(f"{{{EPUB_OPS_NS}}}type") == "toc":
                        toc_nav = candidate
                        break
                ol = toc_nav.find("{*}ol") if toc_nav is not None else None
                if ol is not None:
                    entries = parser._nav_ol(ol, posixpath.dirname(nav.href))
        if entries is not None:
            book.toc = parser._merge_split_labels(entries)

    # -----------------------------------------------------
    # Removing the pages
    # -----------------------------------------------------

    def _remove_pages(self, book, plan, report):
        removable = plan.removable
        if not removable:
            return

        opf = getattr(book, "opf_document", None)
        by_href = {m.href: m for m in book.manifest}
        removed_hrefs = {p.chapter.href for p in removable}

        # Guide references to a page that is going away.
        if opf is not None:
            guide = opf.find(f"{{{OPF_NS}}}guide")
            if guide is not None:
                for ref in list(guide.findall(f"{{{OPF_NS}}}reference")):
                    if _path_of(ref.get("href") or "") in removed_hrefs:
                        report.add(
                            "content.opf",
                            "Guide reference removed",
                            f"The {ref.get('type') or 'untyped'} guide reference to "
                            f"{_path_of(ref.get('href') or '')} was removed with the empty page",
                        )
                        guide.remove(ref)
                if guide.find(f"{{{OPF_NS}}}reference") is None:
                    opf.remove(guide)
                book.opf_modified = True

        # The cover page takes the first empty page's place.
        if plan.cover is not None and opf is not None:
            spine_el = opf.find(f"{{{OPF_NS}}}spine")
            cover_item = by_href.get(plan.cover.href)
            old_item = by_href.get(plan.cover_for)
            if spine_el is not None and cover_item is not None and old_item is not None:
                old_ref = next(
                    (r for r in spine_el.findall(f"{{{OPF_NS}}}itemref")
                     if r.get("idref") == old_item.id),
                    None,
                )
                if old_ref is not None:
                    new_ref = etree.Element(f"{{{OPF_NS}}}itemref")
                    new_ref.set("idref", cover_item.id)
                    spine_el.insert(spine_el.index(old_ref), new_ref)
                    if old_item.id in book.spine:
                        book.spine.insert(book.spine.index(old_item.id), cover_item.id)
                    report.add(
                        plan.cover.href,
                        "Cover page restored to reading order",
                        f"{plan.cover.href} was outside the reading order and now opens the book",
                    )

        for page in removable:
            _drop_file(book, page.chapter.href)

        if plan.cover is not None:
            self._move_cover_chapter(book, plan.cover)

        book.opf_modified = True
        book.mark_modified()

    def _move_cover_chapter(self, book, cover):
        """Keeps book.chapters in reading order after the cover page
        joined the spine."""
        if cover.id not in book.spine:
            return
        book.chapters = [c for c in book.chapters if c is not cover]
        position = book.spine.index(cover.id)
        following = book.spine[position + 1] if position + 1 < len(book.spine) else None
        index = len(book.chapters)
        if following is not None:
            index = next(
                (i for i, c in enumerate(book.chapters) if c.id == following),
                len(book.chapters),
            )
        book.chapters.insert(index, cover)
