"""
ebook_fix.linebreaks

Finds stray line breaks (<br/>) sitting at the very start or very end
of a paragraph or heading, during the same single pass the rest of the
analyzer runs, so ebook_fix.modules.linebreak_repair doesn't have to
scan the book itself.

Why this matters: a conversion tool will often leave a <br/> as the
first thing inside a paragraph (or inside a chapter heading, right
before the title). A book's stylesheet usually gives paragraphs no
spacing, so that one <br/> shows up as a whole extra blank line between
two paragraphs, or a gap above a chapter title. Nothing is wrong with
the text itself -- the break is just dead weight.

Only breaks at an *edge* are flagged:

- Leading: nothing but whitespace (and other breaks) comes before the
  <br/> inside its paragraph/heading.
- Trailing: nothing but whitespace (and other breaks) comes after it.

A <br/> in the middle of a paragraph is never flagged -- that's a real
line break (poetry, an address block, a signature), not an artifact.
Breaks that belong to a scene break are kept, since they are doing a
real job (a visual pause):

- Any <br/> inside a scene-break marker paragraph ("* * *", "***",
  "# # #", "---", a lone ornament like a fleuron).
- A trailing <br/> in the paragraph right before an <hr> or a marker
  paragraph, and a leading <br/> in the paragraph right after one.

Breaks that make up the blank space under a chapter header are kept
too (see ebook_fix.headings): a trailing <br/> inside the header, and a
leading <br/> at the top of the paragraph right after it. A leading
<br/> at the start of the header itself is still flagged.

A <br clear="..."/> is never flagged (that's a float-clearing layout
instruction, not an empty line). A paragraph whose only content is a <br/> is not flagged here either;
it's an empty paragraph, which Paragraph Repair already owns (and it
knows when such a paragraph is holding a link target and must stay).

Scope: confirmed main-matter chapters only, when front/back matter
boundaries could be confirmed (same scoping ebook_fix.scene_breaks and
the dangling-ending check in ebook_fix.paragraphs use). Copyright
pages and title pages use deliberate line breaks all the time. Falls
back to checking every chapter when no zones could be confirmed.

Each finding carries a live reference to the actual <br/> element so
repair can act on it directly -- those references aren't saved to the
JSON cache (see serialize.py), only used within a single run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ebook_fix.headings import is_chapter_header

# The only containers a stray break is looked for in: paragraphs and
# headings. Breaks sitting directly in <body>, a <div>, a table cell or a
# list item aren't touched -- there's no clear "edge of a paragraph" to
# define there, and those are usually deliberate layout.
BLOCK_TAGS = frozenset(("p", "h1", "h2", "h3", "h4", "h5", "h6"))

# Elements that count as real content even though they have no text.
_CONTENT_TAGS = frozenset(("img", "image", "svg", "hr", "video", "audio", "object", "math", "table"))


# A scene-break marker paragraph is only ornament glyphs: no letters or
# digits. Three or more glyphs ("* * *", "---", "###"), or one or two
# of the star-like ornaments on their own ("*", "* *", a fleuron).
# A lone dash, a lone period or an ellipsis is NOT a marker.
_ORNAMENTS = frozenset("*#~\u2022\u25e6\u25ca\u2042\u2726\u2727\u2731\u2732\u2756\u2055\u00a7")
_MARKER_GLYPHS = _ORNAMENTS | frozenset("=_+-\u2013\u2014")
_MARKER_MAX_GLYPHS = 20


@dataclass
class StrayLineBreak:
    href: str = ""
    kind: str = ""            # "leading" or "trailing"
    block_tag: str = ""       # tag of the paragraph/heading holding the break
    preview: str = ""
    element: object = None    # live <br> element; not saved to the JSON cache


@dataclass
class ChapterLineBreakSummary:
    href: str = ""
    stray_breaks: list = field(default_factory=list)

    @property
    def stray_break_count(self) -> int:
        return len(self.stray_breaks)


@dataclass
class BookLineBreakSummary:
    chapters: list = field(default_factory=list)

    @property
    def stray_break_count(self) -> int:
        return sum(c.stray_break_count for c in self.chapters)

    @property
    def chapters_with_stray_breaks(self) -> list:
        return [c.href for c in self.chapters if c.stray_breaks]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _local(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _blank(text) -> bool:
    return not (text or "").replace("\u00a0", " ").strip()


def _flatten(el, out: list) -> None:
    """Document-order list of ('start', element) and ('text', string)
    entries for everything inside `el`, not including `el`'s own tail."""
    out.append(("start", el))
    if isinstance(el.tag, str) and el.text:
        out.append(("text", el.text))
    for child in el:
        _flatten(child, out)
        if child.tail:
            out.append(("text", child.tail))


def is_scene_break_marker(el) -> bool:
    """True for a paragraph/div whose whole text is a scene-break
    ornament ("* * *", "***", "# # #", "---", a fleuron)."""
    if el is None or _local(el) not in ("p", "div"):
        return False
    glyphs = "".join("".join(el.itertext()).replace("\u00a0", " ").split())
    if not glyphs or len(glyphs) > _MARKER_MAX_GLYPHS:
        return False
    if any(ch not in _MARKER_GLYPHS for ch in glyphs):
        return False
    if len(glyphs) >= 3:
        return True
    return all(ch in _ORNAMENTS for ch in glyphs)


def _neighbor(block, direction: str):
    """Previous/next sibling element of `block`, skipping comments."""
    sibling = block.getprevious() if direction == "previous" else block.getnext()
    while sibling is not None and not isinstance(sibling.tag, str):
        sibling = sibling.getprevious() if direction == "previous" else sibling.getnext()
    return sibling


def _is_scene_divider(el) -> bool:
    return el is not None and (_local(el) == "hr" or is_scene_break_marker(el))


def _is_spacing_after_header(block, kind: str, markers) -> bool:
    """True when a <br/> is part of the blank space *under* a chapter
    header and should be kept: a trailing break inside the header itself
    ("Chapter One<br/><br/>"), or a leading break at the top of the
    paragraph right after the header. A leading break at the start of
    the header (a gap above the title) is not covered."""
    if kind == "trailing":
        return is_chapter_header(block, markers)
    return is_chapter_header(_neighbor(block, "previous"), markers)


def _belongs_to_scene_break(block, kind: str) -> bool:
    """True when a leading/trailing <br/> in `block` is part of a scene
    break and should be kept."""
    if is_scene_break_marker(block):
        return True
    if kind == "leading":
        return _is_scene_divider(_neighbor(block, "previous"))
    return _is_scene_divider(_neighbor(block, "next"))


def _nearest_block(el):
    parent = el.getparent()
    while parent is not None:
        if _local(parent) in BLOCK_TAGS:
            return parent
        parent = parent.getparent()
    return None


def _preview(block) -> str:
    text = " ".join("".join(block.itertext()).split())
    return text[:60]


def breaks_in_block(block) -> list:
    """Returns [(br_element, "leading" | "trailing"), ...] for `block`,
    in document order. Empty when the block has no real content at all
    (an empty-looking paragraph is Paragraph Repair's business)."""
    entries: list = []
    _flatten(block, entries)

    first_content = None
    last_content = None
    for i, (kind, value) in enumerate(entries):
        if kind == "text":
            has_content = not _blank(value)
        else:
            tag = _local(value)
            has_content = tag in _CONTENT_TAGS
        if has_content:
            if first_content is None:
                first_content = i
            last_content = i

    if first_content is None:
        return []

    found = []
    for i, (kind, value) in enumerate(entries):
        if kind != "start" or _local(value) != "br":
            continue
        if value.get("clear"):
            continue
        if i < first_content:
            found.append((value, "leading"))
        elif i > last_content:
            found.append((value, "trailing"))
    return found


# ---------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------


def analyze_book_linebreaks(book, frontmatter_summary=None, chapter_markers=None) -> BookLineBreakSummary:
    """`frontmatter_summary` should be the analysis the caller already
    computed for this book (see analyzer.py), passed in so this doesn't
    have to re-run it itself. Falls back to computing it if not given.

    `chapter_markers` is the set of confirmed chapter-title elements
    (ebook_fix.headings.chapter_marker_elements); without it only
    heading tags count as chapter headers."""
    summary = BookLineBreakSummary()

    if frontmatter_summary is None:
        from ebook_fix.frontmatter import analyze_book_frontmatter
        frontmatter_summary = analyze_book_frontmatter(book)

    main_hrefs = None
    if frontmatter_summary.boundaries_confirmed:
        from ebook_fix.frontmatter import MAIN_ZONE
        main_hrefs = {cm.href for cm in frontmatter_summary.chapters if cm.zone == MAIN_ZONE}

    for chapter in book.chapters:
        if chapter.document is None:
            continue
        if main_hrefs is not None and chapter.href not in main_hrefs:
            continue

        chapter_summary = ChapterLineBreakSummary(href=chapter.href)
        body = chapter.document.find(".//{*}body")
        if body is None:
            continue

        seen_blocks = set()
        for br in body.iter():
            if _local(br) != "br":
                continue
            block = _nearest_block(br)
            if block is None or block in seen_blocks:
                continue
            seen_blocks.add(block)
            for element, kind in breaks_in_block(block):
                if _belongs_to_scene_break(block, kind):
                    continue
                if _is_spacing_after_header(block, kind, chapter_markers):
                    continue
                chapter_summary.stray_breaks.append(
                    StrayLineBreak(
                        href=chapter.href,
                        kind=kind,
                        block_tag=_local(block),
                        preview=_preview(block),
                        element=element,
                    )
                )

        if chapter_summary.stray_breaks:
            summary.chapters.append(chapter_summary)

    return summary
