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
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ebook_fix.mobi.mobi_header import ExthRecord, MobiHeader, exth_start_offset, read_exth, read_mobi_header
from ebook_fix.mobi.palmdb import PalmDBHeader, is_palmdb, read_palmdb, record_bytes
from ebook_fix.mobi.reader import MobiError, read_text_records

NO_RECORD = 0xFFFFFFFF


@dataclass
class Kf8Book:
    path: Path = None
    header: MobiHeader = None
    exth: list = field(default_factory=list)
    encoding: str = "cp1252"
    flows: list = field(default_factory=list)   # list[bytes]; flows[0] is the main text
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


def read_kf8(path: Path) -> Kf8Book:
    """Opens a KF8 (AZW3) file far enough to separate its main text
    from its embedded styling (Phase 2). Raises MobiError (with a
    message meant for a person) for anything it can't read, including
    -- for now -- everything past this phase: skeleton/fragment
    reassembly, links, images, and metadata aren't read yet, so the
    text in flows[0] is not yet valid standalone HTML (its content is
    real, but split into pieces that haven't been rewoven into pages)."""
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

    return book
