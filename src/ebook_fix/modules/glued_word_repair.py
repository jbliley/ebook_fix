"""
ebook_fix.modules.glued_word_repair

Puts the space back in two words a bad scan ran together, like "thedoor",
"hisguns", "aboutfive" or "brotherfrom". Found in several example books
(RunTogetherText, MM21, Die Trying).

There is no dictionary, so the book itself is the evidence. A word is split
only when all of these hold:

- It is a plain word (lowercase, or with one leading capital) of six letters
  or more that the book writes at most twice in total, and it is never
  capitalized in the middle of a sentence (so it is not a name).
- It splits into exactly one pair of pieces where one piece is a small
  common English word ("the", "his", "and", "from", "that", "about" and so
  on) and the other piece is a word of at least four letters that the book
  writes on its own at least 20 times and at least 10 times as often as the
  glued word.
- Each function word is only allowed on the side where it is not part of
  ordinary compounds. "the", "and", "from", "that", "this", "you" and "but"
  may end the glued word ("acrossthe", "treesand", "brotherfrom");
  "the", "his", "they", "them", "was", "had", "has", "she", "you", "but",
  "that", "this", "about", "across", "their", "would", "could", "should",
  "been", "and" and "from" may start it ("thedoor", "hisguns").
  Words that open with "with", "what", "some", "over", "under", "for" and
  similar are never split, because "withdrew", "somewhat", "overheard" and
  "forgave" are real words, and "therefrom" and "wherefrom" are left alone.
- The book is written in English: "the" appears at least 100 times.

A word that fails any of these is left exactly as it is. Real compounds
("fireplace", "nighttime", "downright") are never split, since neither piece
is one of the small function words. Chapter headings, code and style blocks
are never touched, and a word is only split inside one piece of text.
Every occurrence of a glued word is treated alike, so a second run finds
nothing left to do.

The words of each page come from the page records the analysis store keeps
(ebook_fix.word_stats), so only a page that holds a glued word is opened.
"""
from __future__ import annotations

from ebook_fix.config import GluedWordConfig
from ebook_fix.name_stats import LOWER, MIDDLE, START, WORD, is_plain_word
from ebook_fix.report import Report
from ebook_fix.text_lines import hyphen_slots
from ebook_fix.word_stats import word_stats_for

MIN_WHOLE_LENGTH = 6
MAX_WHOLE_COUNT = 2
MIN_OTHER_LENGTH = 4
MIN_OTHER_COUNT = 20
MIN_RATIO = 10
MIN_THE_COUNT = 100

START_WORDS = frozenset(
    "the his they them was had has she you but that this about across their "
    "would could should been and from".split()
)
END_WORDS = frozenset("the and from that this you but".split())

# Real words that would otherwise look like a glued pair, and the stems that
# make a real old-fashioned word with "from" ("therefrom", "wherefrom").
NOT_GLUED = frozenset({"thataway", "hisself"})
ARCHAIC_STEMS = frozenset({"there", "where", "here"})


def find_glued(totals: dict) -> dict:
    """{glued lowercase word: (first piece, second piece)} for every word the
    book's own counts prove is two words run together. `totals` maps a
    lowercase word to its (middle, start, lower) counts over the whole book."""
    the = totals.get("the")
    if the is None or sum(the) < MIN_THE_COUNT:
        return {}

    def count(word):
        entry = totals.get(word)
        return sum(entry) if entry else 0

    found = {}
    for word, counts in totals.items():
        total = sum(counts)
        if (
            len(word) < MIN_WHOLE_LENGTH
            or total > MAX_WHOLE_COUNT
            or counts[MIDDLE]
            or word in NOT_GLUED
        ):
            continue
        splits = []
        for i in range(3, len(word) - 2):
            first, second = word[:i], word[i:]
            if first in START_WORDS and _real_piece(second, total, count):
                splits.append((first, second))
            if second in END_WORDS and first not in ARCHAIC_STEMS and _real_piece(first, total, count):
                splits.append((first, second))
        if len(set(splits)) == 1:
            found[word] = splits[0]
    return found


def _real_piece(piece: str, whole_count: int, count) -> bool:
    n = count(piece)
    return len(piece) >= MIN_OTHER_LENGTH and n >= MIN_OTHER_COUNT and n >= MIN_RATIO * whole_count


def hits_in_text(text: str, glued: dict):
    """(match, replacement) for every glued word in one piece of text. The
    replacement keeps the original letters and only adds the space."""
    for match in WORD.finditer(text):
        word = match.group(0)
        pieces = glued.get(word.lower())
        if pieces is None or not is_plain_word(word):
            continue
        cut = len(pieces[0])
        yield match, f"{word[:cut]} {word[cut:]}"


class GluedWordRepair:
    name = "Glued Word Repair"

    def __init__(self, config: GluedWordConfig | None = None):
        self.config = config or GluedWordConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        pages = self._pages(book, analysis)
        glued = self._glued(pages)
        for chapter, _record in self._pages_to_open(pages, glued):
            for _owner, _kind, text in hyphen_slots(chapter.document):
                for match, replacement in hits_in_text(text, glued):
                    report.add(
                        chapter.href,
                        "Glued word",
                        f'{chapter.href}: "{match.group(0)}" will become "{replacement}"',
                    )
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        pages = self._pages(book, analysis)
        glued = self._glued(pages)
        for chapter, _record in self._pages_to_open(pages, glued):
            for owner, kind, text in list(hyphen_slots(chapter.document)):
                pieces = []
                changes = []
                last = 0
                for match, replacement in hits_in_text(text, glued):
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
                        "Glued word fixed",
                        f'{chapter.href}: "{before}" became "{after}"',
                    )
        if report.count and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report

    # -----------------------------------------------------

    def _pages(self, book, analysis):
        nav = book.nav_chapter
        pages = []
        for chapter, record in zip(book.chapters, word_stats_for(book, analysis)):
            if chapter is nav or getattr(chapter, "document", None) is None:
                continue
            pages.append((chapter, record))
        return pages

    @staticmethod
    def _glued(pages) -> dict:
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
        return find_glued(totals)

    @staticmethod
    def _pages_to_open(pages, glued):
        if not glued:
            return
        for chapter, record in pages:
            if not glued.keys().isdisjoint(record.words):
                yield chapter, record
