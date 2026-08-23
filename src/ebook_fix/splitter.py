"""
ebook_fix.splitter

Phase 1 of the XHTML Recoder plan (see docs/xhtml_recoder_plan.md):
single-file splitting mechanics. Given one already-parsed XHTML
Chapter and a set of confirmed split points inside it, physically cuts
that one file into several standalone chapter files, wires them into
the manifest/spine in the right reading order, and verifies -- with a
plain word-count check -- that the split didn't lose or duplicate a
single word of content.

Deliberately out of scope for this phase (see the plan doc):
- No cross-reference rewriting. Anything that already pointed at the
  original file (a footnote, a TOC entry) still points at whatever
  ended up keeping that filename after the split -- see
  build_split_documents below for which segment keeps it.
- No NCX/nav regeneration. A book's existing TOC is left exactly as it
  was found.
- No judgment about which boundaries are safe to split on. That's
  structure.py's job (see split_safety_bar.md) -- this module trusts
  whatever markers it's handed and only refuses when the *mechanics*
  can't be done safely (an ambiguous or structurally tangled split
  point), not when the underlying content looks risky. Only a caller
  wiring this up to real analysis (a future review command, per Phase
  5 of the plan) should be filtering for CORROBORATED boundaries
  before it ever gets here.

This reuses modules/gutenberg_repair.py's existing tree-walking
approach (find <body>, then walk up to whichever ancestor is a direct
child of it) rather than inventing a new one -- see
_ancestor_under_body below, the same idea applied to chapter markers
instead of boilerplate markers.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from lxml import etree

from .models import Chapter, ManifestItem

OPF_NS = "http://www.idpf.org/2007/opf"
XHTML_MEDIA_TYPE = "application/xhtml+xml"


class SplitError(Exception):
    """Raised when a requested split can't be done safely -- an
    ambiguous marker, markers out of order, or a document shape this
    module doesn't know how to cut, or a failed integrity check.
    Callers should treat this the same way the rest of the project
    treats "needs a person to look" cases: report it and leave the
    book untouched, don't guess."""


# ---------------------------------------------------------------------
# Input: one requested cut point
# ---------------------------------------------------------------------


@dataclass(slots=True)
class SplitMarker:
    """One place to cut, in document order. `element` is the live
    lxml element the marker text was found on -- the same object
    structure.py/chapters.py already carry (BoundaryEvidence.candidate
    .element), not re-derived here. `number` drives file naming (see
    generate_split_hrefs) when the chapter is numbered; leave it None
    for an unnumbered chapter."""

    element: object
    title: str = ""
    number: int | None = None


# ---------------------------------------------------------------------
# Output: one resulting slice of the file, before it becomes its own
# standalone document
# ---------------------------------------------------------------------


@dataclass(slots=True)
class SplitSegment:
    title: str = ""
    number: int | None = None
    elements: list = field(default_factory=list)  # body children, in order


# ---------------------------------------------------------------------
# Result summary, returned by apply_split
# ---------------------------------------------------------------------


@dataclass(slots=True)
class SplitResult:
    original_href: str = ""
    new_hrefs: list = field(default_factory=list)  # newly created files only
    segment_word_counts: list = field(default_factory=list)  # one per resulting file, in order
    original_word_count: int = 0
    href_by_id: dict = field(default_factory=dict)  # every id that existed in the
    # original file, mapped to whichever href now holds it -- original_href for
    # anything that stayed in segment 0, one of new_hrefs for anything that moved.
    # This is Phase 2a of the XHTML Recoder plan (see docs/xhtml_recoder_plan.md):
    # the foundation cross-reference rewriting needs, before any link is actually
    # touched. Doesn't include ids that were never used as a link target -- it's
    # every id regardless, since there's no way to know in advance which ones a
    # link elsewhere in the book might point at.

    @property
    def word_counts_match(self) -> bool:
        return sum(self.segment_word_counts) == self.original_word_count


# ---------------------------------------------------------------------
# Shared tree helpers -- same idiom as modules/gutenberg_repair.py
# ---------------------------------------------------------------------


def _find_body(document):
    if document is None:
        return None
    return document.find(".//{*}body")


def _find_head(document):
    if document is None:
        return None
    return document.find(".//{*}head")


def _ancestor_under_body(body, element):
    """Walks up from `element` to whichever ancestor is a direct child
    of `body` -- the unit this module cuts on. Same helper
    modules/gutenberg_repair.py already uses for boilerplate removal,
    applied here to chapter markers instead."""
    node = element
    while node is not None:
        parent = node.getparent()
        if parent is None:
            return None
        if parent is body:
            return node
        node = parent
    return None


def _effective_container(body):
    """Some conversions (calibre in particular) wrap a book's entire
    content in one lone <div> directly under <body>, sometimes several
    layers deep, before any real per-chapter content starts. Splitting
    strictly on <body>'s own direct children would see every chapter
    marker as sitting inside that one wrapper and refuse the whole
    book. This walks down through any such single-child, no-text-of-
    its-own wrapper chain and returns the first element that actually
    branches (multiple children) or holds real per-chapter content --
    that's the real unit to cut on. Returns `body` itself, unchanged,
    for the ordinary case where chapters already sit directly under
    it."""
    container = body
    while not (container.text or "").strip():
        children = list(container)
        if len(children) != 1:
            break
        only_child = children[0]
        if not isinstance(only_child.tag, str):
            break
        container = only_child
    return container


def _wrapper_chain(body, container):
    """Elements from `body`'s direct child down to and including
    `container` itself (see _effective_container), in top-down order
    -- every level of wrapping that needs to be recreated in each new
    document, e.g. [<div>] for one wrapper level, [<div>, <div>] for
    two nested ones. Empty when `container` is `body` itself (the
    ordinary case, nothing to rebuild)."""
    if container is body:
        return []
    chain = [container]
    node = container
    while True:
        parent = node.getparent()
        if parent is None:
            return []  # shouldn't happen; container came from a walk down from body
        if parent is body:
            break
        chain.append(parent)
        node = parent
    chain.reverse()
    return chain


def _word_count(element) -> int:
    if element is None:
        return 0
    return len("".join(element.itertext()).split())


def _index_by_identity(seq, item):
    """`list.index()` for dataclasses would fall back to field-value
    equality, not identity -- fine most of the time, but Chapter and
    ManifestItem entries can legitimately compare equal on fields
    while still being different slots in the list. Identity is what
    we actually mean here."""
    for i, candidate in enumerate(seq):
        if candidate is item:
            return i
    return None


def _unique(existing: set, candidate: str, template: str) -> str:
    """Same de-duplication idiom as modules/epub3_upgrade.py's
    _unique -- appends _2, _3, ... until the name is free."""
    if candidate not in existing:
        return candidate
    n = 2
    while template.format(n=n) in existing:
        n += 1
    return template.format(n=n)


# ---------------------------------------------------------------------
# Step 1 -- turn markers into body-level segments
# ---------------------------------------------------------------------


def split_body_at_markers(document, markers: list) -> list:
    """
    Figures out where to cut `document`'s <body> for each marker, and
    groups its direct children into segments -- everything from one
    marker's top-level ancestor (inclusive) up to the next one
    (exclusive). Content before the first marker becomes a leading,
    untitled segment; if there isn't any (the first marker's top-level
    element is <body>'s very first child), no leading segment is
    produced at all, and the first real segment keeps the original
    file (see build_split_documents).

    Doesn't move anything yet -- see build_split_documents for that.

    Raises SplitError if a marker can't be placed unambiguously:
    - the marker's element isn't inside <body> at all,
    - two markers resolve to the same top-level element (both sit
      inside the same body-level container -- there's no safe way to
      divide a single element's content between two files), or
    - the markers aren't given in increasing document order.
    """
    body = _find_body(document)
    if body is None:
        raise SplitError("No <body> element found in this document.")
    if not markers:
        raise SplitError("No split markers given.")

    container = _effective_container(body)
    children = list(container)
    placed: list = []  # (index_in_children, marker, top_element)

    for marker in markers:
        if marker.element is None:
            raise SplitError(f"Marker {marker.title!r} has no element to split at.")
        top = _ancestor_under_body(container, marker.element)
        if top is None:
            raise SplitError(f"Marker {marker.title!r} isn't inside this document's <body>.")
        if any(existing_top is top for _, _, existing_top in placed):
            raise SplitError(
                f"Marker {marker.title!r} sits in the same container element "
                f"as another marker -- can't split one element into two files."
            )
        index = next((i for i, c in enumerate(children) if c is top), None)
        if index is None:
            raise SplitError(
                f"Marker {marker.title!r}'s top-level element isn't a direct "
                f"child of its container -- unexpected document shape."
            )
        placed.append((index, marker, top))

    placed.sort(key=lambda p: p[0])
    indices = [p[0] for p in placed]
    if indices != sorted(set(indices)):
        raise SplitError(
            "Split markers must resolve to distinct positions in document order."
        )

    boundaries = indices + [len(children)]
    segments: list = []

    leading = children[0:boundaries[0]]
    if leading:
        segments.append(SplitSegment(title="", number=None, elements=leading))

    for (start, marker, _), end in zip(placed, boundaries[1:]):
        segments.append(
            SplitSegment(title=marker.title, number=marker.number, elements=children[start:end])
        )

    return segments


# ---------------------------------------------------------------------
# Step 2 -- turn segments into standalone documents
# ---------------------------------------------------------------------


def build_split_documents(document, segments: list) -> list:
    """
    Produces one standalone <html> document per segment. The FIRST
    segment reuses `document` itself -- same <head>, stylesheet links,
    root attributes, and doctype, just a trimmed-down <body>. Every
    later segment gets a fresh <html> document with a deep copy of the
    same <head> (so it keeps the same stylesheets/metadata) and a new
    <body> carrying the original body's tag and attributes.

    Moving each later segment's elements out of `document`'s body (via
    .append() below, which detaches an already-parented lxml element
    automatically) is what leaves `document`'s own body holding only
    segment 0's content -- nothing further needs to be done to it.
    """
    if not segments:
        raise SplitError("No segments to build documents from.")

    body = _find_body(document)
    head = _find_head(document)
    if body is None:
        raise SplitError("No <body> element found in this document.")

    container = _effective_container(body)
    chain = _wrapper_chain(body, container)  # e.g. [<div>] for a calibre-style single wrapper

    documents = [document]

    for segment in segments[1:]:
        new_root = etree.Element(document.tag, nsmap=document.nsmap)
        for key, value in document.attrib.items():
            new_root.set(key, value)

        if head is not None:
            new_root.append(copy.deepcopy(head))

        new_body = etree.SubElement(new_root, body.tag)
        for key, value in body.attrib.items():
            new_body.set(key, value)

        # Rebuild any wrapper chain between <body> and the real
        # per-chapter container (see _effective_container) so this
        # segment's content ends up at the same nesting depth --
        # each wrapper is copied empty, attributes only, since only
        # this segment's own slice of content belongs inside it.
        innermost = new_body
        for wrapper in chain:
            new_wrapper = etree.SubElement(innermost, wrapper.tag)
            for key, value in wrapper.attrib.items():
                new_wrapper.set(key, value)
            innermost = new_wrapper

        for element in segment.elements:
            innermost.append(element)

        documents.append(new_root)

    return documents


def _ids_in_segment(segment: SplitSegment) -> set:
    """Every `id` attribute anywhere inside a segment's elements,
    including the elements themselves -- not just chapter-heading-
    adjacent ids like structure.py's _candidate_ids checks for
    corroboration. A cross-reference can point at any id in the book
    (a footnote definition, a mid-paragraph anchor, anything), so this
    needs the full subtree, not a shortcut."""
    ids = set()
    for element in segment.elements:
        for el in element.iter():
            if not isinstance(el.tag, str):
                continue  # skip comments/PIs, which iter() also yields
            el_id = el.get("id")
            if el_id:
                ids.add(el_id)
    return ids


def build_href_by_id(segments: list, hrefs: list) -> dict:
    """Maps every id that existed in the original file to whichever
    href now holds it, given the same segments/hrefs pairing
    generate_split_hrefs produces (one href per segment, in order,
    with hrefs[0] always the original href). Called once, right after
    the split's own word-count integrity check passes -- see
    apply_split below."""
    href_by_id: dict = {}
    for segment, href in zip(segments, hrefs):
        for el_id in _ids_in_segment(segment):
            href_by_id[el_id] = href
    return href_by_id


def _reassemble_original_body(segments: list) -> None:
    """Undoes build_split_documents if the integrity check below ever
    fails: moves every later segment's elements back where they came
    from, in original order. Segment 0's elements never left `body` in
    the first place, so nothing needs to happen for those."""
    if len(segments) < 2:
        return
    # Every later segment's elements currently live under some other
    # segment's new body; segment 0's first element's parent is the
    # original body they all belong back in.
    original_body = segments[0].elements[0].getparent() if segments[0].elements else None
    if original_body is None:
        return
    for segment in segments[1:]:
        for element in segment.elements:
            original_body.append(element)


# ---------------------------------------------------------------------
# Step 3 -- naming the new files
# ---------------------------------------------------------------------


def generate_split_hrefs(original_href: str, segments: list, existing_hrefs: set) -> list:
    """
    One href per segment, in the same directory as the original file.
    Segment 0 always keeps the original href (it's the same file, just
    trimmed -- see build_split_documents). Every other segment is
    named by chapter number when one was detected (chapter_003.xhtml),
    or by its position among the newly-created files when it wasn't
    (chapter_002.xhtml, counting only the unnumbered ones), per how
    Jacob asked for these to be named. Falls back to _unique on a
    collision with an existing file or another segment from this same
    split.
    """
    directory = PurePosixPath(original_href).parent
    suffix = PurePosixPath(original_href).suffix or ".xhtml"
    prefix = f"{directory}/" if str(directory) not in ("", ".") else ""

    hrefs = [original_href]
    used = set(existing_hrefs) | {original_href}
    unnumbered_position = 0

    for segment in segments[1:]:
        if segment.number is not None:
            stem = f"chapter_{segment.number:03d}"
        else:
            unnumbered_position += 1
            stem = f"chapter_{unnumbered_position:03d}"
        candidate = f"{prefix}{stem}{suffix}"
        href = _unique(used, candidate, f"{prefix}{stem}_{{n}}{suffix}")
        used.add(href)
        hrefs.append(href)

    return hrefs


# ---------------------------------------------------------------------
# Step 4 -- wiring the result into the book
# ---------------------------------------------------------------------


def _wire_into_book(book, chapter, documents: list, segments: list, new_hrefs: list) -> None:
    """Updates every place a chapter's identity is tracked: the live
    OPF <manifest>/<spine> elements, book.manifest, book.spine, and
    book.chapters -- the same four touch points
    modules/gutenberg_repair.py and modules/epub3_upgrade.py already
    keep in sync for a removed/added file, applied here for several
    added files at once."""
    opf = getattr(book, "opf_document", None)
    if opf is None:
        raise SplitError("Book has no OPF document to update.")

    manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
    spine_el = opf.find(f"{{{OPF_NS}}}spine")
    if manifest_el is None or spine_el is None:
        raise SplitError("Book's OPF is missing a <manifest> or <spine>.")

    original_manifest_item = next((m for m in book.manifest if m.href == chapter.href), None)
    if original_manifest_item is None:
        raise SplitError("Original chapter isn't listed in the manifest.")
    original_item_id = original_manifest_item.id

    manifest_el_item = next(
        (i for i in manifest_el.findall(f"{{{OPF_NS}}}item") if i.get("id") == original_item_id),
        None,
    )
    itemref_el = next(
        (i for i in spine_el.findall(f"{{{OPF_NS}}}itemref") if i.get("idref") == original_item_id),
        None,
    )
    if manifest_el_item is None or itemref_el is None:
        raise SplitError("Original chapter's manifest item or spine entry is missing from the OPF.")

    existing_ids = {m.id for m in book.manifest}

    chapter.document = documents[0]
    chapter.modified = True
    if segments[0].title:
        chapter.title = segments[0].title

    new_manifest_items = [original_manifest_item]
    new_chapters = [chapter]
    new_itemref_ids = [original_item_id]
    base = PurePosixPath(book.package_path).parent

    for href, document, segment in zip(new_hrefs[1:], documents[1:], segments[1:]):
        item_id = _unique(
            existing_ids,
            f"split-{PurePosixPath(href).stem}",
            f"split-{PurePosixPath(href).stem}-{{n}}",
        )
        existing_ids.add(item_id)

        new_chapter = Chapter(
            id=item_id,
            href=href,
            media_type=XHTML_MEDIA_TYPE,
            title=segment.title,
            document=document,
            modified=True,
        )
        new_chapters.append(new_chapter)
        new_manifest_items.append(ManifestItem(id=item_id, href=href, media_type=XHTML_MEDIA_TYPE))
        new_itemref_ids.append(item_id)

        item_el = etree.Element(f"{{{OPF_NS}}}item")
        item_el.set("id", item_id)
        item_el.set("href", href)
        item_el.set("media-type", XHTML_MEDIA_TYPE)
        manifest_el_item.addnext(item_el)
        manifest_el_item = item_el  # chain each insert after the last one added

        new_itemref_el = etree.Element(f"{{{OPF_NS}}}itemref")
        new_itemref_el.set("idref", item_id)
        itemref_el.addnext(new_itemref_el)
        itemref_el = new_itemref_el

    manifest_index = _index_by_identity(book.manifest, original_manifest_item)
    book.manifest[manifest_index:manifest_index + 1] = new_manifest_items

    spine_index = book.spine.index(original_item_id)
    book.spine[spine_index:spine_index + 1] = new_itemref_ids

    chapters_index = _index_by_identity(book.chapters, chapter)
    book.chapters[chapters_index:chapters_index + 1] = new_chapters

    book.opf_modified = True
    book.mark_modified()


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------


def apply_split(book, chapter, markers: list) -> SplitResult:
    """
    Phase 1's entry point: physically splits `chapter` (must already
    be one of `book.chapters`) at `markers` and wires the results into
    the book's manifest, spine, and chapter list.

    Safety net: total word count across every resulting file is
    compared against the original file's word count before any
    manifest/spine change is made. A mismatch raises SplitError and
    leaves the book completely untouched -- every moved element is put
    back where it came from first, so a failed split can't half-apply.
    """
    if chapter not in book.chapters:
        raise SplitError("That chapter isn't part of this book.")
    if chapter.document is None:
        raise SplitError("Chapter has no parsed document to split.")

    body = _find_body(chapter.document)
    if body is None:
        raise SplitError("No <body> element found in this document.")
    original_word_count = _word_count(body)

    segments = split_body_at_markers(chapter.document, markers)
    if len(segments) < 2:
        raise SplitError("That split would only produce one file -- nothing to split.")

    documents = build_split_documents(chapter.document, segments)
    segment_word_counts = [_word_count(_find_body(doc)) for doc in documents]

    if sum(segment_word_counts) != original_word_count:
        _reassemble_original_body(segments)
        raise SplitError(
            f"Integrity check failed: {original_word_count} word(s) before the "
            f"split, {sum(segment_word_counts)} word(s) across the resulting "
            f"files. No changes were made."
        )

    existing_hrefs = {m.href for m in book.manifest}
    new_hrefs = generate_split_hrefs(chapter.href, segments, existing_hrefs)
    href_by_id = build_href_by_id(segments, new_hrefs)

    _wire_into_book(book, chapter, documents, segments, new_hrefs)

    return SplitResult(
        original_href=chapter.href,
        new_hrefs=new_hrefs[1:],
        segment_word_counts=segment_word_counts,
        original_word_count=original_word_count,
        href_by_id=href_by_id,
    )