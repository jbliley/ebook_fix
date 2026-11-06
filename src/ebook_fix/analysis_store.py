"""
ebook_fix.analysis_store

The one place an analysis keeps what it found, so the same findings can be
asked for again at any time without reading the whole book again.

Why this exists: repairs change the book, and a finding made before a repair
can be out of date after it. Until now each place that needed a finding to be
current worked that out for itself (the refresh tables in `engine.py`, the
confirming analysis at the start of each pass). The store does it once, in one
place: asking for a finding always gives the answer for the book as it is
right now, and only the pages that changed since it was last made are read
again. See `docs/single_analysis_plan.md` for where this is heading.

What is stored today are the findings that depend on one page's markup and
nothing else (whitespace, ellipsis, apostrophes and possessive candidates).
Each is kept per page, with a fingerprint of the page, by PageFindingCache
(see `ebook_fix.finding_cache` for exactly when a kept finding is trusted).
Findings that depend on more than one page are moved onto the store in a
later stretch of the plan.

The analysis report carries its store (`report.store`), so a Fix that starts
from an analysis already made, such as the Overview's, keeps what that
analysis found. The store is not part of the report's saved form.
"""

from __future__ import annotations

from ebook_fix.apostrophes import analyze_book_apostrophes, analyze_book_possessives
from ebook_fix.ellipsis import analyze_book_ellipsis
from ebook_fix.finding_cache import PageFindingCache
from ebook_fix.whitespace import analyze_book_whitespace

# name -> the function that makes the whole-book finding for it. Every one of
# these depends on a single page's markup and nothing else, and takes the
# page cache as `cache=`. The name is also the attribute it is stored under
# on the analysis report.
PAGE_FINDINGS = {
    "whitespace": analyze_book_whitespace,
    "ellipsis": analyze_book_ellipsis,
    "apostrophes": analyze_book_apostrophes,
    "possessives": analyze_book_possessives,
}


class AnalysisStore:
    """Findings kept for one book, current whenever they are asked for."""

    def __init__(self):
        self._pages = PageFindingCache()

    def findings(self, name, book):
        """The finding called `name` for `book` as it is now. A page that has
        not changed since the finding was last made for it keeps that
        answer; any other page is read again."""
        return PAGE_FINDINGS[name](book, cache=self._pages)

    @property
    def reused(self) -> int:
        """How many page lookups were answered from what was kept."""
        return self._pages.reused

    @property
    def recomputed(self) -> int:
        """How many page lookups had to read the page."""
        return self._pages.recomputed
