"""
metadata.core_fields

Reads a book's core bibliographic fields -- title, author, language,
publisher, date, rights, description, subjects, and series -- as a
single grouped result for the analysis pass. See docs/metadata_plan.md
for the overall design.

For now this is a read-only pass-through over what ebook_fix.parser
and ebook_fix.series already extract; it exists as its own module so
the module boundary matches the plan (metadata.identifiers and
metadata.core_fields as separate concerns), even though core_fields
doesn't yet need any matching/normalization logic of its own the way
identifiers.py does. Formatting-standardization rules (e.g. an author
name convention) are an open question noted in docs/metadata_plan.md,
not yet designed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ebook_fix import series as series_metadata


@dataclass(slots=True)
class BookCoreFieldsSummary:
    title: str = ""
    author: str = ""
    language: str = ""
    publisher: str = ""
    date: str = ""
    rights: str = ""
    description: str = ""
    subjects: list[str] = field(default_factory=list)
    series: str = ""
    series_index: float | None = None


def analyze_book_core_fields(book) -> BookCoreFieldsSummary:
    """Read-only snapshot of a book's core bibliographic fields."""
    result = BookCoreFieldsSummary()

    meta = getattr(book, "metadata", None)
    if meta is not None:
        result.title = getattr(meta, "title", "")
        result.author = getattr(meta, "creator", "")
        result.language = getattr(meta, "language", "")
        result.publisher = getattr(meta, "publisher", "")
        result.date = getattr(meta, "date", "")
        result.rights = getattr(meta, "rights", "")
        result.description = getattr(meta, "description", "")
        result.subjects = list(getattr(meta, "subject", []) or [])

    series_info = series_metadata.read(book)
    result.series = series_info.name
    result.series_index = series_info.index

    return result
