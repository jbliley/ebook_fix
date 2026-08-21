"""
ebook_fix.modules.apostrophe_repair

Uses the missing-apostrophe findings the analyzer already collected
(see ebook_fix.apostrophes) instead of scanning the book itself.

Phase 1 only: known contractions from a closed whitelist. Possessives
are intentionally out of scope -- see ebook_fix.apostrophes and
docs/apostrophe_repair_plan.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Set

from ebook_fix.config import ApostropheRepairConfig
from ebook_fix.report import Report
from ebook_fix.apostrophes import (
    analyze_book_apostrophes,
    normalize_apostrophes_text,
    STRAIGHT_APOSTROPHE,
    CURLY_APOSTROPHE,
)

if TYPE_CHECKING:
    from ebook_fix.book import Book


def resolve_target_apostrophe_char(config: ApostropheRepairConfig, analysis: Any | None = None) -> str:
    """
    Turns config.target_style into an actual character to write.

    "straight" / "curly" force that character outright. "auto" (the
    default) follows whichever style the book's own prose already
    favors, per ebook_fix.typography's per-book apostrophe counts, so
    a repaired contraction doesn't stand out as differently-styled
    from every apostrophe already in the book. Falls back to straight
    if the book has no apostrophes of either style to go on (nothing
    to match yet, and straight is the safer, more universal default).
    """
    if config is None:
        config = ApostropheRepairConfig()

    if config.target_style == "straight":
        return STRAIGHT_APOSTROPHE
    if config.target_style == "curly":
        return CURLY_APOSTROPHE

    typography = getattr(analysis, "typography", None) if analysis is not None else None
    if typography is not None and getattr(typography, "total_curly_apostrophes", 0) > getattr(typography, "total_straight_apostrophes", 0):
        return CURLY_APOSTROPHE
    return STRAIGHT_APOSTROPHE


class ApostropheRepair:
    name: str = "Apostrophe Repair"

    def __init__(self, config: ApostropheRepairConfig | None = None) -> None:
        self.config = config or ApostropheRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book: Book, analysis: Any | None = None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        apostrophes = (
            analysis.apostrophes
            if analysis is not None and getattr(analysis, "apostrophes", None) is not None
            else analyze_book_apostrophes(book)
        )

        target_char = resolve_target_apostrophe_char(self.config, analysis)

        for chapter_summary in apostrophes.chapters:
            for issue in chapter_summary.issues:
                # Recompute with the resolved target character rather
                # than trusting issue.after -- that was captured
                # against the straight-apostrophe analysis default,
                # which may not match what repair would actually
                # write if the book favors curly apostrophes.
                result = normalize_apostrophes_text(issue.before, apostrophe_char=target_char)
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

    def repair(self, book: Book, analysis: Any | None = None) -> None:
        if not self.config.enabled:
            return

        apostrophes = (
            analysis.apostrophes
            if analysis is not None and getattr(analysis, "apostrophes", None) is not None
            else analyze_book_apostrophes(book)
        )

        target_char = resolve_target_apostrophe_char(self.config, analysis)

        changed_hrefs: Set[str] = set()
        for chapter_summary in apostrophes.chapters:
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
                # real, still-valid apostrophe fix just because some
                # unrelated part of the same text changed -- e.g. the
                # Ellipsis Normalizer fixing "..." earlier in the same
                # sentence), recompute fresh against whatever the text
                # actually is right now. normalize_apostrophes_text
                # only ever touches its own specific whitelisted
                # patterns, so re-running it against text another
                # module has already edited is safe: it can't collide
                # with or undo that module's change, since an ellipsis
                # fix and a missing-apostrophe fix never occupy the
                # same characters.
                source_text = issue.before if current_val == issue.before else current_val

                result = normalize_apostrophes_text(source_text, apostrophe_char=target_char)
                if not result.changed:
                    continue

                setattr(host, issue.attr, result.text)
                changed_hrefs.add(issue.href)

        if not changed_hrefs:
            return

        for chapter in book.chapters:
            if chapter.href in changed_hrefs:
                chapter.modified = True

        if hasattr(book, "mark_modified"):
            book.mark_modified()
