"""
ebook_fix.scene_index

One record per page of what the scene steps need to know about it, kept by the
analysis store so those steps open only the pages that can have something to do.

Scene Opener Markers and Scene Break Markers (and the book's own scene marker
that Scene Break Normalizer, Paragraph Repair and Scene Opener Markers all
reuse) each used to walk every paragraph of every page, several times in one
run, to find the few pages that hold a scene opening in capitals or a row of
ornament glyphs. Almost every page holds neither. These are plain facts about
one page's markup, so they are worked out once per page and kept while the page
is unchanged (see ebook_fix.finding_cache for exactly when a kept record is
trusted).

The record holds no elements. For Scene Opener Markers it keeps what the
book-wide checks add up (how many paragraphs have text, how often each
paragraph class and each wrapper element is used, how many paragraphs open
with capitals) and whether the page has any such paragraph at all. For the
scene break marker it keeps whether the page has any paragraph that could be a
marker or the book's own marker. A step that finds from the records that a page
may need work opens that page and does what it always did there, so the answers
are exactly what walking every page gave.

Chapter Header Spacing keeps one more fact: whether the page has any element
stamped as a chapter header. A page with no stamp and none of the analysis's
chapter marker elements has no header to space, so it is not opened.

A page that cannot be loaded (document None) has an empty record with
`loaded=False`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ebook_fix.headings import MARKER_ATTR, SPLIT_MARKER_ATTR
from ebook_fix.scene_openers import WRAPPER_TAGS, _local, _text, opener_candidate


@dataclass(frozen=True)
class PageSceneIndex:
    href: str = ""
    loaded: bool = True
    # Scene Opener Markers: paragraphs with text, how many use each class
    # attribute, how many elements use each "tag.class" wrapper, how many
    # paragraphs open with capitals, and whether any paragraph is an opener
    # candidate (a strong one, or a weak one inside a wrapper).
    paragraphs: int = 0
    class_counts: tuple = ()
    wrapper_counts: tuple = ()
    strong_openers: int = 0
    opener_candidates: bool = False
    # Scene Break Markers: some paragraph could be a text scene break marker
    # or the book's own marker line.
    marker_candidates: bool = False
    # Chapter Header Spacing: some element in the body carries a chapter
    # marker stamp (Chapter Markup's or a Review-tab split's).
    header_stamped: bool = False


@dataclass
class BookSceneIndex:
    chapters: list = field(default_factory=list)


def analyze_scene_page(href, document) -> PageSceneIndex:
    if document is None:
        return PageSceneIndex(href=href, loaded=False)
    from ebook_fix.modules.scene_marker_repair import _is_house_candidate, is_text_marker

    root = document.getroot() if hasattr(document, "getroot") else document

    marker_candidates = False
    for el in root.iter("{*}p"):
        if _is_house_candidate(el) or is_text_marker(el):
            marker_candidates = True
            break

    body = root.find(".//{*}body")
    paragraphs = 0
    classes: dict = {}
    wrappers: dict = {}
    strong = 0
    candidates = False
    stamped = False
    if body is not None:
        for el in body.iter():
            if not stamped and isinstance(el.tag, str) and (el.get(MARKER_ATTR) or el.get(SPLIT_MARKER_ATTR)):
                stamped = True
            name = _local(el)
            if name in WRAPPER_TAGS:
                wrapper_class = el.get("class")
                if wrapper_class:
                    key = f"{name}.{wrapper_class}"
                    wrappers[key] = wrappers.get(key, 0) + 1
                continue
            if name != "p":
                continue
            text = _text(el)
            if text:
                paragraphs += 1
                paragraph_class = el.get("class") or ""
                classes[paragraph_class] = classes.get(paragraph_class, 0) + 1
            lead, _signature, weak = opener_candidate(el, text)
            if lead is not None:
                strong += 1
            if lead is not None or weak:
                candidates = True

    return PageSceneIndex(
        href=href,
        loaded=True,
        paragraphs=paragraphs,
        class_counts=tuple(classes.items()),
        wrapper_counts=tuple(wrappers.items()),
        strong_openers=strong,
        opener_candidates=candidates,
        marker_candidates=marker_candidates,
        header_stamped=stamped,
    )


def analyze_book_scene_index(book, cache=None) -> BookSceneIndex:
    from ebook_fix.finding_cache import analyze_pages

    return analyze_pages(book, "scene_index", analyze_scene_page, BookSceneIndex(), cache=cache)


def scene_index_for(book, analysis=None) -> list:
    """The page records for `book` as it is now, in the order of
    `book.chapters`. They come from the analysis store the report carries when
    there is one, so a page that has not changed is not read again; without a
    store every page is read."""
    store = getattr(analysis, "store", None)
    if store is not None:
        return store.findings("scene_index", book).chapters
    return analyze_book_scene_index(book).chapters
