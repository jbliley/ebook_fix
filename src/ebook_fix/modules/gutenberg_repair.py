"""
ebook_fix.modules.gutenberg_repair

Removes the Project Gutenberg boilerplate ebook_fix.gutenberg already
found (see that module for how detection works and why the two
conversion eras need different handling). Reads the recorded
front/back GutenbergMarker instead of re-scanning the book, same
"analysis is descriptive, repair decides" split every other module in
this project follows.

What actually happens, by case
-------------------------------
- Front matter is always a subtree cut inside a file that also holds
  real content -- both eras put it at the very top of a file, never on
  its own (see ebook_fix.gutenberg's docstring). This walks up from
  the marker/wrapper element to whichever ancestor is a direct child
  of <body>, then removes that ancestor and everything before it under
  <body>. One exception: if a heading (h1-h6) turns up somewhere in
  that leading run, the sweep stops there and leaves it (and anything
  before it) alone -- a heading ahead of the boilerplate is far more
  likely to be a real, conversion-tool-added title than part of PG's
  own header. Confirmed against the Tom Sawyer example: calibre stamps
  a `<h1>` book title in from the OPF metadata *before* the Gutenberg
  boilerplate paragraphs even start, and that title needs to survive.
- Back matter:
  - Modern tag format, whole file is nothing but the footer (the
    common case): the whole spine entry gets dropped -- removed from
    the manifest, the spine, book.chapters, and any nav.xhtml link
    pointing at it.
  - Older text format, or a tag-format footer sharing a file with real
    content: subtree cut mirroring the front-matter one, but in the
    other direction -- removes the marker's top-under-body ancestor
    and everything AFTER it under <body>. No heading guard here: by
    definition, nothing legitimate follows the END marker.
  - trailing_back_matter_hrefs (see ebook_fix.gutenberg) are always
    dropped as whole files, same mechanism as the whole-footer-file
    case above.

Known limitation
-----------------
Whole-file removal cleans up the manifest, the spine, and the EPUB 3
nav document if the book has one, but NOT a legacy NCX -- toc.ncx
isn't loaded as an editable document anywhere in the project yet (see
docs/analysis_roadmap.md), so a book with only an NCX (no nav
document) can be left with a stale NCX entry pointing at a page that
no longer exists. Noted here rather than built around, since adding
NCX-editing support is bigger than this module's job.
"""

from __future__ import annotations
from pathlib import PurePosixPath
from ebook_fix.report import Report
from ebook_fix.gutenberg import analyze_book_gutenberg

OPF_NS = "http://www.idpf.org/2007/opf"
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class GutenbergRepair:
    name = "Gutenberg Boilerplate Removal"

    def __init__(self, config=None):
        self.config = config

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        if self.config is not None and not getattr(self.config, "enabled", True):
            return report

        gb = analysis.gutenberg if analysis is not None else analyze_book_gutenberg(book)
        if not gb.detected:
            return report

        if gb.front_found and getattr(self.config, "fix_front_matter", True):
            report.add(
                gb.front.href,
                "Front disclaimer to remove",
                f"Front Gutenberg disclaimer detected (via {gb.front.method})",
            )

        if gb.back_found and getattr(self.config, "fix_back_matter", True):
            report.add(
                gb.back.href,
                "Back license to remove",
                f"Back Gutenberg license detected (via {gb.back.method})",
            )
            for href in gb.trailing_back_matter_hrefs:
                report.add(
                    href,
                    "Trailing back-matter file to remove",
                    "Whole file is leftover Gutenberg license text with no marker of its own",
                )

        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        if self.config is not None and not getattr(self.config, "enabled", True):
            return

        gb = analysis.gutenberg if analysis is not None else analyze_book_gutenberg(book)
        if not gb.detected:
            return

        changed = False

        if gb.front_found and getattr(self.config, "fix_front_matter", True):
            chapter = _find_chapter(book, gb.front.href)
            if chapter is not None and _remove_front_boilerplate(chapter, gb.front):
                chapter.modified = True
                changed = True

        if gb.back_found and getattr(self.config, "fix_back_matter", True):
            if _repair_back(book, gb.back):
                changed = True
            for href in gb.trailing_back_matter_hrefs:
                if _remove_whole_chapter(book, href):
                    changed = True

        if changed:
            book.mark_modified()


# ---------------------------------------------------------------------
# Shared tree helpers
# ---------------------------------------------------------------------

def _find_chapter(book, href):
    return next((c for c in book.chapters if c.href == href), None)


def _find_body(tree):
    if tree is None:
        return None
    return tree.find(".//{*}body")


def _local_tag(el):
    if not isinstance(el.tag, str):
        return ""
    return el.tag.split("}")[-1].lower()


def _ancestor_under_body(body, el):
    """Walks up from el to whichever ancestor is a direct child of
    body -- the unit repair operates on, since block-level boilerplate
    elements sit directly under <body> in every example seen so far."""
    node = el
    while node is not None:
        parent = node.getparent()
        if parent is None:
            return None
        if parent is body:
            return node
        node = parent
    return None


def _remove_keep_tail(el):
    """Removes el, but if it had non-whitespace tail text (content
    that technically belongs to el in lxml's model, not to its
    neighbors), reattaches that tail rather than silently losing it."""
    tail = el.tail
    parent = el.getparent()
    if parent is None:
        return
    prev = el.getprevious()
    parent.remove(el)
    if tail and tail.strip():
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


# ---------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------

def _remove_front_boilerplate(chapter, marker):
    body = _find_body(chapter.document)
    if body is None or marker.element is None:
        return False
    top = _ancestor_under_body(body, marker.element)
    if top is None:
        return False

    if marker.method == "tag":
        # The wrapper tag IS the whole boilerplate block -- nothing
        # else to sweep, no heading guard needed.
        _remove_keep_tail(top)
        return True

    # method == "text": sweep backward from the marker's top-level
    # ancestor, removing preceding siblings too, but stop at the first
    # heading and leave it (and everything before it) alone -- see
    # module docstring.
    to_remove = [top]
    node = top.getprevious()
    while node is not None:
        if _local_tag(node) in _HEADING_TAGS:
            break
        to_remove.append(node)
        node = node.getprevious()

    for node in to_remove:
        _remove_keep_tail(node)
    return True


# ---------------------------------------------------------------------
# Back matter
# ---------------------------------------------------------------------

def _body_is_boilerplate_only(body, top):
    """True if, aside from `top` itself, body has no other meaningful
    (non-whitespace) content -- the modern-format whole-footer-file
    case, confirmed against the Cthulhu example where <body> has
    exactly one child, the <footer>."""
    if (body.text or "").strip():
        return False
    for child in body:
        if child is top:
            continue
        if "".join(child.itertext()).strip():
            return False
    return True


def _remove_back_boilerplate_subtree(chapter, marker):
    body = _find_body(chapter.document)
    if body is None or marker.element is None:
        return False
    top = _ancestor_under_body(body, marker.element)
    if top is None:
        return False

    # No heading guard in this direction: by definition nothing
    # legitimate follows the END marker.
    to_remove = [top]
    node = top.getnext()
    while node is not None:
        to_remove.append(node)
        node = node.getnext()

    for node in to_remove:
        _remove_keep_tail(node)
    return True


def _repair_back(book, marker):
    chapter = _find_chapter(book, marker.href)
    if chapter is None:
        return False

    if marker.method == "tag":
        body = _find_body(chapter.document)
        top = _ancestor_under_body(body, marker.element) if body is not None and marker.element is not None else None
        if body is not None and top is not None and _body_is_boilerplate_only(body, top):
            return _remove_whole_chapter(book, marker.href)

    if _remove_back_boilerplate_subtree(chapter, marker):
        chapter.modified = True
        return True
    return False


# ---------------------------------------------------------------------
# Whole-file removal
# ---------------------------------------------------------------------

def _remove_whole_chapter(book, href):
    """Drops a spine file entirely: removes it from book.chapters, the
    in-memory manifest/spine lists, the live OPF <manifest>/<spine>
    elements, any link to it in the EPUB3 nav document if the book has
    one (see module docstring for the legacy-NCX limitation), and
    finally book.removed_files so the writer actually drops the
    physical file from the saved archive -- removing it from the
    manifest/spine alone leaves the file itself sitting in the zip."""
    opf = getattr(book, "opf_document", None)
    if opf is None:
        return False

    manifest_item = next((m for m in book.manifest if m.href == href), None)
    if manifest_item is None:
        return False
    item_id = manifest_item.id

    manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
    spine_el = opf.find(f"{{{OPF_NS}}}spine")

    if manifest_el is not None:
        for item in manifest_el.findall(f"{{{OPF_NS}}}item"):
            if item.get("id") == item_id:
                manifest_el.remove(item)
                break

    if spine_el is not None:
        for itemref in spine_el.findall(f"{{{OPF_NS}}}itemref"):
            if itemref.get("idref") == item_id:
                spine_el.remove(itemref)
                break

    book.manifest = [m for m in book.manifest if m.href != href]
    book.spine = [idref for idref in book.spine if idref != item_id]
    book.chapters = [c for c in book.chapters if c.href != href]

    _remove_nav_links(book, href)

    base = PurePosixPath(book.package_path).parent
    book.removed_files.add(str(base / href))

    book.opf_modified = True
    return True


def _remove_nav_links(book, removed_href):
    """A book's EPUB3 nav document can hold its own link to the file
    being dropped -- clean that up too, so the TOC doesn't point at a
    page that no longer exists."""
    nav_href = next(
        (m.href for m in book.manifest if "nav" in (m.properties or "").split()),
        None,
    )
    if not nav_href:
        return
    nav_chapter = _find_chapter(book, nav_href)
    if nav_chapter is None or nav_chapter.document is None:
        return

    targets = []
    for el in nav_chapter.document.iter():
        if _local_tag(el) != "a":
            continue
        target = (el.get("href") or "").split("#")[0]
        if target == removed_href:
            targets.append(el)

    if not targets:
        return

    for a in targets:
        parent = a.getparent()
        if parent is not None and _local_tag(parent) == "li":
            _remove_keep_tail(parent)
        else:
            _remove_keep_tail(a)

    nav_chapter.modified = True
