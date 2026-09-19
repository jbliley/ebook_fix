# ISBN Metadata Lookup — Feature Plan

**Status:** Scoped. UX decisions finalized (2026-09-18). Ready to build.

**Data Source:** Open Library API (free, no auth required)
- Returns: title, author, description, publisher, publish date, cover image URL
- No new dependencies (Python's built-in `urllib`)

## UX Flow

### Lookup Button & Alert
- Location: Metadata section of Repair tab, below the cover art section
- Button label: "Look Up Metadata"
- Text alert below button: "This will fetch book metadata online from Open Library. Requires internet connection."
- Button disabled if offline (detect via failed connection attempt)

### Lookup Strategy
1. **Try ISBN first** — if `dc:identifier` contains a valid ISBN, search by ISBN
2. **Fall back to Title+Author** — if no ISBN or ISBN lookup fails, search by title + author
3. **Handle missing data gracefully** — if title or author is missing, inform user before searching

### Results Display
- Show results in a preview panel below the button (not a popup)
- Display side-by-side comparison:
  - Left column: current EPUB metadata (title, author, publisher, date, description)
  - Right column: lookup result
  - Center: indicator (green checkmark if match, orange warning if differs)

### Comparison Logic
- **Exact match** (all comparable fields agree):
  - Show green confirmation: "Metadata verified — matches current book"
  - No action needed, dismiss panel
  
- **Partial match** (some fields differ):
  - Show orange warning: "Metadata found but differs from current book"
  - User reviews field-by-field: accept/reject each field
  - Only apply approved changes to staged metadata
  
- **No match** (ISBN not found):
  - Show error: "No metadata found for this ISBN"
  - Allow retry with title+author search

### Offline Handling
- Attempt connection check on button click (quick HTTPS HEAD request to open library)
- If offline: show error message below button: "Cannot connect to Open Library. Check your internet connection."

## Implementation Details

### New Files
- `src/ebook_fix/isbn_lookup.py` — lookup logic
  - `extract_isbn_from_epub(book)` → str or None
  - `fetch_isbn_metadata(isbn)` → dict or None
  - `fetch_title_author_metadata(title, author)` → dict or None
  - `compare_metadata(current, lookup_result)` → comparison dict

### Modified Files
- `src/gui/app.py`:
  - New route: `POST /book/<session_id>/lookup` — handles lookup request (async)
  - Returns JSON: lookup results + comparison status
  - Add `_compare_metadata()` helper to build field diff

- `src/gui/templates/repair.html`:
  - Add lookup button and alert text in metadata section
  - Add lookup results preview panel (hidden by default, shown on results)
  - Add JavaScript for async lookup request + UI update + field-by-field accept/reject

### Configuration (Future)
- `ebook_fix.toml` config option: `[isbn_lookup]` section
  - `enabled: true` (default)
  - `auto_lookup: false` (default; future: trigger on Metadata tab open)
  - `include_title_author_fallback: true` (default)
  - `auto_approve_exact_matches: false` (default)

## Error Handling

- **No internet:** Clear message, no hanging
- **ISBN invalid:** Message "Invalid ISBN format" but continue to title+author search
- **API rate-limited:** Message "Open Library is temporarily busy; try again later"
- **API error:** Message "Could not reach Open Library"
- **No metadata found:** Message "No metadata available for this book"
- **Network timeout:** 10 second timeout, graceful failure

## Testing Checklist

- Test with real ISBNs from Jacob's collection
- Test with title+author fallback (for books with no ISBN)
- Test offline scenario
- Test exact match (green confirmation flow)
- Test partial match (orange warning + field-by-field accept/reject)
- Test with old books (20+ years) with unreliable metadata
- Test with missing title or author fields
