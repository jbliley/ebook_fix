"""
ebook_fix.modules.hyphen_leftover_repair

Joins a word that was split by a hyphen at the end of a printed line and
still carries that hyphen in the middle, like "colum-barium" for
"columbarium". Found in "Sandman Slim" (Richard Kadrey).

There is no dictionary to check a word against, so the book itself is used
as the evidence. A hyphenated pair "AAA-BBB" is joined only when all of
these hold:

- Both halves are plain lowercase letters (the first half may start with a
  capital), at least two letters each. All-capital words, names written
  with a capital after the hyphen and anything with three or more parts
  ("mother-in-law") are left alone.
- The first half is not a common prefix ("re", "co", "non", "self", "mid"
  and so on), because those hyphens are a spelling choice, not a leftover.
- At least one half is not a word in its own right anywhere in the book.
  "colum" never appears on its own, so "colum-barium" qualifies, while
  "bear-claws", "gun-fighter" and "wheel-chair" (every half is an ordinary
  word elsewhere in the book) are left alone, since a hyphen between two
  real words is a spelling choice, not a leftover.
- The joined word "AAABBB" appears elsewhere in the book as an ordinary
  word, at least as often as the hyphenated pair does. The book's own
  spelling is the only evidence used, so a word that appears only once, as
  a hyphenated pair, is never touched.

A pair that fails any of these is left exactly as it is. Chapter headings,
the contents page, code and style blocks are never touched, and a pair is
only joined inside one piece of text, never across two elements.

Every occurrence of a pair is treated alike (all joined or none), so a
second run finds nothing left to do.
"""
from __future__ import annotations

import re
from collections import Counter

from ebook_fix.config import HyphenLeftoverConfig
from ebook_fix.report import Report

# The hyphen-minus (escaped so it is a plain character inside a [...] class)
# and the Unicode hyphen, written as escapes so the file stays plain ASCII.
HYPHENS = "\\-\u2010"

# A whole hyphen-joined chain of letters: "word", "well-known",
# "mother-in-law". Anything glued to other word characters, an apostrophe
# (so the pieces of "I've" and "dog's" are not mistaken for words) or a
# double hyphen does not match, so "word--word" is ignored.
_CHAIN = re.compile(
    rf"(?<![\w{HYPHENS}'\u2019])[^\W\d_]+(?:[{HYPHENS}][^\W\d_]+)*(?![\w{HYPHENS}'\u2019])"
)
_SPLIT_ON_HYPHEN = re.compile(f"[{HYPHENS}]")

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

# Text inside any of these (or inside a heading) is never changed.
_SKIPPED_ANCESTORS = frozenset({
    "head", "title", "style", "script", "pre", "code", "svg", "math",
    "h1", "h2", "h3", "h4", "h5", "h6",
})

MIN_HALF_LENGTH = 2


class HyphenLeftoverRepair:
    name = "Mid-Word Hyphen Repair"

    def __init__(self, config: HyphenLeftoverConfig | None = None):
        self.config = config or HyphenLeftoverConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        slots = list(self._slots(book))
        joins = self._pairs_to_join(slots)
        for chapter, _owner, _kind, text in slots:
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
        slots = list(self._slots(book))
        joins = self._pairs_to_join(slots)
        if not joins:
            return report

        for chapter, owner, kind, text in slots:
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

    def _slots(self, book):
        """(chapter, element, "text" or "tail", string) for every piece of
        story text in the book, in reading order."""
        nav = book.nav_chapter
        for chapter in book.chapters:
            if chapter is nav:
                continue
            document = getattr(chapter, "document", None)
            if document is None:
                continue
            for element in document.iter():
                if not isinstance(element.tag, str):
                    # A comment or processing instruction: its tail is
                    # still story text, its own text is not.
                    if element.tail and self._allowed(element.getparent()):
                        yield chapter, element, "tail", element.tail
                    continue
                if element.text and self._allowed(element):
                    yield chapter, element, "text", element.text
                if element.tail and self._allowed(element.getparent()):
                    yield chapter, element, "tail", element.tail

    @staticmethod
    def _allowed(element) -> bool:
        node = element
        while node is not None:
            if isinstance(node.tag, str) and node.tag.rsplit("}", 1)[-1].lower() in _SKIPPED_ANCESTORS:
                return False
            node = node.getparent()
        return True

    # -----------------------------------------------------
    # Deciding which pairs to join
    # -----------------------------------------------------

    def _pairs_to_join(self, slots) -> set:
        """Set of (first, second) lowercase pairs that pass every check."""
        standalone: Counter = Counter()
        pairs: Counter = Counter()

        for _chapter, _owner, _kind, text in slots:
            for match in _CHAIN.finditer(text):
                parts = _SPLIT_ON_HYPHEN.split(match.group(0))
                if len(parts) == 1:
                    standalone[parts[0].lower()] += 1
                elif len(parts) == 2:
                    pairs[(parts[0].lower(), parts[1].lower())] += 1

        joins = set()
        for (first, second), hyphenated in pairs.items():
            if len(first) < MIN_HALF_LENGTH or len(second) < MIN_HALF_LENGTH:
                continue
            if first in PREFIXES:
                continue
            if standalone.get(first, 0) >= 1 and standalone.get(second, 0) >= 1:
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
