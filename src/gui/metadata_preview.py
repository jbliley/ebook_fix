"""
gui.metadata_preview

What Fix This Book is going to do to each metadata field, worked out
ahead of time so the Metadata tab can say so next to the field itself
(see docs/gui_redesign_plan.md, Phase 3). Without this, someone could
retype a title that Title Cleanup was about to fix anyway.

The four repairs that touch metadata (Title Cleanup, Metadata Sync,
Identifier Standardize, Author Initials) only ever change the book in
memory; nothing reaches the file until the whole pass is written. So
the preview simply runs those same repairs, in the same order the
engine runs them, against a throwaway copy of the book and compares
the fields before and after. It never has its own rules, so it cannot
disagree with what the repairs really do.

The caller passes in a book that already has the person's saved
metadata edits applied, because Fix applies those first and the
repairs then work on top of them.
"""
from __future__ import annotations

from types import SimpleNamespace

from ebook_fix import series as series_metadata
from ebook_fix.modules.author_initials_repair import AuthorInitialsRepair
from ebook_fix.modules.identifier_repair import IdentifierStandardizeRepair
from ebook_fix.modules.metadata_repair import MetadataSyncRepair
from ebook_fix.modules.title_cleanup_repair import TitleCleanupRepair
from gui.overview_view import PLAIN_NAMES
from metadata.calibre_backend import read_metadata_opf
from metadata.calibre_detect import detect as detect_calibre
from metadata.core_fields import BookCoreFieldsSummary, analyze_book_core_fields
from metadata.merge import merge_core_fields

# Same order Engine._build_modules() runs them in.
_METADATA_REPAIRS = [
    ("title_cleanup", TitleCleanupRepair),
    ("metadata_repair", MetadataSyncRepair),
    ("identifier_repair", IdentifierStandardizeRepair),
    ("author_initials", AuthorInitialsRepair),
]

# The Engine repeats the whole module list until a pass changes
# nothing (capped at 5). Metadata settles in one or two passes; three
# is plenty here.
_MAX_PASSES = 3

_TEXT_FIELDS = ("title", "author", "publisher", "date", "rights", "description", "language")


def _normalized(value) -> str:
    return " ".join(str(value or "").split())


def _snapshot(book) -> dict:
    summary = analyze_book_core_fields(book)
    values = {name: _normalized(getattr(summary, name)) for name in _TEXT_FIELDS}
    values["series_name"] = _normalized(summary.series)
    values["series_index"] = series_metadata.format_index(summary.series_index)
    return values


def _light_analysis(book):
    """The two things MetadataSyncRepair reads from a full analysis (is
    this a Calibre book, and what do the EPUB and metadata.opf agree
    on), computed straight from the book as it is right now. A full
    EPUBAnalyzer pass would give the same answer for these two fields
    but takes seconds on a big book."""
    context = detect_calibre(book.source) if getattr(book, "source", None) else None
    snapshot = None
    if context is not None and context.is_calibre_managed and context.metadata_opf_path:
        snapshot = read_metadata_opf(context.metadata_opf_path)
    merged = merge_core_fields(
        analyze_book_core_fields(book),
        snapshot.core_fields if snapshot else BookCoreFieldsSummary(),
    )
    return SimpleNamespace(calibre_context=context, merged_core_fields=merged)


def preview_repairs(book, config, selected) -> dict:
    """Returns {field: {"after": value, "by": plain-English repair name}}
    for every metadata field the ticked repairs will change, plus a
    single "series" entry ({"name", "index", "by"}) when the series
    name or position will change.

    `book` is mutated (it is the throwaway copy); `config` is a freshly
    loaded Config the caller does not use for anything else, since each
    repair's switch is set here to match what is ticked."""
    for attr, _cls in _METADATA_REPAIRS:
        getattr(config, attr).enabled = attr in selected

    modules = [(attr, cls(getattr(config, attr))) for attr, cls in _METADATA_REPAIRS if attr in selected]
    if not modules:
        return {}

    before = _snapshot(book)
    changed_by: dict = {}
    current = dict(before)
    for _ in range(_MAX_PASSES):
        analysis = _light_analysis(book)
        pass_changed = False
        for attr, module in modules:
            module.repair(book, analysis)
            after = _snapshot(book)
            for field, value in after.items():
                if value != current[field]:
                    changed_by.setdefault(field, PLAIN_NAMES.get(attr, attr))
                    pass_changed = True
            current = after
        if not pass_changed:
            break

    result: dict = {}
    for field in _TEXT_FIELDS:
        if current[field] != before[field]:
            result[field] = {"after": current[field], "by": changed_by[field]}
    if current["series_name"] != before["series_name"] or current["series_index"] != before["series_index"]:
        result["series"] = {
            "name": current["series_name"],
            "index": current["series_index"],
            "by": changed_by.get("series_name") or changed_by.get("series_index"),
        }
    return result
