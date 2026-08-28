"""
ebook_fix.chapters

Detects chapter-boundary markers inside a book's content -- the "Chapter
Four", "IV", "- 4 -", "Fourth" style text that a lot of source EPUBs bury
in the middle of an ordinary <p> tag with no heading markup, no CSS class,
and nothing structural to flag it as special. This is common in EPUBs
that were built as one giant HTML file per section instead of one file
per chapter (see: RunTogetherText.epub in examples/).

This module is descriptive only, same as css.py and typography.py -- it
finds and scores candidate markers, then reports which ones form a
believable chapter sequence. It does not split files or insert page
breaks; that's a repair module's job, built on top of this analysis.

Why sequence validation matters
--------------------------------
Text pattern matching alone is not enough. A lone "4" sitting in a <p>
tag might be a chapter number -- or it might be a list item, a page
number, a footnote marker, or just a number mentioned in the story. The
strongest signal available isn't any single marker looking right, it's
multiple markers *in document order* counting up together (1, 2, 3, 4...
or I, II, III, IV...). A stray number in running prose doesn't have
neighbors before and after it continuing the count. This module scores
individual candidates first, then looks for the longest run of markers
that count up consistently, and treats that run as the confirmed chapter
sequence.

Image-based chapter markers (a picture of "4" instead of text) are out of
scope for this module -- that needs OCR and will be handled separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from lxml import etree

# Block-level tags we consider as chapter-marker candidates. "div" is
# deliberately excluded: a div very often wraps a whole chapter's worth
# of prose, and treating its full text as "one candidate" would produce
# useless, oversized candidates. p/h1-h6/span cover the way converters
# typically isolate a lone chapter marker.
CANDIDATE_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "span"}

# A candidate's own text has to be short to be considered "isolated" --
# real chapter markers are a number/word/phrase, not a sentence. This is
# generous enough to cover "Chapter Twenty-Seven: The Long Way Home".
MAX_CANDIDATE_WORDS = 8
MAX_CANDIDATE_CHARS = 60

# Words that strongly imply a chapter marker when they appear as a
# prefix, regardless of what number style follows. Split into two
# tiers: CHAPTER_LABEL_WORDS mark an ordinary chapter, PART_LABEL_WORDS
# mark a bigger structural division (Book/Part/Volume) that a novel's
# chapter numbering commonly restarts under -- "BOOK ONE" ... "CHAPTER
# I" ... "CHAPTER II" ... "BOOK TWO" ... "CHAPTER I" again. Older public
# -domain novels (Tolstoy, Hugo, Dumas) do this constantly, and without
# recognizing the restart, only the first part's chapters get counted
# as a "believable sequence" -- the rest look like broken numbering and
# get thrown out.
CHAPTER_LABEL_WORDS = ("chapter", "section")
PART_LABEL_WORDS = ("book", "part", "volume")
LABEL_WORDS = CHAPTER_LABEL_WORDS + PART_LABEL_WORDS

# Same idea as PART_LABEL_WORDS above, but for a label word that trails
# an ordinal instead of leading it -- "FIRST EPILOGUE", "SECOND
# EPILOGUE", rather than "BOOK ONE". War and Peace's own epilogue is
# split exactly this way, and each epilogue starts its own chapter
# numbering back at "CHAPTER I" the same way a new Book/Part/Volume
# does. Without recognizing "First Epilogue"/"Second Epilogue" as a
# part boundary too, that restart looks like broken numbering rather
# than a new part starting, and the detector concludes the book ended
# at the last chapter of the last Book instead of continuing into the
# epilogue(s).
SUFFIX_PART_LABEL_WORDS = ("epilogue", "prologue")

ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
ARABIC_RE = re.compile(r"^\d{1,4}$")
ARABIC_HYPHEN_RE = re.compile(r"^[-\u2013\u2014]\s*(\d{1,4})\s*[-\u2013\u2014]$")

# Case 3 only: a bare number immediately followed by a title, with no
# label word in front of it ("1. The Horror in Clay.", "IV) The Return").
# The separator is deliberately narrow (period, close-paren, or colon)
# rather than any punctuation, so this doesn't fire on ordinary prose
# that happens to start with a number and a dash or comma.
CASE3_ARABIC_TITLE_RE = re.compile(r"^(\d{1,4})[.\):]\s+(.+)$")
CASE3_ROMAN_TITLE_RE = re.compile(r"^([IVXLCDM]+)[.\):]\s+(.+)$", re.IGNORECASE)

_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_ORDINAL_ONES = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
    "eighteenth": 18, "nineteenth": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_ORDINAL_TENS = {
    "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
    "sixtieth": 60, "seventieth": 70, "eightieth": 80, "ninetieth": 90,
}


class MarkerStyle(Enum):
    ROMAN = "roman numeral"
    ARABIC = "arabic numeral"
    ARABIC_HYPHEN = "hyphen-wrapped numeral"
    SPELLED_CARDINAL = "spelled-out number"
    SPELLED_ORDINAL = "spelled-out ordinal"
    # Case 3 only (see analyze_case3_book_chapters below) -- a number
    # with no label word ("Chapter", "Part") immediately followed by a
    # title, e.g. "1. The Horror in Clay." These are never produced by
    # _classify/extract_candidates above; only _classify_case3 /
    # extract_case3_candidates produce them, and only ever get used
    # when the book has no labeled markers and no TOC to detect
    # anything against otherwise.
    UNLABELED_NUMBERED_TITLE_ARABIC = "unlabeled numbered title (arabic)"
    UNLABELED_NUMBERED_TITLE_ROMAN = "unlabeled numbered title (roman)"


@dataclass
class ChapterCandidate:
    href: str = ""
    tag: str = ""
    text: str = ""
    element_index: int = 0          # position of this element within its document, for ordering
    book_order: int = 0             # position across the whole book (spine order + element_index)
    style: MarkerStyle | None = None
    number: int | None = None
    label_prefix: bool = False      # text started with "Chapter"/"Part"/etc.
    label_kind: str | None = None   # "chapter", "part" (Book/Part/Volume), or None (bare numeral)
    isolated: bool = True           # element's own text is *only* the marker
    is_heading_tag: bool = False
    css_hint: bool = False          # class or id mentions chapter/title/heading
    score: float = 0.0
    confirmed: bool = False         # part of the winning sequence
    occurrence_count: int = 1       # how many consecutive files repeated this exact marker
    also_seen_hrefs: list = field(default_factory=list)  # the other files it was folded in from
    part_index: int = 0             # which Book/Part/Volume this candidate falls under (0 = before/without any)
    element: object = None          # live reference to the lxml element this candidate was read from --
                                     # descriptive-only consumers can ignore it; a repair module built on
                                     # top of this analysis (see modules/chapter_markup.py) needs it to
                                     # actually locate the boundary in the tree.


@dataclass
class ChapterSequence:
    style: MarkerStyle | None
    length: int
    candidates: list = field(default_factory=list)
    score_sum: float = 0.0


@dataclass
class BookChapterSummary:
    candidates: list = field(default_factory=list)
    confirmed_boundaries: list = field(default_factory=list)
    best_sequence: ChapterSequence | None = None
    other_sequences: list = field(default_factory=list)
    parts: list = field(default_factory=list)  # detected Book/Part/Volume-level markers, in book order


# ---------------------------------------------------------------------
# Roman numeral parsing
# ---------------------------------------------------------------------

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman_to_int(text: str) -> int | None:
    text = text.upper()
    total = 0
    prev = 0
    for ch in reversed(text):
        val = _ROMAN_VALUES.get(ch)
        if val is None:
            return None
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    if total <= 0:
        return None
    # Round-trip check: reject things like "IIII" or "VV" that technically
    # sum correctly but aren't valid Roman numerals, by regenerating the
    # canonical form and comparing.
    if _int_to_roman(total) != text:
        return None
    return total


def _int_to_roman(n: int) -> str:
    vals = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    out = []
    for v, sym in vals:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


# ---------------------------------------------------------------------
# Spelled-out number parsing ("twenty-seven", "twenty-seventh")
# ---------------------------------------------------------------------

def _leading_spelled_number(words: list) -> tuple[int, int, MarkerStyle] | None:
    """
    Walk a list of (already lowercased) words from the start, consuming
    as many number-words as form a valid spelled-out number. Returns
    (words_consumed, value, style), or None if the first word isn't a
    number word at all. Stops -- without failing -- at the first word
    that isn't part of the number, so callers can decide whether
    whatever's left over (a title, or nothing) is acceptable.
    """
    total = 0
    style = MarkerStyle.SPELLED_CARDINAL
    i = 0
    n = len(words)
    while i < n:
        w = words[i]
        if w == "and" and total > 0:
            i += 1
            continue
        if w == "hundred" and total > 0:
            total *= 100
            i += 1
            continue
        if w in _ONES:
            total += _ONES[w]
            i += 1
            continue
        if w in _ORDINAL_ONES:
            total += _ORDINAL_ONES[w]
            style = MarkerStyle.SPELLED_ORDINAL
            i += 1
            break  # an ordinal ones-word ends the number ("twenty-first", not "twenty-first-two")
        if w in _TENS:
            total += _TENS[w]
            i += 1
            continue
        if w in _ORDINAL_TENS:
            total += _ORDINAL_TENS[w]
            style = MarkerStyle.SPELLED_ORDINAL
            i += 1
            break
        break
    if i == 0 or total <= 0:
        return None
    return i, total, style


def _spelled_to_int(text: str) -> tuple[int, MarkerStyle] | None:
    words = text.lower().replace("-", " ").split()
    if not words:
        return None
    lead = _leading_spelled_number(words)
    if lead is None:
        return None
    consumed, total, style = lead
    if consumed != len(words):
        # Words left over that aren't part of the number -- not a clean
        # whole-text match (caller may still try the "number + title" path).
        return None
    return total, style


def _looks_titleish(text: str) -> bool:
    """
    True if `text` reads like a title/heading rather than a sentence:
    either fully uppercase, or every significant word capitalized.
    Used to gate the "number word directly followed by more words"
    pattern (e.g. "FOURTH MACHINATION") so it doesn't fire on ordinary
    prose that happens to start with a number word ("Four days later...").
    """
    letters_only = re.sub(r"[^A-Za-z\s]", "", text).strip()
    if not letters_only:
        return False
    if letters_only.isupper():
        return True
    minor_words = {
        "a", "an", "the", "of", "and", "or", "in", "on", "to",
        "for", "at", "by", "with", "from", "as", "into", "onto",
        "over", "under", "but", "nor",
    }
    words = letters_only.split()
    for i, w in enumerate(words):
        if i > 0 and w.lower() in minor_words:
            continue
        if not w[:1].isupper():
            return False
    return True


# ---------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------

def _classify_number_only(text: str) -> tuple[MarkerStyle, int] | None:
    """Whole-string number match: arabic, hyphen-wrapped, roman, or a
    spelled-out number with nothing left over."""
    m = ARABIC_HYPHEN_RE.match(text)
    if m:
        return MarkerStyle.ARABIC_HYPHEN, int(m.group(1))

    if ARABIC_RE.match(text):
        return MarkerStyle.ARABIC, int(text)

    if ROMAN_NUMERAL_RE.match(text):
        n = _roman_to_int(text)
        if n is not None:
            return MarkerStyle.ROMAN, n

    spelled = _spelled_to_int(text)
    if spelled is not None:
        n, style = spelled
        return style, n

    return None


def _classify(text: str) -> tuple[MarkerStyle, int, bool, str | None] | None:
    """
    Try to read `text` as a chapter marker. Returns (style, number,
    had_label_prefix, label_kind) or None if it doesn't look like one
    at all. label_kind is "part" for a Book/Part/Volume-style division,
    "chapter" for an ordinary chapter, or None when there's no label
    word at all (a bare "4" or "IV").
    """
    stripped = text.strip()
    if not stripped:
        return None

    first_word = stripped.split(None, 1)[0].strip(":.").lower()
    if first_word in LABEL_WORDS:
        # "Chapter Four: The Long Way Home" -- take the number chunk
        # right after the label word, drop any title that follows.
        working = stripped[len(stripped.split(None, 1)[0]):].strip()
        working = working.lstrip(":.-\u2013\u2014 ").strip()
        if not working:
            return None
        working = re.split(r"[:.\u2013\u2014-]", working, maxsplit=1)[0].strip()
        if not working:
            return None
        result = _classify_number_only(working)
        if result is None:
            return None
        style, number = result
        label_kind = "part" if first_word in PART_LABEL_WORDS else "chapter"
        return style, number, True, label_kind

    # "First Epilogue" / "Second Epilogue" / bare "Epilogue" -- see
    # SUFFIX_PART_LABEL_WORDS above. Only look at the first two words;
    # anything after (a year range, a subtitle) is dropped the same way
    # the label-first branch above drops a title after "Chapter Four:".
    words = stripped.split()
    if words:
        w0 = words[0].strip(":.,;").lower()
        w1 = words[1].strip(":.,;").lower() if len(words) > 1 else None
        if w1 is not None and w1 in SUFFIX_PART_LABEL_WORDS:
            number = _ORDINAL_ONES.get(w0) or _ORDINAL_TENS.get(w0)
            if number is not None:
                return MarkerStyle.SPELLED_ORDINAL, number, True, "part"
        elif w0 in SUFFIX_PART_LABEL_WORDS:
            # Bare "Epilogue"/"Prologue" with no ordinal -- treat as the
            # first (and possibly only) one of its kind.
            return MarkerStyle.SPELLED_ORDINAL, 1, True, "part"

    # No label word. First try treating the whole text as a number
    # ("IV", "4", "Four").
    result = _classify_number_only(stripped)
    if result is not None:
        style, number = result
        return style, number, False, None

    # Fall back to "number word immediately followed by more words, no
    # separator" ("FOURTH MACHINATION"). Gated on the text reading like
    # a title/heading so it doesn't fire on ordinary sentences that
    # happen to start with a number word ("Four days later...").
    words = stripped.lower().replace("-", " ").split()
    lead = _leading_spelled_number(words)
    if lead is not None and _looks_titleish(stripped):
        _, number, style = lead
        return style, number, False, None

    return None


# ---------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------

def _element_own_text(el) -> str:
    return "".join(el.itertext()).strip()


def _has_candidate_descendant(el) -> bool:
    """True if a descendant is itself one of CANDIDATE_TAGS (so `el`
    would just be re-reporting a nested candidate's text)."""
    for child in el.iter():
        if child is el:
            continue
        if not isinstance(child.tag, str):
            continue
        if etree.QName(child).localname.lower() in CANDIDATE_TAGS:
            return True
    return False


def _css_mentions_chapter(el) -> bool:
    for attr in ("class", "id"):
        val = el.get(attr)
        if val and any(h in val.lower() for h in ("chapter", "title", "heading")):
            return True
    return False


def extract_candidates(href: str, tree, classify_fn=None, score_fn=None) -> list:
    """Scan one chapter's document tree for chapter-marker candidates.

    `classify_fn`/`score_fn` default to the module's normal _classify/
    _score_candidate pair. extract_case3_candidates below is the only
    other caller, and passes _classify_case3/_score_case3_candidate
    instead -- same tree walk and same isolation/length rules, just a
    much weaker text pattern, so case 3 detection can't accidentally
    diverge from how case 1/2 candidates are found and re-introduce a
    bug in one without the other.
    """
    if classify_fn is None:
        classify_fn = _classify
    if score_fn is None:
        score_fn = _score_candidate

    candidates = []
    if tree is None:
        return candidates

    index = 0
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        tag = etree.QName(el).localname.lower()
        if tag not in CANDIDATE_TAGS:
            continue
        if _has_candidate_descendant(el):
            # Don't double-count a heading that wraps a span, etc.
            continue

        text = _element_own_text(el)
        if not text:
            index += 1
            continue

        words = text.split()
        if len(words) > MAX_CANDIDATE_WORDS or len(text) > MAX_CANDIDATE_CHARS:
            index += 1
            continue

        classified = classify_fn(text)
        index += 1
        if classified is None:
            continue

        style, number, label_prefix, label_kind = classified
        cand = ChapterCandidate(
            href=href,
            tag=tag,
            text=text,
            element_index=index,
            style=style,
            number=number,
            label_prefix=label_prefix,
            label_kind=label_kind,
            isolated=True,
            is_heading_tag=tag.startswith("h") and len(tag) == 2,
            css_hint=_css_mentions_chapter(el),
            element=el,
        )
        cand.score = score_fn(cand)
        candidates.append(cand)

    return candidates


# ---------------------------------------------------------------------
# Case 3 -- unlabeled numbered titles ("1. The Horror in Clay.")
# ---------------------------------------------------------------------
# Only meant to run when analyze_book_chapters above already came back
# with no confirmed chapters at all -- see Jacob's three-case framework
# in docs/xhtml_recoder_plan.md. Case 3 books have no label word
# ("Chapter", "Part") and no TOC to corroborate against, so this is a
# meaningfully weaker signal than the rest of this module and is kept
# entirely separate rather than folded into _classify/_score_candidate:
# a book that already detects cleanly through the normal path should
# never have its result changed by this pattern existing.


def _classify_case3(text: str) -> tuple[MarkerStyle, int, bool, str | None] | None:
    """Try to read `text` as an unlabeled "number + title" marker.
    Returns the same shape _classify does (style, number,
    had_label_prefix, label_kind) so it can reuse extract_candidates'
    tree walk and ChapterCandidate shape -- label_prefix is always
    False and label_kind always None here, since by definition there's
    no label word.
    """
    stripped = text.strip()
    if not stripped:
        return None

    m = CASE3_ARABIC_TITLE_RE.match(stripped)
    if m:
        title = m.group(2).strip()
        if title and _looks_titleish(title):
            return MarkerStyle.UNLABELED_NUMBERED_TITLE_ARABIC, int(m.group(1)), False, None

    m = CASE3_ROMAN_TITLE_RE.match(stripped)
    if m:
        number = _roman_to_int(m.group(1))
        title = m.group(2).strip()
        if number is not None and title and _looks_titleish(title):
            return MarkerStyle.UNLABELED_NUMBERED_TITLE_ROMAN, number, False, None

    return None


def _score_case3_candidate(c: ChapterCandidate) -> float:
    """Deliberately lower-confidence than _score_candidate -- there's
    no label word to lean on here, so this never earns the label-
    prefix bonus, and a bare arabic number is treated as neutral
    rather than penalized the way it is in _score_candidate, since the
    titleish-title requirement in _classify_case3 already rules out
    the ordinary-prose false positives that penalty exists to catch.
    """
    score = 0.0
    if c.is_heading_tag:
        score += 3
    if c.css_hint:
        score += 1.5
    if c.style == MarkerStyle.UNLABELED_NUMBERED_TITLE_ROMAN:
        score += 1.0
    if len(c.text.split()) <= 6:
        score += 0.5
    return score


def extract_case3_candidates(href: str, tree) -> list:
    return extract_candidates(href, tree, classify_fn=_classify_case3, score_fn=_score_case3_candidate)


def analyze_case3_book_chapters(book) -> BookChapterSummary:
    """Case 3 counterpart to analyze_book_chapters above. Callers
    should only reach for this after confirming the normal analysis
    found nothing (summary.best_sequence is None) -- see
    ebook_fix.structure.analyze_case3_structure, which is what
    map-structure actually calls. There's no Book/Part/Volume handling
    here: that concept is built entirely on label words, which case 3
    text has none of by definition.
    """
    all_candidates = []
    order = 0
    for chapter in book.chapters:
        href = getattr(chapter, "href", "")
        tree = getattr(chapter, "document", None)
        chapter_candidates = extract_case3_candidates(href, tree)
        for c in chapter_candidates:
            order += 1
            c.book_order = order
        all_candidates.extend(chapter_candidates)

    all_candidates = merge_repeated_markers(all_candidates)

    summary = BookChapterSummary(candidates=all_candidates)
    if not all_candidates:
        return summary

    best, all_sequences = _find_best_sequence(all_candidates)
    summary.best_sequence = best
    summary.other_sequences = [s for s in all_sequences if s is not best]

    if best is not None:
        for c in best.candidates:
            c.confirmed = True
        summary.confirmed_boundaries = list(best.candidates)

    return summary


def _score_candidate(c: ChapterCandidate) -> float:
    score = 0.0
    if c.is_heading_tag:
        score += 3
    if c.label_prefix:
        score += 2.5
    if c.css_hint:
        score += 1.5
    if c.style == MarkerStyle.ROMAN:
        score += 1.5
    elif c.style in (MarkerStyle.SPELLED_CARDINAL, MarkerStyle.SPELLED_ORDINAL):
        score += 1.0
    elif c.style == MarkerStyle.ARABIC_HYPHEN:
        score += 1.5
    elif c.style == MarkerStyle.ARABIC:
        # Bare numbers are the weakest signal on their own -- easiest
        # to be a false positive -- unless something else backs it up.
        score -= 0.5
    if len(c.text.split()) <= 3:
        score += 0.5
    return score


# ---------------------------------------------------------------------
# Repeated-header merging
# ---------------------------------------------------------------------
# PDF-to-EPUB conversions frequently split one file per *printed page*
# rather than one file per chapter, and carry the print edition's
# running header -- the chapter title repeated at the top of every
# page -- along with it. Left alone, that repetition makes a single
# chapter look like dozens of them: "CHAPTER I" showing up at the top
# of eleven separate files is one chapter, not eleven. This step folds
# consecutive, identically-worded markers into a single candidate
# before sequence detection ever runs, so a repeated running header
# can't inflate the chapter count.

_MARKER_NORMALIZE_RE = re.compile(r"\s+")


def _normalize_marker_text(text: str) -> str:
    return _MARKER_NORMALIZE_RE.sub(" ", text.strip().lower())


def merge_repeated_markers(candidates: list) -> list:
    """
    Collapse runs of consecutive-in-book-order candidates that share
    the same normalized text, style, and number -- the signature of a
    running header repeating across pages -- into a single candidate.
    The first occurrence is kept as the representative; later ones are
    recorded on it via `occurrence_count` / `also_seen_hrefs` rather
    than discarded outright, so repair modules can still see every
    file the chapter's heading actually touched.

    Runs are tracked per marker style, not across the raw mixed stream:
    a running chapter-title header and a running page number are often
    two separate candidates sitting side by side on every page, and
    that page number changing between occurrences shouldn't stop the
    chapter title's repeats from being recognized as the same run.
    """
    if not candidates:
        return candidates

    by_style: dict = {}
    for c in candidates:
        by_style.setdefault(c.style, []).append(c)

    merged = []
    for style, group in by_style.items():
        ordered = sorted(group, key=lambda c: c.book_order)
        i = 0
        n = len(ordered)
        while i < n:
            current = ordered[i]
            norm = _normalize_marker_text(current.text)
            seen_hrefs = []
            j = i + 1
            while j < n:
                nxt = ordered[j]
                if _normalize_marker_text(nxt.text) != norm or nxt.number != current.number:
                    break
                if nxt.href not in seen_hrefs and nxt.href != current.href:
                    seen_hrefs.append(nxt.href)
                j += 1
            if j - i > 1:
                current.occurrence_count = j - i
                current.also_seen_hrefs = seen_hrefs
            merged.append(current)
            i = j

    merged.sort(key=lambda c: c.book_order)
    return merged


# ---------------------------------------------------------------------
# Sequence validation
# ---------------------------------------------------------------------

# How far ahead a sequence is allowed to jump between confirmed markers
# (e.g. a missed chapter, or a non-numbered interlude) before it's no
# longer considered part of the same run.
MAX_SEQUENCE_GAP = 2

# Shortest run length that counts as a believable chapter sequence.
# Below this, an increasing run of two numbers is too easily coincidence.
MIN_SEQUENCE_LENGTH = 3

# After crossing into a new Book/Part/Volume, a chapter number this
# small or smaller is treated as a legitimate restart ("CHAPTER I"
# again) rather than a break in the sequence. Set higher than 1 to
# tolerate a missing/unlabeled first chapter within the new part.
PART_RESTART_MAX_NUMBER = 2


def _find_best_sequence(candidates: list) -> ChapterSequence | None:
    """
    Best-scoring-increasing-run search, grouped by marker style (a book
    normally sticks to one numbering style throughout). Within a style,
    finds the run of candidates, in book order, where each number is
    greater than the last by no more than MAX_SEQUENCE_GAP, maximizing
    total candidate score rather than raw run length.

    Score, not length, decides the winner across styles. A run of bare,
    unlabeled numbers (score close to 0 each, see _score_candidate) can
    easily outnumber a run of confidently-labeled markers like "CHAPTER
    I", "CHAPTER II" -- that's exactly what happens when a print-to-EPUB
    conversion leaves the printed page number in a running header: the
    page numbers count up across nearly every file in the book, while
    the real chapter headings only increment a few dozen times. Summing
    score instead of counting candidates means that long, low-confidence
    run can't out-rank a shorter, well-labeled one just by being longer.

    Crossing into a new Book/Part/Volume (candidate.part_index goes up,
    see _assign_part_indices) is also allowed to "reset" the count back
    down near 1 without breaking the run -- many classic novels number
    chapters within each part rather than across the whole book
    ("BOOK ONE" / "CHAPTER I"..."CHAPTER XXVIII", then "BOOK TWO" /
    "CHAPTER I" again), and without this the chain would snap at every
    part boundary and only the first part's chapters would ever be
    reported.
    """
    by_style: dict = {}
    for c in candidates:
        by_style.setdefault(c.style, []).append(c)

    best: ChapterSequence | None = None
    all_sequences = []

    for style, group in by_style.items():
        group = sorted(group, key=lambda c: c.book_order)
        n = len(group)
        if n == 0:
            continue

        # dp[i] = (score_sum, run_length, predecessor_index) for the
        # best-scoring run ending at i.
        dp = [(group[i].score, 1, -1) for i in range(n)]
        for i in range(n):
            for j in range(i):
                if group[j].number is None or group[i].number is None:
                    continue
                if group[i].part_index == group[j].part_index:
                    gap = group[i].number - group[j].number
                    valid = 1 <= gap <= MAX_SEQUENCE_GAP
                elif group[i].part_index > group[j].part_index:
                    # Crossed into a later Book/Part/Volume -- numbering
                    # is allowed to restart near 1 instead of needing to
                    # keep counting up from wherever the last part left
                    # off.
                    valid = group[i].number <= PART_RESTART_MAX_NUMBER
                else:
                    valid = False
                if not valid:
                    continue
                candidate_score = dp[j][0] + group[i].score
                candidate_len = dp[j][1] + 1
                # Compare on (score, length) together: score is the
                # primary signal, but when two paths score exactly
                # the same (e.g. a run of equally-weak bare numbers),
                # prefer the longer one rather than stopping early.
                if (candidate_score, candidate_len) > (dp[i][0], dp[i][1]):
                    dp[i] = (candidate_score, candidate_len, j)

        # Walk back from the best-scoring ending point to collect that
        # style's winning run. Same (score, length) tie-break as above --
        # otherwise a tie on score alone would arbitrarily settle on
        # whichever index came first, even a length-1 one.
        end_idx = max(range(n), key=lambda i: (dp[i][0], dp[i][1]))
        score_sum, length, _ = dp[end_idx]
        if length < MIN_SEQUENCE_LENGTH:
            continue

        chain = []
        idx = end_idx
        while idx != -1:
            chain.append(group[idx])
            idx = dp[idx][2]
        chain.reverse()

        seq = ChapterSequence(style=style, length=length, candidates=chain, score_sum=score_sum)
        all_sequences.append(seq)
        if best is None or seq.score_sum > best.score_sum:
            best = seq

    all_sequences.sort(key=lambda s: s.score_sum, reverse=True)
    return best, all_sequences


# ---------------------------------------------------------------------
# Part/Book/Volume sequence validation
# ---------------------------------------------------------------------
#
# Everything above validates chapter candidates against each other.
# Part/Book/Volume-level candidates never went through any equivalent
# check -- a detected "Book Three" was trusted the instant it was
# found, no differently than if it had been part of a believable
# counting-up run. That's backwards: a Part boundary sits a level
# above a chapter boundary structurally, but until now had a *weaker*
# bar to clear than a chapter did (none at all). This closes that gap.

def _part_division_word(text: str) -> str:
    """Which structural label word (if any) is actually behind a part
    candidate's text -- "book", "part", "volume", "epilogue", or
    "prologue". Used purely as a grouping key for sequence validation
    below, so "BOOK ONE".."BOOK FIFTEEN" and an unrelated "FIRST
    EPILOGUE"/"SECOND EPILOGUE" numbering track in the same book are
    never forced to count up against each other -- each structural
    word gets its own independent sequence.

    Re-derives this from the candidate's own text rather than
    threading a new field through _classify/ChapterCandidate, since
    only this one, later, narrowly-scoped check needs it.
    """
    words = text.strip().split()
    if not words:
        return ""
    first = words[0].strip(":.,;").lower()
    if first in PART_LABEL_WORDS:
        return first
    if len(words) > 1:
        second = words[1].strip(":.,;").lower()
        if second in SUFFIX_PART_LABEL_WORDS:
            return second
    if first in SUFFIX_PART_LABEL_WORDS:
        return first
    return ""


def _find_best_part_sequence(part_candidates: list) -> list:
    """
    Validates that Part/Book/Volume-level candidates actually count up
    sensibly against their own same-kind neighbors, the way chapters
    already have to. Returns the subset of `part_candidates` that
    passed -- callers mark exactly this subset `.confirmed = True`;
    anything left out stays untrusted (e.g. a garbled OCR line that
    happened to start with "Book" but doesn't fit anywhere in the
    count).

    Grouped first by `_part_division_word` (so "Book" and "Epilogue"
    numbering never compete against each other) and then, within a
    division word, by number style, same as chapter sequencing.
    Deliberately no equivalent of chapters.py's PART_RESTART_MAX_NUMBER
    carve-out -- Parts are the top of the hierarchy here, so there's no
    still-higher grouping for them to legitimately restart under.

    Unlike chapter sequences, a group of only one candidate (a book
    with a single "Prologue" and nothing else of its kind) is trusted
    on its own label score alone rather than discarded -- there's
    nothing to validate a *sequence* against, and MIN_SEQUENCE_LENGTH's
    reasoning ("two numbers counting up is too easy to be coincidence")
    doesn't apply to a single, explicitly labeled marker the way it
    does to a bare unlabeled number. A group of two or more still has
    to actually count up correctly to be trusted; an outlier that
    breaks the count is excluded the same way chapters.py excludes one.
    """
    groups: dict = {}
    for c in part_candidates:
        groups.setdefault((_part_division_word(c.text), c.style), []).append(c)

    validated: list = []
    for group in groups.values():
        group = sorted(group, key=lambda c: c.book_order)
        n = len(group)
        if n == 1:
            validated.extend(group)
            continue

        dp = [(group[i].score, 1, -1) for i in range(n)]
        for i in range(n):
            for j in range(i):
                if group[j].number is None or group[i].number is None:
                    continue
                gap = group[i].number - group[j].number
                if not (1 <= gap <= MAX_SEQUENCE_GAP):
                    continue
                candidate_score = dp[j][0] + group[i].score
                candidate_len = dp[j][1] + 1
                if (candidate_score, candidate_len) > (dp[i][0], dp[i][1]):
                    dp[i] = (candidate_score, candidate_len, j)

        end_idx = max(range(n), key=lambda i: (dp[i][0], dp[i][1]))
        chain = []
        idx = end_idx
        while idx != -1:
            chain.append(group[idx])
            idx = dp[idx][2]
        chain.reverse()
        validated.extend(chain)

    return validated


# ---------------------------------------------------------------------
# Book-level entry point
# ---------------------------------------------------------------------

def _assign_part_indices(chapter_candidates: list, part_candidates: list) -> None:
    """
    Stamps each chapter candidate with how many Book/Part/Volume
    markers came before it in the book (0 if none yet). Mutates the
    candidates in place. Both lists must already share the same
    book_order numbering.
    """
    if not part_candidates:
        return
    parts_sorted = sorted(part_candidates, key=lambda c: c.book_order)
    idx = 0
    n = len(parts_sorted)
    for c in sorted(chapter_candidates, key=lambda c: c.book_order):
        while idx < n and parts_sorted[idx].book_order <= c.book_order:
            idx += 1
        c.part_index = idx


def analyze_book_chapters(book) -> BookChapterSummary:
    all_candidates = []
    order = 0
    for chapter in book.chapters:
        href = getattr(chapter, "href", "")
        tree = getattr(chapter, "document", None)
        chapter_candidates = extract_candidates(href, tree)
        for c in chapter_candidates:
            order += 1
            c.book_order = order
        all_candidates.extend(chapter_candidates)

    all_candidates = merge_repeated_markers(all_candidates)

    part_candidates = [c for c in all_candidates if c.label_kind == "part"]
    chapter_candidates = [c for c in all_candidates if c.label_kind != "part"]
    _assign_part_indices(chapter_candidates, part_candidates)

    for c in _find_best_part_sequence(part_candidates):
        c.confirmed = True

    summary = BookChapterSummary(
        candidates=all_candidates,
        parts=sorted(part_candidates, key=lambda c: c.book_order),
    )

    if not chapter_candidates:
        return summary

    best, all_sequences = _find_best_sequence(chapter_candidates)
    summary.best_sequence = best
    summary.other_sequences = [s for s in all_sequences if s is not best]

    if best is not None:
        for c in best.candidates:
            c.confirmed = True
        summary.confirmed_boundaries = list(best.candidates)

    return summary
