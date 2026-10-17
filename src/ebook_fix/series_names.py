"""
ebook_fix.series_names

Tidies a series name so one series is spelled one way everywhere, using
the proper name with its leading "The" (Calibre ignores "The" when
sorting, so the proper form costs nothing). Jacob's rule (2026-10-17):
"The Dark Tower" and "Dark Tower" are one series, and the name with "The"
is the one to keep.

A leading "The" cannot be guessed from the name alone, so the proper
spelling comes from names already known:

1. series_names.txt next to ebook_fix.toml: one proper name per line
   ("#" starts a comment). Wins over everything else.
2. The series already in the Calibre library the book sits in (read
   straight from metadata.db, read-only, so it works with Calibre open).

A name that matches a known one, ignoring case, a leading The/A/An,
punctuation and a trailing "Series", becomes the known spelling. With no
match the name is only given basic tidying: spacing, surrounding quotes,
a trailing "Series" word dropped ("Dark Tower Series" -> "Dark Tower"),
and an ALL CAPS name put in Title Case. Nothing is ever invented.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

NAMES_FILENAME = "series_names.txt"

_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_TRAILING_SERIES = re.compile(r"\s*[\(\[]?\bseries\b[\)\]]?\s*$", re.IGNORECASE)


def series_key(name: str) -> str:
    """Comparison form: no leading article, no trailing "Series", no
    punctuation, lowercase."""
    text = _TRAILING_SERIES.sub("", (name or "").strip())
    text = _LEADING_ARTICLE.sub("", text)
    return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip().casefold()


def _basic_tidy(name: str) -> str:
    text = re.sub(r"\s+", " ", (name or "").replace("\xa0", " ")).strip(" \t\"'“”,;:-")
    stripped = _TRAILING_SERIES.sub("", text).strip()
    if len(stripped) >= 3 and re.search(r"[^\W\d_]", stripped):
        text = stripped
    if len(text) > 3 and text.isupper():
        text = text.title()
    return text


def read_names_file(path) -> list:
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def read_calibre_series(metadata_db_path) -> list:
    """Series names in a Calibre library's metadata.db. Read-only and
    immutable, so a running Calibre is not disturbed; any problem gives
    an empty list."""
    if not metadata_db_path:
        return []
    try:
        uri = Path(metadata_db_path).resolve().as_uri() + "?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True, timeout=2) as db:
            return [row[0] for row in db.execute("SELECT name FROM series") if row[0]]
    except (sqlite3.Error, OSError, ValueError):
        return []


def known_series_names(book_source=None, names_file=None) -> dict:
    """{series_key: proper name}. The names file wins over the library."""
    known: dict = {}
    if book_source:
        try:
            from metadata.calibre_detect import detect
            ctx = detect(book_source)
            if ctx.is_calibre_managed:
                for name in read_calibre_series(ctx.metadata_db_path):
                    known.setdefault(series_key(name), name)
        except Exception:
            pass
    file_path = Path(names_file) if names_file else Path.cwd() / NAMES_FILENAME
    for name in read_names_file(file_path):
        known[series_key(name)] = name
    return known


def tidy_series_name(name: str, known: dict | None = None) -> str:
    """The proper spelling of `name`: the known name that matches it, else
    the name with basic tidying."""
    tidy = _basic_tidy(name)
    if known:
        match = known.get(series_key(tidy))
        if match:
            return match
    return tidy


def tidy_for_book(name: str, book_source=None) -> str:
    return tidy_series_name(name, known_series_names(book_source))
