"""
ebook_fix.modules.hyphen_leftover_repair

Joins a word that was split by a hyphen at the end of a printed line and
still carries that hyphen in the middle, like "colum-barium" for
"columbarium". Found in "Sandman Slim" (Richard Kadrey).

There is no dictionary to check a word against, so the book itself is used
as the evidence. A hyphenated pair "AAA-BBB" is joined only when all of
these hold:

- Both halves are plain lowercase letters (the first half may start with a
  capital), at least three letters each ("Pa-pa", "Do-reen" and other
  short stutters or names are left alone). All-capital words, names written
  with a capital after the hyphen and anything with three or more parts
  ("mother-in-law") are left alone.
- The first half is not a common prefix ("re", "co", "non", "self", "mid"
  and so on), because those hyphens are a spelling choice, not a leftover.
- Neither half is a word in its own right anywhere in the book. "colum"
  and "barium" never appear on their own, so "colum-barium" qualifies,
  while "bear-claws", "bow-legged", "barrel-chested", "sun-baked" and
  "gun-fighter" (the first half is an ordinary word elsewhere in the book)
  are left alone, since a real word followed by a hyphen is a compound the
  author spelled that way, not a leftover. This is deliberately strict: a
  leftover hyphen that is not joined is a small blemish, while a real
  hyphen that is removed changes the author's spelling. (Before 2026-10-18
  only one half had to be a non-word, which joined real compounds such as
  "bow-legged" and "demi-god" whenever the book also spelled them closed
  once.)
- The pair appears hyphenated only once in the whole book. A word that is
  hyphenated the same way two or more times ("orang-utans", "zig-zag",
  "Sugar-loaf") is the author's spelling; a line-break leftover is a
  one-off.
- The joined word "AAABBB" appears elsewhere in the book as an ordinary
  word, at least as often as the hyphenated pair does. The book's own
  spelling is the only evidence used, so a word that appears only once, as
  a hyphenated pair, is never touched.

A pair that fails any of these is left exactly as it is. Chapter headings,
the contents page, code and style blocks are never touched, and a pair is
only joined inside one piece of text, never across two elements.

Every occurrence of a pair is treated alike (all joined or none), so a
second run finds nothing left to do.

The words and pairs of each page come from the page records the analysis store
keeps (ebook_fix.text_lines), so only a page that holds a pair to be joined is
opened.
"""
from __future__ import annotations

from collections import Counter

from ebook_fix.config import HyphenLeftoverConfig
from ebook_fix.report import Report
from ebook_fix.text_lines import (
    CHAIN as _CHAIN,
    SPLIT_ON_HYPHEN as _SPLIT_ON_HYPHEN,
    hyphen_slots,
    text_lines_for,
)

# First halves that are real prefixes. A hyphen after one of these is the
# author's spelling, not a line-break leftover.
PREFIXES = frozenset({
    "a", "all", "an", "anglo", "anti", "auto", "bi", "co", "counter", "de",
    "dis", "down", "ex", "extra", "fore", "half", "hyper", "ill", "in",
    "inter", "intra", "ir", "mid", "mis", "multi", "near", "neo", "non",
    "off", "on", "out", "over", "post", "pre", "pro", "pseudo", "quasi",
    "re", "self", "semi", "sub", "super", "tri", "ultra", "un", "under",
    "up", "vice", "well",
})

MIN_HALF_LENGTH = 3


class HyphenLeftoverRepair:
    name = "Mid-Word Hyphen Repair"

    def __init__(self, config: HyphenLeftoverConfig | None = None):
        self.config = config or HyphenLeftoverConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        pages = self._pages(book, analysis)
        joins = self._pairs_to_join(pages)
        for chapter, _record in self._pages_to_open(pages, joins):
            for _owner, _kind, text in hyphen_slots(chapter.document):
                for match in _CHAIN.finditer(text):
                    replacement = self._replacement(match.group(0), joins)
                    if replacement is not None:
                        report.add(
                            chapter.href,
                            "Mid-word hyphen",
                            f'{chapter.href}: "{match.group(0)}" will become "{replacement}"',
                        )
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        pages = self._pages(book, analysis)
        joins = self._pairs_to_join(pages)
        if not joins:
            return report

        for chapter, _record in self._pages_to_open(pages, joins):
            for owner, kind, text in list(hyphen_slots(chapter.document)):
                changes = []

                def swap(match):
                    replacement = self._replacement(match.group(0), joins)
                    if replacement is None:
                        return match.group(0)
                    changes.append((match.group(0), replacement))
                    return replacement

                new_text = _CHAIN.sub(swap, text)
                if not changes:
                    continue
                if kind == "text":
                    owner.text = new_text
                else:
                    owner.tail = new_text
                chapter.modified = True
                for before, after in changes:
                    report.add(
                        chapter.href,
                        "Mid-word hyphen joined",
                        f'{chapter.href}: "{before}" became "{after}"',
                    )

        if report.count and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report

    # -----------------------------------------------------
    # Finding the text to read and change
    # -----------------------------------------------------

    def _pages(self, book, analysis):
        """(chapter, page record) for every page of story text in the book, in
        reading order. The records (ebook_fix.text_lines) say which words and
        hyphenated pairs each page holds, so no page is walked to find them."""
        nav = book.nav_chapter
        pages = []
        for chapter, record in zip(book.chapters, text_lines_for(book, analysis)):
            if chapter is nav:
                continue
            if getattr(chapter, "document", None) is None:
                continue
            pages.append((chapter, record))
        return pages

    @staticmethod
    def _pages_to_open(pages, joins):
        """The pages that hold at least one pair that is going to be joined.
        Every other page has nothing to report or change."""
        for chapter, record in pages:
            if any(pair in joins for pair in record.pairs):
                yield chapter, record

    # -----------------------------------------------------
    # Deciding which pairs to join
    # -----------------------------------------------------

    def _pairs_to_join(self, pages) -> set:
        """Set of (first, second) lowercase pairs that pass every check."""
        standalone: Counter = Counter()
        pairs: Counter = Counter()

        for _chapter, record in pages:
            standalone.update(record.words)
            pairs.update(record.pairs)

        joins = set()
        for (first, second), hyphenated in pairs.items():
            if len(first) < MIN_HALF_LENGTH or len(second) < MIN_HALF_LENGTH:
                continue
            if first in PREFIXES:
                continue
            if hyphenated >= 2:
                continue
            if standalone.get(first, 0) >= 1 or standalone.get(second, 0) >= 1:
                continue
            joined_count = standalone.get(first + second, 0)
            if joined_count < 1 or joined_count < hyphenated:
                continue
            joins.add((first, second))
        return joins

    @staticmethod
    def _replacement(chain: str, joins: set):
        """The joined word for a chain that should be joined, else None.
        Only a plain lowercase pair (first half may start with a capital)
        is ever changed, and the first half keeps its capital."""
        parts = _SPLIT_ON_HYPHEN.split(chain)
        if len(parts) != 2:
            return None
        first, second = parts
        if (first.lower(), second.lower()) not in joins:
            return None
        if not second.islower():
            return None
        if not (first.islower() or (first[:1].isupper() and first[1:].islower())):
            return None
        return first + second
