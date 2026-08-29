"""
ebook_fix.scene_breaks

Finds every <hr> in the book and classifies each one as one of two
very different things that both end up looking identical in the
markup:

  1. A real scene break -- a deliberate pause mid-chapter (a time
     skip, a change of viewpoint) that the author or a print edition
     marked with a divider, conventionally rendered in ebooks as a
     centered "* * *" line rather than a literal horizontal rule.
  2. A leftover chapter-edge artifact -- many conversion tools (or
     source documents converted from a print layout that used a full
     page break between chapters) drop an <hr> right where a chapter
     already starts or ends, purely as a visual separator that's
     entirely redundant with the chapter boundary itself already being
     there. Keeping this reads as a stray, meaningless rule sitting
     right next to (or overlapping in intent with) the chapter's own
     heading.

The distinction: is there any real text between this <hr> and the
nearest chapter boundary in each direction? If BOTH directions have
real content before running into a boundary (or the file's own start/
end, if no chapter marker governs that side), it's mid-chapter -- a
real scene break. If EITHER direction has nothing but blank space
before hitting a boundary, this <hr> IS effectively sitting at that
boundary -- an edge artifact.

Deliberately scoped to a single file at a time: a chapter marker in a
*different* file than the <hr> doesn't change this calculus, since
crossing a file boundary is already itself a fresh page/chapter-ish
transition in a well-structured EPUB -- so this only ever looks at
chapter markers that share the same document as the <hr> being
classified, and otherwise falls back to that file's own start/end.

Analysis only, same "read-first, never mutate" pattern as every other
ebook_fix.* analysis module -- see ebook_fix.modules.scene_break_repair
for the module that actually acts on this.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from ebook_fix.chapters import BookChapterSummary, analyze_book_chapters
from ebook_fix.frontmatter import BookFrontMatterSummary, analyze_book_frontmatter, MAIN_ZONE
from ebook_fix.text_range import text_range, text_before, text_to_end_of_doc, text_range_strictly_after

MID_CHAPTER = "mid-chapter"   # real scene break -- replace with "* * *"
CHAPTER_EDGE = "chapter-edge"  # redundant with an existing boundary -- remove


@dataclass
class SceneBreakIssue:
    href: str = ""
    element: object = None    # live <hr> element; not saved to the JSON cache
    classification: str = ""  # MID_CHAPTER or CHAPTER_EDGE


@dataclass
class ChapterSceneBreakSummary:
    href: str = ""
    issues: list = field(default_factory=list)   # [SceneBreakIssue]

    @property
    def mid_chapter_count(self) -> int:
        return sum(1 for i in self.issues if i.classification == MID_CHAPTER)

    @property
    def chapter_edge_count(self) -> int:
        return sum(1 for i in self.issues if i.classification == CHAPTER_EDGE)


@dataclass
class BookSceneBreakSummary:
    chapters: list = field(default_factory=list)   # [ChapterSceneBreakSummary]

    @property
    def total_issue_count(self) -> int:
        return sum(len(c.issues) for c in self.chapters)

    @property
    def total_mid_chapter_count(self) -> int:
        return sum(c.mid_chapter_count for c in self.chapters)

    @property
    def total_chapter_edge_count(self) -> int:
        return sum(c.chapter_edge_count for c in self.chapters)

    @property
    def chapters_with_issues(self) -> list:
        return [c.href for c in self.chapters if c.issues]


def _document_order(root, elements_of_interest: set) -> dict:
    """Maps each element in `elements_of_interest` to its position in
    document order, via one tree walk -- so any two of those elements'
    relative order can then be compared cheaply. Keyed by the element
    object itself (a plain dict, not `id()` -- lxml's C-level proxies
    have unstable id() values during iteration, see chapters.py's own
    note on this)."""
    order = {}
    for i, el in enumerate(root.iter()):
        if el in elements_of_interest:
            order[el] = i
    return order


def analyze_chapter_scene_breaks(href: str, tree, local_markers: list) -> ChapterSceneBreakSummary:
    """`local_markers` should already be filtered to markers that share
    this document -- see analyze_book_scene_breaks."""
    summary = ChapterSceneBreakSummary(href=href)
    if tree is None:
        return summary

    root = tree if hasattr(tree, "iter") else tree.getroot()
    if root is None:
        return summary

    hrs = [el for el in root.iter() if isinstance(el.tag, str)
           and etree.QName(el).localname.lower() == "hr"]
    if not hrs:
        return summary

    order = _document_order(root, set(hrs) | set(local_markers))
    markers_sorted = sorted(local_markers, key=lambda el: order[el])
    marker_positions = [order[el] for el in markers_sorted]

    for hr in sorted(hrs, key=lambda el: order[el]):
        hr_pos = order[hr]

        # Nearest marker strictly before/after this <hr>, if any --
        # else the file's own start/end governs that side.
        prev_marker = None
        next_marker = None
        for marker, pos in zip(markers_sorted, marker_positions):
            if pos < hr_pos:
                prev_marker = marker
            elif pos > hr_pos and next_marker is None:
                next_marker = marker

        before_text = (
            text_range_strictly_after(prev_marker, hr) if prev_marker is not None
            else text_before(root, hr)
        )
        after_text = (
            text_range(hr, next_marker) if next_marker is not None
            else text_to_end_of_doc(hr)
        )
        # text_to_end_of_doc(hr)/text_range(hr, ...) both include the
        # <hr> element's own (nonexistent) text content at the start --
        # harmless, an <hr> can't have element text of its own, but
        # its tail (the text right after it in the markup) would
        # legitimately count as "after" content, which is correct.

        has_content_before = bool(before_text.strip())
        has_content_after = bool(after_text.strip())

        classification = MID_CHAPTER if (has_content_before and has_content_after) else CHAPTER_EDGE
        summary.issues.append(SceneBreakIssue(href=href, element=hr, classification=classification))

    return summary


def analyze_book_scene_breaks(book, chapter_summary: BookChapterSummary | None = None,
                               frontmatter_summary: BookFrontMatterSummary | None = None) -> BookSceneBreakSummary:
    """`chapter_summary`/`frontmatter_summary` should be the analyses
    the caller already computed for this book (see engine.py's
    AnalysisReport), passed in so this doesn't have to re-run either
    itself. Falls back to computing both if not given.

    Deliberately skips any file frontmatter.py classifies as front or
    back matter (a title page, copyright page, table of contents,
    acknowledgments) -- confirmed by testing against a real book: a
    title page's own decorative <hr> (between the author byline and a
    "Contents" heading, say) has real text on both sides by the same
    literal rule this module uses for actual chapter content, but
    isn't a narrative "scene break" in any meaningful sense, and
    replacing it with a centered "* * *" would look out of place next
    to book-metadata content it was never meant to punctuate. This
    module's whole premise -- real content on both sides means a
    deliberate authorial pause -- only holds inside actual chapter
    prose. Falls back to checking every file when frontmatter.py
    couldn't confirm any zones at all (nothing reliable to exclude).

    Falls back to case 3 detection (see chapters.py's
    analyze_case3_book_chapters, and Jacob's three-case framework in
    xhtml_recoder_plan.md) when the normal analysis confirmed no
    chapters at all -- confirmed necessary by testing against a real
    book: The Call of Cthulhu marks its own three internal sections
    ("1. The Horror in Clay.", etc.) as plain styled paragraphs the
    normal chapter analysis doesn't pick up on, each preceded by an
    <hr>. With no local marker to check against, every <hr> in that
    one-file book was being judged purely against the file's own
    start/end -- calling all three of those genuine section-edge <hr>s
    "mid-chapter" real scene breaks instead of the edge artifacts they
    actually are. Using case 3's candidates here is a much lower-
    stakes use of that signal than trusting it enough to physically
    split a file (which case 3 candidates are never allowed to do,
    see split_safety_bar.md) -- getting a scene-break classification
    slightly wrong is a soft, easily-noticed cosmetic miss, not a
    file cut in the wrong place.
    """
    if chapter_summary is None:
        chapter_summary = analyze_book_chapters(book)
    if frontmatter_summary is None:
        frontmatter_summary = analyze_book_frontmatter(book, chapter_summary=chapter_summary)

    confirmed = list(chapter_summary.confirmed_boundaries or [])
    if not confirmed:
        from ebook_fix.chapters import analyze_case3_book_chapters
        case3_summary = analyze_case3_book_chapters(book)
        confirmed = list(case3_summary.confirmed_boundaries or [])

    main_hrefs = None
    if frontmatter_summary.boundaries_confirmed:
        main_hrefs = {cm.href for cm in frontmatter_summary.chapters if cm.zone == MAIN_ZONE}

    markers_by_href: dict = {}
    for c in confirmed:
        if c.element is not None:
            markers_by_href.setdefault(c.href, []).append(c.element)

    summary = BookSceneBreakSummary()
    for chapter in getattr(book, "chapters", []) or []:
        href = getattr(chapter, "href", "")
        if main_hrefs is not None and href not in main_hrefs:
            continue
        tree = getattr(chapter, "document", None)
        summary.chapters.append(
            analyze_chapter_scene_breaks(href, tree, markers_by_href.get(href, []))
        )
    return summary
