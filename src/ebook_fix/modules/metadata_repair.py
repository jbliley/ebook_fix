"""
ebook_fix.modules.metadata_repair

Writes back the core-field values metadata.merge has already decided
are safe -- see metadata/merge.py's MergedCoreFields.epub_updates().
Only ever runs on a Calibre-managed book (analysis.calibre_context.
is_calibre_managed): the metadata.opf sidecar sitting next to it is
treated as a second source confirming the value, which is what makes
writing it automatically (rather than only ever through review and,
eventually, a GUI) a reasonable trade. A standalone EPUB has no such
backup, so this module does nothing for one -- unchanged from before,
still just flagged in identifier_review.csv if anything looks off.

A field only ever gets written here once metadata.merge has already
ruled out ambiguity: either the two sides already agree (nothing to
do), one side was simply empty (an unambiguous backfill), or the two
sides are the same value in a different rendering (the "Last, First"
author case) -- see merge.py's _author_field/_language_field. A
genuine disagreement (MergedField.mismatch) is never written here; it
stays in identifier_review.csv exactly as before. Language is never
touched at all -- both codes are already correct for their own
format, see metadata/language_codes.py.

Keeping metadata.opf's own copy in sync is a separate step -- see
metadata/calibre_write.py, invoked from engine.py only after a repair
has actually been written to disk, so a --dry-run repair never
touches metadata.opf, matching how it never writes the EPUB output
either.
"""
from __future__ import annotations

from ebook_fix.config import MetadataRepairConfig
from ebook_fix.report import Report
from metadata.core_fields import apply_core_field_updates, write_subjects


class MetadataSyncRepair:
    name = "Metadata Sync"

    def __init__(self, config: MetadataRepairConfig | None = None):
        self.config = config or MetadataRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self._is_applicable(analysis):
            return report

        merged = analysis.merged_core_fields
        for field_name, value in merged.epub_updates().items():
            report.add(
                "content.opf",
                f"{field_name.replace('_', ' ').title()} will be updated",
                f"-> {value!r} (confirmed by metadata.opf)",
            )

        new_subjects = merged.subjects_for_epub()
        if new_subjects is not None:
            report.add(
                "content.opf",
                "Subjects will be filled in",
                f"-> {', '.join(new_subjects)} (from metadata.opf, EPUB had none)",
            )

        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self._is_applicable(analysis):
            return report

        merged = analysis.merged_core_fields
        updates = merged.epub_updates()
        changed_fields = apply_core_field_updates(book, updates) if updates else []
        for field_name in changed_fields:
            report.add(
                "content.opf",
                f"{field_name.replace('_', ' ').title()} updated",
                f"-> {updates.get(field_name, '')!r} (confirmed by metadata.opf)",
            )

        new_subjects = merged.subjects_for_epub()
        if new_subjects is not None and write_subjects(book, new_subjects):
            report.add(
                "content.opf",
                "Subjects filled in",
                f"-> {', '.join(new_subjects)} (from metadata.opf, EPUB had none)",
            )

        return report

    # -----------------------------------------------------
    # Shared helpers
    # -----------------------------------------------------

    def _is_applicable(self, analysis) -> bool:
        if not self.config.enabled or analysis is None:
            return False
        calibre_context = getattr(analysis, "calibre_context", None)
        return bool(calibre_context and calibre_context.is_calibre_managed)
