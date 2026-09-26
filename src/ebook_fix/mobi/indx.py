"""
ebook_fix.mobi.indx

Reads the table of contents a MOBI book carries as an "NCX index": a
small set of INDX records (a header record, then one or more data
records) followed by CTOC records holding the heading text.

Layout, confirmed against the real MOBI7 sample (examples/MOBI-Example.mobi,
35 entries) and cross-checked entry-for-entry against a reference
unpacker's output:

- INDX header record: "INDX" tag, header length, then the entry-record
  count, text encoding, total entry count and CTOC record count. A TAGX
  block starts right after the header and says which pieces of
  information each entry carries.
- INDX data records: an entry table (IDXT) of offsets, each pointing at
  an entry made of a short id string, control bytes, then the values for
  every tag the control bytes switched on, written as variable-width
  integers.
- CTOC records: heading text as (length, text) pairs, addressed by byte
  offset (each CTOC record counts as 0x10000 bytes of offset space).

Only the pieces a table of contents needs are kept: where the entry
points (a byte offset into the book's text), its heading, and how deeply
it is nested.

`read_indx_records()` below is the generic INDX-table walk (header +
TAGX, then one or more data records, then optional CTOC label records)
with none of the NCX-specific interpretation -- `read_ncx_entries()` is
now just one consumer of it. A KF8 book's skeleton, fragment, guide and
chapter-TOC indices (docs/azw3_kf8_conversion_plan.md, Phase 3) are laid
out exactly the same way and read through the same function, in
ebook_fix.mobi.kf8; only the tag numbers' meanings differ table to
table, which is why those readers live there instead of here.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from ebook_fix.mobi.palmdb import PalmDBHeader, record_bytes

# Tag numbers used by NCX entries.
TAG_POSITION = 1     # byte offset into the book text the entry points at
TAG_LENGTH = 2       # length of the section the entry covers
TAG_LABEL = 3        # offset of the heading text within the CTOC data
TAG_LEVEL = 4        # nesting depth, 0 = top level


@dataclass
class NcxEntry:
    label: str = ""
    position: int = 0   # byte offset into the decompressed book text
    level: int = 0      # 0 = top level


class NcxIndexError(ValueError):
    """The INDX records were unreadable."""


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Forward variable-width integer: 7 bits per byte, the byte with the
    0x80 flag set is the *last* one. Returns (value, bytes_consumed)."""
    value = 0
    consumed = 0
    while True:
        if pos + consumed >= len(data):
            raise NcxIndexError("Ran off the end of an INDX record.")
        byte = data[pos + consumed]
        consumed += 1
        value = (value << 7) | (byte & 0x7F)
        if byte & 0x80:
            return value, consumed
        if consumed > 5:
            raise NcxIndexError("Malformed variable-width integer in an INDX record.")


def _decode_text(raw: bytes, encoding: int) -> str:
    if encoding == 65001:
        return raw.decode("utf-8", errors="replace")
    return raw.decode("cp1252", errors="replace")


def _read_ctoc(record: bytes) -> dict[int, bytes]:
    """CTOC record -> {offset within record: text bytes}."""
    strings: dict[int, bytes] = {}
    pos = 0
    while pos < len(record):
        if record[pos] == 0:
            break
        start = pos
        length, used = _read_varint(record, pos)
        pos += used
        strings[start] = record[pos:pos + length]
        pos += length
    return strings


def _read_tagx(record: bytes, offset: int) -> tuple[list[tuple[int, int, int, int]], int]:
    """Returns ([(tag, values_per_entry, bitmask, end_flag), ...],
    control_byte_count)."""
    if record[offset:offset + 4] != b"TAGX":
        raise NcxIndexError("INDX header has no TAGX block.")
    first_entry_offset, control_bytes = struct.unpack_from(">II", record, offset + 4)
    tags = []
    pos = offset + 12
    end = offset + first_entry_offset
    while pos + 4 <= end:
        tags.append(struct.unpack_from("BBBB", record, pos))
        pos += 4
    return tags, control_bytes


def _decode_entry(entry: bytes, tags, control_bytes: int) -> dict[int, list[int]]:
    """One INDX entry's raw bytes -> {tag: [values]}."""
    id_length = entry[0]
    pos = 1 + id_length
    controls = entry[pos:pos + control_bytes]
    pos += control_bytes

    # First decide, per tag, how many values (or how many bytes of
    # values) follow.
    plan = []
    control_index = 0
    for tag, values_per_entry, mask, end_flag in tags:
        if end_flag == 1:
            control_index += 1
            continue
        if control_index >= len(controls):
            break
        value = controls[control_index] & mask
        if not value:
            continue
        if value == mask:
            if bin(mask).count("1") > 1:
                total_bytes, used = _read_varint(entry, pos)
                pos += used
                plan.append((tag, None, total_bytes, values_per_entry))
            else:
                plan.append((tag, 1, None, values_per_entry))
        else:
            m, v = mask, value
            while m & 1 == 0:
                m >>= 1
                v >>= 1
            plan.append((tag, v, None, values_per_entry))

    result: dict[int, list[int]] = {}
    for tag, count, total_bytes, values_per_entry in plan:
        values = []
        if count is not None:
            for _ in range(count * values_per_entry):
                value, used = _read_varint(entry, pos)
                pos += used
                values.append(value)
        else:
            consumed = 0
            while consumed < total_bytes:
                value, used = _read_varint(entry, pos)
                pos += used
                consumed += used
                values.append(value)
        result[tag] = values
    return result


def read_indx_records(
    data: bytes, palmdb: PalmDBHeader, first_index_record: int
) -> tuple[list[tuple[bytes, dict[int, list[int]]]], dict[int, bytes], int]:
    """Generic INDX-table walk: INDX header + TAGX, then one or more
    INDX data records, then optional CTOC label records. No
    domain-specific tag interpretation -- that's each caller's job
    (read_ncx_entries below for MOBI7's NCX; ebook_fix.mobi.kf8 for a
    KF8 book's skeleton, fragment, guide and chapter-TOC indices, which
    are laid out identically but use these same tag numbers for
    different things table to table).

    Returns (entries, ctoc, encoding): entries is
    [(raw_id_bytes, {tag: [values]}), ...] in index order (reading
    order); ctoc maps a label's byte offset to its raw text bytes;
    encoding is the INDX header's own text-encoding code (1252 or
    65001), needed to decode both entry ids and CTOC text since callers
    that use either do differ on it. Raises NcxIndexError if the
    structure isn't what this module knows."""
    if not (0 <= first_index_record < len(palmdb.records)):
        raise NcxIndexError("INDX index record number is out of range.")

    header = record_bytes(data, palmdb, first_index_record)
    if header[:4] != b"INDX":
        raise NcxIndexError("INDX index record doesn't start with INDX.")

    header_length = struct.unpack_from(">I", header, 4)[0]
    entry_record_count = struct.unpack_from(">I", header, 24)[0]
    encoding = struct.unpack_from(">I", header, 28)[0]
    ctoc_count = struct.unpack_from(">I", header, 52)[0]

    tags, control_bytes = _read_tagx(header, header_length)

    ctoc_first = first_index_record + 1 + entry_record_count
    ctoc: dict[int, bytes] = {}
    for i in range(ctoc_count):
        rec_index = ctoc_first + i
        if rec_index >= len(palmdb.records):
            break
        for off, text in _read_ctoc(record_bytes(data, palmdb, rec_index)).items():
            ctoc[off + i * 0x10000] = text

    entries: list[tuple[bytes, dict[int, list[int]]]] = []
    for i in range(entry_record_count):
        rec_index = first_index_record + 1 + i
        if rec_index >= len(palmdb.records):
            break
        record = record_bytes(data, palmdb, rec_index)
        if record[:4] != b"INDX":
            raise NcxIndexError("Expected an INDX data record.")
        idxt_start = struct.unpack_from(">I", record, 20)[0]
        entry_count = struct.unpack_from(">I", record, 24)[0]
        if record[idxt_start:idxt_start + 4] != b"IDXT":
            raise NcxIndexError("INDX data record has no IDXT table.")
        offsets = list(struct.unpack_from(">%dH" % entry_count, record, idxt_start + 4))
        offsets.append(idxt_start)
        for n in range(entry_count):
            raw = record[offsets[n]:offsets[n + 1]]
            id_length = raw[0]
            entry_id = raw[1:1 + id_length]
            values = _decode_entry(raw, tags, control_bytes)
            entries.append((entry_id, values))
    return entries, ctoc, encoding


def read_ncx_entries(data: bytes, palmdb: PalmDBHeader, first_index_record: int) -> list[NcxEntry]:
    """Reads the whole NCX index starting at `first_index_record`.
    Raises NcxIndexError if the structure isn't what this module knows.
    Returns entries in index order (which is reading order)."""
    _entries, ctoc, encoding = read_indx_records(data, palmdb, first_index_record)

    entries: list[NcxEntry] = []
    for _entry_id, values in _entries:
        position = values.get(TAG_POSITION, [None])[0]
        label_offset = values.get(TAG_LABEL, [None])[0]
        if position is None or label_offset is None:
            continue
        label = _decode_text(ctoc.get(label_offset, b""), encoding).strip()
        if not label:
            continue
        level = values.get(TAG_LEVEL, [0])[0]
        entries.append(NcxEntry(label=label, position=position, level=level))
    return entries
