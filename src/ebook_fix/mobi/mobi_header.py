"""
ebook_fix.mobi.mobi_header

Reads record 0 of a MOBI-family PalmDB file: the PalmDOC compression
header, the MOBI header proper, and (if present) the EXTH metadata
block. EXTH is roughly analogous to what an EPUB's OPF <metadata>
holds -- title, author, language, and so on -- just a different
binary encoding.

Field offsets confirmed against a real MOBI7 sample
(examples/MOBI-Example.mobi, 29 EXTH records including title, author,
publisher, ISBN, ASIN, description, and a cover image reference).
The generation-detection logic for AZW3/KF8 hybrid files below is
written from documented field layouts but NOT yet confirmed against a
real AZW3 sample -- see docs/format_support_plan.md.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field

# PalmDOC-level compression, read at the very start of record 0.
COMPRESSION_NONE = 1
COMPRESSION_PALMDOC = 2
COMPRESSION_HUFFCDIC = 17480

COMPRESSION_NAMES = {
    COMPRESSION_NONE: "None",
    COMPRESSION_PALMDOC: "PalmDOC",
    COMPRESSION_HUFFCDIC: "HUFF/CDIC",
}

ENCRYPTION_NAMES = {
    0: "None",
    1: "Old Mobipocket",
    2: "Mobipocket",
}

# EXTH record types this module knows how to label. Only the
# well-documented, commonly-populated ones -- anything else prints as
# "unknown_<type>" rather than guessing at a label. Confirmed against
# the real sample: 100, 101, 103, 104, 105, 106, 108, 112, 113, 116,
# 129, 131, 201, 204-207, 501, 503, 524. The rest are included on
# general MOBI-format documentation but unconfirmed.
EXTH_TYPES = {
    100: "author",
    101: "publisher",
    102: "imprint",
    103: "description",
    104: "isbn",
    105: "subject",
    106: "publishing_date",
    108: "contributor",
    109: "rights",
    112: "source",
    113: "asin",
    116: "start_reading_offset",
    121: "kf8_boundary_record",
    131: "cde_content_type",
    201: "cover_record_offset",
    202: "thumbnail_record_offset",
    203: "has_fake_cover",
    204: "creator_software",
    205: "creator_major_version",
    206: "creator_minor_version",
    207: "creator_build_number",
    501: "cde_type",
    503: "updated_title",
    524: "language",
}


@dataclass
class ExthRecord:
    record_type: int = 0
    name: str = ""
    value: str = ""     # best-effort text decode, for display
    raw: bytes = b""    # original bytes, for tags that are actually integers (see exth_int)


@dataclass
class MobiHeader:
    header_length: int = 0          # length of the MOBI header itself, used to find EXTH
    mobi_type: int = 0
    text_encoding: int = 0
    unique_id: int = 0
    file_version: int = 0           # 6 or below = MOBI7, 8+ = KF8
    full_name: str = ""
    language_code: int = 0
    first_non_book_record_index: int = 0
    first_image_record: int = 0
    exth_flags: int = 0
    has_exth: bool = False
    compression: int = COMPRESSION_NONE
    text_length: int = 0
    text_record_count: int = 0
    encryption_type: int = 0


@dataclass
class MobiMetadata:
    title: str = ""
    author: str = ""
    publisher: str = ""
    language: str = ""
    date: str = ""
    isbn: str = ""
    asin: str = ""
    description: str = ""
    subjects: list = field(default_factory=list)
    rights: str = ""


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding).strip("\x00").strip()
            if text:
                return text
        except UnicodeDecodeError:
            continue
    return repr(raw)


def read_mobi_header(data: bytes, record0_offset: int) -> MobiHeader:
    """Reads the PalmDOC compression fields plus the MOBI header that
    follows them, both inside record 0. Raises ValueError if no
    "MOBI" identifier is found near the start of the record -- every
    real MOBI/AZW/AZW3/PRC file has one; a PalmDB file without it is
    some other kind of Palm OS document, not a book this module
    understands."""
    compression = _u16(data, record0_offset)
    text_length = _u32(data, record0_offset + 4)
    text_record_count = _u16(data, record0_offset + 8)
    encryption_type = _u16(data, record0_offset + 12)

    search_end = min(len(data), record0_offset + 4096)
    mobi_offset = data.find(b"MOBI", record0_offset, search_end)
    if mobi_offset == -1:
        raise ValueError(
            "No MOBI header found in record 0 -- this PalmDB file "
            "isn't a MOBI/AZW/AZW3/PRC book this module recognizes."
        )

    header_length = _u32(data, mobi_offset + 4)
    mobi_type = _u32(data, mobi_offset + 8)
    text_encoding = _u32(data, mobi_offset + 12)
    unique_id = _u32(data, mobi_offset + 16)
    file_version = _u32(data, mobi_offset + 20)
    first_non_book_record_index = _u32(data, mobi_offset + 64)
    full_name_offset = _u32(data, mobi_offset + 68)
    full_name_length = _u32(data, mobi_offset + 72)
    language_code = _u32(data, mobi_offset + 76)
    first_image_record = _u32(data, mobi_offset + 92)
    exth_flags = _u32(data, mobi_offset + 112) if header_length >= 116 else 0

    name_start = record0_offset + full_name_offset
    full_name = _decode(data[name_start : name_start + full_name_length])

    return MobiHeader(
        header_length=header_length,
        mobi_type=mobi_type,
        text_encoding=text_encoding,
        unique_id=unique_id,
        file_version=file_version,
        full_name=full_name,
        language_code=language_code,
        first_non_book_record_index=first_non_book_record_index,
        first_image_record=first_image_record,
        exth_flags=exth_flags,
        has_exth=bool(exth_flags & 0x40),
        compression=compression,
        text_length=text_length,
        text_record_count=text_record_count,
        encryption_type=encryption_type,
    )


def exth_start_offset(mobi_offset_in_data: int, header_length: int) -> int:
    """The MOBI header is immediately followed by EXTH (if present) --
    no padding between them was needed in the real sample tested, but
    the spec describes the header length as already accounting for
    any trailing padding, so this is just header start + header
    length rather than a fixed constant."""
    return mobi_offset_in_data + header_length


def read_exth(data: bytes, exth_offset: int) -> list[ExthRecord]:
    """Parses the EXTH metadata block starting at `exth_offset`.
    Returns an empty list if there's no "EXTH" tag there at all --
    the caller should already have checked `MobiHeader.has_exth`
    first, but this stays defensive rather than assuming."""
    if data[exth_offset : exth_offset + 4] != b"EXTH":
        return []

    total_length = _u32(data, exth_offset + 4)
    count = _u32(data, exth_offset + 8)
    end = min(len(data), exth_offset + total_length)

    pos = exth_offset + 12
    records = []
    for _ in range(count):
        if pos + 8 > end:
            break

        record_type = _u32(data, pos)
        record_length = _u32(data, pos + 4)
        if record_length < 8 or pos + record_length > end:
            break

        raw = data[pos + 8 : pos + record_length]
        name = EXTH_TYPES.get(record_type, f"unknown_{record_type}")
        records.append(ExthRecord(record_type=record_type, name=name, value=_decode(raw), raw=raw))
        pos += record_length

    return records


def exth_int(records: list[ExthRecord], record_type: int, default: int | None = None) -> int | None:
    """Several EXTH tags (cover/thumbnail offsets, version numbers,
    content type, etc.) are raw 4-byte big-endian integers, not text
    -- `ExthRecord.value` is a best-effort text decode meant for
    tags that actually are text, so it isn't reliable for these.
    Reads the first matching record's raw bytes as an integer
    instead. Returns `default` if the tag isn't present or isn't
    4 bytes long."""
    for record in records:
        if record.record_type == record_type:
            if len(record.raw) == 4:
                return struct.unpack(">I", record.raw)[0]
            return default
    return default


def extract_metadata(header: MobiHeader, exth_records: list[ExthRecord]) -> MobiMetadata:
    """Builds the comparable-to-EPUB metadata summary out of the raw
    EXTH records. Subject (105) is genuinely multi-valued -- a real
    book can and does carry a dozen of them -- so that one collects
    every match instead of just the first, unlike everything else
    here."""
    by_type: dict[int, list[str]] = {}
    for record in exth_records:
        by_type.setdefault(record.record_type, []).append(record.value)

    def first(*types: int, default: str = "") -> str:
        for t in types:
            if t in by_type:
                return by_type[t][0]
        return default

    # An "Updated Title" (503) EXTH record, when present, is meant to
    # override the PalmDB/record-0 full name -- otherwise the full
    # name from the MOBI header itself is the title.
    title = first(503, default=header.full_name)

    return MobiMetadata(
        title=title,
        author=first(100),
        publisher=first(101),
        language=first(524),
        date=first(106),
        isbn=first(104),
        asin=first(113),
        description=first(103),
        subjects=by_type.get(105, []),
        rights=first(109),
    )
