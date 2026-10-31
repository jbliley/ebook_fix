"""
ebook_fix.mobi.palmdoc

Reads a classic PalmDOC book (a plain-text .pdb file, type "TEXt" and
creator "REAd"). It is the same PalmDB container and the same LZ77
text compression a MOBI uses, but without the MOBI header, images,
formatting or metadata: just compressed text. This module turns that
text into simple paragraph markup and hands back the same MobiBook
object read_mobi() returns, so the existing MOBI converter, CLI and GUI
handle the rest unchanged.

Only plain PalmDOC is supported. Other .pdb flavours (eReader,
Plucker, zTXT and so on) use different formats and are refused with a
clear message.
"""
from __future__ import annotations

import re
import struct
import zlib
from html import escape
from pathlib import Path

from ebook_fix.mobi.decompress import DecompressError, palmdoc_decompress
from ebook_fix.mobi.mobi_header import COMPRESSION_NONE, COMPRESSION_PALMDOC, MobiHeader
from ebook_fix.mobi.palmdb import PalmDBHeader, record_bytes

PALMDOC_TYPE = "TEXt"


def is_palmdoc(data: bytes, palmdb: PalmDBHeader) -> bool:
    """True for a plain PalmDOC text book: type "TEXt" and a record 0 with
    no MOBI header after the 16-byte PalmDOC header."""
    if palmdb.db_type != PALMDOC_TYPE or not palmdb.records:
        return False
    start = palmdb.records[0].offset
    return data[start + 16:start + 20] != b"MOBI"


# -- text -> paragraphs ------------------------------------------------

def _decode(raw: bytes) -> str:
    raw = raw.replace(b"\x00", b"")
    if any(b > 127 for b in raw):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return raw.decode("cp1252", errors="replace")


def _is_hard_wrapped(blocks: list) -> bool:
    """True if blank lines separate paragraphs and each paragraph is
    itself broken into short lines (the old 60-76 column style), so the
    lines inside one block should be joined back together."""
    multi = [b for b in blocks if len(b) > 1]
    if len(multi) < max(3, len(blocks) * 0.3):
        return False
    lengths = sorted(len(line) for b in multi for line in b)
    if not lengths:
        return False
    p90 = lengths[int(len(lengths) * 0.9)]
    return p90 <= 100 and sum(lengths) / len(lengths) <= 85


def text_to_paragraphs(text: str) -> list:
    """Splits plain text into a list of paragraph strings."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    has_blank_separators = any(not line for line in lines[1:-1])

    if not has_blank_separators:
        return [line for line in lines if line]

    blocks: list = []
    current: list = []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    if _is_hard_wrapped(blocks):
        return [" ".join(b) for b in blocks]
    return [line for b in blocks for line in b]


def paragraphs_to_markup(paragraphs: list) -> bytes:
    body = "\n".join(f"<p>{escape(p, quote=False)}</p>" for p in paragraphs)
    return f"<html><head></head><body>\n{body}\n</body></html>".encode("utf-8")


# -- metadata ----------------------------------------------------------

def _letters(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _tidy(value: str) -> str:
    return re.sub(r"[_\s]+", " ", value).strip()


def guess_metadata(db_name: str, first_lines: list) -> tuple:
    """Best-effort (title, authors, year) from the database name and the
    first few lines of text. PalmDOC has no metadata fields, so this is
    a guess and the Metadata tab is where it gets corrected."""
    raw = db_name.strip()
    title, authors = re.sub(r"[_\s]+", " ", raw).strip(), []
    m = re.match(r"^(?P<last>[^_,\-]+?)\s*[_,]\s*(?P<first>[^-]+?)\s+-\s+(?P<title>.+)$", raw)
    if m:
        authors = [f"{_tidy(m.group('first'))} {_tidy(m.group('last'))}"]
        title = _tidy(m.group("title"))
    else:
        m = re.match(r"^(?P<author>.+?)\s+-\s+(?P<title>.+)$", raw)
        if m:
            authors = [_tidy(m.group("author"))]
            title = _tidy(m.group("title"))

    year = ""
    for line in first_lines[:3]:
        candidate = re.sub(r"\s*\((\d{4})\)\s*$", "", line).strip()
        if candidate and _letters(title) and _letters(title) in _letters(candidate) and len(candidate) < 90:
            title = candidate
            y = re.search(r"\((\d{4})\)\s*$", line)
            year = y.group(1) if y else ""
            break

    if not authors:
        for line in first_lines[:6]:
            m = re.match(r"^(?:an? [a-z ]*?)?by\s+(.{3,60})$", line.strip(), re.I)
            if m:
                authors = [m.group(1).strip()]
                break
    return title, authors, year


# -- the reader --------------------------------------------------------

def read_palmdoc(path: Path, data: bytes, palmdb: PalmDBHeader):
    """Reads a PalmDOC book into a MobiBook. Raises MobiError with a
    message meant for a person."""
    from ebook_fix.mobi.reader import MobiBook, MobiError

    start = palmdb.records[0].offset
    try:
        compression, _unused, text_length, record_count, record_size, encryption = struct.unpack(
            ">HHIHHH", data[start:start + 14]
        )
    except struct.error:
        raise MobiError("The file's header is damaged or too short to read.")

    if encryption != 0:
        raise MobiError(
            "This book is DRM-protected (encrypted), so it can't be read or converted. "
            "ebook_fix doesn't remove DRM."
        )
    if compression not in (COMPRESSION_NONE, COMPRESSION_PALMDOC):
        raise MobiError(f"Unsupported text compression type ({compression}).")

    parts = []
    for i in range(1, record_count + 1):
        if i >= len(palmdb.records) or palmdb.records[i].offset >= len(data):
            raise MobiError("The file ends before all of its text does (an incomplete download or a damaged file).")
        record = record_bytes(data, palmdb, i)
        if compression == COMPRESSION_PALMDOC:
            try:
                record = palmdoc_decompress(record)
            except (DecompressError, IndexError) as exc:
                raise MobiError(f"Couldn't decompress text record {i}: {exc}")
        parts.append(record)
    raw = b"".join(parts)

    if text_length and len(raw) < text_length * 0.95:
        raise MobiError(
            f"The book's text is incomplete ({len(raw):,} of {text_length:,} bytes "
            "could be read), so the file looks damaged or cut off."
        )

    text = _decode(raw)
    paragraphs = text_to_paragraphs(text)
    if not paragraphs:
        raise MobiError("The book doesn't contain any readable text.")

    title, authors, year = guess_metadata(palmdb.name, paragraphs)

    header = MobiHeader(
        compression=compression,
        text_length=text_length,
        text_record_count=record_count,
        record_size=record_size,
        unique_id=zlib.crc32(data) & 0xFFFFFFFF,
        file_version=0,
        full_name=palmdb.name,
    )
    book = MobiBook(path=path, header=header)
    book.generation = "PalmDOC (plain-text .pdb)"
    book.encoding = "utf-8"
    book.text = paragraphs_to_markup(paragraphs)
    book.title = title
    book.authors = authors
    book.date = year
    book.warnings.append(
        "A PalmDOC .pdb file holds only plain text: it has no images, formatting, table of contents or "
        "real metadata. The title and author were guessed from the file name and the opening lines, "
        "so check them on the Metadata tab."
    )
    return book
