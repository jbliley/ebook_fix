"""
ebook_fix.modules.whitespace

Uses the whitespace findings the analyzer already collected (see
ebook_fix.whitespace) instead of scanning the book itself. This used
to be the one repair tool left in the analysis-first migration that
still scanned the book twice -- once during `analyze`, again during
`repair` -- see docs/analysis_first_migration_plan.md, Phase 5.

Every issue the analyzer found already carries the exact normalized
replacement text alongside a live reference to the element it came
from, so repair just applies `issue.after` directly rather than
recomputing anything.

If this ever runs without an analysis handed to it, it falls back to
scanning the book itself via ebook_fix.whitespace.analyze_book_whitespace.
"""

from __future__ import annotations
from ebook_fix.config import WhitespaceRepairConfig
from ebook_fix.report import Report
from ebook_fix.whitespace import analyze_book_whitespace


class WhitespaceRepair:
    name = "Whitespace Normalizer"

    def __init__(self, config: WhitespaceRepairConfig | None = None):
        self.config = config or WhitespaceRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        if not self.config.enabled:
            return report

        whitespace = analysis.whitespace if analysis is not None else analyze_book_whitespace(book)
        for chapter_summary in whitespace.chapters:
            for issue in chapter_summary.issues:
                report.add(
                    issue.href,
                    issue.category,
                    f"{issue.category}: {issue.before!r} -> {issue.after!r}",
                )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        if not self.config.enabled:
            return

        whitespace = analysis.whitespace if analysis is not None else analyze_book_whitespace(book)

        changed_hrefs = set()
        for chapter_summary in whitespace.chapters:
            for issue in chapter_summary.issues:
                host = issue.element
                if host is None:
                    continue
                # If an earlier repair module in this same run (e.g.
                # Paragraph Repair merging paragraphs) already changed
                # this exact text/tail since analysis ran, don't
                # overwrite something we no longer recognize.
                if getattr(host, issue.attr, None) != issue.before:
                    continue
                setattr(host, issue.attr, issue.after if issue.after else None)
                changed_hrefs.add(issue.href)

        if not changed_hrefs:
            return

        for chapter in book.chapters:
            if chapter.href in changed_hrefs:
                chapter.modified = True
        book.mark_modified()
