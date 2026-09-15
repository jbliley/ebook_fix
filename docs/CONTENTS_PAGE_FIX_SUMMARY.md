# Contents Page Detection Fix

## Problem

The Case 3 chapter-split candidate detector was generating false-positive suggestions to split in-body Contents pages (plain-text listings of chapter names/numbers with no links). When a file like MM21's `part1.xhtml` contained chapter names like "One", "Two", "Three", etc. listed sequentially without being actual chapter headers, the detector read them as a sequence of unlabeled chapter markers and proposed splitting that single Contents file into multiple separate files.

## Solution

Added `_is_contents_page()` function to detect and skip Contents pages before Case 3 candidate extraction runs.

### Detection Strategy

The function uses a multi-signal approach:

1. **Filename/ID hints** - Look for paths containing `toc`, `contents`, `table`, `index`, `front`, `intro`, `about`
2. **Text pattern analysis**:
   - Minimum 8+ elements required (prevents false positives on short files)
   - Very few or no links (Contents pages have almost none)
   - Majority of lines are short (under 40 characters)
   - Very little sentence-ending punctuation
   - Multiple sequential chapter-name-like patterns

3. **Confidence gating**:
   - With filename hints: 5+ chapter patterns required (fairly confident the filename is a signal)
   - Without filename hints: Either 15+ patterns (very high confidence) OR 8+ patterns concentrated in first 75% of file

### Integration

Modified `analyze_case3_book_chapters()` to skip any detected Contents pages:

```python
for chapter in book.chapters:
    if _is_contents_page(chapter.href, chapter.document):
        continue  # Skip Contents pages
    # ... extract candidates from actual chapters
```

## Testing Results

### Contents Page Detection

- **MM21.epub**: Correctly identifies `part1.xhtml` as Contents page
- **Images-PageNumbers.epub**: No false positives (page-number-heavy split books)
- **All sample books**: Zero false positives on narrative content

### Case 3 Detection Regression

Full regression test across all 18 sample books:

| Book | Normal Detection | Case 3 Detection | Status |
|------|-----------------|------------------|--------|
| MM21.epub | 40 chapters | None (was falsely detecting part1) | FIXED |
| All others | Maintained | Maintained | OK |

## Files Modified

- `src/ebook_fix/chapters.py`
  - Added `_is_contents_page()` function (~140 lines)
  - Modified `analyze_case3_book_chapters()` to skip detected Contents pages
  - Extended chapter-name pattern regex to include up to Fifty

## Edge Cases Handled

1. **PDF-split books** (Images-PageNumbers.epub): Has very few elements per page, properly rejected as Contents pages
2. **Scattered numbers in narrative**: If < 5 patterns found, returns False
3. **Multi-language support**: English chapter names only (Spelled-out numbers, Roman numerals, Arabic numerals with "Chapter" prefix)
4. **Title pages**: Early pattern match cutoff before reaching 5 matches prevents false identification

## Known Limitations

- Detection assumes English chapter naming conventions (future work for internationalization)
- Contents pages with links interspersed won't be detected (but these would be rare, as real Contents pages typically have no links at all)
- Very short books (fewer than 8 text elements total) can't be analyzed

## Future Work

The same `_is_contents_page()` function can be reused for:
1. Linking in-body Contents pages to their corresponding chapters
2. Auto-generating metadata or navigation hints
3. Other UI features that need to distinguish Contents pages from narrative content
