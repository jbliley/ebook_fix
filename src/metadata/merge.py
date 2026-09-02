"""
metadata.merge

Reconciles a book's own internal metadata against a Calibre
metadata.opf sidecar, when both exist. Per docs/metadata_plan.md,
neither source is treated as automatically correct -- a real book
(Fellowship of the Ring) showed the two genuinely disagreeing on ISBN
and publisher, with series info that existed only in metadata.opf. So
this module's job is to record both sides and flag disagreement,
not to silently pick a winner. Deciding what to do about a flagged
mismatch is a person's call (or the GUI's, later) -- this module never
resolves one on its own.

When a book isn't Calibre-managed, or the metadata.opf couldn't be
read, the "calibre" side of every comparison is simply empty and
nothing is ever flagged as a mismatch -- so callers can run this
unconditionally and get sensible single-source behavior for free.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ebook_fix import series as series_metadata
from metadata.core_fields import BookCoreFieldsSummary
from metadata.identifiers import BookIdentifierSummary, IdentifierMatch


@dataclass(slots=True)
class MergedIdentifier:
    matched_scheme: str = ""      # "" for an unmatched/fallback entry
    normalized_value: str = ""
    is_fallback: bool = False
    sources: list[str] = field(default_factory=list)  # "epub" and/or "calibre_opf"


@dataclass(slots=True)
class MergedIdentifierSummary:
    identifiers: list[MergedIdentifier] = field(default_factory=list)

    @property
    def primary(self) -> MergedIdentifier | None:
        if not self.identifiers:
            return None
        for ident in self.identifiers:
            if ident.matched_scheme == "ISBN":
                return ident
        for ident in self.identifiers:
            if not ident.is_fallback:
                return ident
        return self.identifiers[0]

    def conflicts(self) -> list[list[MergedIdentifier]]:
        """Groups of same-scheme identifiers that disagree on value --
        e.g. two different ISBNs, one from each source. Fallback
        (unmatched) entries are never grouped this way; there's no
        scheme to compare them on."""
        by_scheme: dict[str, list[MergedIdentifier]] = {}
        for ident in self.identifiers:
            if ident.is_fallback or not ident.matched_scheme:
                continue
            by_scheme.setdefault(ident.matched_scheme, []).append(ident)
        return [group for group in by_scheme.values() if len(group) > 1]


@dataclass(slots=True)
class MergedField:
    epub_value: str = ""
    calibre_value: str = ""

    @property
    def display_value(self) -> str:
        """Whichever side actually has data; metadata.opf is what
        Calibre-Web and Calibre itself actually display, so it's
        preferred as the single value when both sides are non-empty
        but agree, or when only one side has data at all. When they
        disagree, .mismatch is what should drive the UI, not this."""
        return self.calibre_value or self.epub_value

    @property
    def mismatch(self) -> bool:
        return bool(self.epub_value) and bool(self.calibre_value) and self.epub_value != self.calibre_value


@dataclass(slots=True)
class MergedCoreFields:
    title: MergedField = field(default_factory=MergedField)
    author: MergedField = field(default_factory=MergedField)
    language: MergedField = field(default_factory=MergedField)
    publisher: MergedField = field(default_factory=MergedField)
    date: MergedField = field(default_factory=MergedField)
    rights: MergedField = field(default_factory=MergedField)
    description: MergedField = field(default_factory=MergedField)
    series: MergedField = field(default_factory=MergedField)
    series_index: MergedField = field(default_factory=MergedField)  # formatted string, see series.format_index
    subjects_epub: list[str] = field(default_factory=list)
    subjects_calibre: list[str] = field(default_factory=list)

    def mismatched_fields(self) -> list[str]:
        """Names of every field where both sources have data and it
        disagrees -- what a review display should actually list."""
        names = ["title", "author", "language", "publisher", "date",
                 "rights", "description", "series", "series_index"]
        return [name for name in names if getattr(self, name).mismatch]

    @property
    def subjects_mismatch(self) -> bool:
        return bool(self.subjects_epub) and bool(self.subjects_calibre) \
            and set(self.subjects_epub) != set(self.subjects_calibre)


def merge_identifiers(
    epub_identifiers: list[IdentifierMatch],
    calibre_identifiers: list[IdentifierMatch],
) -> MergedIdentifierSummary:
    merged: dict[tuple[str, str], MergedIdentifier] = {}
    order: list[tuple[str, str]] = []

    for source_name, source_list in (("epub", epub_identifiers), ("calibre_opf", calibre_identifiers)):
        for ident in source_list:
            key = (ident.matched_scheme, ident.normalized_value)
            if key not in merged:
                merged[key] = MergedIdentifier(
                    matched_scheme=ident.matched_scheme,
                    normalized_value=ident.normalized_value,
                    is_fallback=ident.is_fallback,
                )
                order.append(key)
            merged[key].sources.append(source_name)

    return MergedIdentifierSummary(identifiers=[merged[k] for k in order])


def _field(epub_value: str, calibre_value: str) -> MergedField:
    return MergedField(epub_value=epub_value or "", calibre_value=calibre_value or "")


def merge_core_fields(
    epub_fields: BookCoreFieldsSummary,
    calibre_fields: BookCoreFieldsSummary,
) -> MergedCoreFields:
    result = MergedCoreFields(
        title=_field(epub_fields.title, calibre_fields.title),
        author=_field(epub_fields.author, calibre_fields.author),
        language=_field(epub_fields.language, calibre_fields.language),
        publisher=_field(epub_fields.publisher, calibre_fields.publisher),
        date=_field(epub_fields.date, calibre_fields.date),
        rights=_field(epub_fields.rights, calibre_fields.rights),
        description=_field(epub_fields.description, calibre_fields.description),
        series=_field(epub_fields.series, calibre_fields.series),
        series_index=_field(
            series_metadata.format_index(epub_fields.series_index),
            series_metadata.format_index(calibre_fields.series_index),
        ),
        subjects_epub=list(epub_fields.subjects),
        subjects_calibre=list(calibre_fields.subjects),
    )
    return result
