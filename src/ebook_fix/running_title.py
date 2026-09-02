"""
ebook_fix.running_title

Detects a book-title heading repeated as a running header at the top
of every confirmed chapter -- a leftover from a conversion tool (often
Calibre, going by the class names seen so far, e.g. "Battle of the
Mountain Man") that stamped the book's own title into a shared
template used for every page, rather than reserving it for the book's
actual title page.

Deliberately scoped to CONFIRMED chapters only (see
chapters.BookChapterSummary.confirmed_boundaries) -- front matter (a
real title page, a Contents page) legitimately showing the book's
title is not this problem, and this module has no business touching
those. This is purely descriptive, same split as every other analysis
module here: repair (see modules/running_title_repair.py) decides what
to actually remove.

Detection rule
---------------
For each confirmed chapter, walk its document from the top looking for
a heading element (h1-h6) whose own text matches the book's title
(whitespace-normalized, case-insensitive), stopping the search the
moment the chapter's own detected marker element is reached (see
ChapterCandidate.element) -- a match has to appear BEFORE the chapter
actually starts to count as a leading running header, not a
coincidental match somewhere in the chapter's real content. Only the
first such heading per chapter is recorded; a book repeating its own
title several times before finally reaching the chapter marker is
unusual enough that stopping at the first match is the safe default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip()).casefold()


@dataclass
class RunningTitleMarker:
    href: str = ""
    heading_text: str = ""
    element: object = None  # live lxml reference to the matching heading


@dataclass
class BookRunningTitleSummary:
    markers: list = field(default_factory=list)

    @property
    def detected(self) -> bool:
        return bool(self.markers)


def analyze_book_running_titles(book, chapter_summary=None) -> BookRunningTitleSummary:
    summary = BookRunningTitleSummary()

    title = getattr(getattr(book, "metadata", None), "title", "") or ""
    norm_title = _normalize(title)
    if not norm_title:
        return summary

    if chapter_summary is None:
        from ebook_fix.chapters import analyze_book_chapters
        chapter_summary = analyze_book_chapters(book)

    # First confirmed candidate per file, in case a file somehow has
    # more than one (not expected in practice, but harmless to guard).
    marker_by_href = {}
    for c in chapter_summary.confirmed_boundaries:
        marker_by_href.setdefault(c.href, c)

    by_href = {getattr(ch, "href", ""): ch for ch in getattr(book, "chapters", []) or []}

    for href, candidate in marker_by_href.items():
        if candidate.element is None:
            continue
        chapter = by_href.get(href)
        tree = getattr(chapter, "document", None) if chapter is not None else None
        if tree is None:
            continue

        for el in tree.iter():
            if el is candidate.element:
                break
            if not isinstance(el.tag, str):
                continue
            if etree.QName(el).localname.lower() not in _HEADING_TAGS:
                continue
            text = "".join(el.itertext())
            if _normalize(text) == norm_title:
                summary.markers.append(
                    RunningTitleMarker(href=href, heading_text=text.strip(), element=el)
                )
                break

    return summary
