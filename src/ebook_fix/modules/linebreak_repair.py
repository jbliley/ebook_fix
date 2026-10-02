"""
ebook_fix.modules.linebreak_repair

Uses the stray line break findings the analyzer already collected (see
ebook_fix.linebreaks) instead of scanning the book itself.

Removes a <br/> that sits at the very start or very end of a paragraph
or heading inside the main story. Left alone, each one renders as an
extra blank line between paragraphs (or a gap above a chapter title)
whenever the book's stylesheet gives paragraphs no spacing of their own.

Only the <br/> itself goes. Any text that followed it is kept, and a
<br/> in the middle of a paragraph (poetry, an address block, a
signature) is never touched.

Runs before Paragraph Repair on purpose: if a paragraph that starts with
a stray break got merged into the paragraph before it, that break would
end up stranded in the middle of a sentence.

If this ever runs without an analysis handed to it, it falls back to
scanning the book itself via ebook_fix.linebreaks.analyze_book_linebreaks.
"""

from __future__ import annotations

from ebook_fix.config import LineBreakRepairConfig
from ebook_fix.linebreaks import analyze_book_linebreaks
from ebook_fix.report import Report

_EDGE_WHITESPACE = " \t\r\n"


class LineBreakRepair:
    name = "Stray Line Break Removal"

    def __init__(self, config: LineBreakRepairConfig | None = None):
        self.config = config or LineBreakRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        if not self.config.remove_stray_breaks:
            return report

        summary = self._summary(book, analysis)
        for chapter_summary in summary.chapters:
            for stray in chapter_summary.stray_breaks:
                report.add(chapter_summary.href, f"Stray {stray.kind} line break")
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        report = Report(self.name)
        if not self.config.remove_stray_breaks:
            return report

        summary = self._summary(book, analysis)
        by_href = {c.href: c for c in summary.chapters}

        for chapter in book.chapters:
            chapter_summary = by_href.get(chapter.href)
            if chapter_summary is None:
                continue

            changed = False
            for stray in chapter_summary.stray_breaks:
                if self._remove_break(stray.element, stray.kind):
                    changed = True
                    report.add(
                        chapter.href,
                        f"Stray {stray.kind} line break removed",
                        f"Removed a {stray.kind} <br/> in <{stray.block_tag}>: {stray.preview!r}",
                    )

            if changed:
                chapter.modified = True
                book.mark_modified()

        return report

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    @staticmethod
    def _summary(book, analysis):
        existing = getattr(analysis, "linebreaks", None) if analysis is not None else None
        return existing if existing is not None else analyze_book_linebreaks(book)

    @staticmethod
    def _remove_break(br, kind) -> bool:
        """Removes `br`, keeping its tail text, and trims the stray
        whitespace left at the paragraph's edge. Returns False if the
        element is already gone."""
        parent = br.getparent()
        if parent is None:
            return False

        tail = br.tail
        br.tail = None
        previous = br.getprevious()

        if previous is not None:
            merged = (previous.tail or "") + (tail or "")
        else:
            merged = (parent.text or "") + (tail or "")

        if kind == "leading":
            merged = merged.lstrip(_EDGE_WHITESPACE)
        else:
            merged = merged.rstrip(_EDGE_WHITESPACE)
        merged = merged or None

        if previous is not None:
            previous.tail = merged
        else:
            parent.text = merged

        parent.remove(br)
        return True
