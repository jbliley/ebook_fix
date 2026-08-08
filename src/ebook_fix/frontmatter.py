"""
ebook_fix.frontmatter

Classifies each spine entry as front matter (title page, copyright,
dedication, epigraph, table of contents, etc.), back matter (afterword,
acknowledgments, about-the-author, colophon, etc.), or main content --
best-effort, confidence-scored, in the same spirit as chapters.py's own
boundary detection.

Why this exists
----------------
Front and back matter pages are short by nature -- a copyright page or
a dedication is often a handful of lines. Before this module,
analyzer.py's "thin chapter" flag couldn't tell the difference between
that and a genuinely broken/empty chapter, so every book with a normal
title page and copyright notice reported "problems" that weren't
problems. This module gives analyzer.py (and, eventually, a repair
module) a real answer to "is this the kind of page that's SUPPOSED to
be short," instead of guessing from word count alone.

How zones are decided
----------------------
This piggybacks on chapters.py's own confirmed chapter sequence rather
than re-detecting structure itself: everything in spine order before
the first confirmed chapter boundary is "front," everything after the
last one is "back," everything from the first through the last is
"main." If chapters.py couldn't confirm a sequence at all (no
believable chapter numbering found), zones fall back to "unknown" for
every spine entry -- position-based guessing without a real anchor
point is exactly the kind of unreliable pattern-matching this project
tries to avoid (see chapters.py's own docstring on sequence
validation). Label detection (copyright/dedication/TOC/etc.) still
runs either way, since keyword matches don't depend on knowing the
zone.

Labeling
--------
Two independent signals get checked per spine entry: the filename
(e.g. "titlepage.xhtml", "copyright.xhtml") and the page's own text
(e.g. "All Rights Reserved", "Table of Contents"). A text match is
trusted over a filename hint alone, since filenames are conventions a
given conversion tool may or may not follow, but actual page content
is the real evidence. When neither signal fires, a spine entry still
gets a zone-only label ("front matter" / "back matter" / "main
content") if a zone could be determined at all.

This is deliberately narrow and pattern-based, not exhaustive -- it
will miss unusual title pages and odd dedications, and that's fine.
Anything it doesn't recognize keeps a lower confidence rather than a
label it isn't sure of.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

FRONT_ZONE = "front"
BACK_ZONE = "back"
MAIN_ZONE = "main"
UNKNOWN_ZONE = "unknown"

# A front/back-matter page is usually short. Used only as a soft signal
# for confidence, never as the sole reason something gets a label.
SHORT_PAGE_WORD_THRESHOLD = 150


class MatterLabel(Enum):
    COVER = "cover"
    TITLE_PAGE = "title page"
    COPYRIGHT = "copyright page"
    DEDICATION = "dedication"
    EPIGRAPH = "epigraph"
    TABLE_OF_CONTENTS = "table of contents"
    ACKNOWLEDGMENTS = "acknowledgments"
    AFTERWORD = "afterword"
    ABOUT_AUTHOR = "about the author"
    COLOPHON = "colophon"
    FRONT_MATTER = "front matter"   # zone known, no more specific label matched
    BACK_MATTER = "back matter"     # zone known, no more specific label matched
    MAIN_CONTENT = "main content"


@dataclass
class ChapterMatter:
    href: str = ""
    zone: str = UNKNOWN_ZONE
    label: str = MatterLabel.MAIN_CONTENT.value
    confidence: str = "low"   # "high", "medium", "low"
    reason: str = ""


@dataclass
class BookFrontMatterSummary:
    chapters: list = field(default_factory=list)
    front_matter_count: int = 0
    back_matter_count: int = 0
    main_content_count: int = 0
    # Whether chapters.py found a confirmed chapter sequence to anchor
    # zones on at all -- if False, every entry above is zone "unknown"
    # and only text/filename label matches (not zone) can be trusted.
    boundaries_confirmed: bool = False


# ---------------------------------------------------------------------
# Text/filename pattern matching
# ---------------------------------------------------------------------

_COPYRIGHT_RE = re.compile(
    r"all rights reserved|copyright\s*(?:\u00a9|\(c\))|\bisbn\b|"
    r"library of congress|printed in the united states",
    re.IGNORECASE,
)
_TOC_RE = re.compile(r"table of contents|^\s*contents\s*$", re.IGNORECASE)
_ACK_RE = re.compile(r"acknowledge?ments", re.IGNORECASE)
_AFTERWORD_RE = re.compile(r"\bafterword\b", re.IGNORECASE)
_ABOUT_AUTHOR_RE = re.compile(r"about the author", re.IGNORECASE)
_COLOPHON_RE = re.compile(r"\bcolophon\b", re.IGNORECASE)
_DEDICATION_WORD_RE = re.compile(r"\bdedicat(e|ed|ion)\b", re.IGNORECASE)
_DEDICATION_OPENER_RE = re.compile(r"^\s*(for|to)\s+\S", re.IGNORECASE)
# A short line opening with an em/en dash or plain hyphen -- the
# classic "-- Author Name" attribution line under an epigraph quote.
_ATTRIBUTION_LINE_RE = re.compile(r"^[\u2014\u2013-]\s*\S")

# Checked against the spine entry's filename (case-insensitive
# substring match). Order matters only in that the first hit wins;
# entries are specific enough that overlap shouldn't matter in
# practice.
HREF_HINTS = (
    ("cover", MatterLabel.COVER),
    ("titlepage", MatterLabel.TITLE_PAGE),
    ("title-page", MatterLabel.TITLE_PAGE),
    ("copyright", MatterLabel.COPYRIGHT),
    ("dedicat", MatterLabel.DEDICATION),
    ("epigraph", MatterLabel.EPIGRAPH),
    ("toc", MatterLabel.TABLE_OF_CONTENTS),
    ("contents", MatterLabel.TABLE_OF_CONTENTS),
    ("acknowledg", MatterLabel.ACKNOWLEDGMENTS),
    ("afterword", MatterLabel.AFTERWORD),
    ("aboutauthor", MatterLabel.ABOUT_AUTHOR),
    ("about-author", MatterLabel.ABOUT_AUTHOR),
    ("colophon", MatterLabel.COLOPHON),
)


def _spine_ordered_chapters(book):
    """book.chapters loads in manifest order, not spine order (see
    docs/analysis_roadmap.md -- fixing that at the parser level is a
    bigger, separate change). Classification here cares about reading
    order specifically, so it builds its own spine-ordered list
    locally rather than trusting book.chapters' existing order."""
    by_id = {c.id: c for c in book.chapters}
    ordered = []
    seen = set()
    for idref in book.spine:
        chapter = by_id.get(idref)
        if chapter is not None:
            ordered.append(chapter)
            seen.add(id(chapter))
    # Anything present in book.chapters but missing from the spine
    # (shouldn't normally happen) is tacked on at the end so nothing
    # silently disappears from the summary.
    for chapter in book.chapters:
        if id(chapter) not in seen:
            ordered.append(chapter)
    return ordered


def _chapter_text(chapter):
    if chapter.document is None:
        return ""
    return "".join(chapter.document.itertext())


def _label_from_href(href):
    lower = href.lower()
    for hint, label in HREF_HINTS:
        if hint in lower:
            return label
    return None


def _label_from_text(text, word_count):
    if _COPYRIGHT_RE.search(text):
        return MatterLabel.COPYRIGHT
    if _TOC_RE.search(text[:200]):
        return MatterLabel.TABLE_OF_CONTENTS
    if _ACK_RE.search(text):
        return MatterLabel.ACKNOWLEDGMENTS
    if _AFTERWORD_RE.search(text):
        return MatterLabel.AFTERWORD
    if _ABOUT_AUTHOR_RE.search(text):
        return MatterLabel.ABOUT_AUTHOR
    if _COLOPHON_RE.search(text):
        return MatterLabel.COLOPHON
    if _DEDICATION_WORD_RE.search(text):
        return MatterLabel.DEDICATION

    stripped = text.strip()
    if word_count <= 40 and _DEDICATION_OPENER_RE.match(stripped):
        return MatterLabel.DEDICATION

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if word_count <= 60 and lines and any(_ATTRIBUTION_LINE_RE.match(l) for l in lines[-2:]):
        return MatterLabel.EPIGRAPH

    return None


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

def analyze_book_frontmatter(book, chapter_summary=None) -> BookFrontMatterSummary:
    """Classify every spine entry into a front/back/main zone plus a
    best-guess label. `chapter_summary` should be the
    chapters.BookChapterSummary the analyzer already computed for this
    book -- passed in so this doesn't have to re-run chapter boundary
    detection itself. Falls back to computing it if none is given, so
    this still works called standalone."""
    if chapter_summary is None:
        from ebook_fix.chapters import analyze_book_chapters
        chapter_summary = analyze_book_chapters(book)

    ordered = _spine_ordered_chapters(book)
    href_order = [chapter.href for chapter in ordered]

    confirmed_hrefs = {c.href for c in (chapter_summary.confirmed_boundaries or [])}

    first_idx = None
    last_idx = None
    if confirmed_hrefs:
        for i, href in enumerate(href_order):
            if href in confirmed_hrefs:
                if first_idx is None:
                    first_idx = i
                last_idx = i

    summary = BookFrontMatterSummary(boundaries_confirmed=first_idx is not None)

    for i, chapter in enumerate(ordered):
        text = _chapter_text(chapter)
        word_count = len(text.split())

        if first_idx is None:
            zone = UNKNOWN_ZONE
        elif i < first_idx:
            zone = FRONT_ZONE
        elif i > last_idx:
            zone = BACK_ZONE
        else:
            zone = MAIN_ZONE

        href_label = _label_from_href(chapter.href)
        text_label = _label_from_text(text, word_count)

        if text_label is not None:
            label = text_label
            confidence = "high"
            reason = f"page content matches the {label.value} pattern"
        elif href_label is not None:
            label = href_label
            confidence = "medium"
            reason = f"filename hints at {label.value}"
        elif zone == FRONT_ZONE:
            label = MatterLabel.FRONT_MATTER
            confidence = "medium" if word_count <= SHORT_PAGE_WORD_THRESHOLD else "low"
            reason = "comes before the first confirmed chapter"
        elif zone == BACK_ZONE:
            label = MatterLabel.BACK_MATTER
            confidence = "medium" if word_count <= SHORT_PAGE_WORD_THRESHOLD else "low"
            reason = "comes after the last confirmed chapter"
        elif zone == MAIN_ZONE:
            label = MatterLabel.MAIN_CONTENT
            confidence = "high"
            reason = "falls within the confirmed chapter sequence"
        else:
            label = MatterLabel.MAIN_CONTENT
            confidence = "low"
            reason = "no confirmed chapter sequence to anchor a zone on"

        summary.chapters.append(ChapterMatter(
            href=chapter.href, zone=zone, label=label.value,
            confidence=confidence, reason=reason,
        ))

        if zone == FRONT_ZONE:
            summary.front_matter_count += 1
        elif zone == BACK_ZONE:
            summary.back_matter_count += 1
        elif zone == MAIN_ZONE:
            summary.main_content_count += 1

    return summary
