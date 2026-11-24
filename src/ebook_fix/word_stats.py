"""
ebook_fix.word_stats

One record per page of how each word of three letters or more is written on
that page, kept by the analysis store so Glued Word Repair can tell a word a
bad scan ran together ("thedoor") from a real compound ("fireplace") using
only how the book itself writes its words.

It is the same record as ebook_fix.name_stats, only with shorter words kept:

    lowercase word -> (capitalized in the middle of a sentence, capitalized at
                       the start of a sentence or text piece, written in
                       lowercase)

Words are read from the same text as Mid-Word Hyphen Repair
(ebook_fix.text_lines.hyphen_slots), so headings, code and style blocks are
skipped. A word with a capital anywhere but the first letter is not recorded.
A page that cannot be loaded has an empty record with `loaded=False`.
"""

from __future__ import annotations

from ebook_fix.name_stats import (
    LOWER,
    MIDDLE,
    START,
    WORD,
    PageNameStats,
    BookNameStats,
    is_mid_sentence,
    is_plain_word,
)
from ebook_fix.text_lines import hyphen_slots

# Words shorter than this are not recorded.
MIN_WORD_LENGTH = 3

__all__ = [
    "MIN_WORD_LENGTH",
    "MIDDLE",
    "START",
    "LOWER",
    "analyze_word_stats_page",
    "analyze_book_word_stats",
    "word_stats_for",
]


def analyze_word_stats_page(href, document) -> PageNameStats:
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
            entry = counts.get(word.lower())
            if entry is None:
                entry = counts[word.lower()] = [0, 0, 0]
            entry[slot] += 1
    return PageNameStats(
        href=href,
        loaded=True,
        words={key: tuple(entry) for key, entry in counts.items()},
    )


def analyze_book_word_stats(book, cache=None) -> BookNameStats:
    from ebook_fix.finding_cache import analyze_pages

    return analyze_pages(book, "word_stats", analyze_word_stats_page, BookNameStats(), cache=cache)


def word_stats_for(book, analysis=None) -> list:
    """The page records for `book` as it is now, in the order of
    `book.chapters`, from the analysis store when the report carries one."""
    store = getattr(analysis, "store", None)
    if store is not None:
        return store.findings("word_stats", book).chapters
    return analyze_book_word_stats(book).chapters
