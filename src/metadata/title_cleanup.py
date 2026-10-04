"""
metadata.title_cleanup

Recognizes a book title that is really a leftover filename, such as
"Kadrey, Richard - 01 Sandman Slim - Sandman Slim" (Author - NN Series -
Title), and works out what it should say. Found in "Sandman Slim" by
Richard Kadrey, where the title, the sort title and the author were all
filename junk.

The one rule that makes this safe: the title is only touched when its
first or last " - " separated piece IS the book's own author (in either
"Last, First" or "First Last" order). A title that merely contains a
dash ("Wolf Hall - A Novel") never matches, because nothing in it is the
author's name. Everything else here is just working out what is left
once the author is taken off:

  Author - Title                 -> Title
  Title - Author                 -> Title
  Author - 01 Series - Title     -> Title, series "Series", number 1
  Author - Series 01 - Title     -> Title, series "Series", number 1
  Author - Series #1 - Title     -> Title, series "Series", number 1
  Author - Series, Book 1 - Title-> Title, series "Series", number 1
  Author - 1 - Title             -> Title (a bare number is dropped)
  Author - Something - Title     -> "Something - Title" (author removed
                                    only; whether "Something" is a
                                    series is a guess, so it stays in
                                    the title)

A separator is a hyphen, en dash or em dash with a space on each side,
so hyphenated words and ranges are never split.

Author order is a separate, smaller rule (see fix_author_order): a
single "Last, First" author is rewritten as "First Last", Jacob's
preferred form, but only when something else confirms the comma form
was a deliberate sort name stored as a display name: the OPF's own
file-as value is the same text, or the title started with the same text.
That is what keeps a name like "Smith, Jr." or a company name with a
comma from being rearranged.

No file is read or written here. See modules/title_cleanup_repair.py for
the module that applies it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from metadata.author_names import detect_reversed_author

# A spaced hyphen, en dash or em dash.
_SEPARATOR = re.compile(r"\s+[-\u2013\u2014]\s+")

_NUMBER = r"\d{1,3}(?:\.\d{1,2})?"

# "01 Sandman Slim", "1. Sandman Slim", "01: Sandman Slim"
_NUMBER_FIRST = re.compile(rf"^(?P<index>{_NUMBER})[\s.:]+(?P<series>\S.*)$")

# "Sandman Slim 01", "Sandman Slim #1", "Sandman Slim, Book 1",
# "Sandman Slim Vol. 1"
_NUMBER_LAST = re.compile(
    rf"^(?P<series>.*?\S)[\s,]+(?:#|book\s*|vol\.?\s*|volume\s*)?(?P<index>{_NUMBER})$",
    re.IGNORECASE,
)

_NAME_WORD = re.compile(r"^[^\W\d_][\w'\u2019.\-]*$", re.UNICODE)


@dataclass(slots=True)
class TitleParse:
    title: str
    series: str = ""
    series_index: float | None = None
    # True when the author piece in the title was written "Last, First".
    author_was_comma_form: bool = False
    # Which end of the title the author piece was on, for reporting.
    author_position: str = "start"


def _fold(text: str) -> str:
    """Comparison form: no periods or commas, one space, lowercase."""
    text = re.sub(r"[.,]", " ", text or "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _is_author(piece: str, author: str) -> bool:
    if not piece or not author:
        return False
    if _fold(piece) == _fold(author):
        return True
    return detect_reversed_author(piece, author) is not None


def _has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _series_from(piece: str):
    """(series, index) if `piece` is a series name with a number, else
    None."""
    piece = piece.strip()
    for pattern in (_NUMBER_FIRST, _NUMBER_LAST):
        match = pattern.match(piece)
        if match is None:
            continue
        series = match.group("series").strip(" ,:-")
        if not _has_letters(series):
            continue
        try:
            index = float(match.group("index"))
        except ValueError:
            continue
        return series, index
    return None


def parse_filename_title(title: str, author: str) -> TitleParse | None:
    """Returns what a filename-style title should become, or None if
    this title is not one (see the module docstring for the rule)."""
    title = (title or "").strip()
    author = (author or "").strip()
    if not title or not author:
        return None

    pieces = [p.strip() for p in _SEPARATOR.split(title)]
    if len(pieces) < 2 or any(not p for p in pieces):
        return None

    if _is_author(pieces[0], author):
        position, rest, author_piece = "start", pieces[1:], pieces[0]
    elif _is_author(pieces[-1], author):
        position, rest, author_piece = "end", pieces[:-1], pieces[-1]
    else:
        return None

    series, index = "", None
    if len(rest) == 2 and position == "start":
        found = _series_from(rest[0])
        if found is not None:
            series, index = found
            rest = rest[1:]
        elif re.fullmatch(_NUMBER, rest[0]):
            # "Author - 1 - Title": a bare book number, no series name
            # to record.
            rest = rest[1:]

    new_title = " - ".join(rest).strip()
    if not new_title or not _has_letters(new_title) or len(new_title) >= len(title):
        return None

    return TitleParse(
        title=new_title,
        series=series,
        series_index=index,
        author_was_comma_form=author_piece.count(",") == 1,
        author_position=position,
    )


def author_first_last(author: str, file_as: str = "", title_confirms: bool = False) -> str | None:
    """'Last, First' -> 'First Last', or None if this author should be
    left alone.

    The comma form has to look like one person's name (exactly one
    comma, one to three plain name words on each side, no "&" or "and"),
    and has to be confirmed by `file_as` being the same text or by
    `title_confirms` (the title started with it)."""
    author = (author or "").strip()
    if author.count(",") != 1:
        return None
    last, first = (part.strip() for part in author.split(","))
    if not last or not first:
        return None
    for side in (last, first):
        words = side.split()
        if not 1 <= len(words) <= 3 or not all(_NAME_WORD.match(w) for w in words):
            return None
        if any(w.casefold() in ("and", "jr", "jr.", "sr", "sr.", "inc", "inc.", "ltd", "ltd.", "co", "co.") for w in words):
            return None
    confirmed = title_confirms or (bool(file_as.strip()) and _fold(file_as) == _fold(author))
    if not confirmed:
        return None
    return f"{first} {last}"


@dataclass(slots=True)
class FilenameSuggestion:
    """What a filename-style title and a "Last, First" author should
    become. Each field is None when it needs no change."""
    title: str | None = None
    author: str | None = None
    series: str | None = None
    series_index: float | None = None
    # The parse the title suggestion came from, if the title matched.
    parse: TitleParse | None = None

    @property
    def has_any(self) -> bool:
        return any(v is not None for v in (self.title, self.author, self.series))


def suggest_from_filename_title(
    title: str,
    author: str,
    file_as: str = "",
    single_author: bool = True,
    has_series: bool = False,
    fix_title: bool = True,
    fix_series: bool = True,
    fix_author: bool = True,
) -> FilenameSuggestion:
    """The single place that decides what a filename-style title and a
    "Last, First" author should become, so the Title Cleanup repair and
    the Metadata form's suggestion buttons can never disagree.

    `single_author` is False when the book lists more than one author
    (an author is then never rearranged). `has_series` is True when the
    book already has a series, which is never overwritten. The three
    fix_ flags mirror the Title Cleanup switches."""
    suggestion = FilenameSuggestion()
    parse = parse_filename_title(title, author)

    if parse is not None and fix_title:
        suggestion.title = parse.title
        suggestion.parse = parse
        if parse.series and fix_series and not has_series:
            suggestion.series = parse.series
            suggestion.series_index = parse.series_index

    if fix_author and author and single_author:
        confirmed_by_title = bool(parse and parse.author_was_comma_form)
        new_author = author_first_last(author, file_as, confirmed_by_title)
        if new_author is not None:
            suggestion.author = new_author

    return suggestion
