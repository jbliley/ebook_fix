"""
metadata.review

Appends a row to a running CSV log for anything metadata.identifiers
or metadata.merge couldn't confidently resolve on its own: an
unmatched identifier that fell back to bare DC, or a field where the
EPUB and a Calibre metadata.opf sidecar genuinely disagree. See
docs/metadata_plan.md, "Review log".

The point of this file isn't any single entry -- it's the pattern
across many books. A handful of books all hitting the same unmatched
identifier prefix, or the same field always disagreeing the same way,
is a signal worth promoting into identifier_schemes.json (or deciding
a standing preference for) instead of hand-fixing books one at a time.

Defaults to identifier_review.csv in the current working directory,
matching how ebook_fix.config's ebook_fix.toml also defaults to cwd.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from metadata.identifiers import IdentifierMatch
from metadata.merge import MergedCoreFields, MergedIdentifierSummary

DEFAULT_REVIEW_FILENAME = "identifier_review.csv"

FIELDNAMES = [
    "timestamp", "book_path", "title", "author", "kind",
    "field_or_scheme", "epub_or_raw_value", "calibre_or_raw_scheme", "note",
]


def default_review_path() -> Path:
    return Path.cwd() / DEFAULT_REVIEW_FILENAME


def _append_rows(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def log_unmatched_identifiers(
    book_path,
    title: str,
    author: str,
    identifiers: list[IdentifierMatch],
    source: str,
    path: Path | None = None,
) -> int:
    """Appends one row per fallback (unmatched) identifier from a
    single source (an EPUB's own OPF, or a metadata.opf sidecar).
    Returns how many rows were written."""
    path = path or default_review_path()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [
        {
            "timestamp": timestamp,
            "book_path": str(book_path),
            "title": title,
            "author": author,
            "kind": "unmatched_identifier",
            "field_or_scheme": source,
            "epub_or_raw_value": ident.raw_value,
            "calibre_or_raw_scheme": ident.raw_scheme,
            "note": "",
        }
        for ident in identifiers
        if ident.is_fallback
    ]
    _append_rows(rows, path)
    return len(rows)


def log_merge_conflicts(
    book_path,
    title: str,
    author: str,
    merged_identifiers: MergedIdentifierSummary,
    merged_core_fields: MergedCoreFields,
    path: Path | None = None,
) -> int:
    """Appends one row per EPUB-vs-metadata.opf disagreement: both
    same-scheme identifier conflicts and core-field mismatches.
    Returns how many rows were written."""
    path = path or default_review_path()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []

    for group in merged_identifiers.conflicts():
        values = " / ".join(f"{i.normalized_value} ({'+'.join(i.sources)})" for i in group)
        rows.append({
            "timestamp": timestamp,
            "book_path": str(book_path),
            "title": title,
            "author": author,
            "kind": "identifier_conflict",
            "field_or_scheme": group[0].matched_scheme,
            "epub_or_raw_value": values,
            "calibre_or_raw_scheme": "",
            "note": "",
        })

    for field_name in merged_core_fields.mismatched_fields():
        mf = getattr(merged_core_fields, field_name)
        rows.append({
            "timestamp": timestamp,
            "book_path": str(book_path),
            "title": title,
            "author": author,
            "kind": "field_mismatch",
            "field_or_scheme": field_name,
            "epub_or_raw_value": mf.epub_value,
            "calibre_or_raw_scheme": mf.calibre_value,
            "note": "",
        })

    if merged_core_fields.subjects_mismatch:
        rows.append({
            "timestamp": timestamp,
            "book_path": str(book_path),
            "title": title,
            "author": author,
            "kind": "field_mismatch",
            "field_or_scheme": "subjects",
            "epub_or_raw_value": ", ".join(merged_core_fields.subjects_epub),
            "calibre_or_raw_scheme": ", ".join(merged_core_fields.subjects_calibre),
            "note": "",
        })

    _append_rows(rows, path)
    return len(rows)
