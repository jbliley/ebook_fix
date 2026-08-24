"""
ebook_fix.modules.whitespace

Uses the whitespace findings the analyzer already collected (see
ebook_fix.whitespace) instead of scanning the book itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Set

from ebook_fix.config import WhitespaceRepairConfig
from ebook_fix.report import Report
from ebook_fix.whitespace import (
    NormalizationRules,
    analyze_book_whitespace,
    is_whitespace_only,
    normalize_fragment,
)

if TYPE_CHECKING:
    from ebook_fix.book import Book


class WhitespaceRepair:
    name: str = "Whitespace Normalizer"

    def __init__(self, config: WhitespaceRepairConfig | None = None) -> None:
        self.config = config or WhitespaceRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book: Book, analysis: Any | None = None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        whitespace = (
            analysis.whitespace
            if analysis is not None and getattr(analysis, "whitespace", None) is not None
            else analyze_book_whitespace(book)
        )
        rules = self._rules()

        for chapter_summary in whitespace.chapters:
            for issue in chapter_summary.issues:
                if issue.is_whitespace_only:
                    if not self.config.collapse_whitespace_only_nodes:
                        continue
                    report.add(
                        issue.href,
                        issue.category,
                        f"{issue.category}: collapsed to a single space",
                    )
                    continue

                # Recompute with the config's active rules rather than
                # trusting issue.after -- that was captured with every
                # rule on, and may not match what repair would
                # actually do with some categories turned off.
                result = normalize_fragment(
                    issue.before,
                    leading_glue=issue.leading_glue,
                    trailing_glue=issue.trailing_glue,
                    rules=rules,
                )
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

        whitespace = (
            analysis.whitespace
            if analysis is not None and getattr(analysis, "whitespace", None) is not None
            else analyze_book_whitespace(book)
        )
        rules = self._rules()

        changed_hrefs: Set[str] = set()
        for chapter_summary in whitespace.chapters:
            for issue in chapter_summary.issues:
                host = issue.element
                if host is None or not hasattr(issue, "attr"):
                    continue

                # An earlier repair module in this same run (e.g.
                # Paragraph Repair merging paragraphs, or Chapter
                # Markup splitting one) may have already changed this
                # exact text/tail since analysis ran. Rather than
                # discarding the whitespace fix outright in that case
                # -- which silently left doubled spaces and similar
                # issues sitting in the output whenever another module
                # happened to touch the same node first -- recompute
                # against whatever the text/tail actually is right
                # now. normalize_fragment is pure and safe to run on
                # any text regardless of where it came from, so this
                # costs nothing when the node wasn't touched (current
                # value equals issue.before, same result either way)
                # and closes the gap when it was.
                current_val = getattr(host, issue.attr, None)
                if current_val is None:
                    continue

                if issue.is_whitespace_only:
                    if not self.config.collapse_whitespace_only_nodes:
                        continue
                    if not is_whitespace_only(current_val):
                        # A later module gave this node real content
                        # since analysis ran -- it's no longer a pure
                        # whitespace node, so collapsing it to " "
                        # would eat that content. Leave it alone.
                        continue
                    if current_val == " ":
                        continue
                    new_text: str | None = " "
                    report.add(
                        issue.href,
                        issue.category,
                        f"{issue.category}: collapsed to a single space",
                    )
                else:
                    result = normalize_fragment(
                        current_val,
                        leading_glue=issue.leading_glue,
                        trailing_glue=issue.trailing_glue,
                        rules=rules,
                    )
                    if not result.changed:
                        continue
                    new_text = result.text
                    report.add(
                        issue.href,
                        issue.category,
                        f"{issue.category}: {current_val!r} -> {result.text!r}",
                    )

                setattr(host, issue.attr, new_text if new_text else None)
                changed_hrefs.add(issue.href)

        if not changed_hrefs:
            return report

        for chapter in book.chapters:
            if chapter.href in changed_hrefs:
                chapter.modified = True
        
        if hasattr(book, "mark_modified"):
            book.mark_modified()

        return report

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _rules(self) -> NormalizationRules:
        return NormalizationRules(
            fix_leading_indent=self.config.fix_leading_indent,
            fix_trailing_indent=self.config.fix_trailing_indent,
            fix_repeated_whitespace=self.config.fix_repeated_whitespace,
            fix_tabs=self.config.fix_tabs,
            fix_space_before_punct=self.config.fix_space_before_punct,
            fix_missing_sentence_space=self.config.fix_missing_sentence_space,
        )