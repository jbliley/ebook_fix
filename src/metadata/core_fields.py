"""
metadata.core_fields

Reads a book's core bibliographic fields -- title, author, language,
publisher, date, rights, description, subjects, and series -- as a
single grouped result for the analysis pass, and writes confidently-
resolved values back out. See docs/metadata_plan.md for the overall
design.

Reading is a plain pass-through over what ebook_fix.parser and
ebook_fix.series already extract. Writing (write_core_field,
write_subjects, apply_core_field_updates below) only ever gets called
with a value metadata.merge has already decided is safe -- either the
EPUB and a Calibre metadata.opf sidecar already agree, one side was
simply empty, or the two sides are the same value in a different
rendering (e.g. the reversed-author-name case) -- see
metadata.merge.MergedCoreFields.epub_updates()/.calibre_updates().
This module never decides *what* to write, only *how*.

The write functions are deliberately duck-typed against anything with
an .opf_document (and, optionally, .metadata and .mark_modified()) --
both a real Book and metadata.calibre_backend.OpfShim (used to write a
bare metadata.opf sidecar) work unmodified, so metadata.calibre_write
doesn't need its own copy of this logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from ebook_fix import series as series_metadata

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"

# Core-fields name -> (dc: element tag, book.metadata attribute name).
# Only scalar, single-element fields belong here -- "series"/
# "series_index" are dispatched to ebook_fix.series instead (it
# already knows how to keep both the calibre and EPUB3 collection
# conventions in sync), and "subjects" is a list, handled by
# write_subjects() below.
_DC_FIELD_MAP = {
    "title": "title",
    "author": "creator",
    "publisher": "publisher",
    "date": "date",
    "rights": "rights",
    "description": "description",
}


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


def write_core_field(target, field_name: str, value: str) -> bool:
    """Writes a single scalar core field (title, author, publisher,
    date, rights, or description -- see _DC_FIELD_MAP) into target's
    OPF, creating the dc: element if it doesn't exist yet. Also
    updates target.metadata (the in-memory snapshot ebook_fix's other
    analysis reads), when target has one, so a later re-analysis pass
    within the same run sees the new value instead of the stale one.

    Returns True if a change was actually made (False for an unknown
    field_name, a target with no OPF to write to, or a value that
    already matches what's there)."""
    tag = _DC_FIELD_MAP.get(field_name)
    if tag is None:
        return False

    opf = getattr(target, "opf_document", None)
    if opf is None:
        return False

    metadata_el = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata_el is None:
        return False

    el = opf.find(f".//{{{DC_NS}}}{tag}")
    if el is not None and (el.text or "") == value:
        return False

    if el is None:
        el = etree.SubElement(metadata_el, f"{{{DC_NS}}}{tag}")
    el.text = value

    metadata_snapshot = getattr(target, "metadata", None)
    if metadata_snapshot is not None:
        # tag doubles as the Metadata dataclass's attribute name for
        # every field here (dc:creator -> .creator, dc:date -> .date,
        # etc.) -- see _DC_FIELD_MAP.
        setattr(metadata_snapshot, tag, value)

    target.opf_modified = True
    if hasattr(target, "mark_modified"):
        target.mark_modified()
    return True


def write_subjects(target, subjects: list[str]) -> bool:
    """Replaces target's dc:subject list wholesale. Only ever called
    when metadata.merge found one side completely empty (see
    MergedCoreFields.subjects_for_epub()/.subjects_for_calibre()) --
    a genuine disagreement between two non-empty subject lists is left
    alone, same as every other field."""
    opf = getattr(target, "opf_document", None)
    if opf is None:
        return False

    metadata_el = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata_el is None:
        return False

    existing = opf.findall(f".//{{{DC_NS}}}subject")
    current = [(el.text or "").strip() for el in existing]
    if current == list(subjects):
        return False

    for el in existing:
        el.getparent().remove(el)
    for value in subjects:
        new_el = etree.SubElement(metadata_el, f"{{{DC_NS}}}subject")
        new_el.text = value

    metadata_snapshot = getattr(target, "metadata", None)
    if metadata_snapshot is not None:
        metadata_snapshot.subject = list(subjects)

    target.opf_modified = True
    if hasattr(target, "mark_modified"):
        target.mark_modified()
    return True


def apply_core_field_updates(target, updates: dict[str, str]) -> list[str]:
    """Applies a set of confidently-resolved core-field values (from
    MergedCoreFields.epub_updates() or .calibre_updates()) to any
    OPF-backed target -- a real Book, or metadata.calibre_write's
    sidecar shim. "series"/"series_index" are dispatched to
    ebook_fix.series (it already writes both the calibre and EPUB3
    collection conventions); everything else goes through
    write_core_field(). Returns the list of field names actually
    changed."""
    changed: list[str] = []

    series_value = updates.get("series")
    if series_value is not None:
        index = None
        series_index_value = updates.get("series_index")
        if series_index_value:
            try:
                index = float(series_index_value)
            except ValueError:
                index = None
        series_metadata.write(target, series_value, index)
        changed.append("series")
        if series_index_value:
            changed.append("series_index")

    for field_name, value in updates.items():
        if field_name in ("series", "series_index"):
            continue
        if write_core_field(target, field_name, value):
            changed.append(field_name)

    return changed
