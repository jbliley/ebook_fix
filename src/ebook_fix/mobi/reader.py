"""
ebook_fix.mobi.reader

Opens a MOBI/AZW/PRC file and pulls out everything a converter needs:
the book's raw markup (decompressed and stitched back together), its
images, its metadata, its cover, and its table of contents. This is
the "opener" half of MOBI support -- convert.py is the half that turns
what this returns into an EPUB.

Only the classic MOBI7 generation is supported here. A pure AZW3/KF8
file is recognized and refused with a clear message (its internals are
organized very differently -- separate skeleton, fragment and flow
indexes -- and are the next planned step; see docs/mobi_conversion_plan.md).
A hybrid file (a MOBI7 book and a KF8 book packed into one) is read
through its MOBI7 half, which is how the file itself is laid out.

DRM-protected books are refused, not attempted.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ebook_fix.mobi.decompress import (
    DecompressError,
    HuffCdicReader,
    palmdoc_decompress,
    strip_trailing_entries,
)
from ebook_fix.mobi.indx import NcxIndexError, read_ncx_entries
from ebook_fix.mobi.mobi_header import (
    COMPRESSION_HUFFCDIC,
    COMPRESSION_NONE,
    COMPRESSION_PALMDOC,
    ExthRecord,
    MobiHeader,
    exth_int,
    exth_start_offset,
    read_exth,
    read_mobi_header,
)
from ebook_fix.mobi.palmdb import PalmDBHeader, is_palmdb, read_palmdb, record_bytes

NO_RECORD = 0xFFFFFFFF


class MobiError(Exception):
    """A MOBI file that can't be opened or converted. The message is
    written to be shown to a person as-is."""


@dataclass
class MobiImage:
    recindex: int = 0          # 1-based number the book's own markup uses
    record_index: int = 0      # PalmDB record it came from
    media_type: str = ""
    data: bytes = b""


@dataclass
class MobiBook:
    path: Path = None
    generation: str = "MOBI7"
    is_hybrid: bool = False
    header: MobiHeader = None
    exth: list = field(default_factory=list)
    encoding: str = "cp1252"       # "utf-8" or "cp1252"
    text: bytes = b""              # the decompressed book markup
    images: dict = field(default_factory=dict)   # recindex -> MobiImage
    cover_recindex: int | None = None
    ncx: list = field(default_factory=list)      # list[NcxEntry]
    start_offset: int | None = None              # EXTH 116: where reading should begin
    warnings: list = field(default_factory=list)

    # Metadata, from the EXTH block (title also falls back to the
    # header's own full name).
    title: str = ""
    authors: list = field(default_factory=list)
    publisher: str = ""
    description: str = ""
    isbn: str = ""
    asin: str = ""
    language: str = ""
    date: str = ""
    rights: str = ""
    subjects: list = field(default_factory=list)
    contributors: list = field(default_factory=list)


def _sniff_image(data: bytes) -> str | None:
    """Image formats an EPUB can carry. (BMP, which MOBI also allows, is
    deliberately not here -- it isn't a core EPUB image type, and
    converting it would need an imaging library this project doesn't
    depend on.)"""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _exth_values(records: list, record_type: int) -> list:
    return [r.value for r in records if r.record_type == record_type and r.value]


def read_text_records(data: bytes, palmdb: PalmDBHeader, header: MobiHeader) -> bytes:
    """Decompresses every text record and joins them. Shared with
    ebook_fix.mobi.kf8: for a KF8 book this is the input flow-
    separation splits into flow 0 (the main text) and whatever flows
    follow it (CSS, SVG) -- see docs/azw3_kf8_conversion_plan.md,
    Phase 2. For a MOBI7 book the whole return value already is the
    book's text, no further splitting needed."""
    if header.compression not in (COMPRESSION_NONE, COMPRESSION_PALMDOC, COMPRESSION_HUFFCDIC):
        raise MobiError(f"Unsupported text compression type ({header.compression}).")

    huff = None
    if header.compression == COMPRESSION_HUFFCDIC:
        if not header.huff_record_count:
            raise MobiError("This HUFF/CDIC book has no dictionary records.")
        first = header.huff_record_index
        try:
            huff = HuffCdicReader(
                record_bytes(data, palmdb, first),
                [record_bytes(data, palmdb, first + i) for i in range(1, header.huff_record_count)],
            )
        except (DecompressError, struct.error, IndexError) as exc:
            raise MobiError(f"Couldn't read the book's HUFF/CDIC dictionary: {exc}")

    parts = []
    for i in range(1, header.text_record_count + 1):
        if i >= len(palmdb.records) or palmdb.records[i].offset >= len(data):
            raise MobiError("The file ends before all of its text does (an incomplete download or a damaged file).")
        record = record_bytes(data, palmdb, i)
        try:
            record = strip_trailing_entries(record, header.extra_data_flags)
            if header.compression == COMPRESSION_PALMDOC:
                record = palmdoc_decompress(record)
            elif header.compression == COMPRESSION_HUFFCDIC:
                record = huff.unpack(record)
        except (DecompressError, struct.error, IndexError) as exc:
            raise MobiError(f"Couldn't decompress text record {i}: {exc}")
        parts.append(record)
    return b"".join(parts)


def _read_images(data: bytes, palmdb: PalmDBHeader, header: MobiHeader) -> tuple[dict, int]:
    """Returns ({recindex: MobiImage}, number of skipped unsupported
    image records)."""
    images: dict = {}
    skipped = 0
    first = header.first_image_record
    if first == NO_RECORD or first <= 0 or first >= len(palmdb.records):
        return images, skipped
    for record_index in range(first, len(palmdb.records)):
        blob = record_bytes(data, palmdb, record_index)
        media_type = _sniff_image(blob)
        if media_type:
            recindex = record_index - first + 1
            images[recindex] = MobiImage(
                recindex=recindex,
                record_index=record_index,
                media_type=media_type,
                data=blob,
            )
        elif blob[:2] == b"BM" and len(blob) > 30:
            skipped += 1
    return images, skipped


def _find_valid_kf8_boundary(data: bytes, palmdb: PalmDBHeader, exth: list) -> int | None:
    """Where EXTH 121 ("KF8 boundary") claims a second MOBI header
    begins, or None if there's no EXTH 121, or if there is one but a
    real header doesn't actually verify at the record it names.

    Confirmed against a real file that this second check matters: a
    real AZW3 sample carried an EXTH 121 record whose value pointed at
    that same file's own HUFF dictionary record, not a second header --
    stale or misleading metadata from whatever tool produced it, not a
    genuine two-header hybrid (the file has exactly one MOBI header in
    it, confirmed by scanning every record). Trusting EXTH 121's mere
    presence would have misclassified that file as a hybrid and tried
    to read a MOBI7 half that isn't really there. See
    docs/azw3_kf8_conversion_plan.md, Phase 1."""
    for record in exth:
        if record.record_type != 121:
            continue
        raw = record.raw
        if len(raw) < 4:
            return None
        candidate = struct.unpack(">I", raw[:4])[0]
        if not (0 < candidate < len(palmdb.records)):
            return None
        try:
            read_mobi_header(data, palmdb.records[candidate].offset)
        except (ValueError, struct.error, IndexError):
            return None
        return candidate
    return None


def read_mobi(path: Path) -> MobiBook:
    """Opens a MOBI-family file. Raises MobiError (with a message meant
    for a person) for anything it can't or won't convert."""
    path = Path(path)
    data = path.read_bytes()

    if not is_palmdb(data):
        raise MobiError("This doesn't look like a MOBI/AZW/PRC book (no PalmDB container found).")

    try:
        palmdb = read_palmdb(data)
        record0 = palmdb.records[0].offset
        header = read_mobi_header(data, record0)
    except (ValueError, IndexError, struct.error) as exc:
        raise MobiError(str(exc))

    if header.encryption_type != 0:
        raise MobiError(
            "This book is DRM-protected (encrypted), so it can't be read or converted. "
            "ebook_fix doesn't remove DRM."
        )

    exth: list[ExthRecord] = []
    if header.has_exth:
        exth = read_exth(data, exth_start_offset(header.mobi_offset, header.header_length))

    has_boundary = _find_valid_kf8_boundary(data, palmdb, exth) is not None
    if header.file_version >= 8 and not has_boundary:
        raise MobiError(
            "This is an AZW3/KF8 book (the newer Kindle format). ebook_fix can convert classic "
            "MOBI files so far; AZW3/KF8 conversion is the next planned step."
        )

    book = MobiBook(path=path, header=header, exth=exth)
    book.generation = "KF8 hybrid (reading its MOBI7 half)" if has_boundary else "MOBI7"
    book.is_hybrid = has_boundary
    if has_boundary:
        book.warnings.append(
            "This file holds both a MOBI7 and a KF8 version of the book; the MOBI7 version was "
            "converted. (Hybrid files haven't been tested against a real sample.)"
        )

    if header.text_encoding == 65001:
        book.encoding = "utf-8"
    else:
        book.encoding = "cp1252"
        if header.text_encoding not in (1252, 0):
            book.warnings.append(
                f"Unrecognized text encoding ({header.text_encoding}); assumed Windows-1252."
            )

    try:
        book.text = read_text_records(data, palmdb, header)
    except (struct.error, IndexError) as exc:
        raise MobiError(f"The book's text couldn't be read: {exc}")

    if header.text_length and len(book.text) != header.text_length:
        difference = len(book.text) - header.text_length
        if header.file_version >= 8 and difference > 0:
            # KF8: header.text_length is flow 0's length specifically
            # (the main HTML text), not the total size of every text
            # record decompressed. The text records legitimately keep
            # going past it with additional flows -- embedded CSS and
            # SVG -- concatenated right after flow 0 in the same
            # decompressed stream. Confirmed against a real HUFF/CDIC
            # sample: the extra bytes here matched the book's own CSS
            # byte-for-byte, and the visible text up to text_length
            # matched a reference tool's independently-decoded output
            # exactly. Splitting flow 0 out from what follows it is
            # Phase 2 (see docs/azw3_kf8_conversion_plan.md); nothing
            # to warn about here, since this is the expected shape, not
            # a symptom of anything wrong.
            pass
        elif difference < 0 and len(book.text) < header.text_length * 0.95:
            raise MobiError(
                f"The book's text is incomplete ({len(book.text):,} of {header.text_length:,} bytes "
                "could be read), so the file looks damaged or cut off."
            )
        else:
            book.warnings.append(
                f"Decompressed text is {abs(difference):,} bytes "
                f"{'longer' if difference > 0 else 'shorter'} than the file's header says; "
                "internal links may land slightly off."
            )

    book.images, skipped = _read_images(data, palmdb, header)
    if skipped:
        book.warnings.append(f"{skipped} BMP image(s) were skipped (BMP isn't an EPUB image format).")

    cover_offset = exth_int(exth, 201)
    if cover_offset is not None and cover_offset + 1 in book.images:
        book.cover_recindex = cover_offset + 1

    start = exth_int(exth, 116)
    if start is not None and start != NO_RECORD:
        book.start_offset = start

    if header.ncx_index_record != NO_RECORD and header.ncx_index_record != 0:
        try:
            book.ncx = read_ncx_entries(data, palmdb, header.ncx_index_record)
        except (NcxIndexError, struct.error, IndexError) as exc:
            book.warnings.append(f"The book's table of contents couldn't be read ({exc}).")

    updated_title = _exth_values(exth, 503)
    book.title = updated_title[0] if updated_title else header.full_name
    book.authors = _exth_values(exth, 100)
    publishers = _exth_values(exth, 101)
    book.publisher = publishers[0] if publishers else ""
    descriptions = _exth_values(exth, 103)
    book.description = descriptions[0] if descriptions else ""
    isbns = _exth_values(exth, 104)
    book.isbn = isbns[0] if isbns else ""
    asins = _exth_values(exth, 113)
    book.asin = asins[0] if asins else ""
    languages = _exth_values(exth, 524)
    book.language = languages[0] if languages else ""
    dates = _exth_values(exth, 106)
    book.date = dates[0] if dates else ""
    rights = _exth_values(exth, 109)
    book.rights = rights[0] if rights else ""
    book.subjects = _exth_values(exth, 105)
    book.contributors = _exth_values(exth, 108)
    return book
