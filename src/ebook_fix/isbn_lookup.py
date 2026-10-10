"""Online metadata lookup from Open Library, by ISBN or by title and author.

Nothing in here changes a book. A lookup only produces *suggestions*
(field name -> value Open Library has) that the Metadata tab shows as blue
notes under each field, with a "Use it" button; the person decides.

What this module does:
- Finds the ISBN(s) in a book's identifiers (any scheme, with the ISBN
  check digit verified so a mistyped number is reported instead of
  searched).
- Fetches the book from Open Library. Open Library's ISBN record only
  holds author *keys*, and the description lives on the separate "work"
  record, so those are fetched with follow-up requests.
- Turns the result into suggestions, leaving alone anything that already
  matches (a different capitalization or "Last, First" order is not a
  difference worth bothering anyone about).

Open Library needs no account or key. Only built-in Python modules are
used.
"""
from __future__ import annotations

import calendar
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from metadata.identifiers import analyze_book_identifiers

_BASE_URL = "https://openlibrary.org"
_USER_AGENT = "ebook-fix/1.0 (https://github.com/jbliley/ebook_fix)"
_TIMEOUT_SECONDS = 10

# The fields a lookup can suggest, named the same as the Metadata tab's
# form fields (field-title, field-author, ...).
SUGGESTED_FIELDS = ("title", "author", "publisher", "date", "description")

# How many of a book's ISBNs to try before giving up (e.g. a print ISBN
# and an ebook ISBN may both be listed; only one may be in Open Library).
_MAX_ISBNS_TRIED = 3

# Open Library keys look like /authors/OL34184A and /works/OL45804W.
# Anything else is ignored rather than put into a web address.
_AUTHOR_KEY = re.compile(r"^/authors/OL\d+A$")
_WORK_KEY = re.compile(r"^/works/OL\d+W$")


class LookupFailure(Exception):
    """The lookup could not be completed.

    code is 'offline' (could not connect), 'busy' (Open Library asked us
    to slow down) or 'error' (anything else). message is plain English
    meant to be shown to the person.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# ISBNs
# ---------------------------------------------------------------------------

def clean_isbn(value: str) -> str:
    """Strips spaces and hyphens and upper-cases a trailing x."""
    return re.sub(r"[\s-]", "", value or "").upper()


def is_valid_isbn(isbn: str) -> bool:
    """True if isbn is a real ISBN-10 or ISBN-13 (check digit included)."""
    digits = clean_isbn(isbn)
    if re.fullmatch(r"97[89]\d{10}", digits):
        total = sum(int(ch) * (3 if i % 2 else 1) for i, ch in enumerate(digits[:12]))
        return (10 - total % 10) % 10 == int(digits[12])
    if re.fullmatch(r"\d{9}[\dX]", digits):
        total = sum((10 - i) * (10 if ch == "X" else int(ch)) for i, ch in enumerate(digits))
        return total % 11 == 0
    return False


def _isbn_shape(text: str) -> Optional[str]:
    """The cleaned digits if text is shaped like an ISBN (an 'ISBN:' or
    'urn:isbn:' label is allowed), else None. Shape only, no check digit."""
    value = (text or "").strip()
    value = re.sub(r"^(urn:isbn:|isbn[\s:_-]*)", "", value, flags=re.IGNORECASE)
    digits = clean_isbn(value)
    if re.fullmatch(r"(97[89])?\d{9}[\dX]", digits):
        return digits
    return None


def find_isbn_candidates(book) -> tuple[list[str], list[str]]:
    """Reads every dc:identifier on the book and returns (valid, invalid).

    valid: ISBNs that pass the check digit, ISBN-13 first.
    invalid: ISBN-shaped numbers that fail it (almost always a typo in
    the book's own metadata), so the person can be told.

    An identifier counts whatever its scheme is labeled, because books
    often carry an ISBN with no scheme, a wrong scheme, or a 'urn:isbn:'
    prefix. Only values that pass the check digit are trusted.
    """
    valid: list[str] = []
    invalid: list[str] = []
    try:
        identifiers = analyze_book_identifiers(book).identifiers
    except Exception:
        identifiers = []

    for ident in identifiers:
        for text in (ident.normalized_value, ident.raw_value):
            digits = _isbn_shape(text)
            if digits is None:
                continue
            if is_valid_isbn(digits):
                if digits not in valid:
                    valid.append(digits)
            elif ident.matched_scheme == "ISBN" and digits not in invalid:
                # Only call it a mistyped ISBN when the book itself says
                # it is one; a random 10-digit number is not our business.
                invalid.append(digits)
            break

    valid.sort(key=lambda value: len(value) != 13)  # stable: 13-digit first
    return valid, invalid


def extract_isbn_from_epub(book) -> Optional[str]:
    """The best valid ISBN found on the book, or None."""
    valid, _invalid = find_isbn_candidates(book)
    return valid[0] if valid else None


# ---------------------------------------------------------------------------
# Talking to Open Library
# ---------------------------------------------------------------------------

def _get_json(url: str):
    """Fetches JSON. Returns None for 'not found'; raises LookupFailure
    for anything that went wrong."""
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        if err.code == 429:
            raise LookupFailure("busy", "Open Library is temporarily busy. Try again in a minute.")
        raise LookupFailure("error", f"Open Library returned an error ({err.code}). Try again later.")
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
        raise LookupFailure("offline", "Cannot connect to Open Library. Check your internet connection.")
    except ValueError:
        raise LookupFailure("error", "Open Library sent a reply that could not be read. Try again later.")


def _get_json_quietly(url: str):
    """Like _get_json, but a failure just means 'no extra detail'. Used
    for the follow-up requests (author names, work description) so one
    failed extra request does not throw away a good result."""
    try:
        return _get_json(url)
    except LookupFailure:
        return None


def _author_names(author_keys: list) -> str:
    names = []
    for key in author_keys[:3]:
        if not isinstance(key, str) or not _AUTHOR_KEY.match(key):
            continue
        data = _get_json_quietly(f"{_BASE_URL}{key}.json")
        if isinstance(data, dict):
            name = (data.get("name") or data.get("personal_name") or "").strip()
            if name:
                names.append(name)
    return " & ".join(names)


def _clean_description(value) -> str:
    """Open Library descriptions are sometimes a {'value': ...} object and
    often end with a '----------' divider, a '([source][1])' tag and link
    lines. Keeps just the description itself."""
    if isinstance(value, dict):
        value = value.get("value")
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\r\n?", "\n", value)
    text = re.split(r"\n\s*-{5,}\s*(?:\n|$)", text)[0]
    text = re.sub(r"\(\[source\]\[\d+\]\)", "", text)
    text = re.sub(r"^\[\d+\]:\s*\S+.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _work_description(work_keys: list) -> str:
    for key in work_keys[:1]:
        if isinstance(key, str) and _WORK_KEY.match(key):
            data = _get_json_quietly(f"{_BASE_URL}{key}.json")
            if isinstance(data, dict):
                return _clean_description(data.get("description"))
    return ""


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def normalize_date(text) -> str:
    """Turns Open Library's free-text dates into the form EPUBs use:
    'October 1, 1988' -> '1988-10-01', 'Oct 1988' -> '1988-10',
    '1988' -> '1988'. Anything it cannot read comes back empty."""
    value = str(text or "").strip()
    if not value:
        return ""

    def month_of(name: str) -> int:
        return _MONTHS.get(name[:3].lower(), 0)

    def build(year: str, month: int, day: int) -> str:
        """The most precise real date: year-month-day if that day exists,
        else year-month, else just the year."""
        if not 1 <= month <= 12:
            return year
        if not 1 <= day <= calendar.monthrange(int(year), month)[1]:
            return f"{year}-{month:02d}"
        return f"{year}-{month:02d}-{day:02d}"

    match = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", value)
    if match:
        year, month, day = match.groups()
        if not month:
            return year
        if not day:
            return build(year, int(month), 1)[:7] if 1 <= int(month) <= 12 else year
        return build(year, int(month), int(day))

    match = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", value)
    if match and month_of(match.group(1)):
        return build(match.group(3), month_of(match.group(1)), int(match.group(2)))

    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", value)
    if match and month_of(match.group(2)):
        return build(match.group(3), month_of(match.group(2)), int(match.group(1)))

    match = re.fullmatch(r"([A-Za-z]{3,9})\.?,?\s+(\d{4})", value)
    if match and month_of(match.group(1)):
        return f"{match.group(2)}-{month_of(match.group(1)):02d}"

    # Anything else ("c1988", "[1988?]", "Spring 1988"): just the year.
    match = re.search(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)", value)
    return match.group(1) if match else ""


def fetch_isbn_metadata(isbn: str) -> Optional[dict]:
    """Looks one ISBN up on Open Library.

    Returns a dict with title, author, publisher, date, description and
    isbn (any of them may be empty), or None if Open Library has no
    record for it. Raises LookupFailure if the lookup itself failed.
    """
    digits = clean_isbn(isbn)
    if not is_valid_isbn(digits):
        return None

    edition = _get_json(f"{_BASE_URL}/isbn/{digits}.json")
    if not isinstance(edition, dict):
        return None

    author_keys = [a.get("key") for a in edition.get("authors") or [] if isinstance(a, dict)]
    work_keys = [w.get("key") for w in edition.get("works") or [] if isinstance(w, dict)]
    publishers = edition.get("publishers") or []

    description = _clean_description(edition.get("description"))
    if not description:
        description = _work_description(work_keys)

    return {
        "title": (edition.get("title") or "").strip(),
        "author": _author_names(author_keys),
        "publisher": publishers[0].strip() if publishers and isinstance(publishers[0], str) else "",
        "date": normalize_date(edition.get("publish_date")),
        "description": description,
        "isbn": digits,
    }


def _loose(text) -> str:
    """Lower-case letters and digits only, for 'is this the same text'."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _best_search_hit(docs: list, title: str) -> dict:
    """The search result whose title matches best; the first one when
    none match exactly. Open Library already ranks by relevance."""
    wanted = _loose(title)
    for doc in docs:
        if isinstance(doc, dict) and _loose(doc.get("title")) == wanted:
            return doc
    for doc in docs:
        if isinstance(doc, dict):
            return doc
    return {}


def fetch_title_author_metadata(title: str, author: Optional[str] = None) -> Optional[dict]:
    """Searches Open Library by title (and author, if given).

    Same result shape as fetch_isbn_metadata. Search results describe the
    *book* rather than one edition, so publisher and date here are only
    a guess at the edition in hand (see build_suggestions). Returns None
    if nothing was found. Raises LookupFailure if the search failed.
    """
    title = (title or "").strip()
    if not title:
        return None

    params = {
        "title": title,
        "limit": "5",
        "fields": "key,title,author_name,publisher,first_publish_year,isbn",
    }
    if author and author.strip():
        params["author"] = author.strip()

    data = _get_json(f"{_BASE_URL}/search.json?{urllib.parse.urlencode(params)}")
    docs = data.get("docs") if isinstance(data, dict) else None
    if not docs:
        return None

    doc = _best_search_hit(docs, title)
    if not doc:
        return None

    isbns = [clean_isbn(i) for i in doc.get("isbn") or [] if isinstance(i, str)]
    isbns = [i for i in isbns if is_valid_isbn(i)]
    isbns.sort(key=lambda value: len(value) != 13)

    names = [n.strip() for n in (doc.get("author_name") or [])[:3] if isinstance(n, str) and n.strip()]
    publishers = doc.get("publisher") or []
    year = doc.get("first_publish_year")

    return {
        "title": (doc.get("title") or "").strip(),
        "author": " & ".join(names),
        "publisher": publishers[0].strip() if publishers and isinstance(publishers[0], str) else "",
        "date": str(year) if year else "",
        "description": _work_description([doc.get("key")]),
        "isbn": isbns[0] if isbns else "",
    }


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------

def _words(text) -> list:
    return sorted(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _year(text) -> str:
    match = re.search(r"\d{4}", str(text or ""))
    return match.group(0) if match else ""


def build_suggestions(current: dict, lookup: dict, edition_exact: bool) -> dict:
    """Works out which fields Open Library would change.

    current: the values on the page right now (title, author, ...).
    lookup: the dict from fetch_isbn_metadata / fetch_title_author_metadata.
    edition_exact: True for an ISBN lookup (the record is this exact
    edition), False for a title/author search (the record is only the
    same book). Edition-specific details, publisher and date, are then
    only offered to fill a blank, never to replace what the book has.

    Returns {field: suggested value}. A field is left out when Open
    Library has nothing for it, or when the page already agrees
    (ignoring capitalization, punctuation and 'Last, First' order).
    """
    suggestions: dict = {}

    def have(field: str) -> str:
        return " ".join(str(current.get(field) or "").split())

    def found(field: str) -> str:
        return str(lookup.get(field) or "").strip()

    # Title: any real difference.
    if found("title") and _loose(found("title")) != _loose(have("title")):
        suggestions["title"] = found("title")

    # Author: same people in any order or punctuation count as the same.
    if found("author") and _words(found("author")) != _words(have("author")):
        suggestions["author"] = found("author")

    # Publisher: names vary a lot ("Penguin" / "Penguin Books Ltd."), so
    # one containing the other counts as the same.
    publisher = found("publisher")
    if publisher:
        mine, theirs = _loose(have("publisher")), _loose(publisher)
        if not mine:
            suggestions["publisher"] = publisher
        elif edition_exact and mine not in theirs and theirs not in mine:
            suggestions["publisher"] = publisher

    # Date: same year counts as the same.
    date = found("date")
    if date:
        if not have("date"):
            suggestions["date"] = date
        elif edition_exact and _year(date) and _year(date) != _year(have("date")):
            suggestions["date"] = date

    # Description: only ever fills a blank; a description someone wrote
    # or cleaned up is not replaced by a catalog blurb.
    if found("description") and not have("description"):
        suggestions["description"] = found("description")

    return suggestions


# ---------------------------------------------------------------------------
# One call for the GUI
# ---------------------------------------------------------------------------

def run_lookup(search_type: str, current: dict, book=None) -> dict:
    """Runs a whole lookup and returns what the Metadata tab needs.

    search_type: 'isbn' (needs book, to read its identifiers) or
    'title_author' (uses the title and author in current).
    current: the page's present field values.

    The result always has 'status' and 'message'. status is 'success',
    'not_found', 'no_isbn', 'offline', 'busy' or 'error'. A success also
    has 'suggestions' ({field: value}) and 'found' (title, author, isbn
    of the Open Library record, for the status line).
    """
    current = {field: str((current or {}).get(field) or "") for field in SUGGESTED_FIELDS}

    try:
        if search_type == "isbn":
            valid, invalid = find_isbn_candidates(book)
            if not valid:
                if invalid:
                    message = (
                        f"This book lists the ISBN {invalid[0]}, but it fails the ISBN check digit test, "
                        "so it is probably mistyped. Try Look Up by Title/Author instead."
                    )
                else:
                    message = "No ISBN was found in this book's identifiers. Try Look Up by Title/Author instead."
                return {"status": "no_isbn", "message": message}

            result = None
            for isbn in valid[:_MAX_ISBNS_TRIED]:
                result = fetch_isbn_metadata(isbn)
                if result:
                    break
            if not result:
                return {
                    "status": "not_found",
                    "message": f"Open Library has no record for ISBN {valid[0]}. Try Look Up by Title/Author instead.",
                }
            edition_exact = True
        else:
            title = current["title"].strip()
            if not title:
                return {"status": "error", "message": "Type a title first, then search."}
            result = fetch_title_author_metadata(title, current["author"].strip() or None)
            if not result:
                return {
                    "status": "not_found",
                    "message": "Open Library found nothing for that title and author. "
                               "Check the spelling, or try the ISBN lookup.",
                }
            edition_exact = False
    except LookupFailure as failure:
        return {"status": failure.code, "message": failure.message}

    suggestions = build_suggestions(current, result, edition_exact)

    heading = f'Found "{result["title"]}"' if result["title"] else "Found a record"
    if result["author"]:
        heading += f" by {result['author']}"
    if result["isbn"]:
        heading += f" (ISBN {result['isbn']})"
    heading += " on Open Library."

    if suggestions:
        count = len(suggestions)
        tail = f" {count} suggestion{'s' if count != 1 else ''} shown under the fields above."
    else:
        tail = " Nothing to change, this page already agrees with it."

    return {
        "status": "success",
        "source": search_type,
        "found": {key: result.get(key, "") for key in ("title", "author", "isbn")},
        "suggestions": suggestions,
        "message": heading + tail,
    }
