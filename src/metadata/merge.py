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
from metadata import author_names, language_codes, title_match
from metadata.core_fields import BookCoreFieldsSummary
from metadata.identifiers import BookIdentifierSummary, IdentifierMatch


# Every MergedCoreFields field that can actually be written back out,
# used by .epub_updates()/.calibre_updates() below. "language" is
# deliberately excluded -- both sides are already correct for their
# own format (see language_codes.py), so there's never anything to
# write there.
_WRITABLE_FIELDS = ("title", "author", "publisher", "date", "rights", "description", "series", "series_index")


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
    # Set when epub_value and calibre_value differ as plain strings but
    # are recognized as the *same* value once a known convention is
    # accounted for (e.g. "en" vs "eng", or "Smith, John" vs "John
    # Smith") -- see language_codes.py and author_names.py. When this
    # is set the field is never a mismatch, regardless of the raw
    # string comparison.
    equivalent: bool = False
    # For an equivalent field where one rendering should always win
    # (currently just the reversed-author case), the corrected value
    # to use instead of either raw side. Empty when there's nothing to
    # correct (e.g. the language-code case, where both sides are
    # already "correct" for their own format).
    normalized_value: str = ""
    # Short human-readable explanation of why an equivalent field
    # isn't flagged, or what was corrected -- for display only.
    note: str = ""

    @property
    def display_value(self) -> str:
        """The single value to show/use: the corrected form if one
        was determined, otherwise whichever side actually has data.
        metadata.opf is what Calibre-Web and Calibre itself actually
        display, so it's preferred when both sides are non-empty but
        agree, or when only one side has data at all. When they
        disagree and aren't equivalent, .mismatch is what should
        drive the UI, not this."""
        return self.normalized_value or self.calibre_value or self.epub_value

    @property
    def mismatch(self) -> bool:
        if self.equivalent:
            return False
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

    def epub_updates(self) -> dict[str, str]:
        """Field name -> confidently-resolved value, for every
        writable field (everything below except "language" -- see
        language_codes.py, there's nothing to write there) where the
        EPUB's own current value doesn't already match. A genuine
        mismatch is never included, only what's already agreed on, was
        simply missing from the EPUB, or was corrected (e.g. a
        reversed author name) -- see metadata.core_fields for what
        actually applies these."""
        result = {}
        for name in _WRITABLE_FIELDS:
            mf = getattr(self, name)
            if mf.mismatch:
                continue
            value = mf.display_value
            if value and value != mf.epub_value:
                result[name] = value
        return result

    def calibre_updates(self) -> dict[str, str]:
        """Same as epub_updates(), but for what metadata.opf is
        missing or has stale -- e.g. backfilling a series the EPUB has
        and metadata.opf doesn't, or correcting metadata.opf's own
        copy of a reversed author name."""
        result = {}
        for name in _WRITABLE_FIELDS:
            mf = getattr(self, name)
            if mf.mismatch:
                continue
            value = mf.display_value
            if value and value != mf.calibre_value:
                result[name] = value
        return result

    def subjects_for_epub(self) -> list[str] | None:
        """The calibre subject list, if the EPUB's own is simply empty
        and calibre has one -- an unambiguous backfill, not a guess.
        None otherwise (including a genuine disagreement between two
        non-empty lists, which stays flagged rather than resolved)."""
        if self.subjects_mismatch or self.subjects_epub or not self.subjects_calibre:
            return None
        return list(self.subjects_calibre)

    def subjects_for_calibre(self) -> list[str] | None:
        """The mirror of subjects_for_epub(): the EPUB's subject list,
        if metadata.opf's own is simply empty."""
        if self.subjects_mismatch or self.subjects_calibre or not self.subjects_epub:
            return None
        return list(self.subjects_epub)


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


def _language_field(epub_value: str, calibre_value: str) -> MergedField:
    """Language gets its own comparison because EPUB (ISO 639-1, e.g.
    "en") and Calibre (ISO 639-2/B, e.g. "eng") are both correct for
    their own format -- see language_codes.py. Neither side is
    "wrong", so there's nothing to correct, just a mismatch to
    suppress when the two codes are the same language."""
    mf = _field(epub_value, calibre_value)
    if mf.epub_value and mf.calibre_value and mf.epub_value != mf.calibre_value:
        if language_codes.codes_equivalent(mf.epub_value, mf.calibre_value):
            mf.equivalent = True
            mf.note = (
                f"'{mf.calibre_value}' (Calibre/ISO 639-2) and "
                f"'{mf.epub_value}' (EPUB/ISO 639-1) are the same "
                "language -- expected convention, not a mismatch."
            )
    return mf


def _author_field(epub_value: str, calibre_value: str) -> MergedField:
    """Detects the common "Last, First" vs "First Last" reversal and
    always resolves it to "First Last" rather than flagging it, per
    Jacob's standing preference -- see author_names.py. A genuinely
    different name still falls through as a normal mismatch."""
    mf = _field(epub_value, calibre_value)
    if mf.epub_value and mf.calibre_value and mf.epub_value != mf.calibre_value:
        canonical = author_names.detect_reversed_author(mf.epub_value, mf.calibre_value)
        if canonical is not None:
            mf.equivalent = True
            mf.normalized_value = canonical
            mf.note = (
                f"'{mf.calibre_value}' / '{mf.epub_value}' were the same name in "
                f"reversed order -- standardized to '{canonical}'."
            )
    return mf


def _title_field(epub_value: str, calibre_value: str) -> MergedField:
    """Recognizes a purely-cosmetic title difference (whitespace,
    smart-punctuation style, ALL CAPS) as the same title, resolved the
    same way the language/author non-issues are -- see
    title_match.py. A likely subtitle or series-annotation difference
    is a real editorial call, not a formatting cleanup, so it's never
    auto-resolved -- it still counts as a mismatch, just with a note
    explaining the likely relationship so a person can decide quickly
    instead of seeing a bare, unexplained MISMATCH."""
    mf = _field(epub_value, calibre_value)
    if mf.epub_value and mf.calibre_value and mf.epub_value != mf.calibre_value:
        canonical = title_match.titles_equivalent(mf.epub_value, mf.calibre_value)
        if canonical is not None:
            mf.equivalent = True
            mf.normalized_value = canonical
            mf.note = (
                f"'{mf.calibre_value}' / '{mf.epub_value}' were the same title "
                f"in different formatting -- standardized to '{canonical}'."
            )
        else:
            note = title_match.detect_subtitle_relationship(mf.epub_value, mf.calibre_value)
            if note:
                mf.note = note
    return mf


def merge_core_fields(
    epub_fields: BookCoreFieldsSummary,
    calibre_fields: BookCoreFieldsSummary,
) -> MergedCoreFields:
    result = MergedCoreFields(
        title=_title_field(epub_fields.title, calibre_fields.title),
        author=_author_field(epub_fields.author, calibre_fields.author),
        language=_language_field(epub_fields.language, calibre_fields.language),
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
