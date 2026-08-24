"""
ebook_fix.modules.ellipsis_repair

Uses the ellipsis findings the analyzer already collected (see
ebook_fix.ellipsis) instead of scanning the book itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Set

from ebook_fix.config import EllipsisRepairConfig
from ebook_fix.report import Report
from ebook_fix.ellipsis import analyze_book_ellipsis, normalize_ellipsis_text

if TYPE_CHECKING:
    from ebook_fix.book import Book


class EllipsisRepair:
    name: str = "Ellipsis Normalizer"

    def __init__(self, config: EllipsisRepairConfig | None = None) -> None:
        self.config = config or EllipsisRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book: Book, analysis: Any | None = None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        ellipsis = (
            analysis.ellipsis
            if analysis is not None and getattr(analysis, "ellipsis", None) is not None
            else analyze_book_ellipsis(book)
        )

        for chapter_summary in ellipsis.chapters:
            for issue in chapter_summary.issues:
                # Recompute with the config's active target style
                # rather than trusting issue.after -- that was
                # captured against the Unicode default, which may not
                # match what repair would actually write if "ascii"
                # is configured instead.
                result = normalize_ellipsis_text(issue.before, target_style=self.config.target_style)
                if not result.changed:
                    continue
                report.add(
                    issue.href,
                    issue.category,
                    f"{issue.category}: {issue.before!r} -> {result.text!r}",
                )

        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book: Book, analysis: Any | None = None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        ellipsis = (
            analysis.ellipsis
            if analysis is not None and getattr(analysis, "ellipsis", None) is not None
            else analyze_book_ellipsis(book)
        )

        changed_hrefs: Set[str] = set()
        for chapter_summary in ellipsis.chapters:
            for issue in chapter_summary.issues:
                host = issue.element
                if host is None or not hasattr(issue, "attr"):
                    continue

                current_val = getattr(host, issue.attr, None)
                if current_val is None:
                    continue

                # If an earlier repair module in this same run already
                # changed this exact text/tail since analysis ran, the
                # snapshot in issue.before is stale -- but rather than
                # skip the node outright (which would silently drop a
                # real, still-valid ellipsis fix just because some
                # unrelated part of the same text changed -- e.g.
                # Paragraph Repair merging this text with an adjacent
                # paragraph), recompute fresh against whatever the
                # text actually is right now. normalize_ellipsis_text
                # only ever touches "..."/literal-dot-run patterns, so
                # re-running it against text another module has
                # already edited is safe -- see the same reasoning in
                # apostrophe_repair.py's repair().
                source_text = issue.before if current_val == issue.before else current_val

                result = normalize_ellipsis_text(source_text, target_style=self.config.target_style)
                if not result.changed:
                    continue

                report.add(
                    issue.href,
                    issue.category,
                    f"{issue.category}: {source_text!r} -> {result.text!r}",
                )
                setattr(host, issue.attr, result.text)
                changed_hrefs.add(issue.href)

        if not changed_hrefs:
            return report

        for chapter in book.chapters:
            if chapter.href in changed_hrefs:
                chapter.modified = True

        if hasattr(book, "mark_modified"):
            book.mark_modified()

        return report
