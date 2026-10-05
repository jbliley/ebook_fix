"""
ebook_fix.modules.scene_opener_repair

Puts a centered "* * *" in front of a scene change that has no visible
marker, only a telltale opening: the first words in capitals. Found in
"Sandman Slim" (Richard Kadrey), where each of 56 scene changes is a
paragraph beginning like

    THERE'S AN UNLIT parking lot behind an out-of-business movie multiplex.

with no marker, rule or heading anywhere. See ebook_fix.scene_openers for
exactly what counts as an opener and the book-wide checks that keep
ordinary prose from being touched (several such openers, rare among the
paragraphs, all looking alike, and never the first thing in a file or
right under a heading, rule, marker or blank spacer).

Only a marker is added. The opener keeps its text, its capitals and its
styling, and nothing is removed. It is a scene break, not a chapter: the
openers carry no label or number, so the chapter splitter has nothing to
recognize them by (and Sandman Slim's sections run from 78 words to over
6,000).

Runs right after Scene Break Normalizer. A second run finds each opener
already preceded by a marker and does nothing.
"""
from __future__ import annotations

from lxml import etree

from ebook_fix.config import SceneOpenerConfig
from ebook_fix.headings import chapter_marker_elements
from ebook_fix.report import Report
from ebook_fix.scene_openers import analyze_book_scene_openers

SCENE_BREAK_TEXT = "* * *"


class SceneOpenerRepair:
    name = "Scene Opener Markers"

    def __init__(self, config: SceneOpenerConfig | None = None):
        self.config = config or SceneOpenerConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        summary = self._summary(book, analysis)
        for chapter_summary in summary.chapters:
            for opener in chapter_summary.openers:
                report.add(
                    opener.href,
                    "Scene opener without a marker",
                    f'Scene opening "{opener.lead}" will get a centered "{SCENE_BREAK_TEXT}" before it',
                )
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        summary = self._summary(book, analysis)

        changed = set()
        for chapter_summary in summary.chapters:
            for opener in chapter_summary.openers:
                p = opener.element
                parent = p.getparent() if p is not None else None
                if parent is None:
                    continue
                marker = etree.Element(p.tag)
                marker.text = SCENE_BREAK_TEXT
                marker.set("style", "text-align: center;")
                marker.tail = "\n"
                parent.insert(parent.index(p), marker)
                changed.add(opener.href)
                report.add(
                    opener.href,
                    "Scene opener marked",
                    f'Added a centered "{SCENE_BREAK_TEXT}" before the scene opening "{opener.lead}"',
                )

        if changed:
            for chapter in book.chapters:
                if chapter.href in changed:
                    chapter.modified = True
            if hasattr(book, "mark_modified"):
                book.mark_modified()
        return report

    # -----------------------------------------------------

    def _summary(self, book, analysis):
        frontmatter = getattr(analysis, "frontmatter", None) if analysis is not None else None
        chapters = getattr(analysis, "chapters", None) if analysis is not None else None
        markers = chapter_marker_elements(chapters) if chapters is not None else None
        return analyze_book_scene_openers(book, frontmatter_summary=frontmatter, chapter_markers=markers)
