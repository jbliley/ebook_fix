"""
ebook_fix.modules.scene_marker_repair

Makes every text scene-break marker look the same: a centered "* * *" with
a blank line above and below it.

Found in "The Numbers Killer": scene changes are marked with a paragraph
holding "# # #". Scene Break Normalizer only converts horizontal rules
(and blank spacer paragraphs), so the "# # #" lines survived repair. Worse,
once Paragraph Spacing removes the gap between body paragraphs, a marker has
nothing above or below it, so it runs into the text.

What this does, to each paragraph that holds nothing but three or more
ornament glyphs ("# # #", "###", "***", "* * * *", "- - -", "~ ~ ~", a row of
dashes or underscores):

- its text becomes "* * *";
- it is centered, with no indent and one blank line (1em) above and below.

Only the main story is touched, never the title page, copyright page or a
teaser after "The End". A marker made by another module (Scene Break Normalizer, a blank spacer
paragraph, Scene Opener Markers) is already "* * *" but is only centered, so
it gets the same spacing. Nothing else is touched: a paragraph with any
letter or digit in it, a heading or chapter header, a paragraph with a
picture, and a one or two glyph ornament (a lone fleuron, "*") are left as
they are.

Runs after the other scene break modules. A second run finds each marker
already done and does nothing.
"""
from __future__ import annotations

from ebook_fix.config import SceneMarkerConfig
from ebook_fix.headings import chapter_marker_elements, is_chapter_header
from ebook_fix.linebreaks import _MARKER_GLYPHS, _MARKER_MAX_GLYPHS, _ORNAMENTS
from ebook_fix.report import Report

SCENE_BREAK_TEXT = "* * *"
MARKER_ATTR = "data-ebookfix-scenebreak"
MARKER_STYLE = "text-align: center; text-indent: 0; margin: 1em 0;"
# The section sign is an ornament elsewhere in the tool, but a lone "§§§"
# is as likely to be a citation as a break, so it is not rewritten here.
_REWRITABLE = (_MARKER_GLYPHS | _ORNAMENTS) - {"§"}


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _glyphs(el) -> str:
    return "".join("".join(el.itertext()).replace(" ", " ").split())


def is_text_marker(el) -> bool:
    """A paragraph whose whole text is three or more break ornaments."""
    if el is None or _local(el) != "p":
        return False
    if any(_local(d) in ("img", "image", "svg", "table", "hr") for d in el.iterdescendants() if isinstance(d.tag, str)):
        return False
    glyphs = _glyphs(el)
    if not (3 <= len(glyphs) <= _MARKER_MAX_GLYPHS):
        return False
    return all(ch in _REWRITABLE for ch in glyphs)


def _is_done(el) -> bool:
    return (
        el.get(MARKER_ATTR) == "true"
        and "".join(el.itertext()).strip() == SCENE_BREAK_TEXT
        and (el.get("style") or "").strip() == MARKER_STYLE
    )


def _main_hrefs(book, analysis):
    """Hrefs of the pages in the book's main content, or None when the front
    and back matter boundaries could not be confirmed (then every page counts).
    A title page's "***" and a teaser's "* * *" divider are not scene breaks."""
    from ebook_fix.frontmatter import MAIN_ZONE, analyze_book_frontmatter
    summary = getattr(analysis, "frontmatter", None) if analysis is not None else None
    if summary is None:
        summary = analyze_book_frontmatter(book)
    if not getattr(summary, "boundaries_confirmed", False):
        return None
    return {cm.href for cm in summary.chapters if cm.zone == MAIN_ZONE}


def find_markers(book, analysis=None) -> list:
    """[(chapter, element)] for each marker paragraph not yet in final form."""
    chapters = getattr(analysis, "chapters", None) if analysis is not None else None
    heading_markers = chapter_marker_elements(chapters) if chapters is not None else None
    found = []
    main_hrefs = _main_hrefs(book, analysis)
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None or (main_hrefs is not None and chapter.href not in main_hrefs):
            continue
        for el in doc.iter("{*}p"):
            if not is_text_marker(el) or is_chapter_header(el, heading_markers):
                continue
            if _is_done(el):
                continue
            found.append((chapter, el))
    return found


class SceneMarkerRepair:
    name = "Scene Break Markers"

    def __init__(self, config: SceneMarkerConfig | None = None):
        self.config = config or SceneMarkerConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for chapter, el in find_markers(book, analysis):
            report.add(chapter.href, "Scene break marker", self._describe(el, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        changed = set()
        for chapter, el in find_markers(book, analysis):
            before = _glyphs(el)
            for child in list(el):
                el.remove(child)
            el.text = SCENE_BREAK_TEXT
            if "class" in el.attrib:
                del el.attrib["class"]
            el.set("style", MARKER_STYLE)
            el.set(MARKER_ATTR, "true")
            changed.add(chapter.href)
            report.add(chapter.href, "Scene break marker", f'"{before}" is now a centered "{SCENE_BREAK_TEXT}" with space above and below')
        if changed:
            for chapter in book.chapters:
                if chapter.href in changed:
                    chapter.modified = True
            if hasattr(book, "mark_modified"):
                book.mark_modified()
        return report

    @staticmethod
    def _describe(el, will):
        return f'"{_glyphs(el)}" will become a centered "{SCENE_BREAK_TEXT}" with space above and below'
