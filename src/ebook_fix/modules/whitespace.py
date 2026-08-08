"""
ebook_fix.modules.whitespace

Uses the whitespace findings the analyzer already collected (see
ebook_fix.whitespace) instead of scanning the book itself. This used
to be the one repair tool left in the analysis-first migration that
still scanned the book twice -- once during `analyze`, again during
`repair` -- see docs/analysis_first_migration_plan.md, Phase 5.

The analyzer always runs with every rule turned on, to find the full
picture regardless of what the current config says (same as every
other analysis module -- config only controls what repair modules
DO with what was found, not what the analyzer looks for). So this
module can't just apply `issue.after` as-is: if the config has some
categories turned off, the text that node should actually end up with
is different from what the analyzer recorded. Rather than re-walking
the book to recompute that, each WhitespaceIssue already carries its
original `before` text plus the glue-sensitivity flags analysis
figured out for it -- cheap to run back through
ebook_fix.whitespace.normalize_fragment with the config's rules
applied, no DOM re-scan required.

If this ever runs without an analysis handed to it, it falls back to
scanning the book itself via ebook_fix.whitespace.analyze_book_whitespace.
"""

from __future__ import annotations

from ebook_fix.config import WhitespaceRepairConfig
from ebook_fix.report import Report
from ebook_fix.whitespace import (
    NormalizationRules,
    analyze_book_whitespace,
    normalize_fragment,
)


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
        rules = self._rules()

        for chapter_summary in whitespace.chapters:
            for issue in chapter_summary.issues:
                if issue.is_whitespace_only:
                    if not self.config.collapse_whitespace_only_nodes:
                        continue
                    report.add(issue.href, issue.category, f"{issue.category}: collapsed to a single space")
                    continue

                # Recompute with the config's active rules rather than
                # trusting issue.after -- that was captured with every
                # rule on, and may not match what repair would
                # actually do with some categories turned off.
                result = normalize_fragment(
                    issue.before, leading_glue=issue.leading_glue,
                    trailing_glue=issue.trailing_glue, rules=rules,
                )
                if not result.changed:
                    continue
                report.add(issue.href, issue.category, f"{issue.category}: {issue.before!r} -> {result.text!r}")

        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        if not self.config.enabled:
            return

        whitespace = analysis.whitespace if analysis is not None else analyze_book_whitespace(book)
        rules = self._rules()

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

                if issue.is_whitespace_only:
                    if not self.config.collapse_whitespace_only_nodes:
                        continue
                    new_text = " "
                else:
                    result = normalize_fragment(
                        issue.before, leading_glue=issue.leading_glue,
                        trailing_glue=issue.trailing_glue, rules=rules,
                    )
                    if not result.changed:
                        continue
                    new_text = result.text

                setattr(host, issue.attr, new_text if new_text else None)
                changed_hrefs.add(issue.href)

        if not changed_hrefs:
            return

        for chapter in book.chapters:
            if chapter.href in changed_hrefs:
                chapter.modified = True
        book.mark_modified()

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
