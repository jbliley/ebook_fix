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
# prefix, regardless of what number style follows.
LABEL_WORDS = ("chapter", "part", "book", "section")

ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
ARABIC_RE = re.compile(r"^\d{1,4}$")
ARABIC_HYPHEN_RE = re.compile(r"^[-\u2013\u2014]\s*(\d{1,4})\s*[-\u2013\u2014]$")

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
    isolated: bool = True           # element's own text is *only* the marker
    is_heading_tag: bool = False
    css_hint: bool = False          # class or id mentions chapter/title/heading
    score: float = 0.0
    confirmed: bool = False         # part of the winning sequence


@dataclass
class ChapterSequence:
    style: MarkerStyle | None
    length: int
    candidates: list = field(default_factory=list)


@dataclass
class BookChapterSummary:
    candidates: list = field(default_factory=list)
    confirmed_boundaries: list = field(default_factory=list)
    best_sequence: ChapterSequence | None = None
    other_sequences: list = field(default_factory=list)


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
        "for", "at", "by", "with",
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


def _classify(text: str) -> tuple[MarkerStyle, int, bool] | None:
    """
    Try to read `text` as a chapter marker. Returns (style, number,
    had_label_prefix) or None if it doesn't look like one at all.
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
        return style, number, True

    # No label word. First try treating the whole text as a number
    # ("IV", "4", "Four").
    result = _classify_number_only(stripped)
    if result is not None:
        style, number = result
        return style, number, False

    # Fall back to "number word immediately followed by more words, no
    # separator" ("FOURTH MACHINATION"). Gated on the text reading like
    # a title/heading so it doesn't fire on ordinary sentences that
    # happen to start with a number word ("Four days later...").
    words = stripped.lower().replace("-", " ").split()
    lead = _leading_spelled_number(words)
    if lead is not None and _looks_titleish(stripped):
        _, number, style = lead
        return style, number, False

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


def extract_candidates(href: str, tree) -> list:
    """Scan one chapter's document tree for chapter-marker candidates."""
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

        classified = _classify(text)
        index += 1
        if classified is None:
            continue

        style, number, label_prefix = classified
        cand = ChapterCandidate(
            href=href,
            tag=tag,
            text=text,
            element_index=index,
            style=style,
            number=number,
            label_prefix=label_prefix,
            isolated=True,
            is_heading_tag=tag.startswith("h") and len(tag) == 2,
            css_hint=_css_mentions_chapter(el),
        )
        cand.score = _score_candidate(cand)
        candidates.append(cand)

    return candidates


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
# Sequence validation
# ---------------------------------------------------------------------

# How far ahead a sequence is allowed to jump between confirmed markers
# (e.g. a missed chapter, or a non-numbered interlude) before it's no
# longer considered part of the same run.
MAX_SEQUENCE_GAP = 2

# Shortest run length that counts as a believable chapter sequence.
# Below this, an increasing run of two numbers is too easily coincidence.
MIN_SEQUENCE_LENGTH = 3


def _find_best_sequence(candidates: list) -> ChapterSequence | None:
    """
    Longest-increasing-run search, grouped by marker style (a book
    normally sticks to one numbering style throughout). Within a style,
    finds the longest run of candidates, in book order, where each
    number is greater than the last by no more than MAX_SEQUENCE_GAP.
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

        # dp[i] = (run_length, predecessor_index) for the best run ending at i
        dp = [(1, -1)] * n
        for i in range(n):
            for j in range(i):
                if group[j].number is None or group[i].number is None:
                    continue
                gap = group[i].number - group[j].number
                if 1 <= gap <= MAX_SEQUENCE_GAP:
                    candidate_len = dp[j][0] + 1
                    if candidate_len > dp[i][0]:
                        dp[i] = (candidate_len, j)

        # Walk back from every ending point to collect each maximal run,
        # keep the longest one for this style.
        end_idx = max(range(n), key=lambda i: dp[i][0])
        length = dp[end_idx][0]
        if length < MIN_SEQUENCE_LENGTH:
            continue

        chain = []
        idx = end_idx
        while idx != -1:
            chain.append(group[idx])
            idx = dp[idx][1]
        chain.reverse()

        seq = ChapterSequence(style=style, length=length, candidates=chain)
        all_sequences.append(seq)
        if best is None or seq.length > best.length:
            best = seq

    all_sequences.sort(key=lambda s: s.length, reverse=True)
    return best, all_sequences


# ---------------------------------------------------------------------
# Book-level entry point
# ---------------------------------------------------------------------

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
