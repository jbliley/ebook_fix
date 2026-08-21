"""
ebook_fix.apostrophes

DOM-aware detection of a specific conversion artifact: a dropped
apostrophe glyph left behind as a plain space, splitting a word into
two separate "words" -- "don t" instead of "don't", "it s" instead
of "it's", "dog s" instead of "dog's".

Two phases, two very different risk levels (see
docs/apostrophe_repair_plan.md):

- Phase 1, CONTRACTIONS (below): matched against a closed whitelist
  of known word pairs, so this is safe to auto-repair. See
  ebook_fix.modules.apostrophe_repair.
- Phase 2, POSSESSIVES (further down): there's no closed whitelist to
  check a possessive against, since any noun can take one, and the
  shape is genuinely ambiguous on its own (it could be a possessive
  OR a plain plural that lost its space -- only a person reading the
  sentence can tell). This phase only ever flags candidates for
  manual review; nothing here is ever auto-repaired.

Uses the same text/tail tree walker as ebook_fix.whitespace and
ebook_fix.ellipsis (iter_text_slots), so protected content
(pre/code/script/style/svg/math) is skipped here too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ebook_fix.whitespace import iter_text_slots

STRAIGHT_APOSTROPHE = "'"
CURLY_APOSTROPHE = "\u2019"  # '

VALID_TARGET_STYLES = ("straight", "curly")

# Fragment -> words that legitimately precede it to form a real
# contraction. Kept deliberately narrow, especially the "s" group:
# a bare "'s" is also the possessive marker, which this module does
# not attempt to repair (see module docstring), so only words where
# the contraction reading is effectively the only plausible one are
# included.
_CONTRACTION_PAIRS = {
    "t": [
        "don", "doesn", "didn", "isn", "aren", "wasn", "weren",
        "hasn", "haven", "hadn", "couldn", "wouldn", "shouldn",
        "won", "can", "mustn", "needn", "ain",
    ],
    "ll": ["i", "you", "we", "they", "he", "she", "it", "that", "there", "who", "what"],
    "ve": ["i", "you", "we", "they", "could", "would", "should", "might", "must"],
    "re": ["you", "we", "they"],
    "d": ["i", "you", "we", "they", "he", "she", "it", "who", "there"],
    "m": ["i"],
    "s": ["it", "that", "there", "here", "what", "who", "let", "he", "she"],
}

# Fixed idioms that don't share a common fragment with anything above,
# so they're listed as explicit (first, second) pairs instead.
_IDIOM_PAIRS = [
    ("y", "all"),
    ("o", "clock"),
    ("ma", "am"),
]

# (first_word_lower, second_word_lower) -> True, built once at import
# time from the tables above.
_ALLOWED_PAIRS = frozenset(
    (word, fragment)
    for fragment, words in _CONTRACTION_PAIRS.items()
    for word in words
) | frozenset(_IDIOM_PAIRS)

# Standalone words that are archaic contractions missing their
# LEADING apostrophe -- a different bug shape from everything above:
# a dropped-apostrophe artifact at the very front of the word instead
# of a space in the middle ("Tis the season" instead of "'Tis the
# season"), so it needs its own detection, not the word-gap pattern.
#
# Kept deliberately narrower than it could be. Two real words were
# considered and excluded specifically because they're too ambiguous
# to auto-repair safely:
# - "twill" -- the archaic contraction for "it will", but ALSO a real,
#   ordinary word (the fabric weave denim is made from). Auto-adding
#   an apostrophe to every "twill" in a book about clothing or
#   textiles would be a real, not theoretical, false positive.
# - "tween" -- the archaic contraction for "between", but ALSO
#   common modern slang for the pre-teen demographic. Same problem.
# Everything kept below has no common competing meaning as an
# ordinary standalone word.
_LEADING_APOSTROPHE_WORDS = ["tis", "twas", "twere", "twould", "tisn't", "twasn't", "gainst"]

# Word boundary before AND a negative lookbehind against an apostrophe
# or opening quote mark already sitting there (straight, or either
# direction of curly single quote -- some typesetting uses an opening
# curly quote as the leading apostrophe instead of a closing one).
# Alternatives sorted longest-first so e.g. "twasn't" matches whole
# rather than stopping at "twas".
_LEADING_APOSTROPHE_RE = re.compile(
    r"(?<![\u2018\u2019'])\b(" + "|".join(sorted(_LEADING_APOSTROPHE_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Matches "word " (a word plus the single space after it), with the
# following word checked via a zero-width lookahead rather than
# consumed as part of the match. That's deliberate: a plain
# "word word" pattern would eat both words on every match, so a
# three-word chain like "yes ma am" would only ever get one chance
# to match ("yes ma"), and lose the real pair ("ma am") right after
# it. The lookahead keeps the second word available for the *next*
# match to start from instead.
#
# Exactly one space (not a tab, not a run of spaces -- a wider gap is
# more likely a different problem, see the plan doc's matching
# rules). Deliberately requires plain letters on both sides so this
# never reaches into an already-punctuated word.
#
# The trailing (?!/) guards against "s/he", "and/or", "his/her" style
# abbreviations -- real text found in one of the sample books
# ("...a user who notifies you...that s/he does not agree...") that
# would otherwise get wrongly read as "that" + "s" (-> "that's/he").
# A slash right after the second word means it's paired with a THIRD
# word via the slash, not a genuine two-word contraction gap.
_PAIR_RE = re.compile(r"\b([A-Za-z]+) (?=([A-Za-z]+)\b(?!/))")


@dataclass
class ApostropheNormalizeResult:
    text: str
    match_count: int = 0
    changed: bool = False


def normalize_apostrophes_text(text: str, apostrophe_char: str = STRAIGHT_APOSTROPHE) -> ApostropheNormalizeResult:
    """
    Pure, unit-testable normalization of one piece of text (an
    element's .text or .tail). Two independent passes, since they're
    different bug shapes (see module docstring and the comments on
    each regex above):

    1. Whitelisted "word word" gaps -> "word<apostrophe_char>word",
       preserving the original casing of both words.
    2. Standalone archaic-contraction words missing their leading
       apostrophe -> "<apostrophe_char>word", preserving casing.

    Anything not on either whitelist is left completely alone.
    """
    result = ApostropheNormalizeResult(text=text)
    if not text:
        return result
    # Fast reject: skip the (more expensive) regex work entirely for
    # text with no space AND no possible leading-apostrophe word in
    # it -- the space-gap pattern always needs a space, but the
    # leading-apostrophe pattern doesn't (a lone "Tis" with nothing
    # else in its own text node is rare but real, e.g. wrapped in its
    # own <em>).
    if " " not in text and not _LEADING_APOSTROPHE_RE.search(text):
        return result

    def _repl_pair(m: re.Match) -> str:
        first, second = m.group(1), m.group(2)
        if (first.lower(), second.lower()) not in _ALLOWED_PAIRS:
            return m.group(0)
        result.match_count += 1
        # The lookahead means `second` was never consumed by this
        # match -- only replace the "word " part with "word'",
        # leaving the second word in place right after it.
        return f"{first}{apostrophe_char}"

    def _repl_leading(m: re.Match) -> str:
        result.match_count += 1
        return f"{apostrophe_char}{m.group(0)}"

    new_text = _PAIR_RE.sub(_repl_pair, text)
    new_text = _LEADING_APOSTROPHE_RE.sub(_repl_leading, new_text)
    result.text = new_text
    result.changed = new_text != text
    return result


# ---------------------------------------------------------------------
# Report data model
# ---------------------------------------------------------------------

@dataclass
class ApostropheIssue:
    href: str = ""
    element: object = None    # live host element; not saved to the JSON cache
    attr: str = ""             # "text" or "tail"
    category: str = "Missing apostrophe (contraction)"
    before: str = ""
    after: str = ""


@dataclass
class ChapterApostropheSummary:
    href: str = ""
    match_count: int = 0
    issues: list = field(default_factory=list)   # [ApostropheIssue], live refs -- see serialize.py


@dataclass
class BookApostropheSummary:
    chapters: list = field(default_factory=list)   # [ChapterApostropheSummary]

    @property
    def total_match_count(self) -> int:
        return sum(c.match_count for c in self.chapters)

    @property
    def total_issue_count(self) -> int:
        return sum(len(c.issues) for c in self.chapters)

    @property
    def chapters_with_issues(self) -> list:
        return [c.href for c in self.chapters if c.issues]


def analyze_chapter_apostrophes(href: str, tree) -> ChapterApostropheSummary:
    summary = ChapterApostropheSummary(href=href)
    if tree is None:
        return summary

    for host, attr, text, protected in iter_text_slots(tree):
        if protected:
            continue

        # Analysis always normalizes toward the straight apostrophe to
        # find everything there is to find and capture a stable
        # `before`; the repair module recomputes the actual `after`
        # using whatever apostrophe character the config/book style
        # resolves to -- same pattern as ebook_fix.ellipsis.
        result = normalize_apostrophes_text(text, apostrophe_char=STRAIGHT_APOSTROPHE)
        if not result.changed:
            continue

        summary.match_count += result.match_count
        summary.issues.append(
            ApostropheIssue(
                href=href, element=host, attr=attr,
                before=text, after=result.text,
            )
        )

    return summary


def analyze_book_apostrophes(book) -> BookApostropheSummary:
    summary = BookApostropheSummary()
    for chapter in book.chapters:
        summary.chapters.append(
            analyze_chapter_apostrophes(getattr(chapter, "href", ""), getattr(chapter, "document", None))
        )
    return summary


# ---------------------------------------------------------------------
# Phase 2: possessive candidates (flag-only, NEVER auto-repaired)
# ---------------------------------------------------------------------
#
# Same underlying artifact as the contractions above -- a dropped
# apostrophe left behind as a plain space -- but for the possessive
# marker ("the dog s bone" -> "the dog's bone") instead of a
# contraction. This is a fundamentally different risk level: a
# contraction can be checked against a closed, known list of words
# that actually contract, but ANY noun can take a possessive, so
# there's no equivalent list to check against here.
#
# Worse, the shape is genuinely ambiguous on its own: a bare "s"
# split off from the word before it could mean the word should gain
# an apostrophe (a possessive, "dog s" -> "dog's"), OR it could mean
# the two should simply be joined with no apostrophe at all (a
# straight plural that got a stray space, "CD s" -> "CDs", "1990 s"
# -> "1990s"). Only a person reading the sentence can tell which one
# is right.
#
# So this section only ever produces PossessiveCandidate entries for
# a person to review -- see docs/apostrophe_repair_plan.md, Phase 2.
# Nothing here is wired into ApostropheRepair or any other repair
# module, and that's deliberate: PossessiveCandidate lives on its own
# BookPossessiveSummary, entirely separate from ApostropheIssue and
# BookApostropheSummary above, specifically so there's no code path
# by which a repair module could reach these and "fix" one on its
# own.

SAMPLE_PAD = 25

# Words already covered by the "s" contraction fragment above
# (it's/that's/there's/here's/what's/who's/let's/he's/she's) --
# skipped here so the exact same split isn't reported twice, once as
# a safe auto-fixable contraction and again as a manual-review
# possessive candidate.
_CONTRACTION_S_WORDS = frozenset(_CONTRACTION_PAIRS["s"])

# A word (letters and/or digits, so "1990 s" -> "1990s"/"1990's" is
# caught alongside "dog s" -> "dogs"/"dog's") followed by a single
# space and a lone "s". Two guards, both found necessary against the
# real sample books during testing, not just theoretical:
#
# - Requires 2+ characters in the first word. A single letter in
#   front is almost always a letter-spaced heading artifact ("T O M
#   S A W Y E R"), not a real word taking a possessive -- without
#   this, every letter-spaced title in a book floods the review list
#   with junk like "M's"/"Ms".
# - The trailing negative lookahead blocks a bare "s" immediately
#   followed by ./,;:-/em-dash/en-dash with no space. That shape is
#   almost always a middle initial or abbreviation ("Michael S.
#   Hart", "in S. Latitude 34°", "s/he"), not a possessive marker.
#   This is a real trade-off: a sentence that genuinely ends on a
#   possessive right before a period ("...it was the dog s.") would
#   also get skipped by this guard. That's judged the better default
#   for a review list specifically -- a list buried in abbreviation
#   noise is one nobody will actually read through to find the real
#   candidates in.
_BARE_S_RE = re.compile(r"\b([A-Za-z0-9]{2,}) ([sS])\b(?![./,;:\-\u2013\u2014])")


def _snippet(text: str, start: int, end: int) -> str:
    lo = max(0, start - SAMPLE_PAD)
    hi = min(len(text), end + SAMPLE_PAD)
    return " ".join(text[lo:hi].split())


@dataclass
class PossessiveCandidate:
    href: str = ""
    element: object = None    # live host element; not saved to the JSON cache
    attr: str = ""             # "text" or "tail"
    word: str = ""             # the word immediately before the bare "s", original casing
    context: str = ""          # short surrounding snippet, for a person to read and judge
    possessive_reading: str = ""  # e.g. "dog's" -- if this is a possessive
    plural_reading: str = ""      # e.g. "dogs"  -- if this is just a plural that lost its space


@dataclass
class ChapterPossessiveSummary:
    href: str = ""
    candidates: list = field(default_factory=list)   # [PossessiveCandidate]


@dataclass
class BookPossessiveSummary:
    chapters: list = field(default_factory=list)   # [ChapterPossessiveSummary]

    @property
    def total_candidate_count(self) -> int:
        return sum(len(c.candidates) for c in self.chapters)

    @property
    def chapters_with_candidates(self) -> list:
        return [c.href for c in self.chapters if c.candidates]


def analyze_chapter_possessives(href: str, tree) -> ChapterPossessiveSummary:
    summary = ChapterPossessiveSummary(href=href)
    if tree is None:
        return summary

    for host, attr, text, protected in iter_text_slots(tree):
        if protected or " s" not in text.lower():
            continue

        for m in _BARE_S_RE.finditer(text):
            word = m.group(1)
            if word.lower() in _CONTRACTION_S_WORDS:
                # Already reported (and safely auto-fixable) as a
                # contraction above -- don't report it a second time
                # here as a manual-review item too.
                continue

            summary.candidates.append(
                PossessiveCandidate(
                    href=href, element=host, attr=attr,
                    word=word,
                    context=_snippet(text, m.start(), m.end()),
                    possessive_reading=f"{word}'s",
                    plural_reading=f"{word}s",
                )
            )

    return summary


def analyze_book_possessives(book) -> BookPossessiveSummary:
    summary = BookPossessiveSummary()
    for chapter in book.chapters:
        summary.chapters.append(
            analyze_chapter_possessives(getattr(chapter, "href", ""), getattr(chapter, "document", None))
        )
    return summary
