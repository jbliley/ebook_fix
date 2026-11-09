"""
ebook_fix.modules.extra_page_break_repair

Removes forced page breaks that can only produce an empty page. Every
spine file already starts on a new page in a reading app, so a forced
break with nothing visible between it and the start (or end) of its file
stacks a second break on top of the file boundary. Found in "The Loner:
Crossfire": each chapter file opened with a blank paragraph carrying
page-break-after, so every chapter was preceded by a blank page.

Only inline style declarations are removed, and only the forced-break
properties (page-break-before/after, break-before/after); the rest of the
style attribute is kept. A break that has visible text or a picture on
the page before it is never touched. Re-running finds nothing to do.

Whether a page forces any break at all comes from the page records the analysis
store keeps (ebook_fix.text_lines), so only a page that does is opened.
"""
from __future__ import annotations

from ebook_fix.config import ExtraPageBreakConfig
from ebook_fix.page_breaks import redundant_forced_breaks, _strip_break_props
from ebook_fix.report import Report
from ebook_fix.text_lines import text_lines_for


class ExtraPageBreakRepair:
    name = "Extra Page Break Removal"

    def __init__(self, config: ExtraPageBreakConfig | None = None):
        self.config = config or ExtraPageBreakConfig()

    def _find(self, book, analysis=None):
        # The page records (ebook_fix.text_lines) say whether any element of a
        # page forces a page break; a page where none does is not opened.
        for chapter, record in zip(book.chapters, text_lines_for(book, analysis)):
            doc = getattr(chapter, "document", None)
            if doc is None or not record.forced_break:
                continue
            found = redundant_forced_breaks(doc)
            if found:
                yield chapter, found

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for chapter, found in self._find(book, analysis):
            report.add(chapter.href, "Extra page break before a chapter", f"{len(found)} forced break(s) would only add an empty page")
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        changed = False
        for chapter, found in list(self._find(book, analysis)):
            for el, props in found:
                _strip_break_props(el, set(props))
            chapter.modified = True
            changed = True
            report.add(chapter.href, "Extra page break removed", f"{len(found)} forced break(s)")
        if changed:
            book.mark_modified()
        return report
