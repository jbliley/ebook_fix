"""
ebook_fix.modules.recurring_name_repair

Fixes a character, place or invented name that a bad scan or OCR pass spelled
wrongly in a few places, like "Readier" for "Reacher" in "Die Trying" (Lee
Child). There is no name list and none has to be typed in: the book itself is
the dictionary.

How names are found (ebook_fix.name_stats keeps the counts):

- A name is a word of five letters or more that is capitalized in the middle
  of sentences (right after another word, a comma or a semicolon, where only a
  name or proper noun gets a capital) several times and is never written in
  lowercase anywhere in the book. "Reacher" qualifies; "clock" does not,
  because it is also written in lowercase.
- A capital at the start of a sentence, after a quote or at the start of a
  paragraph proves nothing, so it never counts toward a word being a name.

How a wrong spelling is found:

- It is a word of five letters or more that is capitalized, rare (written at
  most ten times), and could be the name as a scan would damage it: the same
  length, with every difference being a pair of letters a scan really mixes up
  (c/e/o, c/d, i/l, h/n, g/c and so on), or a group of letters read as one
  ("rn" as "m", "cl" as "d"). A single letter that a scan would not mix up
  ("Robert" for "Rupert": b is not p), a letter added or dropped ("Margraves"
  for "Margrave") or only an accent ("Kremlin" for "Krémlin") means a different
  word or another person, and is never treated as a damaged copy. This is
  deliberately strict: a book with hundreds of names has many pairs that are
  close but are different people.

A wrong spelling is replaced automatically only when every one of these holds:

- The name appears capitalized in the middle of sentences at least 12 times and
  at least 50 times as often as the wrong spelling does, so this is meant for
  the names a book is built around (its main characters and places). A name
  that appears less often is only listed for review.
- The wrong spelling is written at most 3 times in the whole book, is never
  written in lowercase (so it is not an ordinary word the author used), and
  differs from the name by at most two mixed-up letters, none of them a swap
  between two vowels ("Carrie" and "Corrie" are two people, not one error).
- No other name is nearly as close, so the choice is not a guess between two.
- The piece of text holding it does not also hold the name. "Keller said
  Kellen was late" is two people, not a damaged copy.

Everything else that looks like a damaged name, such as a word that is also an
ordinary lowercase word in the book ("Clock 17" for "Glock 17", where "clock"
is a real word) or a name that appears only a handful of times, is never
changed. It is listed by review_candidates() so a person can check it. A
wrong spelling that is also an ordinary word is only considered where it is
capitalized in the middle of a sentence, so a real "clock" or a sentence that
opens with "Clock" is never touched.

Words of fewer than five letters, capitals inside a word ("McCoy"), words in
all capitals, chapter headings, the contents page, code and style blocks are
never read or changed. Only the letters of the wrong word are replaced, so a
possessive ("Readier's") keeps its ending. Every occurrence that qualifies is
fixed, so a second run finds nothing left to do.

The counts of each page come from the page records the analysis store keeps
(ebook_fix.name_stats), so only a page that holds a suspected wrong spelling
is opened.

This module cannot repair a systematic scan error that appears many times, or
a wrong word that is a real word in a name's place and appears next to no
reference, because the book gives no evidence either way. That would need a
language model reading the sentence.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ebook_fix.config import RecurringNameConfig
from ebook_fix.name_stats import (
    LOWER,
    MIDDLE,
    START,
    WORD,
    is_capitalized,
    is_mid_sentence,
    name_stats_for,
)
from ebook_fix.report import Report
from ebook_fix.text_lines import hyphen_slots

# --- How much evidence an automatic fix needs -------------------------------

# A name capitalized in the middle of sentences this often is certain enough to
# fix a damaged copy of it; this often is enough to flag one for review.
CONFIDENT_NAME_MIDDLE = 12
REVIEW_NAME_MIDDLE = 5
# The wrong spelling may be written at most this many times.
CONFIDENT_MAX_WRONG = 3
REVIEW_MAX_WRONG = 10
# The name must appear this many times as often as the wrong spelling.
CONFIDENT_RATIO = 50
REVIEW_RATIO = 4
# The most mixed-up letters allowed (see name_distance; each costs 0.5). Review
# allows up to 1.5 when both words are at least six letters long.
CONFIDENT_DISTANCE = 1.0
REVIEW_DISTANCE = 1.5
REVIEW_LONG_WORD = 6
# A second name this close to the wrong spelling makes the choice a guess.
AMBIGUITY_GAP = 1.0

# --- What a scan tends to mix up --------------------------------------------

# Letters a scan reads as each other, in lowercase. These are the ONLY
# differences allowed between a name and its damaged copy.
#
# STRONG pairs look alike on the page, so a scan really does swap them. An
# automatic fix may use only these (and the letter groups below).
STRONG_PAIRS = frozenset(
    {
        "ce", "co", "uv", "vy", "nu", "hn", "bh", "il", "it", "lt", "ij",
        "ft", "cd", "hi", "gq", "gc", "gy",
    }
)
# WEAK pairs are two vowels. A scan does swap them, but so many real names
# differ by one vowel ("Carrie" and "Corrie", "Russie" and "Russia", "Thebes"
# and "Thobos") that the book's counts cannot tell a scan error from a
# different word. A difference like this is only ever listed for review.
WEAK_PAIRS = frozenset({"ae", "ao", "eo", "ou"})
CONFUSABLE_PAIRS = STRONG_PAIRS | WEAK_PAIRS
# Several letters a scan reads as one, and the reverse.
MULTI_LETTER = (("rn", "m"), ("cl", "d"), ("li", "h"), ("vv", "w"), ("ii", "u"))
# What a difference that is not a mix-up costs: more than any limit above.
NOT_A_MIXUP = 99.0
# What each mixed-up letter, and each folded letter group, costs.
MIXUP_COST = 0.5


def _substitution_cost(a: str, b: str, allow_weak: bool) -> float:
    if a == b:
        return 0.0
    pairs = CONFUSABLE_PAIRS if allow_weak else STRONG_PAIRS
    if (a + b) in pairs or (b + a) in pairs:
        return MIXUP_COST
    return NOT_A_MIXUP


def weighted_distance(a: str, b: str, allow_weak: bool = True) -> float:
    """The cost of turning `a` into `b` letter by letter: 0.5 for each letter a
    scan mixes up with the one in the same place, and NOT_A_MIXUP for words of
    different lengths or any other difference. With allow_weak=False only the
    strong pairs count as mix-ups."""
    if len(a) != len(b):
        return NOT_A_MIXUP
    total = 0.0
    for x, y in zip(a, b):
        total += _substitution_cost(x, y, allow_weak)
        if total >= NOT_A_MIXUP:
            return NOT_A_MIXUP
    return total


def _collapse(word: str) -> str:
    for several, one in MULTI_LETTER:
        word = word.replace(several, one)
    return word


def name_distance(a: str, b: str, allow_weak: bool = True) -> float:
    """How far apart two lowercase words are as a scan could make them. Also
    tries both words with the letter groups a scan reads as one letter ("rn" as
    "m") folded together, at a cost of 0.5. NOT_A_MIXUP (99) means a scan would
    not make this difference."""
    best = weighted_distance(a, b, allow_weak)
    folded_a, folded_b = _collapse(a), _collapse(b)
    if (folded_a, folded_b) != (a, b):
        best = min(best, MIXUP_COST + weighted_distance(folded_a, folded_b, allow_weak))
    return best


def _strip_accents(word: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c)
    )


def _could_match(a: str, b: str) -> bool:
    """Cheap test before the real comparison: a damaged word keeps the first
    two or the last two letters of the original, is not the original with
    letters added or dropped on the end ("Margraves" is not a damaged
    "Margrave") and is not the original with only its accents changed."""
    if a.startswith(b) or b.startswith(a):
        return False
    if _strip_accents(a) == _strip_accents(b):
        return False
    return a[:2] == b[:2] or a[-2:] == b[-2:]


# --- Deciding which words are names and which are damaged copies -------------


@dataclass(frozen=True)
class Suspect:
    wrong: str  # the damaged spelling, lowercase
    name: str  # the name it looks like, lowercase
    distance: float
    # True when it is safe to replace without a person looking.
    confident: bool
    # True when the wrong spelling is also written in lowercase in the book (an
    # ordinary word), so only a capitalized one in the middle of a sentence is
    # ever considered.
    lower_seen: bool
    name_count: int  # how often the name is written, capitalized
    wrong_count: int  # how often the wrong spelling is written, capitalized


def find_suspects(totals: dict) -> dict:
    """{wrong word: Suspect} for every word in `totals` (lowercase word ->
    (capitalized in the middle, capitalized at a start, lowercase) counts for
    the whole book) that looks like a damaged copy of one of the book's names."""
    names = {
        word: counts
        for word, counts in totals.items()
        if counts[MIDDLE] >= REVIEW_NAME_MIDDLE and counts[LOWER] == 0
    }
    if not names:
        return {}
    names_by_length: dict = {}
    for word in names:
        names_by_length.setdefault(len(word), []).append(word)

    suspects: dict = {}
    for word, (middle, start, lower) in totals.items():
        if word in names:
            continue
        # A word that is also an ordinary lowercase word only counts where it
        # is capitalized in the middle of a sentence.
        written = middle if lower else middle + start
        if written < 1 or written > REVIEW_MAX_WRONG:
            continue

        rivals = []
        for length in (len(word) - 1, len(word), len(word) + 1):
            for name in names_by_length.get(length, ()):
                if not _could_match(word, name):
                    continue
                limit = (
                    REVIEW_DISTANCE
                    if min(len(word), len(name)) >= REVIEW_LONG_WORD
                    else CONFIDENT_DISTANCE
                )
                distance = name_distance(word, name)
                if distance <= limit:
                    rivals.append((distance, name))
        if not rivals:
            continue
        rivals.sort()
        distance, name = rivals[0]
        name_middle = names[name][MIDDLE]
        name_total = names[name][MIDDLE] + names[name][START]
        if name_middle < REVIEW_NAME_MIDDLE or name_total < REVIEW_RATIO * written:
            continue
        ambiguous = len(rivals) > 1 and rivals[1][0] - distance < AMBIGUITY_GAP
        # An automatic fix may use only the strong, look-alike letter pairs.
        strong_distance = name_distance(word, name, allow_weak=False)
        confident = (
            not lower
            and not ambiguous
            and strong_distance <= CONFIDENT_DISTANCE
            and name_middle >= CONFIDENT_NAME_MIDDLE
            and written <= CONFIDENT_MAX_WRONG
            and name_total >= CONFIDENT_RATIO * written
        )
        suspects[word] = Suspect(
            wrong=word,
            name=name,
            distance=distance,
            confident=confident,
            lower_seen=bool(lower),
            name_count=name_total,
            wrong_count=written,
        )
    return suspects


def hits_in_text(text: str, suspects: dict):
    """(match, suspect, decision) for every suspected wrong spelling in one
    piece of text. decision is "fix" when it is safe to replace, "review" when
    it only looks wrong."""
    present = None
    for match in WORD.finditer(text):
        word = match.group(0)
        suspect = suspects.get(word.lower())
        if suspect is None or not is_capitalized(word):
            continue
        if suspect.lower_seen and not is_mid_sentence(text, match.start()):
            continue
        if present is None:
            present = {m.group(0).lower() for m in WORD.finditer(text)}
        # A piece of text that also holds the name is probably two different
        # people, not a damaged copy.
        if suspect.confident and suspect.name not in present:
            yield match, suspect, "fix"
        else:
            yield match, suspect, "review"


class RecurringNameRepair:
    name = "Recurring Name Repair"

    def __init__(self, config: RecurringNameConfig | None = None):
        self.config = config or RecurringNameConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        pages = self._pages(book, analysis)
        suspects = self._confident(self._suspects(pages))
        for chapter, _record in self._pages_to_open(pages, suspects):
            for _owner, _kind, text in hyphen_slots(chapter.document):
                for match, suspect, decision in hits_in_text(text, suspects):
                    if decision == "fix":
                        report.add(
                            chapter.href,
                            "Recurring name",
                            f'{chapter.href}: "{match.group(0)}" will become '
                            f'"{suspect.name.capitalize()}"',
                        )
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        pages = self._pages(book, analysis)
        suspects = self._confident(self._suspects(pages))
        if not suspects:
            return report

        for chapter, _record in self._pages_to_open(pages, suspects):
            for owner, kind, text in list(hyphen_slots(chapter.document)):
                changes = []
                pieces = []
                last = 0
                for match, suspect, decision in hits_in_text(text, suspects):
                    if decision != "fix":
                        continue
                    replacement = suspect.name.capitalize()
                    pieces.append(text[last:match.start()])
                    pieces.append(replacement)
                    last = match.end()
                    changes.append((match.group(0), replacement))
                if not changes:
                    continue
                pieces.append(text[last:])
                new_text = "".join(pieces)
                if kind == "text":
                    owner.text = new_text
                else:
                    owner.tail = new_text
                chapter.modified = True
                for before, after in changes:
                    report.add(
                        chapter.href,
                        "Recurring name fixed",
                        f'{chapter.href}: "{before}" became "{after}"',
                    )

        if report.count and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report

    def review_candidates(self, book, analysis=None) -> list:
        """Words that look like a damaged name but are not safe to change
        without a person looking, one dict per place: href, wrong, name,
        context, and why it was not changed. Nothing is changed."""
        if not self.config.enabled:
            return []
        pages = self._pages(book, analysis)
        suspects = self._suspects(pages)
        found = []
        for chapter, _record in self._pages_to_open(pages, suspects):
            for _owner, _kind, text in hyphen_slots(chapter.document):
                for match, suspect, decision in hits_in_text(text, suspects):
                    if decision != "review":
                        continue
                    found.append(
                        {
                            "href": chapter.href,
                            "wrong": match.group(0),
                            "name": suspect.name.capitalize(),
                            "context": " ".join(
                                text[max(0, match.start() - 30):match.end() + 30].split()
                            ),
                            "why": self._why(suspect),
                        }
                    )
        return found

    @staticmethod
    def _why(suspect: Suspect) -> str:
        if suspect.lower_seen:
            return f'"{suspect.wrong}" is also an ordinary word in this book'
        if suspect.confident:
            return "the name is in the same piece of text, so it may be another person"
        return "not enough evidence to be sure"

    # -----------------------------------------------------
    # Finding the text to read and change
    # -----------------------------------------------------

    def _pages(self, book, analysis):
        """(chapter, page record) for every page of story text in the book, in
        reading order."""
        nav = book.nav_chapter
        pages = []
        for chapter, record in zip(book.chapters, name_stats_for(book, analysis)):
            if chapter is nav:
                continue
            if getattr(chapter, "document", None) is None:
                continue
            pages.append((chapter, record))
        return pages

    @staticmethod
    def _suspects(pages) -> dict:
        totals: dict = {}
        for _chapter, record in pages:
            for word, counts in record.words.items():
                entry = totals.get(word)
                if entry is None:
                    totals[word] = list(counts)
                else:
                    entry[0] += counts[0]
                    entry[1] += counts[1]
                    entry[2] += counts[2]
        return find_suspects(totals)

    @staticmethod
    def _confident(suspects: dict) -> dict:
        return {word: s for word, s in suspects.items() if s.confident}

    @staticmethod
    def _pages_to_open(pages, suspects):
        """The pages that hold at least one suspected wrong spelling. Every
        other page has nothing to report or change."""
        if not suspects:
            return
        for chapter, record in pages:
            if not suspects.keys().isdisjoint(record.words):
                yield chapter, record
