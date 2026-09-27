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
from dataclasses import dataclass, field
from pathlib import Path

from ebook_fix.mobi.indx import NcxIndexError, _decode_text, read_indx_records
from ebook_fix.mobi.mobi_header import ExthRecord, MobiHeader, exth_start_offset, read_exth, read_mobi_header
from ebook_fix.mobi.palmdb import PalmDBHeader, is_palmdb, read_palmdb, record_bytes
from ebook_fix.mobi.reader import MobiError, read_text_records

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
class Kf8Book:
    path: Path = None
    header: MobiHeader = None
    exth: list = field(default_factory=list)
    encoding: str = "cp1252"
    flows: list = field(default_factory=list)   # list[bytes]; flows[0] is the main text
    skeleton_table: list = field(default_factory=list)   # list[SkeletonEntry]
    fragment_table: list = field(default_factory=list)   # list[FragmentEntry]
    guide_table: list = field(default_factory=list)      # list[GuideEntry]; empty if the book has none
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

    return book
