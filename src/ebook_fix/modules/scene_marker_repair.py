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

The marker is the book's own when it has one. A book that already marks its
scene changes with a row of decorative symbols ("\u221e \u221e \u221e \u221e \u221e", a row of
diamonds) keeps that marker: the most common such line in the main story
becomes the book's marker, and every scene break the tool adds or tidies uses
it instead of "* * *". Horizontal-rule lookalikes (a long run of dashes or
underscores, a line drawn from box characters) never count. A book with no
marker of its own, or with only one such line, gets "* * *" as before.

Runs after the other scene break modules. A second run finds each marker
already done and does nothing.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from ebook_fix.config import SceneMarkerConfig
from ebook_fix.headings import chapter_marker_elements, is_chapter_header
from ebook_fix.linebreaks import _MARKER_GLYPHS, _MARKER_MAX_GLYPHS, _ORNAMENTS, _SYMBOL_CATEGORIES
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


# A marker of the book's own must be short and must not be a horizontal rule
# drawn in text: a run of dashes, underscores or box-drawing characters.
_HOUSE_MAX_GLYPHS = 9
_HOUSE_MIN_COUNT = 2
_RULE_GLYPHS = frozenset("-_=\u2013\u2014\u2012\u2015\u2500\u2501\u2550\u2504\u2505\u2508\u2509")


def _marker_text(el) -> str:
    """The paragraph's text with every run of whitespace made one space."""
    return " ".join("".join(el.itertext()).replace("\u00a0", " ").split())


def _is_rule_like(text: str) -> bool:
    glyphs = text.replace(" ", "")
    if not glyphs or any(ch not in _RULE_GLYPHS for ch in glyphs):
        return False
    # "- - -" is a spaced marker; "-----" or a long run is a drawn line.
    return " " not in text or len(glyphs) > 5


def _is_house_candidate(el) -> bool:
    """A paragraph holding only a short row of marker glyphs that could be
    this book's own scene break, "\u221e \u221e \u221e" or "* * *" but not a drawn line."""
    if el is None or _local(el) != "p":
        return False
    if any(_local(d) in ("img", "image", "svg", "table", "hr") for d in el.iterdescendants() if isinstance(d.tag, str)):
        return False
    text = _marker_text(el)
    glyphs = text.replace(" ", "")
    if not 1 <= len(glyphs) <= _HOUSE_MAX_GLYPHS or _is_rule_like(text):
        return False
    for ch in glyphs:
        if ch in _REWRITABLE or unicodedata.category(ch) in _SYMBOL_CATEGORIES:
            continue
        return False
    # One or two glyphs is a marker only when they are star-like ornaments;
    # a lone "+" or "=" is not.
    return len(glyphs) >= 3 or all(ch in _ORNAMENTS for ch in glyphs)


def house_marker_text(book, analysis=None) -> str:
    """The text every scene break in this book should show: the book's own
    most common marker line in the main story (at least twice), else "* * *"."""
    chapters = getattr(analysis, "chapters", None) if analysis is not None else None
    heading_markers = chapter_marker_elements(chapters) if chapters is not None else None
    main_hrefs = _main_hrefs(book, analysis)
    counts: Counter = Counter()
    first_seen: dict = {}
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None or (main_hrefs is not None and chapter.href not in main_hrefs):
            continue
        for el in doc.iter("{*}p"):
            if not _is_house_candidate(el) or is_chapter_header(el, heading_markers):
                continue
            text = _marker_text(el)
            counts[text] += 1
            first_seen.setdefault(text, len(first_seen))
    if not counts:
        return SCENE_BREAK_TEXT
    best = max(counts, key=lambda t: (counts[t], -first_seen[t]))
    return best if counts[best] >= _HOUSE_MIN_COUNT else SCENE_BREAK_TEXT


def is_text_marker(el, house: str = SCENE_BREAK_TEXT) -> bool:
    """A paragraph whose whole text is three or more break ornaments, or the
    book's own marker line."""
    if el is None or _local(el) != "p":
        return False
    if any(_local(d) in ("img", "image", "svg", "table", "hr") for d in el.iterdescendants() if isinstance(d.tag, str)):
        return False
    if house != SCENE_BREAK_TEXT and _marker_text(el) == house:
        return True
    glyphs = _glyphs(el)
    if not (3 <= len(glyphs) <= _MARKER_MAX_GLYPHS):
        return False
    return all(ch in _REWRITABLE for ch in glyphs)


def _declarations(style: str) -> set:
    return {
        re.sub(r"\s+", " ", d.strip().lower())
        for d in (style or "").split(";") if d.strip()
    }


def _is_done(el, house: str = SCENE_BREAK_TEXT) -> bool:
    # Another repair may add a property to the marker (for example a page break
    # after the last scene break of a chapter); that is still a finished marker.
    return (
        el.get(MARKER_ATTR) == "true"
        and "".join(el.itertext()).strip() == house
        and _declarations(MARKER_STYLE) <= _declarations(el.get("style"))
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


def find_markers(book, analysis=None, house: str | None = None) -> list:
    """[(chapter, element)] for each marker paragraph not yet in final form."""
    if house is None:
        house = house_marker_text(book, analysis)
    chapters = getattr(analysis, "chapters", None) if analysis is not None else None
    heading_markers = chapter_marker_elements(chapters) if chapters is not None else None
    found = []
    main_hrefs = _main_hrefs(book, analysis)
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None or (main_hrefs is not None and chapter.href not in main_hrefs):
            continue
        for el in doc.iter("{*}p"):
            if not is_text_marker(el, house) or is_chapter_header(el, heading_markers):
                continue
            if _is_done(el, house):
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
        house = house_marker_text(book, analysis)
        for chapter, el in find_markers(book, analysis, house):
            report.add(chapter.href, "Scene break marker", self._describe(el, house))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        changed = set()
        house = house_marker_text(book, analysis)
        for chapter, el in find_markers(book, analysis, house):
            before = _glyphs(el)
            for child in list(el):
                el.remove(child)
            el.text = house
            if "class" in el.attrib:
                del el.attrib["class"]
            el.set("style", MARKER_STYLE)
            el.set(MARKER_ATTR, "true")
            changed.add(chapter.href)
            report.add(chapter.href, "Scene break marker", f'"{before}" is now a centered "{house}" with space above and below')
        if changed:
            for chapter in book.chapters:
                if chapter.href in changed:
                    chapter.modified = True
            if hasattr(book, "mark_modified"):
                book.mark_modified()
        return report

    @staticmethod
    def _describe(el, house):
        return f'"{_glyphs(el)}" will become a centered "{house}" with space above and below'
