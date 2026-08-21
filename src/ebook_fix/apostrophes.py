"""
ebook_fix.apostrophes

DOM-aware detection of a specific conversion artifact: a dropped
apostrophe glyph left behind as a plain space, splitting a
contraction into two separate "words" -- "don t" instead of "don't",
"it s" instead of "it's".

Scope (Phase 1 of docs/apostrophe_repair_plan.md): CONTRACTIONS only,
matched against a closed whitelist of known word pairs. Possessives
("the dog s bone" -> "the dog's bone") are a much higher false-
positive-risk problem -- there's no fixed word list to check a
possessive against, since any noun can take one -- and are
deliberately out of scope here. See the plan doc for why that's a
separate, later phase.

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
    element's .text or .tail). Replaces every whitelisted "word word"
    gap with "word<apostrophe_char>word", preserving the original
    casing of both words. Anything not on the whitelist is left
    completely alone.
    """
    result = ApostropheNormalizeResult(text=text)
    if not text or " " not in text:
        return result

    def _repl(m: re.Match) -> str:
        first, second = m.group(1), m.group(2)
        if (first.lower(), second.lower()) not in _ALLOWED_PAIRS:
            return m.group(0)
        result.match_count += 1
        # The lookahead means `second` was never consumed by this
        # match -- only replace the "word " part with "word'",
        # leaving the second word in place right after it.
        return f"{first}{apostrophe_char}"

    new_text = _PAIR_RE.sub(_repl, text)
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
        if protected or " " not in text:
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
