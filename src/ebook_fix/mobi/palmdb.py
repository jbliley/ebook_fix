"""
ebook_fix.mobi.palmdb

Reads the PalmDB container that wraps every MOBI/AZW/AZW3/PRC file --
the old Palm OS document database format: a fixed header, a flat list
of record offsets, then the records themselves back to back. Not a
zip archive, so nothing here uses `zipfile`, and none of this
project's existing EPUB parsing applies underneath it.

Field layout confirmed against a real MOBI7 sample
(examples/MOBI-Example.mobi) rather than written from spec alone --
see docs/format_support_plan.md for what's confirmed and what's
still open (no AZW3/KF8 sample exists yet, so the hybrid case below
`mobi_header.py` handles is unconfirmed).
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field

# Type/creator codes PalmDB uses for a MOBI-family book. "TEXt" shows
# up on some very old PalmDOC-only files; "BOOK" is what every real
# MOBI/AZW/AZW3/PRC sample so far uses.
BOOK_TYPE_CODES = (b"BOOK", b"TEXt")


@dataclass
class PalmDBRecord:
    index: int = 0
    offset: int = 0
    length: int = 0  # distance to the next record's offset, or EOF for the last one


@dataclass
class PalmDBHeader:
    name: str = ""
    db_type: str = ""
    creator: str = ""
    record_count: int = 0
    records: list = field(default_factory=list)


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def is_palmdb(data: bytes) -> bool:
    """True if `data` looks like a PalmDB container at all. Doesn't
    confirm it's specifically a book (vs. some other Palm OS
    database) -- just that the outer container shape is right, so
    the caller knows whether it's even worth trying `read_palmdb`."""
    return len(data) >= 78 and data[60:64] in BOOK_TYPE_CODES


def read_palmdb(data: bytes) -> PalmDBHeader:
    """Parses the PalmDB header and record offset list. Raises
    ValueError if `data` is too small or doesn't look like a PalmDB
    file at all -- callers should check `is_palmdb` first if they
    want to fail with a more specific message."""
    if len(data) < 78:
        raise ValueError("File is too small to be a valid PalmDB file.")

    name = data[0:32].split(b"\x00", 1)[0].decode("latin-1", errors="replace").strip()
    db_type = data[60:64].decode("latin-1", errors="replace")
    creator = data[64:68].decode("latin-1", errors="replace")
    record_count = _u16(data, 76)

    offsets = [_u32(data, 78 + i * 8) for i in range(record_count)]

    records = []
    for i, offset in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else len(data)
        records.append(PalmDBRecord(index=i, offset=offset, length=end - offset))

    return PalmDBHeader(
        name=name,
        db_type=db_type,
        creator=creator,
        record_count=record_count,
        records=records,
    )


def record_bytes(data: bytes, header: PalmDBHeader, index: int) -> bytes:
    """Slices out one record's raw bytes by index. Small helper so
    callers (mobi_header.py, analyzer.py) don't each re-derive
    start/end from the record list themselves."""
    record = header.records[index]
    return data[record.offset : record.offset + record.length]
