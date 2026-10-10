# ISBN Metadata Lookup: Feature Plan

**Status:** Built (2026-10-10). Replaces the first version, which never worked: the page script that was supposed to read the ISBN was a stub that always returned nothing, so "Look Up by ISBN" always said "No ISBN found".

**Data Source:** Open Library API (free, no auth required)
- No new dependencies (Python's built-in `urllib`)
- Nothing is sent unless the person clicks a Look Up button. Only the ISBN, or the title and author, leaves the computer.

## What Open Library actually returns

These facts shaped the design and are easy to get wrong:

- The ISBN record (`/isbn/<isbn>.json`) lists authors only as keys (`/authors/OL...A`), never names. Each author needs a follow-up request (`/authors/OL...A.json`).
- The ISBN record usually has no description. It lives on the "work" record (`/works/OL...W.json`), which needs its own request. Descriptions are sometimes plain text and sometimes `{"type": ..., "value": ...}`, and often end with a `----------` divider, a `([source][1])` tag, and link lines that must be trimmed.
- Dates are free text (`October 1, 1988`, `Oct 1988`, `c1988`, `1988`) and are converted to the EPUB form (`1988-10-01`, `1988-10`, `1988`).
- The search endpoint describes the book, not one edition, so its publisher and date are only a guess at the edition in hand.

## UX Flow

### Lookup box
- Location: bottom of the Metadata tab, below Series.
- Buttons: **Look Up by ISBN** and **Look Up by Title/Author**.
- Text above the buttons explains that suggestions appear in blue under the fields and nothing changes until the person clicks Use it.
- Buttons are disabled while a search is running.

### Approve first
A lookup never fills anything in. Each suggestion shows as a blue note under its field, built the same way as the "Fix This Book will change this to" notes:

- "Open Library has: <value>" with a **Use it** button and a line saying how it was found.
- Clicking Use it puts the value in the field, which then autosaves like any typed edit.
- A note disappears on its own once the field holds that value, whether the person clicked Use it or typed it.
- A small row under the lookup box offers **Use all suggestions** and **Clear suggestions**.
- These notes are separate from the Fix This Book notes and are not counted in the "Fix This Book will change N details" banner.
- Long descriptions are shortened in the note only; Use it always fills the full text.

### Lookup strategy
1. **ISBN search** reads the ISBN from the book's own identifiers (see below). If a book lists several, up to three are tried, ISBN-13 first, until one is found.
2. **Title/Author search** uses the Title and Author fields as they are on the page, including edits not yet saved. A title is required.
3. If the ISBN search finds nothing, the message points the person to the Title/Author search.

### Finding the ISBN
Reads every `dc:identifier` through the existing identifier classifier (`metadata.identifiers`), so a book with several identifiers (ISBN, ASIN, UUID, Calibre ID) is handled properly. An identifier counts as an ISBN whatever its scheme label says (a bare number, a wrong scheme, `ISBN:` or `urn:isbn:` prefixes), but only if it passes the ISBN check digit test. A number labeled as an ISBN that fails the check is reported as probably mistyped instead of being searched.

### What gets suggested
A field is only suggested when Open Library has a value and the page does not already agree:

- **Title:** any real difference. Capitalization and punctuation are ignored.
- **Author:** same people in any order count as the same (`Dahl, Roald` equals `Roald Dahl`). Up to three authors, joined with " & ".
- **Publisher:** one name containing the other counts as the same. After an ISBN search it can replace a different publisher; after a title search it only fills a blank.
- **Date:** same year counts as the same. After an ISBN search it can replace a different year; after a title search it only fills a blank.
- **Description:** only ever fills a blank, so a description someone wrote or cleaned up is never replaced by a catalog blurb.
- Series is not suggested (Open Library does not reliably have it).

### Messages
- Found: `Found "<title>" by <author> (ISBN <isbn>) on Open Library. N suggestions shown under the fields above.` Or, if nothing differs: `Nothing to change, this page already agrees with it.`
- No ISBN in the book: points to the Title/Author search.
- Mistyped ISBN: names the number and says it fails the check digit test.
- Not found: suggests the other search type.
- No internet: `Cannot connect to Open Library. Check your internet connection.`
- Rate limited: `Open Library is temporarily busy. Try again in a minute.`
- Other server problem or unreadable reply: asks the person to try again later.
- Every request has a 10 second timeout, so the page never hangs.

## Implementation

### `src/ebook_fix/isbn_lookup.py`
- `is_valid_isbn(isbn)`, `find_isbn_candidates(book)`, `extract_isbn_from_epub(book)`
- `fetch_isbn_metadata(isbn)`, `fetch_title_author_metadata(title, author)`
- `normalize_date(text)`
- `build_suggestions(current, lookup, edition_exact)`
- `run_lookup(search_type, current, book)`: runs a whole lookup and returns the status, message and suggestions for the page
- `LookupFailure`: carries a code (`offline`, `busy`, `error`) and a plain-English message

### `src/gui/app.py`
- `POST /book/<session_id>/lookup` takes `search_type` and `current` (the page's field values) and returns `status`, `message`, and on success `suggestions` and `found`. It changes nothing on the server.

### `src/gui/templates/details.html`
- One extra blue note per field (`data-lookup`), the lookup box, and the script that shows suggestions, handles Use it, Use all and Clear, and shows messages.

### Configuration (Future)
- `ebook_fix.toml` option `[isbn_lookup]`, for example `enabled` (default true), to turn the buttons off entirely.
- Optionally offer to add a found ISBN to the book's identifiers (not done yet; there is no form field for it).

## Testing Checklist

- ISBN-10, ISBN-13, hyphenated, and `X` check digits; a mistyped ISBN
- Books with a bare ISBN, a labeled ISBN, a `urn:isbn:` value, several ISBNs, and none
- Open Library answers: found, not found (404), busy (429), server error, unreadable reply, no connection, timeout
- Author names, work description, and date conversion from a real ISBN record
- Suggestions: nothing nags when only capitalization, name order, year, or publisher wording differs
- Page: notes appear, Use it fills the full text, a note clears when the field matches, Use all, Clear, buttons re-enable after any failure
- Still to try with real books from the library: the Title/Author search against live Open Library, and old books (20+ years) with unreliable metadata
