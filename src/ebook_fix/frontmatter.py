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

Extra pages (added 2026-10-18)
-------------------------------
Three more labels name pages that are not part of the book itself:
advertisement, newsletter sign-up and "books by the author". They are
descriptive only (nothing removes a page on the strength of them yet;
see docs/backmatter_cleanup_plan.md). They are looked for only on pages
in the front or back zone, never in the main story, and only after every
older label has had its chance: a page that already reads as About the
Author or Copyright keeps that label even if it also mentions a
newsletter. Each check is conservative about length (an ad or sign-up
is short) and asks for more than one signal before it says "high".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from ebook_fix import zone_anchors

CHAPTER_ANCHOR_NOTE = "the first and last confirmed chapter"

FRONT_ZONE = "front"
BACK_ZONE = "back"
MAIN_ZONE = "main"
UNKNOWN_ZONE = "unknown"

# A front/back-matter page is usually short. Used only as a soft signal
# for confidence, never as the sole reason something gets a label.
SHORT_PAGE_WORD_THRESHOLD = 150

# A front or back page with no label of its own and at least this many words
# is story text, not matter. Longest unlabeled non-story page seen across the
# example books: 355 words (a "praise for" page). Shortest story page that
# needs rescuing: 723 words (a split continuation page). Project Gutenberg
# license pages (559 to 2,917 words) are excluded by name instead.
LONG_STORY_PAGE_WORDS = 600

# A shorter page still counts as story when it carries a "Chapter ..." heading
# the chapter finder saw (just not as part of the confirmed run) and has this
# many words. Short chapters of 228 and 328 words were the cases found.
CHAPTER_HEADED_PAGE_WORDS = 200

_LICENSE_MARKERS = ("project gutenberg", "gutenberg.org", "gutenberg-tm", "gutenberg\u2122")


def _is_license_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _LICENSE_MARKERS)


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
    FICTION_DISCLAIMER = "fiction disclaimer"
    FRONT_MATTER = "front matter"   # zone known, no more specific label matched
    BACK_MATTER = "back matter"     # zone known, no more specific label matched
    # A preview of another book ("Turn the page for an exciting preview
    # of ...") after the story. Back matter: not part of this book.
    TEASER_CHAPTER = "teaser chapter"
    # An extra short story or chapter the author put after the book ("Read
    # on for an exclusive short story by ..."). It is part of what the
    # reader bought, so unlike a teaser it is never offered for removal.
    BONUS_STORY = "bonus story"
    # Pages that are not part of the book itself (see the "Extra pages"
    # note at the top of this file). They can sit at the front or the
    # back; the zone found from position stays what the page says.
    ADVERTISEMENT = "advertisement"
    NEWSLETTER = "newsletter sign-up"
    BOOKS_BY = "books by the author"
    MAIN_CONTENT = "main content"
    # A bare Prologue/Epilogue -- Jacob's call (docs/analysis_roadmap.md):
    # these are unnumbered chapters of the main story, not front/back
    # matter, so they're deliberately absent from FRONT_LABELS/
    # BACK_LABELS below and zone_for_label falls through to MAIN_ZONE
    # for both, same as MAIN_CONTENT.
    PROLOGUE = "prologue"
    EPILOGUE = "epilogue"


FRONT_LABELS = {
    MatterLabel.COVER, MatterLabel.TITLE_PAGE, MatterLabel.COPYRIGHT,
    MatterLabel.PUBLISHER, MatterLabel.DEDICATION, MatterLabel.EPIGRAPH,
    MatterLabel.TABLE_OF_CONTENTS, MatterLabel.FICTION_DISCLAIMER,
    MatterLabel.FRONT_MATTER,
}
BACK_LABELS = {
    MatterLabel.ACKNOWLEDGMENTS, MatterLabel.AFTERWORD,
    MatterLabel.ABOUT_AUTHOR, MatterLabel.COLOPHON, MatterLabel.BACK_MATTER,
    MatterLabel.TEASER_CHAPTER, MatterLabel.ADVERTISEMENT, MatterLabel.NEWSLETTER,
    MatterLabel.BOOKS_BY, MatterLabel.BONUS_STORY,
}


def zone_for_label(label_value: str) -> str:
    """Which zone a given MatterLabel value implies -- used both to
    derive a reviewer's chosen label into a zone below, and by the GUI
    to group review items without duplicating this mapping. Every
    MatterLabel not in FRONT_LABELS/BACK_LABELS above (i.e. just
    MAIN_CONTENT) implies MAIN_ZONE."""
    try:
        label = MatterLabel(label_value)
    except ValueError:
        return MAIN_ZONE
    if label in FRONT_LABELS:
        return FRONT_ZONE
    if label in BACK_LABELS:
        return BACK_ZONE
    return MAIN_ZONE


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
# Opening words of a preview of another book, matched on the letters only
# (page text often has words run together where tags were: "PAGEFOR").
# Only looked for in the first few hundred characters of a page that comes
# after the last chapter.
_TEASER_PHRASES = (
    "turnthepagefor", "previewof", "excerptfrom", "sneakpeek", "readonforapreview",
    "readonforanexcerpt", "teaserchapter", "firstchapterof", "chapterofthenew",
)


# The title page of a bonus story: a short page that announces "an exclusive
# short story" (or a bonus story/chapter) and names it.
_BONUS_PHRASES = (
    "exclusiveshortstory", "bonusshortstory", "bonusstory", "bonuschapter",
    "exclusivestory", "specialshortstory", "shortstoryby", "bonusnovella",
)
BONUS_TITLE_MAX_WORDS = 60


def _looks_like_bonus_story_title(text: str, word_count: int) -> bool:
    if word_count > BONUS_TITLE_MAX_WORDS:
        return False
    letters = re.sub(r"[^a-z]", "", text[:500].lower())
    return any(phrase in letters for phrase in _BONUS_PHRASES)


def _looks_like_teaser(text: str) -> bool:
    letters = re.sub(r"[^a-z]", "", text[:500].lower())
    return any(phrase in letters for phrase in _TEASER_PHRASES)


# ---------------------------------------------------------------------
# Extra pages: "books by", newsletter sign-ups, advertisements
# ---------------------------------------------------------------------

# A heading near the start of the page (searched in the first 160
# characters, since many books repeat a running title above every page).
# "Also by John Doe" is the author's own list and is "high"; the
# publisher's version ("Also available from ...", "Be sure to read ...") is
# the same kind of page but less certain, so it is "medium".
_BOOKS_BY_HEADING_RE = re.compile(
    r"\b(?:also by|books by|other books by|more books by|other titles by|titles by|novels by|"
    r"other novels by|other works by|by the same author)\b",
    re.IGNORECASE,
)
_BOOKS_BY_PUBLISHER_RE = re.compile(
    r"\b(?:also available|other titles from|more titles from|more from|look for these|other books from|"
    r"don'?t miss these|you may also enjoy|you might also enjoy|be sure to read)\b",
    re.IGNORECASE,
)
_HEADING_WINDOW = 160
_BOOKS_BY_MAX_WORDS = 500

_NEWSLETTER_TOPIC_RE = re.compile(r"\bnewsletters?\b|\bmailing list\b|\be-?mail list\b")
_NEWSLETTER_ACTION_RE = re.compile(r"\bsign(?:ing)? up\b|\bsubscribe\b|\bjoin (?:my|our|the|her|his|us)\b")
_NEWSLETTER_EXTRA_RE = re.compile(
    r"\be-?mail address\b|\bin your inbox\b|\bby e-?mail\b|\bunsubscribe\b|"
    r"\bfree (?:e-?book|book|short story|novella|copy|gift)\b|\bgiveaways?\b|\bnew releases?\b"
)
_SOCIAL_RE = re.compile(r"\b(?:facebook|twitter|instagram|goodreads|tiktok|pinterest|youtube|bookbub)\b")
_FOLLOW_RE = re.compile(r"\bfollow (?:me|us|the author|her|him)\b|\bconnect with\b|\bfind me\b|\bvisit (?:me|my)\b")
_NEWSLETTER_HIGH_WORDS = 150
_NEWSLETTER_MAX_WORDS = 250

# Strong signals say "this page is selling something"; weak ones also turn
# up in ordinary acknowledgments or notes, so they only count alongside a
# strong one.
_AD_STRONG = (
    re.compile(r"\bbuy (?:now|it now|it today|this book|your copy|the e-?book)\b"),
    re.compile(r"\bavailable (?:now )?(?:wherever|at|from|on) (?:books|e-?books|your favorite|major|all major|amazon|barnes|kindle|apple|kobo)"),
    re.compile(r"\b(?:order|pre-?order) (?:your|a|the)? ?(?:copy|now|today)\b"),
    re.compile(r"\b(?:discover|find) your next (?:great|favorite) (?:read|book)\b"),
    re.compile(r"\bspecial offer\b|\bexclusive offer\b|\blimited[- ]time\b|\bsave \d+ ?%|\be-?book deals?\b"),
    re.compile(r"\b(?:coming soon|on sale now|in stores now)\b"),
    # A publisher promoting its own website or catalog.
    re.compile(r"\bvisit (?:us|our) (?:web ?site|online|at)\b|\bonline catalog\b|\bour (?:web ?site|online catalog)\b"),
)
_AD_WEAK = (
    re.compile(r"\b(?:amazon|barnes\s*&\s*noble|barnesandnoble|kobo|apple books|ibooks|google play|kindle store|audible)\b"),
    re.compile(r"\bif you (?:enjoyed|liked|loved) this (?:book|story|novel)\b|\bloved this (?:book|story)\b"),
    re.compile(r"\bleave (?:us |me )?a (?:review|rating)\b|\bwrite a review\b"),
    re.compile(r"\bwww\.[a-z0-9-]+\.(?:com|net|org)\b|https?://"),
)
_AD_MAX_WORDS = 300
_AD_SHORT_WORDS = 100


def _extra_label_from_text(text: str, word_count: int):
    """(label, confidence, reason) for a front/back page that is a "books by"
    list, a newsletter sign-up or an advertisement, or None. Only ever
    called on a page the older, more specific checks did not recognise."""
    flat = " ".join(text.split())
    if not flat:
        return None
    lower = flat.lower()

    if word_count <= _BOOKS_BY_MAX_WORDS:
        head = flat[:_HEADING_WINDOW]
        if _BOOKS_BY_HEADING_RE.search(head):
            return (MatterLabel.BOOKS_BY, "high",
                    "opens with an \"Also by\" style heading and is a short list page")
        if _BOOKS_BY_PUBLISHER_RE.search(head):
            return (MatterLabel.BOOKS_BY, "medium",
                    "opens like a publisher's list of other titles")

    if word_count <= _NEWSLETTER_MAX_WORDS:
        topic = bool(_NEWSLETTER_TOPIC_RE.search(lower))
        action = bool(_NEWSLETTER_ACTION_RE.search(lower))
        if topic and action:
            extra = bool(_NEWSLETTER_EXTRA_RE.search(lower))
            if extra and word_count <= _NEWSLETTER_HIGH_WORDS:
                return (MatterLabel.NEWSLETTER, "high",
                        "short page asking the reader to sign up to a newsletter or mailing list")
            return (MatterLabel.NEWSLETTER, "medium",
                    "asks the reader to sign up to a newsletter or mailing list")
        if word_count <= _NEWSLETTER_HIGH_WORDS and len(set(_SOCIAL_RE.findall(lower))) >= 2 \
                and _FOLLOW_RE.search(lower):
            return (MatterLabel.NEWSLETTER, "medium",
                    "short page of social media links asking the reader to follow the author")

    if word_count <= _AD_MAX_WORDS:
        strong = sum(1 for p in _AD_STRONG if p.search(lower))
        weak = sum(1 for p in _AD_WEAK if p.search(lower))
        if strong >= 1 and strong + weak >= 2:
            return (MatterLabel.ADVERTISEMENT, "high",
                    "short page that sells or promotes a book (buy, order or availability wording)")
        if strong >= 1 and word_count <= _AD_SHORT_WORDS:
            return (MatterLabel.ADVERTISEMENT, "medium",
                    "very short page with selling wording")
    return None


# The standard "this is a work of fiction" boilerplate found on the
# page right after the title page in most novels. Distinctive enough
# wording (nobody writes "entirely/purely coincidental" about anything
# else) that a match is trusted at "high" confidence like the other
# fixed-phrase checks above, rather than the softer heuristics below.
_FICTION_DISCLAIMER_RE = re.compile(
    r"is a work of fiction|resemblance to actual (?:persons|events|locales)|"
    r"(?:entirely|purely) coincidental",
    re.IGNORECASE,
)
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
    # Added 2026-10-18. "adcard" is the name Penguin Random House uses
    # for its "Also by this author" page.
    ("newsletter", MatterLabel.NEWSLETTER),
    ("adcard", MatterLabel.BOOKS_BY),
    ("alsoby", MatterLabel.BOOKS_BY),
    ("also-by", MatterLabel.BOOKS_BY),
    ("also_by", MatterLabel.BOOKS_BY),
    ("booksby", MatterLabel.BOOKS_BY),
    ("books-by", MatterLabel.BOOKS_BY),
    ("books_by", MatterLabel.BOOKS_BY),
    ("advert", MatterLabel.ADVERTISEMENT),
)


def _picture_ad_reason(chapter) -> str:
    """Why a page with almost no text is a picture advertisement, or ""
    (see extra_partial.py). Imported here because that module builds on
    this one's phrase lists."""
    from ebook_fix.extra_partial import ad_picture_page_reason
    return ad_picture_page_reason(chapter)


def _spine_ordered_chapters(book):
    """`book.chapters` already loads in spine (reading) order directly
    from `parser.py` -- this just re-derives the same order locally as
    a defensive belt-and-suspenders measure (and to explicitly surface
    any manifest-only entry the spine doesn't read, tacked onto the
    end), since classification here specifically depends on reading
    order being right and would rather not silently assume it."""
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


_CONTENTS_MIN_LINKS = 8
_CONTENTS_LINK_SHARE = 0.7


def _is_contents_link_page(chapter, word_count: int) -> bool:
    """A page that is a list of links to other pages of the book (an HTML
    contents page): at least 8 internal links and most of its words are
    link text. Found on The Mist, whose contents page names "About the
    Author" and was read as that page."""
    doc = chapter.document
    if doc is None or word_count == 0:
        return False
    links = 0
    link_words = 0
    for a in doc.iter():
        if not isinstance(a.tag, str) or not a.tag.rsplit("}", 1)[-1].lower() == "a":
            continue
        href = a.get("href") or ""
        if not href or re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", href):
            continue
        links += 1
        link_words += len("".join(a.itertext()).split())
    return links >= _CONTENTS_MIN_LINKS and link_words >= _CONTENTS_LINK_SHARE * word_count


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
    if _ABOUT_AUTHOR_RE.search(text[:_HEADING_WINDOW + 90]):
        # Only as the page's own heading. Anywhere in the text it also matched
        # a contents list that names the page ("... About the Author ...").
        return MatterLabel.ABOUT_AUTHOR, "high"
    if _COLOPHON_RE.search(text):
        return MatterLabel.COLOPHON, "high"
    if _FICTION_DISCLAIMER_RE.search(text):
        return MatterLabel.FICTION_DISCLAIMER, "high"

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
        creator_words = [w for w in re.findall(r"[\w'-]+", metadata.creator or "")
                          if len(w) >= _TITLE_MATCH_MIN_WORD_LEN]
        # A title tag written "Author, First - Book Title" (a Calibre file
        # name) carries the author's name; those words are not the title.
        creator_lower = {w.lower() for w in creator_words}
        title_words = [w for w in re.findall(r"[\w'-]+", metadata.title or "")
                        if len(w) >= _TITLE_MATCH_MIN_WORD_LEN and w.lower() not in creator_lower]
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

def analyze_book_frontmatter(book, chapter_summary=None, overrides=None) -> BookFrontMatterSummary:
    """Classify every spine entry into a front/back/main zone plus a
    best-guess label. `chapter_summary` should be the
    chapters.BookChapterSummary the analyzer already computed for this
    book -- passed in so this doesn't have to re-run chapter boundary
    detection itself. Falls back to computing it if none is given, so
    this still works called standalone.

    `overrides`, if given, is an {href: MatterLabel value} dict of a
    person's own resolution from the GUI Review tab -- see
    `_frontmatter_review_groups`/`save_review` in gui/app.py. An
    override wins over every other signal below unconditionally: a
    person's explicit call is worth more than any pattern match, and
    gets recorded at "high" confidence with its own distinct reason so
    it reads differently from an auto-detected high-confidence match
    in the GUI/CLI output. The zone an override implies is derived
    from the label itself (see `zone_for_label` above) rather than
    taken as a separate input, so the Review tab only ever needs to
    collect one choice per page, not two.

    This is the single point every other analyzer/repair module's own
    frontmatter awareness flows through (color.py, scene_breaks.py,
    paragraphs.py, class_map.py all take a `frontmatter_summary`
    parameter and default to computing it fresh only if none was
    passed in) -- so a caller that threads a `BookFrontMatterSummary`
    computed with overrides through to those doesn't need any changes
    of its own to make a reviewer's correction take effect there too.
    """
    if chapter_summary is None:
        from ebook_fix.chapters import analyze_book_chapters
        chapter_summary = analyze_book_chapters(book)
    overrides = overrides or {}

    ordered = _spine_ordered_chapters(book)
    href_order = [chapter.href for chapter in ordered]

    confirmed_boundaries = chapter_summary.confirmed_boundaries or []
    confirmed_hrefs = {c.href for c in confirmed_boundaries}

    # href -> "prologue"/"epilogue" for every bare Prologue/Epilogue
    # chapters.py found (see its "unnumbered" label_kind) -- used below
    # to give these their own label instead of falling through to the
    # generic MAIN_CONTENT catch-all. Only the first marker per href is
    # kept; a file wouldn't realistically carry more than one of these.
    # Pages carrying a heading that literally starts with the word "Chapter"
    # that the confirmed run did not include. A bare number is not enough:
    # page numbers and glossary entries produce those (Images-PageNumbers).
    candidate_hrefs = {
        c.href for c in (getattr(chapter_summary, "candidates", None) or [])
        if re.match(r"\s*chapter\b", getattr(c, "text", "") or "", re.I)
    }
    unnumbered_by_href = {}
    for c in getattr(chapter_summary, "unnumbered_chapters", None) or []:
        unnumbered_by_href.setdefault(c.href, c.text.strip().split()[0].lower())

    # Guard against a real anomaly this surfaced on a sample book: a
    # single page (e.g. a one-page anchored table of contents) can
    # itself contain markers for every chapter number in the book,
    # so chapters.py legitimately confirms a full 1..N sequence but
    # every single boundary object's .href collapses to that one
    # page. Zone-anchoring below assumes each confirmed boundary marks
    # the START of a distinct chapter file -- fed a dozens-strong
    # sequence that all resolves to one href, first_idx and last_idx
    # collapse to that single index too, so literally everything
    # after that one page (every real chapter file that follows it)
    # reads as "back matter." A book with one genuine chapter in one
    # file is exactly this same shape by coincidence (one boundary,
    # one href) and is NOT this anomaly -- the tell is specifically
    # more than one distinct boundary collapsing into fewer hrefs than
    # boundaries, which only happens when multiple markers were found
    # crammed into shared pages. Treated the same as no confirmed
    # sequence at all (honest "low confidence, unknown zone" for every
    # page) rather than confidently mislabeling real chapters as back
    # matter -- see analyze_structure()/split-structure for the
    # separate, richer detection this book's own chapter *splitting*
    # already goes through instead of this older sequence.
    #
    # Phase 4 of docs/detection_confidence_plan.md narrowed this guard.
    # A contents page is short (a dozen words per marker at most), while
    # a file that really holds the chapters is long: when the one shared
    # file has plenty of words for every marker (Watermarks-
    # SmallChapterNumbers: 24 chapters in a 63,000 word file) the markers
    # are real chapters, and the zones are anchored on that file.
    if len(confirmed_boundaries) > 1 and len(confirmed_hrefs) == 1:
        shared_href = next(iter(confirmed_hrefs))
        shared_words = 0
        for chapter in ordered:
            if chapter.href == shared_href:
                shared_words = len(_chapter_text(chapter).split())
                break
        only_page = len(ordered) == 1  # the whole book is one file: it is the story
        if not only_page and not zone_anchors.markers_are_prose(shared_words, len(confirmed_boundaries)):
            confirmed_hrefs = set()

    first_idx = None
    last_idx = None
    anchor_note = CHAPTER_ANCHOR_NOTE
    if confirmed_hrefs:
        for i, href in enumerate(href_order):
            if href in confirmed_hrefs:
                if first_idx is None:
                    first_idx = i
                last_idx = i
    elif not confirmed_boundaries:
        # No chapter sequence at all. A Project Gutenberg file still says
        # in its own words where the book starts and ends.
        anchor = zone_anchors.find_gutenberg_anchor([_chapter_text(c) for c in ordered])
        if anchor is not None:
            first_idx, last_idx = anchor.first_idx, anchor.last_idx
            anchor_note = anchor.source

    summary = BookFrontMatterSummary(boundaries_confirmed=first_idx is not None)

    for i, chapter in enumerate(ordered):
        override_label = overrides.get(chapter.href)
        if override_label is not None:
            try:
                label = MatterLabel(override_label)
            except ValueError:
                # Not a real MatterLabel value -- stale or tampered
                # form data. Falls through to normal detection below
                # rather than raising, same as any other malformed
                # input this project treats defensively.
                override_label = None
        if override_label is not None:
            zone = zone_for_label(override_label)
            confidence = "high"
            reason = "confirmed by reviewer in the GUI Review tab"
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
            continue

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
        if zone == BACK_ZONE and _looks_like_bonus_story_title(text, word_count):
            text_label, text_confidence = MatterLabel.BONUS_STORY, "high"
        elif text_label is not MatterLabel.COPYRIGHT and _is_contents_link_page(chapter, word_count):
            text_label, text_confidence = MatterLabel.TABLE_OF_CONTENTS, "high"
        elif text_label in (MatterLabel.TABLE_OF_CONTENTS, MatterLabel.TITLE_PAGE) \
                and zone in (FRONT_ZONE, BACK_ZONE) and not _is_license_text(text):
            # A "mini contents" page above a "thank you, sign up for our
            # newsletters" notice, or a publisher's "Also by" list that names
            # the author, matches the contents or title page pattern first.
            # What the page says it is wins when it clearly says so.
            extra_first = _extra_label_from_text(text, word_count)
            if extra_first is not None and extra_first[1] == "high":
                text_label, text_confidence = extra_first[0], extra_first[1]

        if text_label is not None:
            label = text_label
            confidence = text_confidence
            reason = f"page content matches the {label.value} pattern"
        elif zone in (FRONT_ZONE, BACK_ZONE) and word_count <= 3 and (picture_ad := _picture_ad_reason(chapter)):
            label = MatterLabel.ADVERTISEMENT
            confidence = "high"
            reason = picture_ad
        elif href_label is not None:
            label, exact = href_label
            confidence = "high" if exact else "medium"
            reason = (
                f"filename is exactly a {label.value} page" if exact
                else f"filename hints at {label.value}"
            )
        elif unnumbered_by_href.get(chapter.href) == "prologue":
            label = MatterLabel.PROLOGUE
            confidence = "high"
            reason = "recognized as a prologue -- treated as an unnumbered chapter of the main story"
        elif unnumbered_by_href.get(chapter.href) == "epilogue":
            label = MatterLabel.EPILOGUE
            confidence = "high"
            reason = "recognized as an epilogue -- treated as an unnumbered chapter of the main story"
        elif zone == BACK_ZONE and _looks_like_teaser(text):
            label = MatterLabel.TEASER_CHAPTER
            confidence = "medium"
            reason = "opens like a preview of another book"
        elif zone in (FRONT_ZONE, BACK_ZONE) and (extra := _extra_label_from_text(text, word_count)) is not None:
            label, confidence, reason = extra
        elif zone in (FRONT_ZONE, BACK_ZONE) and not _is_license_text(text) and (
            word_count >= LONG_STORY_PAGE_WORDS
            or (word_count >= CHAPTER_HEADED_PAGE_WORDS and chapter.href in candidate_hrefs)
        ):
            # Phase 5 of docs/detection_confidence_plan.md. Real front and
            # back matter is short. A long page with none of the wording
            # any front or back matter label looks for is story text the
            # confirmed chapter run did not reach (the run restarts in an
            # omnibus, or skips chapters whose numbers are hard to read).
            zone = MAIN_ZONE
            label = MatterLabel.MAIN_CONTENT
            confidence = "medium"
            reason = (
                f"story text ({word_count} words, " + (
                    "long page" if word_count >= LONG_STORY_PAGE_WORDS else "opens with a chapter heading"
                ) + ") outside the confirmed chapter run, with no front or back matter wording"
            )
        elif zone == FRONT_ZONE:
            label = MatterLabel.FRONT_MATTER
            confidence = "medium" if word_count <= SHORT_PAGE_WORD_THRESHOLD else "low"
            reason = "comes before the first confirmed chapter" if anchor_note == CHAPTER_ANCHOR_NOTE else f"comes before {anchor_note}"
        elif zone == BACK_ZONE:
            label = MatterLabel.BACK_MATTER
            confidence = "medium" if word_count <= SHORT_PAGE_WORD_THRESHOLD else "low"
            reason = "comes after the last confirmed chapter" if anchor_note == CHAPTER_ANCHOR_NOTE else f"comes after {anchor_note}"
        elif zone == MAIN_ZONE:
            label = MatterLabel.MAIN_CONTENT
            confidence = "high"
            reason = (
                "falls within the confirmed chapter sequence"
                if anchor_note == CHAPTER_ANCHOR_NOTE
                else f"falls between {anchor_note}"
            )
        else:
            label = MatterLabel.MAIN_CONTENT
            confidence = "low"
            reason = "no confirmed chapter sequence to anchor a zone on"

        if (summary.chapters and summary.chapters[-1].label == MatterLabel.BONUS_STORY.value
                and zone == MAIN_ZONE and label is MatterLabel.MAIN_CONTENT and confidence != "high"):
            # The story itself, straight after its title page. It stays in
            # the main zone so it is repaired like any story text.
            label = MatterLabel.BONUS_STORY
            confidence = "medium"
            reason = "story text straight after a bonus story title page"
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
