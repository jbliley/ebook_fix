"""
ebook_fix.toc_generate

Builds a table-of-contents entry tree (the same TocEntry shape
book.toc already uses -- see models.py) directly from a book's own
detected chapter structure, for a book that has no NCX and no EPUB3
nav document with any TOC entries at all (book.toc_source == "" --
see docs/analysis_roadmap.md, "Next: TOC generation when missing").

This is the "generate one from nothing" piece the rest of the TOC
tooling has always deferred: toc.py only ever validates a TOC that
already exists, and crossref.py's generate_missing_ncx_entries only
ever extends an NCX document that already has at least a shell to add
navPoints into. Neither helps a book that has no navigation at all.

Only ever built from chapters.py's normal (case 1/2) confirmed
sequence -- structure.build_structure fed by analyze_book_chapters,
never analyze_case3_book_chapters. Per Jacob's three-case framework
(see docs/xhtml_recoder_plan.md), case 3 detection is deliberately
weak and always routed to manual review; generating an authoritative-
looking TOC from it would overstate how sure this actually is. A book
with nothing confirmed the normal way gets an empty TocEntry list
here, same as a book with nothing detected at all.

Where each entry links
-----------------------
Most books this runs against haven't been physically split (that's a
separate, heavier operation -- see splitter.py) -- most chapters still
share a file with others. So every entry needs a fragment id pointing
at the actual chapter-start element, not just a whole-file href. Two
sources for that id, in priority order:

1. If ebook_fix.modules.chapter_markup already ran (earlier in the
   repair pipeline -- see engine.py), every confirmed CHAPTER boundary
   is already wrapped in its own <div epub:type="chapter" id="...">.
   Reusing that id keeps one canonical anchor per chapter instead of
   stamping a second, redundant one right next to it.
2. Otherwise (chapter_markup disabled, or a PART boundary, which
   chapter_markup never wraps at all), a fresh id is assigned directly
   on the boundary's own block-level element, using the same
   "walk up to the nearest block tag" logic chapter_markup.py uses, so
   a heading that already reads naturally as the chapter start is what
   actually gets the id -- not some deeply nested inline <span>.

Either way, chapter.modified is set whenever an id actually gets
added, so the writer re-serializes that chapter.

This module also holds the shared nav.xhtml/toc.ncx builders used by
both modules/epub3_upgrade.py (whenever it needs to generate a nav
document with nothing existing to mirror) and modules/toc_generation.py
(the module that actually notices and fixes a book with no navigation
at all) -- one set of building blocks instead of two copies that could
quietly drift apart.
"""

from __future__ import annotations

from itertools import count

from lxml import etree

from ebook_fix.chapters import analyze_book_chapters
from ebook_fix.models import TocEntry
from ebook_fix.modules.chapter_markup import BLOCK_TAGS, element_tag
from ebook_fix.structure import NodeKind, build_structure

XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_OPS_NS = "http://www.idpf.org/2007/ops"
NCX_NS_URI = "http://www.daisy.org/z3986/2005/ncx/"

# The class chapter_markup.py stamps on the wrapper <div> it creates
# around each confirmed chapter -- checked below so a generated
# fragment id can reuse that wrapper's own id instead of adding one of
# its own right next to it.
CHAPTER_WRAPPER_CLASS = "ebookfix-chapter-wrapper"


# ---------------------------------------------------------------------
# Building a TocEntry tree from the book's own detected structure
# ---------------------------------------------------------------------

def _find_block(element):
    """Walk up from a (possibly inline) marker element to the nearest
    block-level ancestor -- the same idiom chapter_markup.py uses, so
    a fragment id lands on the real chapter-start paragraph/heading
    rather than an inline <span> inside it."""
    node = element
    while node is not None:
        if element_tag(node) in BLOCK_TAGS:
            return node
        parent = node.getparent()
        if parent is None:
            return node
        node = parent
    return None


def _existing_wrapper_id(element):
    """Looks for a chapter_markup-created wrapper among this element's
    own ancestors, and returns its id if it already has one. None if
    chapter_markup never wrapped this boundary (module disabled, this
    pass hasn't reached it yet, or it's a PART boundary -- chapter_markup
    only ever wraps individual chapters, never Parts)."""
    node = element
    while node is not None:
        classes = (node.get("class") or "").split()
        if CHAPTER_WRAPPER_CLASS in classes:
            wrapper_id = node.get("id")
            if wrapper_id:
                return wrapper_id
        node = node.getparent()
    return None


def _ensure_anchor_id(chapter, element, used_ids, candidate_id):
    """Returns a stable id for this boundary's element, reusing one
    that already exists (a chapter_markup wrapper, or an id the
    element already happened to carry) before ever assigning a new
    one. Sets chapter.modified when a new id actually gets written."""
    existing = _existing_wrapper_id(element)
    if existing:
        return existing

    block = _find_block(element)
    if block is None:
        return ""

    block_id = block.get("id")
    if block_id:
        used_ids.add(block_id)
        return block_id

    new_id = candidate_id
    n = 2
    while new_id in used_ids:
        new_id = f"{candidate_id}-{n}"
        n += 1
    block.set("id", new_id)
    used_ids.add(new_id)
    chapter.modified = True
    return new_id


def build_toc_entries(book, chapter_summary=None) -> list[TocEntry]:
    """The main entry point. Returns a nested TocEntry list mirroring
    the book's detected Part/chapter structure (front matter's own
    placeholder node is skipped -- it isn't a real, titled section),
    or an empty list if nothing was confirmed the normal (non-case3)
    way.

    `chapter_summary` lets a caller that already has a
    BookChapterSummary on hand (e.g. the repair pipeline's shared
    per-run analysis) pass it in instead of this re-running
    analyze_book_chapters(book) itself.
    """
    summary = chapter_summary if chapter_summary is not None else analyze_book_chapters(book)
    tree = build_structure(summary)
    if not tree.nodes:
        return []

    chapters_by_href = {c.href: c for c in book.chapters}
    used_ids_by_href: dict[str, set] = {}
    anchor_counter = count(1)

    def used_ids_for(href):
        if href not in used_ids_by_href:
            chapter = chapters_by_href.get(href)
            doc = chapter.document if chapter is not None else None
            used_ids_by_href[href] = (
                {e.get("id") for e in doc.iter() if e.get("id")}
                if doc is not None else set()
            )
        return used_ids_by_href[href]

    def entry_for(node):
        href = node.start_href
        candidate = node.evidence.candidate if node.evidence is not None else None
        element = getattr(candidate, "element", None)
        chapter = chapters_by_href.get(href)

        anchor_id = ""
        if chapter is not None and element is not None:
            anchor_id = _ensure_anchor_id(
                chapter, element, used_ids_for(href), f"toc-anchor-{next(anchor_counter)}"
            )
        target = f"{href}#{anchor_id}" if anchor_id else href

        return TocEntry(
            label=node.title,
            href=target,
            children=[entry_for(child) for child in node.children],
        )

    return [
        entry_for(node)
        for node in tree.nodes
        if not (node.kind == NodeKind.FRONT_MATTER and node.evidence is None)
    ]


# ---------------------------------------------------------------------
# Fallback: one entry per spine chapter file, for a book with nothing
# confirmed to build a real structure from. Moved here from
# modules/epub3_upgrade.py so every nav-generation call site shares
# one implementation.
# ---------------------------------------------------------------------

def build_spine_fallback_entries(book) -> list[TocEntry]:
    entries = []
    by_id = {item.id: item for item in book.manifest}
    chapters_by_id = {c.id: c for c in book.chapters}
    for idref in book.spine:
        item = by_id.get(idref)
        if item is None or item.media_type != "application/xhtml+xml":
            continue
        label = _spine_chapter_label(chapters_by_id.get(idref)) or item.href
        entries.append(TocEntry(label=label, href=item.href, children=[]))
    return entries


def _spine_chapter_label(chapter):
    if chapter is None or chapter.document is None:
        return None
    doc = chapter.document
    head_title = doc.find(".//{*}head/{*}title")
    if head_title is not None:
        text = (head_title.text or "").strip()
        if text:
            return text
    for level in range(1, 7):
        heading = doc.find(f".//{{*}}h{level}")
        if heading is not None:
            text = "".join(heading.itertext()).strip()
            if text:
                return text
    return None


# ---------------------------------------------------------------------
# Rendering a TocEntry tree as EPUB3 nav.xhtml bytes
# ---------------------------------------------------------------------

def _build_toc_ol(E, entries):
    ol = etree.Element(E + "ol")
    for entry in entries:
        li = etree.SubElement(ol, E + "li")
        if entry.href:
            a = etree.SubElement(li, E + "a")
            a.set("href", entry.href)
            a.text = entry.label or entry.href
        else:
            # EPUB 3 nav requires a <span>, not bare <li> text, for an
            # entry with nothing to link to (rare -- a heading-only
            # navPoint with no content src).
            span = etree.SubElement(li, E + "span")
            span.text = entry.label or ""
        if entry.children:
            li.append(_build_toc_ol(E, entry.children))
    return ol


def build_nav_bytes(book, entries, title=None) -> bytes:
    """Renders `entries` (a TocEntry list -- book.toc's own existing
    entries, this module's build_toc_entries output, or
    build_spine_fallback_entries's output) as a complete EPUB3
    nav.xhtml document."""
    E = f"{{{XHTML_NS}}}"
    html = etree.Element(E + "html", nsmap={None: XHTML_NS, "epub": EPUB_OPS_NS})
    head = etree.SubElement(html, E + "head")
    title_el = etree.SubElement(head, E + "title")
    title_el.text = title or getattr(book.metadata, "title", "") or "Table of Contents"

    body = etree.SubElement(html, E + "body")
    nav = etree.SubElement(body, E + "nav")
    nav.set(f"{{{EPUB_OPS_NS}}}type", "toc")
    nav.set("id", "toc")
    heading = etree.SubElement(nav, E + "h1")
    heading.text = "Table of Contents"
    nav.append(_build_toc_ol(E, entries))

    return etree.tostring(
        html,
        xml_declaration=True,
        encoding="utf-8",
        doctype="<!DOCTYPE html>",
    )


# ---------------------------------------------------------------------
# Rendering a TocEntry tree as a brand-new toc.ncx document
# ---------------------------------------------------------------------

def _max_depth(entries, level=1):
    if not entries:
        return level - 1
    return max(
        (_max_depth(e.children, level + 1) if e.children else level)
        for e in entries
    )


def _build_nav_point(E, entry, play_order_counter):
    n = next(play_order_counter)
    point = etree.Element(E + "navPoint")
    point.set("id", f"navpoint-{n}")
    point.set("playOrder", str(n))

    nav_label = etree.SubElement(point, E + "navLabel")
    text_el = etree.SubElement(nav_label, E + "text")
    text_el.text = entry.label or entry.href

    content = etree.SubElement(point, E + "content")
    content.set("src", entry.href)

    for child in entry.children:
        point.append(_build_nav_point(E, child, play_order_counter))
    return point


def build_ncx_document(book, entries):
    """Builds a complete, brand-new <ncx> root element (head, docTitle,
    navMap) from `entries` -- nothing in the project has needed to do
    this before now; every prior NCX-editing function (crossref.py's
    generate_missing_ncx_entries, rewrite_ncx_links) only ever edited
    an NCX a book already shipped with. Returns the live lxml element,
    same idiom as book.opf_document -- the caller sets
    book.ncx_document/book.ncx_href/book.ncx_modified so the writer
    serializes it."""
    E = f"{{{NCX_NS_URI}}}"
    ncx = etree.Element(E + "ncx", nsmap={None: NCX_NS_URI})
    ncx.set("version", "2005-1")

    head = etree.SubElement(ncx, E + "head")
    meta_uid = etree.SubElement(head, E + "meta")
    meta_uid.set("name", "dtb:uid")
    meta_uid.set("content", getattr(book.metadata, "identifier", "") or "")
    meta_depth = etree.SubElement(head, E + "meta")
    meta_depth.set("name", "dtb:depth")
    meta_depth.set("content", str(max(_max_depth(entries), 1)))
    for name in ("dtb:totalPageCount", "dtb:maxPageNumber"):
        meta = etree.SubElement(head, E + "meta")
        meta.set("name", name)
        meta.set("content", "0")

    doc_title = etree.SubElement(ncx, E + "docTitle")
    title_text = etree.SubElement(doc_title, E + "text")
    title_text.text = getattr(book.metadata, "title", "") or "Untitled"

    nav_map = etree.SubElement(ncx, E + "navMap")
    play_order_counter = count(1)
    for entry in entries:
        nav_map.append(_build_nav_point(E, entry, play_order_counter))

    return ncx
