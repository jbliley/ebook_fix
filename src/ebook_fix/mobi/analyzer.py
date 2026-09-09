"""
ebook_fix.mobi.analyzer

Ties palmdb.py and mobi_header.py together into one report, the same
role ebook_fix.analyzer plays for EPUB -- open the file, read it
once, hand back a structured result. Analysis only: nothing here
modifies or writes a MOBI file. See docs/format_support_plan.md for
the phased plan this is Phase 0/1 of.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from ebook_fix.cover import sniff_image_media_type
from ebook_fix.report import console, print_header
from ebook_fix.mobi.palmdb import is_palmdb, read_palmdb, record_bytes
from ebook_fix.mobi.mobi_header import (
    COMPRESSION_NAMES,
    ENCRYPTION_NAMES,
    read_mobi_header,
    exth_start_offset,
    read_exth,
    extract_metadata,
    exth_int,
)

# File extensions this module will attempt to open. `cli.py` checks
# against this list to decide whether to route here instead of the
# EPUB-only Engine pipeline.
MOBI_EXTENSIONS = (".mobi", ".azw", ".azw3", ".prc")


@dataclass
class MobiAnalysisReport:
    path: object = None
    file_size: int = 0
    generation: str = ""              # "MOBI7", "KF8 hybrid", "KF8/AZW3", or "unrecognized"
    generation_confirmed: bool = True  # False for the KF8 cases -- see module docstring
    compression: str = ""
    encryption: str = ""
    text_length: int = 0
    text_record_count: int = 0
    palmdb_record_count: int = 0
    exth_tag_count: int = 0
    metadata: object = None
    cover_record_index: int = -1      # -1 if no cover reference found
    cover_media_type: str = ""
    error: str = ""                   # set instead of everything else if the file couldn't be read


def _detect_generation(file_version: int, exth_records) -> tuple[str, bool]:
    """Returns (label, confirmed). MOBI7 (file_version < 8) is
    confirmed against a real sample; the two KF8 cases are written
    from documented field layouts only -- flagged unconfirmed until a
    real AZW3 sample exists to check against (see
    docs/format_support_plan.md)."""
    if file_version < 8:
        return "MOBI7 (classic Mobipocket)", True

    has_boundary = any(r.record_type == 121 for r in exth_records)
    if has_boundary:
        return "KF8 hybrid (MOBI7 + KF8)", False
    return "KF8/AZW3", False


def analyze_mobi(path: Path) -> MobiAnalysisReport:
    """Reads and analyzes one MOBI/AZW/AZW3/PRC file. Never raises for
    a malformed or unsupported file -- any problem is captured in the
    report's `error` field instead, matching how the rest of this
    project prefers reporting a problem over crashing the CLI."""
    report = MobiAnalysisReport(path=path)

    data = path.read_bytes()
    report.file_size = len(data)

    if not is_palmdb(data):
        report.error = (
            "This doesn't look like a PalmDB container at all -- not "
            "a MOBI/AZW/AZW3/PRC file this module recognizes."
        )
        return report

    try:
        palmdb = read_palmdb(data)
        record0_offset = palmdb.records[0].offset
        header = read_mobi_header(data, record0_offset)
    except (ValueError, IndexError) as exc:
        report.error = str(exc)
        return report

    mobi_offset = data.find(b"MOBI", record0_offset, min(len(data), record0_offset + 4096))
    exth_records = []
    if header.has_exth:
        exth_offset = exth_start_offset(mobi_offset, header.header_length)
        exth_records = read_exth(data, exth_offset)

    generation, confirmed = _detect_generation(header.file_version, exth_records)

    report.generation = generation
    report.generation_confirmed = confirmed
    report.compression = COMPRESSION_NAMES.get(header.compression, f"Unknown ({header.compression})")
    report.encryption = ENCRYPTION_NAMES.get(header.encryption_type, f"Unknown ({header.encryption_type})")
    report.text_length = header.text_length
    report.text_record_count = header.text_record_count
    report.palmdb_record_count = palmdb.record_count
    report.exth_tag_count = len(exth_records)
    report.metadata = extract_metadata(header, exth_records)

    cover_offset = exth_int(exth_records, 201)
    if cover_offset is not None:
        cover_index = header.first_image_record + cover_offset
        if 0 <= cover_index < len(palmdb.records):
            report.cover_record_index = cover_index
            blob = record_bytes(data, palmdb, cover_index)
            report.cover_media_type = sniff_image_media_type(blob) or "unrecognized image format"

    return report


def print_mobi_report(report: MobiAnalysisReport) -> None:
    """Prints a report in the same plain-text, underlined-header
    style `analyze` already uses for EPUB (see ebook_fix.report),
    clearly labeled as MOBI-specific wherever the two formats don't
    have a directly comparable answer."""
    print_header("[File]")
    console.print(f"Path: {report.path}")
    console.print(f"File size: {report.file_size:,} bytes")

    if report.error:
        console.print(f"\nERROR: {report.error}")
        return

    console.print("")
    print_header("[Book Metadata]")
    m = report.metadata
    console.print(f"Title: {m.title or '(none found)'}")
    console.print(f"Author: {m.author or '(none found)'}")
    console.print(f"Language: {m.language or '(none found, no EXTH language tag)'}")
    console.print(f"Publisher: {m.publisher or '(none found)'}")
    console.print(f"Date: {m.date or '(none found)'}")
    if m.isbn:
        console.print(f"ISBN: {m.isbn}")
    if m.asin:
        console.print(f"ASIN: {m.asin}")
    if m.subjects:
        console.print(f"Subjects/Genre: {', '.join(m.subjects)}")
    if m.description:
        console.print(f"Description: {m.description}")

    console.print("")
    print_header("[File Contents]")
    generation_line = report.generation
    if not report.generation_confirmed:
        generation_line += " (detection unconfirmed -- no real AZW3 sample tested yet)"
    console.print(f"Generation: {generation_line}")
    console.print(f"Compression: {report.compression}")
    console.print(f"Encryption: {report.encryption}")
    console.print(f"Text length (uncompressed): {report.text_length:,} bytes")
    console.print(f"Text records: {report.text_record_count}")
    console.print(f"PalmDB records (total): {report.palmdb_record_count}")
    console.print(f"EXTH metadata tags: {report.exth_tag_count}")
    if report.cover_record_index >= 0:
        console.print(f"Cover image: record {report.cover_record_index} ({report.cover_media_type})")
    else:
        console.print("Cover image: not found")
