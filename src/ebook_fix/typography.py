"""
ebook_fix.typography

Descriptive text and typography analysis.

This module is purely observational -- it does not decide whether anything
is "wrong," it just records what's actually in the text so the analyzer's
report (and eventually repair modules) can reason about it. A chapter full
of straight quotes isn't a problem by itself; a *book* that's half curly
and half straight quotes is a strong signal of a patchwork conversion.

Everything here operates on plain text already extracted from a chapter's
HTML (see analyzer.py, which builds this via `"".join(tree.itertext())`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------------
# Character constants
# ---------------------------------------------------------------------

CURLY_DOUBLE_OPEN = "\u201c"   # “
CURLY_DOUBLE_CLOSE = "\u201d"  # ”
CURLY_SINGLE_OPEN = "\u2018"   # ‘
CURLY_SINGLE_CLOSE = "\u2019"  # ’ (doubles as the curly apostrophe)
STRAIGHT_DOUBLE = '"'
STRAIGHT_SINGLE = "'"
EN_DASH = "\u2013"             # –
EM_DASH = "\u2014"             # —
UNICODE_ELLIPSIS = "\u2026"    # …
NBSP = "\u00a0"
ZERO_WIDTH_SPACE = "\u200b"
ZERO_WIDTH_NON_JOINER = "\u200c"
SOFT_HYPHEN = "\u00ad"
BOM = "\ufeff"

# Fragments that commonly appear when a UTF-8 file gets decoded/re-encoded
# through Latin-1 or Windows-1252 at some point in a book's conversion
# history. Presence of these almost always means real characters (curly
# quotes, accented letters, em dashes) got mangled.
MOJIBAKE_MARKERS = [
    "\u00e2\u20ac\u201c",  # â€" family markers below cover the common cases
    "\u00e2\u20ac\u2122",  # â€™  (mangled ’)
    "\u00e2\u20ac\u0153",  # â€œ  (mangled “)
    "\u00c3\u00a9",        # Ã©   (mangled é)
    "\u00c3\u00a8",        # Ã¨   (mangled è)
    "\u00c3\u00af",        # Ã¯   (mangled ï)
    "\u00c3\u00b4",        # Ã´   (mangled ô)
    "\u00c3\u00bc",        # Ã¼   (mangled ü)
    "\u00c2\u00a0",        # Â    (mangled nbsp)
    "\u00ef\u00bb\u00bf",  # ï»¿  (UTF-8 BOM read as text)
]

SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\u201c])')
SPACING_AFTER_SENTENCE_RE = re.compile(r'[.!?]( {1,})(?=[A-Z"\u201c\u2018])')
REPEATED_PUNCT_RE = re.compile(r'([!?])\1+|\.{4,}')
ASCII_ELLIPSIS_RE = re.compile(r'(?<!\.)\.\.\.(?!\.)')
STANDALONE_HYPHEN_RE = re.compile(r'(?<!-)-(?!-)')
DOUBLE_HYPHEN_RE = re.compile(r'(?<!-)--(?!-)')
# Runs of 3+ consecutive all-caps words (crude "shouting" / OCR-artifact
# detector). Deliberately requires a run, not a single word, so acronyms
# like "NASA" or "OK" don't trip it on their own.
ALL_CAPS_RUN_RE = re.compile(r'\b[A-Z]{2,}(?:\s+[A-Z]{2,}){2,}\b')

SAMPLE_LIMIT = 5
SAMPLE_PAD = 20


def _style_from_counts(straight_count: int, curly_count: int) -> str:
    if straight_count > 0 and curly_count > 0:
        return "mixed"
    if curly_count > 0:
        return "curly"
    if straight_count > 0:
        return "straight"
    return "none"


def _sample(snippets: list, text: str, start: int, end: int) -> None:
    if len(snippets) >= SAMPLE_LIMIT:
        return
    lo = max(0, start - SAMPLE_PAD)
    hi = min(len(text), end + SAMPLE_PAD)
    snippet = " ".join(text[lo:hi].split())
    snippets.append(snippet)


# ---------------------------------------------------------------------
# Per-chapter report
# ---------------------------------------------------------------------

@dataclass
class TypographyReport:
    # Quote style (double quotes / dialogue marks only)
    straight_double_quotes: int = 0
    curly_double_quotes: int = 0
    quote_style: str = "none"  # "straight" | "curly" | "mixed" | "none"

    # Apostrophe style (tracked separately from quote marks -- it's very
    # common and often intentional for a book to use curly double-quotes
    # for dialogue while leaving contraction apostrophes as plain ' since
    # many quote-conversion scripts only target quotation marks).
    straight_apostrophes: int = 0
    curly_apostrophes: int = 0
    apostrophe_style: str = "none"  # "straight" | "curly" | "mixed" | "none"

    # Dashes
    hyphen_count: int = 0
    en_dash_count: int = 0
    em_dash_count: int = 0
    double_hyphen_count: int = 0    # "--" likely standing in for an em dash
    spaced_em_dash_count: int = 0   # " — " (space-padded em dash)

    # Spacing
    double_space_after_sentence: int = 0
    single_space_after_sentence: int = 0
    nbsp_count: int = 0

    # Ellipsis
    unicode_ellipsis_count: int = 0
    ascii_ellipsis_count: int = 0

    # Invisible / control characters
    zero_width_space_count: int = 0
    soft_hyphen_count: int = 0
    control_char_count: int = 0
    bom_found: bool = False

    # Encoding artifacts
    mojibake_count: int = 0
    mojibake_samples: list = field(default_factory=list)

    # Emphasis / shouting
    all_caps_run_count: int = 0
    all_caps_samples: list = field(default_factory=list)

    # Punctuation
    repeated_punctuation_count: int = 0
    repeated_punctuation_samples: list = field(default_factory=list)

    # Sentence structure
    sentence_count: int = 0
    avg_sentence_words: float = 0.0
    longest_sentence_words: int = 0
    shortest_sentence_words: int = 0


def analyze_text(text: str) -> TypographyReport:
    """
    Run every typography check against a block of plain text already
    extracted from a chapter (e.g. via `"".join(tree.itertext())`).
    Pure function -- does not mutate `text`, has no side effects.
    """
    r = TypographyReport()
    if not text:
        return r

    # --- Quotes (double quotes / dialogue marks) ---
    r.straight_double_quotes = text.count(STRAIGHT_DOUBLE)
    r.curly_double_quotes = text.count(CURLY_DOUBLE_OPEN) + text.count(CURLY_DOUBLE_CLOSE)
    r.quote_style = _style_from_counts(r.straight_double_quotes, r.curly_double_quotes)

    # --- Apostrophes (tracked independently -- see dataclass note above) ---
    r.straight_apostrophes = text.count(STRAIGHT_SINGLE)
    r.curly_apostrophes = text.count(CURLY_SINGLE_OPEN) + text.count(CURLY_SINGLE_CLOSE)
    r.apostrophe_style = _style_from_counts(r.straight_apostrophes, r.curly_apostrophes)

    # --- Dashes ---
    r.double_hyphen_count = len(DOUBLE_HYPHEN_RE.findall(text))
    r.hyphen_count = len(STANDALONE_HYPHEN_RE.findall(text))
    r.en_dash_count = text.count(EN_DASH)
    r.em_dash_count = text.count(EM_DASH)
    r.spaced_em_dash_count = text.count(f" {EM_DASH} ")

    # --- Spacing after sentence-ending punctuation ---
    for m in SPACING_AFTER_SENTENCE_RE.finditer(text):
        if len(m.group(1)) >= 2:
            r.double_space_after_sentence += 1
        else:
            r.single_space_after_sentence += 1
    r.nbsp_count = text.count(NBSP)

    # --- Ellipsis ---
    r.unicode_ellipsis_count = text.count(UNICODE_ELLIPSIS)
    r.ascii_ellipsis_count = len(ASCII_ELLIPSIS_RE.findall(text))

    # --- Invisible / control characters ---
    r.zero_width_space_count = text.count(ZERO_WIDTH_SPACE) + text.count(ZERO_WIDTH_NON_JOINER)
    r.soft_hyphen_count = text.count(SOFT_HYPHEN)
    r.bom_found = BOM in text
    r.control_char_count = sum(
        1 for ch in text
        if unicodedata.category(ch) == "Cc" and ch not in ("\n", "\r", "\t")
    )

    # --- Mojibake ---
    for marker in MOJIBAKE_MARKERS:
        idx = text.find(marker)
        while idx != -1:
            r.mojibake_count += 1
            _sample(r.mojibake_samples, text, idx, idx + len(marker))
            idx = text.find(marker, idx + len(marker))

    # --- All-caps runs ("shouting" / possible OCR artifact) ---
    for m in ALL_CAPS_RUN_RE.finditer(text):
        r.all_caps_run_count += 1
        _sample(r.all_caps_samples, text, m.start(), m.end())

    # --- Repeated punctuation ---
    for m in REPEATED_PUNCT_RE.finditer(text):
        r.repeated_punctuation_count += 1
        _sample(r.repeated_punctuation_samples, text, m.start(), m.end())

    # --- Sentence structure ---
    sentences = [s for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]
    r.sentence_count = len(sentences)
    if sentences:
        lengths = [len(s.split()) for s in sentences]
        r.avg_sentence_words = round(sum(lengths) / len(lengths), 1)
        r.longest_sentence_words = max(lengths)
        r.shortest_sentence_words = min(lengths)

    return r


# ---------------------------------------------------------------------
# Book-wide aggregation
# ---------------------------------------------------------------------

@dataclass
class BookTypographySummary:
    total_straight_double_quotes: int = 0
    total_curly_double_quotes: int = 0
    quote_style_inconsistent: bool = False
    straight_quote_chapters: list = field(default_factory=list)
    curly_quote_chapters: list = field(default_factory=list)
    mixed_quote_chapters: list = field(default_factory=list)

    total_straight_apostrophes: int = 0
    total_curly_apostrophes: int = 0
    apostrophe_style_inconsistent: bool = False
    straight_apostrophe_chapters: list = field(default_factory=list)
    curly_apostrophe_chapters: list = field(default_factory=list)
    mixed_apostrophe_chapters: list = field(default_factory=list)

    total_hyphen: int = 0
    total_en_dash: int = 0
    total_em_dash: int = 0
    total_double_hyphen: int = 0
    total_spaced_em_dash: int = 0

    total_double_space_after_sentence: int = 0
    total_single_space_after_sentence: int = 0
    total_nbsp: int = 0

    total_unicode_ellipsis: int = 0
    total_ascii_ellipsis: int = 0

    total_zero_width_space: int = 0
    total_soft_hyphen: int = 0
    total_control_chars: int = 0
    chapters_with_bom: list = field(default_factory=list)

    total_mojibake: int = 0
    chapters_with_mojibake: list = field(default_factory=list)

    total_all_caps_runs: int = 0
    chapters_with_all_caps_runs: list = field(default_factory=list)

    total_repeated_punctuation: int = 0
    chapters_with_repeated_punctuation: list = field(default_factory=list)


def summarize_book(chapter_reports: list) -> BookTypographySummary:
    """
    Aggregate a list of (href, TypographyReport) pairs into a book-wide
    summary, including cross-chapter consistency checks (e.g. quote style
    drift) that only make sense once every chapter has been seen.
    """
    s = BookTypographySummary()
    for href, t in chapter_reports:
        s.total_straight_double_quotes += t.straight_double_quotes
        s.total_curly_double_quotes += t.curly_double_quotes
        if t.quote_style == "straight":
            s.straight_quote_chapters.append(href)
        elif t.quote_style == "curly":
            s.curly_quote_chapters.append(href)
        elif t.quote_style == "mixed":
            s.mixed_quote_chapters.append(href)

        s.total_straight_apostrophes += t.straight_apostrophes
        s.total_curly_apostrophes += t.curly_apostrophes
        if t.apostrophe_style == "straight":
            s.straight_apostrophe_chapters.append(href)
        elif t.apostrophe_style == "curly":
            s.curly_apostrophe_chapters.append(href)
        elif t.apostrophe_style == "mixed":
            s.mixed_apostrophe_chapters.append(href)

        s.total_hyphen += t.hyphen_count
        s.total_en_dash += t.en_dash_count
        s.total_em_dash += t.em_dash_count
        s.total_double_hyphen += t.double_hyphen_count
        s.total_spaced_em_dash += t.spaced_em_dash_count

        s.total_double_space_after_sentence += t.double_space_after_sentence
        s.total_single_space_after_sentence += t.single_space_after_sentence
        s.total_nbsp += t.nbsp_count

        s.total_unicode_ellipsis += t.unicode_ellipsis_count
        s.total_ascii_ellipsis += t.ascii_ellipsis_count

        s.total_zero_width_space += t.zero_width_space_count
        s.total_soft_hyphen += t.soft_hyphen_count
        s.total_control_chars += t.control_char_count
        if t.bom_found:
            s.chapters_with_bom.append(href)

        s.total_mojibake += t.mojibake_count
        if t.mojibake_count:
            s.chapters_with_mojibake.append(href)

        s.total_all_caps_runs += t.all_caps_run_count
        if t.all_caps_run_count:
            s.chapters_with_all_caps_runs.append(href)

        s.total_repeated_punctuation += t.repeated_punctuation_count
        if t.repeated_punctuation_count:
            s.chapters_with_repeated_punctuation.append(href)

    # A book is "inconsistent" if it has a real mix of pure-straight and
    # pure-curly chapters. Chapters that are already internally mixed are
    # counted too, but don't by themselves make an otherwise-uniform book
    # "inconsistent" at the book level -- they're already flagged directly.
    s.quote_style_inconsistent = bool(s.straight_quote_chapters) and bool(s.curly_quote_chapters)
    s.apostrophe_style_inconsistent = bool(s.straight_apostrophe_chapters) and bool(s.curly_apostrophe_chapters)

    return s
