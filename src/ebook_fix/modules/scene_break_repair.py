"""
ebook_fix.modules.scene_break_repair

Uses the scene-break findings the analyzer already collected (see
ebook_fix.scene_breaks) instead of scanning the book itself.

Two different actions, one per classification:
  - MID_CHAPTER: replace the <hr> with a centered "<p>* * *</p>" --
    the conventional ebook rendering of a scene divider. A literal
    horizontal rule reads as a leftover web-page artifact to most
    readers; "* * *" (or a similar glyph) is what every major
    commercial ebook typically uses instead.
  - CHAPTER_EDGE: remove the <hr> outright. It's redundant with a
    chapter boundary that's already marked some other way (a heading,
    a page break); keeping it would just be a stray rule sitting next
    to content that already announces itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Set

from lxml import etree

from ebook_fix.config import SceneBreakRepairConfig
from ebook_fix.report import Report
from ebook_fix.scene_breaks import analyze_book_scene_breaks, MID_CHAPTER, CHAPTER_EDGE

if TYPE_CHECKING:
    from ebook_fix.book import Book

SCENE_BREAK_TEXT = "* * *"


def _remove_keep_tail(el) -> None:
    """Removes el, but if it had non-whitespace tail text (content
    that technically belongs to el in lxml's model, not to its
    neighbors), reattaches that tail rather than silently losing it.
    Same convention as gutenberg_repair.py's own helper of the same
    name -- kept as a separate copy rather than a shared import, since
    it's a three-line generic DOM utility, not something specific
    enough to this project to be worth its own shared module (compare
    text_range.py, which is genuinely ebook_fix-specific document-
    range logic reused by two analysis modules)."""
    tail = el.tail
    parent = el.getparent()
    if parent is None:
        return
    prev = el.getprevious()
    parent.remove(el)
    if tail and tail.strip():
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


def _replace_with_scene_break_marker(hr) -> None:
    """Swaps hr for a centered <p>* * *</p> in the same position,
    preserving hr's tail text (the whitespace/content immediately
    following it in the markup) on the new paragraph."""
    marker = etree.Element(hr.tag)
    marker.tag = "p"
    marker.text = SCENE_BREAK_TEXT
    marker.set("style", "text-align: center;")
    marker.tail = hr.tail
    parent = hr.getparent()
    parent.replace(hr, marker)


class SceneBreakRepair:
    name: str = "Scene Break Normalizer"

    def __init__(self, config: SceneBreakRepairConfig | None = None) -> None:
        self.config = config or SceneBreakRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book: Book, analysis: Any | None = None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        scene_breaks = self._get_summary(book, analysis)

        for chapter_summary in scene_breaks.chapters:
            for issue in chapter_summary.issues:
                if issue.classification == MID_CHAPTER and not self.config.replace_mid_chapter:
                    continue
                if issue.classification == CHAPTER_EDGE and not self.config.remove_chapter_edge:
                    continue
                report.add(issue.href, *self._describe(issue.classification))

        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book: Book, analysis: Any | None = None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        scene_breaks = self._get_summary(book, analysis)

        changed_hrefs: Set[str] = set()
        for chapter_summary in scene_breaks.chapters:
            for issue in chapter_summary.issues:
                hr = issue.element
                if hr is None or hr.getparent() is None:
                    continue

                if issue.classification == MID_CHAPTER:
                    if not self.config.replace_mid_chapter:
                        continue
                    _replace_with_scene_break_marker(hr)
                elif issue.classification == CHAPTER_EDGE:
                    if not self.config.remove_chapter_edge:
                        continue
                    _remove_keep_tail(hr)
                else:
                    continue

                report.add(issue.href, *self._describe(issue.classification))
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
    # Shared helpers
    # -----------------------------------------------------

    def _get_summary(self, book: Book, analysis: Any | None):
        if analysis is not None and getattr(analysis, "scene_breaks", None) is not None:
            return analysis.scene_breaks
        chapter_summary = getattr(analysis, "chapters", None) if analysis is not None else None
        frontmatter_summary = getattr(analysis, "frontmatter", None) if analysis is not None else None
        return analyze_book_scene_breaks(
            book, chapter_summary=chapter_summary, frontmatter_summary=frontmatter_summary
        )

    @staticmethod
    def _describe(classification: str) -> tuple[str, str]:
        if classification == MID_CHAPTER:
            return (
                "Mid-chapter scene break",
                f'Mid-chapter <hr> -> centered "{SCENE_BREAK_TEXT}"',
            )
        return (
            "Chapter-edge <hr> removed",
            "Chapter-edge <hr> removed (redundant with an existing boundary)",
        )
