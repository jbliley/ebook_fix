"""
ebook_fix.mobi.kf8

KF8 (AZW3)-specific structure, built on top of the same low-level
pieces ebook_fix.mobi.reader already uses for MOBI7 (palmdb, header,
EXTH, PalmDOC/HUFF-CDIC decompression). Kept in its own module rather
than folded into reader.py because a KF8 book's actual shape -- several
flows, and (from Phase 3 onward) skeleton/fragment reassembly -- is
different enough from MOBI7's flat "one text blob, split at page
breaks" that growing MobiBook/read_mobi() to cover both would make
both harder to follow. See docs/azw3_kf8_conversion_plan.md for the
phase this belongs to and everything confirmed against the three real
AZW3 samples in examples/ along the way.

Phase 2 (this phase): flow separation. A KF8 book's text records
decompress into one continuous stream that is NOT just the book's
text -- flow 0 is the main HTML, and any flows after it are embedded
CSS (and, unconfirmed against a real sample, SVG). `header.text_length`
is not reliable for telling flow 0 apart from the rest: it means flow
0's own length in one real sample and the *total* decompressed length
across every flow in the other two -- this was the source of a real
bug fixed in Phase 1 (ebook_fix.mobi.reader wrongly treated a longer
decompressed stream as an error). The FDST record gives the exact byte
range of each flow and is trusted here instead, checked for its own
internal consistency rather than against that field.

`read_kf8()` is the KF8 counterpart to ebook_fix.mobi.reader.read_mobi():
where that function refuses any file_version >= 8 book (unless it's
reading a hybrid file's MOBI7 half), this one refuses anything that
ISN'T file_version >= 8, so the two are complementary gatekeepers
rather than overlapping ones.

Phase 3 (docs/azw3_kf8_conversion_plan.md): flow 0 (Phase 2's output) is
not one continuous document -- it's skeleton template pieces and
fragment content pieces interleaved, each skeleton immediately followed
by the fragment(s) that belong inside it. read_skeleton_table(),
read_fragment_table() and read_guide_table() read the three INDX-family
indices that describe this (a fourth, the chapter table of contents,
is Phase 5's job -- it reuses MobiHeader.ncx_index_record, the same
field MOBI7's NCX uses). reassemble_flow0() splices fragment content
back into its skeleton to produce one complete page per skeleton entry,
confirming the tables are being read correctly (Phase 3's own stated
goal) even though turning that into well-formed, individually-named
XHTML files wired into the rest of the converter is Phase 4's job.

Phase 4 (docs/azw3_kf8_conversion_plan.md): every reassemble_flow0() page
turned out to already be a complete, well-formed standalone document on
its own -- its own `<?xml?>` declaration, its own `<html><head>...
</head><body>...</body></html>`, confirmed identical (structurally) on
every single page across all three real samples, not just the first.
The Phase 3 "known gap" note about this was wrong: there was nothing
left to *build* here, only to confirm and then strip back down.
extract_page_body() does that stripping: it pulls out just the
`<body>` element's inner content, as a decoded string, matching the
shape ebook_fix.epub_builder.EpubSpec.pages already expects (the same
shape ebook_fix.mobi.markup.Page.body and the FB2 converter's pages
use) -- so a later phase (7) can finish a KF8 page through the exact
same shared `page_filename()`/`_page_xhtml()` wrapper every other
converter uses, rather than keeping each page's own Kindle-generated
`<head>` (an "Adept.expected..." meta tag that looks DRM-related but
isn't, a `<title>` that's just the book's own title repeated on every
page rather than a real per-page one, and a stylesheet reference in
the KF8 `kindle:flow:...` addressing scheme rather than a real href --
none of that is something Phase 7 wants to carry forward as-is).
Real per-page filenames need no new code here: book.pages is already
in final reading order (confirmed identical to skeleton-table order,
which is confirmed correct -- see Phase 3), so a later phase can call
ebook_fix.epub_builder.page_filename() directly against that order,
exactly like ebook_fix.mobi.convert.py already does for MOBI7.

The Sept 24 exploration notes guessed the labels backwards (see
azw3_kf8_conversion_plan.md's Phase 3 section) -- the 127-entry table in
AZW3-Example.azw3 is FRAGMENT, not skeleton, and the 70-entry
SKELnnnnnnnnnn-keyed one is SKELETON, not fragment. Confirmed multiple
ways: by literally reconstructing pages from all three real samples and
checking the result reads correctly (a `</body></html>` immediately
followed by the exact next fragment's opening content, over and over);
and cross-checked against a reference tool (a third-party MOBI-unpacking
package, used here only as a one-off test oracle to compare table
contents against, not a dependency -- same role it played verifying
HUFF/CDIC in Phase 1).
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field, replace
from pathlib import Path

from ebook_fix.mobi.indx import NcxIndexError, _decode_text, read_indx_records
from ebook_fix.mobi.mobi_header import ExthRecord, MobiHeader, exth_int, exth_start_offset, read_exth, read_mobi_header
from ebook_fix.mobi.palmdb import PalmDBHeader, is_palmdb, read_palmdb, record_bytes
from ebook_fix.mobi.reader import MobiError, MobiImage, _read_images, read_text_records

NO_RECORD = 0xFFFFFFFF

# Tag numbers, confirmed against the skeleton/fragment/guide indices of
# all three real AZW3 samples in examples/ and cross-checked against a
# reference tool -- see the module docstring above and
# docs/azw3_kf8_conversion_plan.md, Phase 3.
_SKEL_TAG_FRAGMENT_COUNT = 1
_SKEL_TAG_GEOMETRY = 6           # 2 values: (start, length) of this skeleton's own template within flow 0

_FRAG_TAG_AID_LABEL = 2          # offset into this table's own CTOC: the fragment's "aid" XPath selector text
_FRAG_TAG_FILE_NUMBER = 3
_FRAG_TAG_SEQUENCE_NUMBER = 4
_FRAG_TAG_GEOMETRY = 6           # 2 values: (start [confirmed unused, see reassemble_flow0], length)

_GUIDE_TAG_LABEL = 1             # offset into this table's own CTOC: the human-readable guide title
_GUIDE_TAG_FRAGMENT_NUMBER = 6   # index into the fragment table (confirmed, not a skeleton/part index, by a
                                  # reference tool's own comment: "fileno is actually a reference into fragtbl")

_TOC_TAG_LABEL = 3               # offset into this table's own CTOC: the chapter/heading title
_TOC_TAG_LEVEL = 4               # nesting depth, 0-based -- directly usable by
                                  # ebook_fix.epub_builder's flat-list-with-level tree builder, same as MOBI7's NCX
_TOC_TAG_CHILD1 = 22             # first child's index, in this table's own storage order (see read_toc_table)
_TOC_TAG_CHILDN = 23             # last child's index (inclusive), same order
_TOC_TAG_POSITION = 6            # 2 values: (fragment_number, offset) -- an index into read_fragment_table()'s
                                  # result and a byte offset into that fragment's own content. It's not base32-
                                  # encoded (that only applies to the textual kindle:pos:fid:...:off:... scheme
                                  # used for hrefs inside a page's own body, which Phase 6 will need to decode --
                                  # this tag's own value is already a plain integer).


@dataclass
class SkeletonEntry:
    name: str = ""             # this table's own entry id (e.g. "SKEL0000000000"); not otherwise used
    fragment_count: int = 0    # how many of read_fragment_table()'s entries, taken in order, insert into this one
    start: int = 0             # byte offset of this skeleton's own template within flow 0
    length: int = 0            # length of that template


@dataclass
class FragmentEntry:
    insert_position: int = 0   # byte offset within flow 0 -- also exactly where this fragment's content starts
    aid: str = ""              # the "aid" attribute value this fragment's content is anchored to
    file_number: int = 0
    sequence_number: int = 0
    length: int = 0            # length of this fragment's own content


@dataclass
class GuideEntry:
    ref_type: str = ""         # e.g. "cover", "toc", "copyright-page" -- an EPUB guide/landmark type
    title: str = ""
    fragment_number: int | None = None   # index into read_fragment_table()'s result, or None if not given


@dataclass
class TocEntry:
    label: str = ""
    level: int = 0
    fragment_number: int = 0   # raw, as read; see resolve_toc_table() for what this turns into
    offset: int = 0
    page_index: int | None = None   # filled in by resolve_toc_table(); None means it couldn't be resolved
    anchor: str = ""                # filled in by resolve_toc_table(); "" means the top of the page


@dataclass
class Kf8Book:
    path: Path = None
    header: MobiHeader = None
    exth: list = field(default_factory=list)
    encoding: str = "cp1252"
    flows: list = field(default_factory=list)   # list[bytes]; flows[0] is the main text
    skeleton_table: list = field(default_factory=list)   # list[SkeletonEntry]
    fragment_table: list = field(default_factory=list)   # list[FragmentEntry]
    guide_table: list = field(default_factory=list)      # list[GuideEntry]; empty if the book has none
    toc: list = field(default_factory=list)              # list[TocEntry], already resolved; empty if the book has none
    images: dict = field(default_factory=dict)            # recindex -> MobiImage; same shape as MobiBook.images
    cover_recindex: int | None = None
    pages: list = field(default_factory=list)            # list[bytes], one per skeleton entry (see reassemble_flow0)
    page_bodies: list = field(default_factory=list)       # list[str], same order, see extract_page_body
    warnings: list = field(default_factory=list)


def read_fdst(data: bytes, palmdb: PalmDBHeader, header: MobiHeader) -> list[tuple[int, int]]:
    """Reads the FDST record's flow byte ranges: [(start, end), ...],
    each a slice into the book's fully decompressed text-record
    stream. Raises MobiError if there's no FDST record, or it doesn't
    look like one -- every real KF8 sample this was built against had
    one; a KF8 book without one isn't something to guess at."""
    if header.fdst_record == NO_RECORD or header.fdst_record >= len(palmdb.records):
        raise MobiError(
            "This KF8 book has no FDST record (the table that separates its text from its "
            "styling), which every sample this converter was built against has. Can't safely "
            "guess at the book's structure without it."
        )
    record = record_bytes(data, palmdb, header.fdst_record)
    if record[0:4] != b"FDST":
        raise MobiError("This KF8 book's FDST record doesn't look like one (wrong tag).")
    try:
        entries_start, count = struct.unpack_from(">II", record, 4)
        flows = []
        for i in range(count):
            start, end = struct.unpack_from(">II", record, entries_start + 8 * i)
            flows.append((start, end))
    except struct.error as exc:
        raise MobiError(f"This KF8 book's FDST record is malformed: {exc}")
    if not flows:
        raise MobiError("This KF8 book's FDST record lists no flows.")
    return flows


def split_flows(text: bytes, ranges: list[tuple[int, int]]) -> list[bytes]:
    """Slices the decompressed text-record stream into its flows.
    Raises MobiError if the ranges don't cleanly cover the stream --
    confirmed, across all three real samples, that flow 0 always
    starts at byte 0 and each later flow starts exactly where the one
    before it ended, with the last flow's end matching the stream's
    own length exactly; anything else means either a misread FDST
    record or a KF8 variant this hasn't been tested against."""
    if ranges[0][0] != 0:
        raise MobiError("This KF8 book's first flow doesn't start at the beginning of its text.")
    for (_, prev_end), (start, _) in zip(ranges, ranges[1:]):
        if start != prev_end:
            raise MobiError("This KF8 book's flows aren't contiguous; can't safely split its text.")
    if ranges[-1][1] != len(text):
        raise MobiError(
            f"This KF8 book's flows account for {ranges[-1][1]:,} bytes of its text, but "
            f"decompressing it produced {len(text):,}. Can't safely split its text."
        )
    return [text[start:end] for start, end in ranges]


def read_skeleton_table(data: bytes, palmdb: PalmDBHeader, header: MobiHeader) -> list[SkeletonEntry]:
    """Reads the skeleton index: one entry per output page, each a byte
    range within flow 0 holding that page's own template markup, with
    an insertion point somewhere inside it for its fragment(s)' content
    (read_fragment_table()) to be spliced into. Raises MobiError if
    there's no skeleton index at all -- every real sample has one."""
    if header.skeleton_index_record == NO_RECORD:
        raise MobiError("This KF8 book has no skeleton index, which every sample this converter was built against has.")
    try:
        entries, _ctoc, encoding = read_indx_records(data, palmdb, header.skeleton_index_record)
    except NcxIndexError as exc:
        raise MobiError(f"This KF8 book's skeleton index is malformed: {exc}")
    table = []
    for entry_id, values in entries:
        geometry = values.get(_SKEL_TAG_GEOMETRY, [0, 0])
        table.append(
            SkeletonEntry(
                name=_decode_text(entry_id, encoding),
                fragment_count=values.get(_SKEL_TAG_FRAGMENT_COUNT, [0])[0],
                start=geometry[0],
                length=geometry[1] if len(geometry) > 1 else 0,
            )
        )
    return table


def read_fragment_table(data: bytes, palmdb: PalmDBHeader, header: MobiHeader) -> list[FragmentEntry]:
    """Reads the fragment index: one entry per piece of real page
    content, in the same order read_skeleton_table()'s fragment_count
    fields expect to consume them. Raises MobiError if there's no
    fragment index at all -- every real sample has one."""
    if header.fragment_index_record == NO_RECORD:
        raise MobiError("This KF8 book has no fragment index, which every sample this converter was built against has.")
    try:
        entries, ctoc, encoding = read_indx_records(data, palmdb, header.fragment_index_record)
    except NcxIndexError as exc:
        raise MobiError(f"This KF8 book's fragment index is malformed: {exc}")
    table = []
    for entry_id, values in entries:
        geometry = values.get(_FRAG_TAG_GEOMETRY, [0, 0])
        aid_offset = values.get(_FRAG_TAG_AID_LABEL, [None])[0]
        aid_text = _decode_text(ctoc.get(aid_offset, b""), encoding) if aid_offset is not None else ""
        try:
            insert_position = int(entry_id)
        except ValueError as exc:
            raise MobiError(f"This KF8 book's fragment index has a non-numeric entry id ({entry_id!r}): {exc}")
        table.append(
            FragmentEntry(
                insert_position=insert_position,
                aid=aid_text,
                file_number=values.get(_FRAG_TAG_FILE_NUMBER, [0])[0],
                sequence_number=values.get(_FRAG_TAG_SEQUENCE_NUMBER, [0])[0],
                length=geometry[1] if len(geometry) > 1 else 0,
            )
        )
    return table


def read_guide_table(data: bytes, palmdb: PalmDBHeader, header: MobiHeader) -> list[GuideEntry]:
    """Reads the guide index (EPUB guide/landmark-style entries: cover,
    table of contents, copyright page, and so on). Not every KF8 book
    has one -- AZW3-Older.azw3 doesn't -- so a missing index isn't an
    error, just an empty result; a malformed one (the index exists but
    can't be read) raises MobiError, same as the other two."""
    if header.guide_index_record == NO_RECORD:
        return []
    try:
        entries, ctoc, encoding = read_indx_records(data, palmdb, header.guide_index_record)
    except NcxIndexError as exc:
        raise MobiError(f"This KF8 book's guide index is malformed: {exc}")
    table = []
    for entry_id, values in entries:
        label_offset = values.get(_GUIDE_TAG_LABEL, [None])[0]
        title = _decode_text(ctoc.get(label_offset, b""), encoding) if label_offset is not None else ""
        table.append(
            GuideEntry(
                ref_type=_decode_text(entry_id, encoding),
                title=title,
                fragment_number=values.get(_GUIDE_TAG_FRAGMENT_NUMBER, [None])[0],
            )
        )
    return table


def read_toc_table(data: bytes, palmdb: PalmDBHeader, header: MobiHeader) -> list[TocEntry]:
    """Reads the book's real, structurally-grounded chapter-by-chapter
    table of contents -- unlike MOBI7, which has no equivalent and
    needs heuristic chapter detection instead. Reuses
    MobiHeader.ncx_index_record, the same field MOBI7's own NCX uses.
    Entries come back with fragment_number/offset still raw; call
    resolve_toc_table() to turn those into a page and an anchor.

    This table's own storage order is NOT depth-first -- it lists
    every top-level entry first, then descends into level 1 for all of
    them, and so on (confirmed against AZW3-Example.azw3: entries 0-15
    are every top-level day/section heading, and only entries 16+ are
    chapters, grouped by which day they belong to via tags 22/23
    rather than appearing right after their own parent). A flat list
    in that order, handed to ebook_fix.epub_builder's stack-based tree
    builder (which assumes a child immediately follows its parent),
    would nest everything under whatever the last-seen entry at the
    right level happened to be -- confirmed wrong by cross-checking
    against a reference tool's own generated toc.ncx before this
    reordering was added, and confirmed right after: reordering by
    tags 22/23 (child1/childn -- read here, used, then discarded; tag
    21, parent, is redundant with walking downward from the top
    instead of up from a child, so it's not read) via depth-first
    traversal, this table's entries in the *returned* list are in the
    right order for that tree builder to work correctly, matching a
    reference tool's own recursive rebuild exactly.

    Treated the same as the guide index: not every book might have
    one, so a missing index isn't an error, just an empty result; a
    malformed one raises MobiError."""
    if header.ncx_index_record == NO_RECORD:
        return []
    try:
        raw_entries, ctoc, encoding = read_indx_records(data, palmdb, header.ncx_index_record)
    except NcxIndexError as exc:
        raise MobiError(f"This KF8 book's table of contents is malformed: {exc}")

    entries: list[TocEntry] = []
    child1s: list[int | None] = []
    childns: list[int | None] = []
    for _entry_id, values in raw_entries:
        label_offset = values.get(_TOC_TAG_LABEL, [None])[0]
        label = _decode_text(ctoc.get(label_offset, b""), encoding) if label_offset is not None else ""
        position = values.get(_TOC_TAG_POSITION, [0, 0])
        entries.append(
            TocEntry(
                label=label,
                level=values.get(_TOC_TAG_LEVEL, [0])[0],
                fragment_number=position[0],
                offset=position[1] if len(position) > 1 else 0,
            )
        )
        child1s.append(values.get(_TOC_TAG_CHILD1, [None])[0])
        childns.append(values.get(_TOC_TAG_CHILDN, [None])[0])

    ordered: list[TocEntry] = []

    def walk(level: int, start: int, end: int) -> None:
        for i in range(max(start, 0), min(end, len(entries))):
            if entries[i].level != level:
                continue
            ordered.append(entries[i])
            child1 = child1s[i]
            if child1 is not None and child1 >= 0:
                childn = childns[i]
                walk(level + 1, child1, (childn if childn is not None else child1) + 1)

    walk(0, 0, len(entries))
    if len(ordered) != len(entries):
        # Some entry's level/child1/childn didn't fit the walk above
        # (an unexpected root level, a missing child pointer on some
        # real-world file this wasn't tested against). Append whatever
        # got missed, in original order, rather than silently dropping
        # part of the table of contents.
        seen = {id(e) for e in ordered}
        ordered.extend(e for e in entries if id(e) not in seen)
    return ordered


def fragment_page_map(skeletons: list[SkeletonEntry], fragments: list[FragmentEntry]) -> list[int]:
    """For each of read_fragment_table()'s entries (by index), which
    page (an index into read_skeleton_table()'s result) it belongs to
    -- the same sequential consumption reassemble_flow0() does, minus
    the byte-splicing. Shared by TOC resolution (below) and, later,
    Phase 6's internal-link resolution, since both need to turn a
    fragment-table index back into a page."""
    mapping = [0] * len(fragments)
    pos = 0
    for page_index, skeleton in enumerate(skeletons):
        for _ in range(skeleton.fragment_count):
            if pos < len(mapping):
                mapping[pos] = page_index
            pos += 1
    return mapping


# The existing id=/name=/aid= attribute closest at-or-before a target
# position, matching the approach a reference tool uses to resolve
# kindle:pos:fid:...:off:... targets -- Kindle's own generator tags
# nearly every element with an "aid=" attribute at fine granularity,
# so a usable one is almost always right there; a brand-new synthetic
# anchor is confirmed unnecessary by every resolution below actually
# landing on or right next to the content the label describes.
_ANCHOR_ID_OR_NAME_RE = re.compile(rb"""<[^>]*\s(?:id|name)\s*=\s*['"]([^'"]*)['"]""", re.I)
_ANCHOR_AID_RE = re.compile(rb"""<[^>]*\said\s*=\s*['"]([^'"]*)['"]""", re.I)


def find_nearest_anchor(page: bytes, position: int) -> str:
    """The nearest existing anchor at or before byte `position` in one
    of reassemble_flow0()'s pages. An "aid=" value comes back prefixed
    "aid-" (it isn't a valid standalone anchor id by itself -- Phase
    6/7 will need to turn a page's own aid= attributes into real id=
    attributes using this same prefix for the two to line up). Returns
    "" (meaning: link to the top of the page) if position lands at or
    before the first content inside <body>, or nothing usable is
    found."""
    position = max(0, min(position, len(page)))
    # A position landing inside a tag's own <...> span means "at this
    # element", not partway through its markup -- snap forward past it.
    next_lt = page.find(b"<", position)
    next_gt = page.find(b">", position)
    if next_lt == position or (next_gt != -1 and (next_lt == -1 or next_gt < next_lt)):
        position = next_gt + 1
    search_end = position
    while True:
        start = page.rfind(b"<", 0, search_end)
        if start == -1:
            return ""
        end = page.find(b">", start)
        if end == -1:
            search_end = start
            continue
        tag = page[start:end + 1]
        lowered = tag[:6].lower()
        if lowered in (b"<body ", b"<body>"):
            return ""
        if lowered != b"<meta ":
            match = _ANCHOR_ID_OR_NAME_RE.match(tag)
            if match:
                return match.group(1).decode("latin-1")
            match = _ANCHOR_AID_RE.match(tag)
            if match:
                return "aid-" + match.group(1).decode("latin-1")
        search_end = start


def resolve_fragment_position(
    skeletons: list[SkeletonEntry], fragments: list[FragmentEntry], fragment_page: list[int], fid: int, off: int
) -> tuple[int, int] | None:
    """Turns a raw (fragment_number, offset) pair -- whether it came
    from a TOC/guide table's own tag 6, or was decoded from a
    kindle:pos:fid:...:off:... string found literally in a page's own
    body (Phase 6, see rewrite_internal_links() below) -- into
    (page_index, local_offset_within_that_page's raw bytes), suitable
    for find_nearest_anchor(). Returns None if fid is out of range."""
    if not (0 <= fid < len(fragments)):
        return None
    fragment = fragments[fid]
    page_index = fragment_page[fid]
    skeleton = skeletons[page_index]
    return page_index, (fragment.insert_position - skeleton.start) + off


def resolve_toc_table(
    entries: list[TocEntry], skeletons: list[SkeletonEntry], fragments: list[FragmentEntry], pages: list[bytes]
) -> list[TocEntry]:
    """Resolves each TOC entry's raw (fragment_number, offset) to a
    real (page_index, anchor), via find_nearest_anchor() above. An
    entry whose fragment_number is out of range comes back with
    page_index left as None rather than raising -- matching how
    ebook_fix.mobi.convert.py already treats a MOBI7 NCX entry
    pointing nowhere real: a per-entry problem, not a reason to fail
    the whole conversion."""
    fragment_page = fragment_page_map(skeletons, fragments)
    resolved = []
    for entry in entries:
        target = resolve_fragment_position(skeletons, fragments, fragment_page, entry.fragment_number, entry.offset)
        if target is None:
            resolved.append(replace(entry, page_index=None, anchor=""))
            continue
        page_index, local_position = target
        anchor = find_nearest_anchor(pages[page_index], local_position)
        resolved.append(replace(entry, page_index=page_index, anchor=anchor))
    return resolved


# Phase 6: rewriting a page's own kindle:pos:fid:...:off:... (internal
# links), kindle:embed:... (images), and kindle:flow:...?mime=text/css
# (stylesheets) references to real hrefs. Confirmed present in all
# three real samples in exactly this shape (kindle:embed always with a
# ?mime= parameter when it's an image; no kindle:flow:...?mime=image/
# svg+xml reference appears in any of the three, so that case is
# handled the same way as the general kindle:flow case below but
# hasn't been exercised against a real sample). None of these three
# functions invent a naming scheme themselves -- each takes a callable
# that supplies the real href for a resolved target, matching how
# Phase 4 already deferred filename assignment to
# ebook_fix.epub_builder.page_filename() rather than inventing its
# own; a later phase supplies image/flow href callables built the same
# way ebook_fix.mobi.convert.py already names MOBI7's images, so a
# KF8 and a MOBI7 conversion produce identically-named image files.
_POSFID_RE = re.compile(r"""kindle:pos:fid:([0-9A-Va-v]+):off:([0-9A-Va-v]+)""")
_UNRESOLVED_HREF_RE = re.compile(r'''\s?href=["']\x03["']''')
_EMBED_RE = re.compile(r"""kindle:embed:([0-9A-Va-v]+)(?:\?mime=[^'"\)]*)?""")
_FLOW_CSS_RE = re.compile(r"""kindle:flow:([0-9A-Va-v]+)\?mime=text/css[^'"\)]*""")
_AID_ATTR_RE = re.compile(r'''\said=['"]([^'"]*)['"]''')


def rewrite_internal_links(
    body: str,
    skeletons: list[SkeletonEntry],
    fragments: list[FragmentEntry],
    fragment_page: list[int],
    pages: list[bytes],
    page_filename,
) -> tuple[str, set[str]]:
    """Rewrites every kindle:pos:fid:...:off:... reference in a page's
    body to a real "filename#anchor" href, via
    resolve_fragment_position()/find_nearest_anchor() above --
    `page_filename` is a callable, index -> str (e.g.
    ebook_fix.epub_builder.page_filename), always applied even for a
    link back to the same page, matching how
    ebook_fix.mobi.markup.resolve_links() already does it for MOBI7's
    own filepos links. Returns the rewritten body, plus the set of
    aid= values (without the "aid-" prefix find_nearest_anchor()
    returns them with) that ended up actually referenced -- needed by
    finish_page_anchors() below, since a page's own internal links can
    reference an aid= just as easily as a TOC or guide entry can.

    A reference whose fragment number is out of range has its whole
    href="..." attribute removed rather than left broken -- matching
    resolve_links()'s own treatment of an unresolvable MOBI7 filepos
    link exactly (leaving plain, unlinked text instead of a dead
    link)."""
    linked_aids: set[str] = set()

    def replace(match: re.Match) -> str:
        fid = int(match.group(1), 32)
        off = int(match.group(2), 32)
        target = resolve_fragment_position(skeletons, fragments, fragment_page, fid, off)
        if target is None:
            return "\x03"
        target_page, local_position = target
        anchor = find_nearest_anchor(pages[target_page], local_position)
        if anchor.startswith("aid-"):
            linked_aids.add(anchor[len("aid-"):])
        href = page_filename(target_page)
        if anchor:
            href += f"#{anchor}"
        return href

    body = _POSFID_RE.sub(replace, body)
    body = _UNRESOLVED_HREF_RE.sub("", body)
    return body, linked_aids


def finish_page_anchors(body: str, linked_aids: set[str]) -> str:
    """Converts a page's own aid= attributes into real id= attributes
    where something actually links to them (linked_aids, gathered from
    TOC/guide resolution and rewrite_internal_links() above, using the
    same "aid-" + value convention find_nearest_anchor() returns), and
    removes the rest. "aid" isn't a standard XHTML attribute -- Kindle's
    own generator adds one to nearly every element, so leaving them
    all in as unused, invalid attributes isn't an option, and turning
    every single one into a real id would work but bloat the output
    with hundreds of ids nothing points at."""

    def replace(match: re.Match) -> str:
        value = match.group(1)
        if value in linked_aids:
            return f' id="aid-{value}"'
        return ""

    return _AID_ATTR_RE.sub(replace, body)


def rewrite_image_refs(body: str, image_href) -> str:
    """Rewrites every kindle:embed:XXXX(?mime=...) reference in a
    page's body to a real image href, via `image_href`, a callable,
    recindex -> str | None (e.g. built the same way
    ebook_fix.mobi.convert._image_filename already names MOBI7's own
    images, so the two generations' converted images end up named
    identically). A reference to a recindex with no matching image is
    removed rather than left pointing at nothing."""

    def replace(match: re.Match) -> str:
        recindex = int(match.group(1), 32)
        href = image_href(recindex)
        return href if href is not None else ""

    return _EMBED_RE.sub(replace, body)


def rewrite_css_flow_refs(body: str, flows: list[bytes], flow_href) -> str:
    """Rewrites every kindle:flow:XXXX?mime=text/css reference in a
    page's body to a real stylesheet href, via `flow_href`, a
    callable, flow_index -> str | None. flow_index is 1-based into
    `flows` (flows[0] is the book's own main text, never a stylesheet
    -- confirmed against all three real samples: every
    kindle:flow:...?mime=text/css reference found pointed only at
    flows[1:], each one plain CSS text). A reference to a flow index
    this book doesn't have is removed rather than left broken."""

    def replace(match: re.Match) -> str:
        flow_index = int(match.group(1), 32)
        href = flow_href(flow_index) if 0 < flow_index < len(flows) else None
        return href if href is not None else ""

    return _FLOW_CSS_RE.sub(replace, body)


def reassemble_flow0(
    flow0: bytes, skeletons: list[SkeletonEntry], fragments: list[FragmentEntry]
) -> list[bytes]:
    """Splices flow 0's fragment content back into each skeleton's own
    template to produce one complete page per skeleton entry, in
    order. This is Phase 3's verification step -- confirming the two
    tables above are being read correctly by checking the reassembled
    pages actually read right. Surprisingly, it turns out to also
    finish the job Phase 4 was expecting to still have to do: every
    resulting page is already a complete, well-formed standalone
    document (see extract_page_body() below and Phase 4's notes in
    docs/azw3_kf8_conversion_plan.md).

    A fragment's own content is the `length` bytes immediately
    following its skeleton's template range in flow 0 -- confirmed
    against all three real samples and against a reference tool. The
    fragment table's own `start` field is confirmed unused for this:
    it duplicates a position already implied by that adjacency and a
    reference tool's own reassembly code doesn't read it either.

    Raises MobiError if a skeleton's fragment_count calls for more
    fragments than the fragment table actually has, if any are left
    over once every skeleton has taken its share, or if a fragment's
    insert position doesn't fall inside its own skeleton's template."""
    pages = []
    fragment_pos = 0
    for skeleton in skeletons:
        template = flow0[skeleton.start:skeleton.start + skeleton.length]
        content_pos = skeleton.start + skeleton.length
        for _ in range(skeleton.fragment_count):
            if fragment_pos >= len(fragments):
                raise MobiError(
                    f"This KF8 book's skeleton table expects more fragments than its fragment "
                    f"table has ({len(fragments)})."
                )
            fragment = fragments[fragment_pos]
            insert_at = fragment.insert_position - skeleton.start
            if not (0 <= insert_at <= len(template)):
                raise MobiError(
                    f"A fragment's insert position falls outside its own skeleton ({skeleton.name!r})."
                )
            content = flow0[content_pos:content_pos + fragment.length]
            template = template[:insert_at] + content + template[insert_at:]
            content_pos += fragment.length
            fragment_pos += 1
        pages.append(template)
    if fragment_pos != len(fragments):
        raise MobiError(
            f"This KF8 book's fragment table has {len(fragments)} entries, but its skeleton "
            f"table only accounts for {fragment_pos} of them."
        )
    return pages


# Phase 4: every reassembled page is already a standalone document with
# its own <body>...</body> -- this pulls just that element's inner
# content back out, as a decoded string, matching the shape
# ebook_fix.epub_builder.EpubSpec.pages already expects. Anchored to
# the very end of the page (there's exactly one <body>...</body> in a
# well-formed page, and every real sample's pages are), so this can't
# be fooled by anything that merely looks like a closing tag earlier on.
_BODY_RE = re.compile(rb"<body\b[^>]*>(.*)</body>\s*</html>\s*\Z", re.S)


def extract_page_body(page: bytes, encoding: str) -> str:
    """Strips one of reassemble_flow0()'s pages down to its <body>
    element's inner content, decoded to a string -- the same shape
    ebook_fix.mobi.markup.Page.body and the FB2 converter's pages
    already use, so Phase 7 can finish a KF8 page through the exact
    same shared page_filename()/_page_xhtml() wrapper every other
    converter does, rather than keeping this page's own Kindle-
    generated <head> (see the module docstring's Phase 4 section for
    why that's not worth carrying forward as-is).

    Confirmed against every page of all three real samples: each one
    has exactly one <body>...</body>, immediately followed by
    </html> and nothing else. Raises MobiError if a page doesn't
    match that shape."""
    match = _BODY_RE.search(page)
    if not match:
        raise MobiError("A reassembled page has no recognizable <body>...</body> element.")
    return match.group(1).decode(encoding, errors="replace")




def read_kf8(path: Path) -> Kf8Book:
    """Opens a KF8 (AZW3) file and rebuilds its actual pages: separates
    its main text from its embedded styling (Phase 2), reads its
    skeleton/fragment/guide indices and splices fragment content back
    into its skeletons (book.pages, one entry per output page, each
    already a complete standalone document -- Phase 3), and pulls out
    each page's inner body content ready for the shared EPUB assembler
    (book.page_bodies, same order -- Phase 4). Raises MobiError (with a
    message meant for a person) for anything it can't read. Still not
    done: links and images inside a page still point at KF8's own
    addressing scheme rather than real hrefs, and the chapter table of
    contents isn't read yet -- Phase 5/6 jobs -- so book.page_bodies is
    real, readable content, correctly split and ordered, but not yet
    wired into a finished, fully cross-referenced EPUB."""
    path = Path(path)
    data = path.read_bytes()

    if not is_palmdb(data):
        raise MobiError("This doesn't look like a MOBI/AZW/PRC book (no PalmDB container found).")

    try:
        palmdb = read_palmdb(data)
        header = read_mobi_header(data, palmdb.records[0].offset)
    except (ValueError, IndexError, struct.error) as exc:
        raise MobiError(str(exc))

    if header.file_version < 8:
        raise MobiError("This isn't a KF8/AZW3 book (it's a classic MOBI file; use the MOBI reader instead).")

    if header.encryption_type != 0:
        raise MobiError(
            "This book is DRM-protected (encrypted), so it can't be read or converted. "
            "ebook_fix doesn't remove DRM."
        )

    exth: list[ExthRecord] = []
    if header.has_exth:
        exth = read_exth(data, exth_start_offset(header.mobi_offset, header.header_length))

    book = Kf8Book(path=path, header=header, exth=exth)

    if header.text_encoding == 65001:
        book.encoding = "utf-8"
    else:
        book.encoding = "cp1252"
        if header.text_encoding not in (1252, 0):
            book.warnings.append(f"Unrecognized text encoding ({header.text_encoding}); assumed Windows-1252.")

    try:
        text = read_text_records(data, palmdb, header)
    except (struct.error, IndexError) as exc:
        raise MobiError(f"The book's text couldn't be read: {exc}")

    ranges = read_fdst(data, palmdb, header)
    book.flows = split_flows(text, ranges)

    # Phase 6: images and cover. Reused directly from ebook_fix.mobi.reader,
    # unchanged -- confirmed against all three real samples that
    # first_image_record, the image records themselves, and EXTH 201
    # (the cover offset) all mean exactly the same thing here as they
    # do for a MOBI7 book (same header field, same record layout).
    book.images, skipped = _read_images(data, palmdb, header)
    if skipped:
        book.warnings.append(f"{skipped} BMP image(s) were skipped (BMP isn't an EPUB image format).")
    cover_offset = exth_int(exth, 201)
    if cover_offset is not None and cover_offset + 1 in book.images:
        book.cover_recindex = cover_offset + 1

    # header.text_length is not used to sanity-check flow 0 here: it
    # turns out to mean different things in different KF8-generating
    # tools' output. In AZW3-Older.azw3 it's flow 0's own length; in
    # AZW3-Example.azw3 and AZW3-Newer.azw3 it's the *total* decompressed
    # length across every flow instead. FDST's own internal consistency
    # (checked in split_flows above -- flows contiguous, covering the
    # whole decompressed stream with nothing left over) is a stronger
    # and more reliable signal than either reading of that field, so
    # nothing here re-derives a warning from comparing against it.

    # Phase 3: skeleton/fragment/guide indices and page reassembly.
    # Skeleton and fragment are load-bearing -- without them flow 0 is
    # just an interleaved byte stream, not readable pages -- so a
    # problem with either is a hard MobiError, same as a missing FDST
    # above. A missing or malformed guide index is not: it's an
    # optional landmark list (AZW3-Older.azw3 has none at all), so a
    # problem reading one is a warning, matching how MOBI7's NCX is
    # treated in ebook_fix.mobi.reader.
    book.skeleton_table = read_skeleton_table(data, palmdb, header)
    book.fragment_table = read_fragment_table(data, palmdb, header)
    book.pages = reassemble_flow0(book.flows[0], book.skeleton_table, book.fragment_table)
    try:
        book.guide_table = read_guide_table(data, palmdb, header)
    except MobiError as exc:
        book.warnings.append(f"The book's guide (landmarks) couldn't be read ({exc}).")

    # Phase 4: each page is already a standalone document (see
    # extract_page_body()'s docstring) -- pull out just its <body>
    # content, in the same shape the shared EPUB assembler expects.
    book.page_bodies = [extract_page_body(page, book.encoding) for page in book.pages]

    # Phase 5: the book's real chapter-by-chapter table of contents.
    # Treated like the guide above -- optional, so a problem reading
    # or resolving it is a warning, not a hard failure; a book that
    # can't be split into skeleton+fragment pages already failed
    # earlier, above, where that's actually load-bearing.
    try:
        raw_toc = read_toc_table(data, palmdb, header)
        book.toc = resolve_toc_table(raw_toc, book.skeleton_table, book.fragment_table, book.pages)
    except MobiError as exc:
        book.warnings.append(f"The book's table of contents couldn't be read ({exc}).")

    return book
