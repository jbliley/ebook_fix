"""
ebook_fix.name_stats

One record per page of how each longer word is written on that page, kept by
the analysis store so Recurring Name Repair can work out which words are names
without walking the book more than once.

A name is recognised from how the book itself writes it. A word that is
capitalized in the middle of sentences, again and again, and is never once
written in lowercase, is a name (or a place, or an invented term). Nobody has
to type the names in. The record keeps, for every word of five letters or more
that is written in plain lowercase or with one leading capital:

    (capitalized in the middle of a sentence, capitalized at the start of a
     sentence or text piece, written in lowercase)

"In the middle of a sentence" means the nearest character before the word, in
the same piece of text, is a letter, a digit, a comma or a semicolon. That is
the only position where a capital is real evidence of a name; a capital after a
full stop, an opening quote or at the start of a piece of text could just be
the start of a sentence, so it is counted separately and never used as proof.

Words are lowercased for the key, so "Reacher" and "REACHER" never need to be
told apart; a word with a capital anywhere but the first letter ("McCoy",
"NATO", "iPhone") is not recorded at all. Text inside headings, code, style
blocks and the page head is skipped, the same text Mid-Word Hyphen Repair
skips (ebook_fix.text_lines.hyphen_slots), so a chapter heading is never read
and never changed.

The record holds no elements. A page that cannot be loaded (document None) has
an empty record with `loaded=False`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ebook_fix.text_lines import hyphen_slots

# Words shorter than this are never read or changed. Short names ("Beck",
# "Hook") sit too close to ordinary words for the book's own counts to be
# trusted.
MIN_WORD_LENGTH = 5

# A run of letters. Digits and underscores end a word, so "Glock17" is not a
# word here (and is left alone).
WORD = re.compile(r"[^\W\d_]+")

# Slots of the (middle, start, lowercase) counts.
MIDDLE, START, LOWER = 0, 1, 2


def is_plain_word(word: str) -> bool:
    """True for a word written in lowercase or with exactly one leading
    capital ("Reacher", "reacher"). Anything else is not read."""
    return word.islower() or (word[:1].isupper() and word[1:].islower())


def is_capitalized(word: str) -> bool:
    """True for a word with one leading capital and the rest lowercase."""
    return len(word) > 1 and word[:1].isupper() and word[1:].islower()


def is_mid_sentence(text: str, start: int) -> bool:
    """True when the word that starts at `start` in `text` follows a letter, a
    digit, a comma or a semicolon (skipping spaces), so a capital there cannot
    be the start of a sentence. A word at the very start of the text, or after
    a full stop, quote, dash or bracket, is not 'in the middle'."""
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0:
        return False
    char = text[i]
    return char.isalnum() or char in ",;"


@dataclass(frozen=True)
class PageNameStats:
    href: str = ""
    loaded: bool = True
    # lowercase word -> (capitalized in the middle of a sentence, capitalized at
    # the start of a sentence or text piece, written in lowercase)
    words: dict = field(default_factory=dict)


@dataclass
class BookNameStats:
    chapters: list = field(default_factory=list)


def analyze_name_stats_page(href, document) -> PageNameStats:
    if document is None:
        return PageNameStats(href=href, loaded=False)
    counts: dict = {}
    for _owner, _kind, text in hyphen_slots(document):
        for match in WORD.finditer(text):
            word = match.group(0)
            if len(word) < MIN_WORD_LENGTH or not is_plain_word(word):
                continue
            if word.islower():
                slot = LOWER
            elif is_mid_sentence(text, match.start()):
                slot = MIDDLE
            else:
                slot = START
            key = word.lower()
            entry = counts.get(key)
            if entry is None:
                entry = counts[key] = [0, 0, 0]
            entry[slot] += 1
    return PageNameStats(
        href=href,
        loaded=True,
        words={key: tuple(entry) for key, entry in counts.items()},
    )


def analyze_book_name_stats(book, cache=None) -> BookNameStats:
    from ebook_fix.finding_cache import analyze_pages

    return analyze_pages(book, "name_stats", analyze_name_stats_page, BookNameStats(), cache=cache)


def name_stats_for(book, analysis=None) -> list:
    """The page records for `book` as it is now, in the order of
    `book.chapters`. They come from the analysis store the report carries when
    there is one, so a page that has not changed is not read again; without a
    store every page is read."""
    store = getattr(analysis, "store", None)
    if store is not None:
        return store.findings("name_stats", book).chapters
    return analyze_book_name_stats(book).chapters
