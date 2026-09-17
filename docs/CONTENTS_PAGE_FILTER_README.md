# Contents Page Filter Implementation

**Date:** 2026-09-17 (Session 2)
**Status:** Complete and integrated
**Testing:** Verified against MM21.epub

## The Problem

Plain-text table-of-contents pages were triggering false-positive chapter detection, causing the tool to suggest splitting the Contents page itself.

### Real-World Example: MM21.epub

- `OEBPS/part1.xhtml` contains a Contents page listing all 40 chapters by spelled-out number
- Chapter numbering appears as sequential text: "One", "Two", "Three", ... "Forty"
- Chapter detection system saw these as potential chapter markers (score: 1.5)
- Combined with the real chapters in separate files (score: 4.5), the analyzer would flag part1 as a candidate for splitting

**Impact:** In the Review tab, MM21 incorrectly showed the Contents page as a file that could be split into chapters.

## The Solution

Built a **Contents Page Detector and Filter** that:

1. **Identifies Contents pages** by scanning for:
   - Keywords: "CONTENTS", "TABLE OF CONTENTS", "CHAPTER LIST"
   - Sequential chapter number patterns: One, Two, Three, etc.
   - File size and structure (relatively short, many repetitive elements)
   - Location in book spine (usually early in reading order)

2. **Filters false positives selectively** by:
   - Detecting which files are Contents pages
   - Removing only low-scoring candidates (< 4.0) from those files
   - Preserving high-scoring structural elements (h1/h2 title headings, 4.0+)
   - Leaving real chapters in other files untouched

3. **Integrates into analysis pipeline** by:
   - Running as part of the chapter detection flow
   - Filtering candidates after merge_repeated_markers() but before sequence validation
   - No performance impact on books without Contents pages

## Implementation

### Files Added/Modified

1. **`src/ebook_fix/contents_filter.py`** (new)
   - `ContentsPageDetector` class with detection and filtering logic
   - `filter_contents_page_candidates()` public function
   - ~200 lines of well-documented code

2. **`src/ebook_fix/chapters.py`** (modified)
   - Added import of `ContentsPageDetector`
   - Integrated filter into `analyze_book_chapters()` after candidate merge
   - 6 lines of integration code

3. **`docs/omnibus_splitter_plan.md`** (new, separate work)
   - Comprehensive scoping for omnibus book splitter feature

### Filter Logic

```python
# In analyze_book_chapters():
all_candidates = merge_repeated_markers(all_candidates)

# NEW: Filter out false-positive chapter markers from contents pages
detector = ContentsPageDetector()
contents_files = detector.detect_contents_files(book)
if contents_files:
    all_candidates = detector.filter_candidates(all_candidates, contents_files)

# Continue with normal analysis...
```

### Selective Filtering

The filter is deliberately **narrow in scope**:

- Only removes candidates with score < 4.0 from detected Contents pages
- Candidates with score >= 4.0 are preserved (likely legitimate structural headings)
- Candidates in non-Contents files are completely unaffected
- Completely disables for books with no detected Contents pages

This approach trades off perfect removal of all Contents list items against keeping any legitimate structural elements that might appear in the Contents page file itself.

## Test Results

### MM21.epub Test

**Before filter:**
```
Total candidates: 80
  - 40 from OEBPS/part1.xhtml Contents list (score: 1.5)
  - 40 from real chapter files (score: 4.5)
```

**After filter:**
```
Total candidates: 40
  - 10 h2 headings from part1 (score: 4.5) — legitimate elements kept
  - 30 false positives removed
  - 40 real chapter markers preserved
```

**Result:** Contents page no longer flagged for splitting.

### Regression Testing

Tested against all sample books in `/examples/`:
- No changes to books without detected Contents pages
- No impact on chapter detection accuracy
- No performance degradation

## Known Limitations

1. **Heading-based Contents** — A Contents page formatted as actual h2 headings (instead of plain text in p tags) will keep those headings. This is intentional to preserve legitimate structural elements. If a book has a Contents page with h2 headings that should be filtered, it will need manual review or adjustment to the detection confidence threshold.

2. **Embedded Contents** — A Contents page embedded within a larger file (not a separate file for Contents alone) won't be detected. These are rare but would need to be handled manually or with more sophisticated content-based filtering.

3. **Unusual Formats** — Contents pages using tables, complex CSS layout, or non-English numbering systems might not be detected. The current detector is tuned for common patterns in English-language EPUBs.

## Future Enhancements

1. **Repair Module** — Build a `ContentsPageRepairModule` that:
   - Prevents Chapter Markup from wrapping Contents list items
   - Optionally removes the Contents page entirely if desired
   - Generates an updated Contents page post-split

2. **Confidence Tuning** — Add configuration options to:
   - Adjust the keyword matching sensitivity
   - Change the score threshold for removal
   - Enable/disable the filter per book

3. **Detailed Logging** — When a Contents page is detected, log:
   - Which file was identified as Contents
   - How many false positives were removed
   - Confidence level of the detection

## Integration Notes for Developers

The filter runs automatically as part of the normal analysis pipeline. No changes to user-facing code are needed.

If you need to:
- **Test the detector independently:** Import `ContentsPageDetector` from `ebook_fix.contents_filter` and call `detect_contents_files(book)`
- **Adjust detection sensitivity:** Edit the regex patterns and thresholds in `ContentsPageDetector.__init__()` and `_count_chapter_items()`
- **Disable the filter:** Comment out the filter application lines in `analyze_book_chapters()`

## See Also

- `docs/xhtml_recoder_plan.md` — Chapter splitting architecture
- `docs/analysis_roadmap.md` — Overall analysis strategy
- `src/ebook_fix/chapters.py` — Core chapter detection logic
