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
    PUBLISHER = "publisher info"
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
# Generic phrasing found on a publisher's imprint/colophon-ish page,
# independent of any specific publisher's name (that check is
# metadata-driven -- see _label_from_text's `metadata` argument).
_PUBLISHER_PHRASE_RE = re.compile(
    r"\bpublished by\b|\ban? imprint of\b|\ba division of\b",
    re.IGNORECASE,
)

# Words too generic to count as a real metadata match on their own --
# without this, a one-word book title like "Contact" or a common
# single-word author surname would corroborate almost any short page.
_TITLE_MATCH_MIN_WORD_LEN = 4

# Checked against the spine entry's filename (case-insensitive
# substring match). Order matters only in that the first hit wins;
# entries are specific enough that overlap shouldn't matter in
# practice.
HREF_HINTS = (
    ("cover", MatterLabel.COVER),
    ("titlepage", MatterLabel.TITLE_PAGE),
    ("title-page", MatterLabel.TITLE_PAGE),
    ("copyright", MatterLabel.COPYRIGHT),
    ("publisher", MatterLabel.PUBLISHER),
    ("imprint", MatterLabel.PUBLISHER),
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


_SKIP_TEXT_TAGS = {"style", "script"}


def _chapter_text(chapter):
    """Visible text only -- skips <style>/<script> content, which
    lxml's itertext() otherwise includes as ordinary text nodes. Left
    in, a CSS rule like "text-align: center" can accidentally satisfy
    a metadata-word match (e.g. a book titled "...Op Center") with
    nothing to do with the actual page content. Scoped to this module
    only, since other modules' own "".join(tree.itertext()) calls are
    a separate, pre-existing convention this isn't meant to change."""
    if chapter.document is None:
        return ""
    from lxml import etree as _etree
    parts = []
    for el in chapter.document.iter():
        if not isinstance(el.tag, str):
            continue
        # el.text is this element's own content -- skip it for
        # style/script. el.tail is text that follows this element,
        # belonging to whatever comes next in the document, so it's
        # always kept regardless of what el itself is.
        if _etree.QName(el).localname.lower() not in _SKIP_TEXT_TAGS and el.text:
            parts.append(el.text)
        if el.tail:
            parts.append(el.tail)
    return "".join(parts)


def _label_from_href(href):
    """Returns (label, exact) or None. `exact` means the filename's
    stem (no extension, no path) IS the hint, e.g. "titlepage.xhtml"
    -- as opposed to the hint just appearing somewhere inside a longer,
    less deliberate filename. An exact match is much stronger evidence
    a conversion tool named the file on purpose, so callers use it to
    decide between "high" and "medium" confidence rather than treating
    every href hint the same."""
    lower = href.lower()
    stem = lower.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    stem = re.sub(r"[_-]", "", stem)
    for hint, label in HREF_HINTS:
        if hint in lower:
            return label, stem == hint
    return None


def _label_from_text(text, word_count, metadata=None):
    if _COPYRIGHT_RE.search(text):
        return MatterLabel.COPYRIGHT, "high"
    if _TOC_RE.search(text[:200]):
        return MatterLabel.TABLE_OF_CONTENTS, "high"
    if _ACK_RE.search(text):
        return MatterLabel.ACKNOWLEDGMENTS, "high"
    if _AFTERWORD_RE.search(text):
        return MatterLabel.AFTERWORD, "high"
    if _ABOUT_AUTHOR_RE.search(text):
        return MatterLabel.ABOUT_AUTHOR, "high"
    if _COLOPHON_RE.search(text):
        return MatterLabel.COLOPHON, "high"

    publisher_name = (getattr(metadata, "publisher", "") or "").strip()
    if len(publisher_name) >= 3 and publisher_name.lower() in text.lower():
        # The book's own metadata names this publisher and the page
        # names it too -- much stronger than the generic phrase check
        # below, since it isn't just matching common boilerplate
        # wording that could appear anywhere.
        return MatterLabel.PUBLISHER, "high"
    if _PUBLISHER_PHRASE_RE.search(text):
        return MatterLabel.PUBLISHER, "medium"

    if _DEDICATION_WORD_RE.search(text):
        return MatterLabel.DEDICATION, "high"

    stripped = text.strip()
    if word_count <= 40 and _DEDICATION_OPENER_RE.match(stripped):
        return MatterLabel.DEDICATION, "high"

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if word_count <= 60 and lines and any(_ATTRIBUTION_LINE_RE.match(l) for l in lines[-2:]):
        return MatterLabel.EPIGRAPH, "high"

    # Title-page corroboration: a short page whose text contains both
    # the book's own title and its author, straight from the book's
    # own metadata rather than a filename guess or generic keyword.
    # Short words are excluded from the match (see
    # _TITLE_MATCH_MIN_WORD_LEN) so a one-word title/surname doesn't
    # corroborate against unrelated short pages by coincidence.
    if word_count <= 60 and metadata is not None:
        title_words = [w for w in re.findall(r"[\w'-]+", metadata.title or "")
                        if len(w) >= _TITLE_MATCH_MIN_WORD_LEN]
        creator_words = [w for w in re.findall(r"[\w'-]+", metadata.creator or "")
                          if len(w) >= _TITLE_MATCH_MIN_WORD_LEN]
        lower_text = text.lower()
        title_hit = bool(title_words) and any(w.lower() in lower_text for w in title_words)
        creator_hit = bool(creator_words) and any(w.lower() in lower_text for w in creator_words)
        if title_hit and creator_hit:
            return MatterLabel.TITLE_PAGE, "high"
        if title_hit or creator_hit:
            return MatterLabel.TITLE_PAGE, "medium"

    return None, None


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
        text_label, text_confidence = _label_from_text(text, word_count, metadata=book.metadata)

        if text_label is not None:
            label = text_label
            confidence = text_confidence
            reason = f"page content matches the {label.value} pattern"
        elif href_label is not None:
            label, exact = href_label
            confidence = "high" if exact else "medium"
            reason = (
                f"filename is exactly a {label.value} page" if exact
                else f"filename hints at {label.value}"
            )
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
