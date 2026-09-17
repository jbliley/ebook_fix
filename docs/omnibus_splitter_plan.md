# Omnibus Splitter -- Planning Doc

**Status:** Scoped, not started. Scheduled as "ways down the road" (Jacob's words).
**Started:** 2026-09-17 (scoping session).

## The idea

An EPUB omnibus edition bundles multiple complete books into one file, each restarting its own chapter numbering and with its own distinct title, copyright, and often metadata. The goal is to split it into separate, complete, valid EPUB files -- one per included book -- where each output looks like a standalone publication, not a cannibalized chapter extraction.

This is fundamentally different from the chapter-splitting work in `xhtml_recoder_plan.md`. That work edits *one EPUB in place*, reorganizing chapters within the same file. This feature *produces multiple independent EPUBs* from a single input, each with its own complete manifest, spine, metadata, OPF document, and cover/title/copyright pages.

Jacob's actual motivation: for a 50+ book series still expanding, he wants each entry as its own file in his Calibre library, not bundled 2-to-a-file omnibus editions. Making each output "look like it was always a single book" ensures the split output is indistinguishable from having purchased each book separately -- users won't see "this looks like it came from a different edition" when they open the book.

## Real example: Mountain Man omnibus

File: `MM5_Complete.epub` (in `examples/`).

**Structure:**
- Title page: "CREED OF THE MOUNTAIN MAN / GUNS OF THE MOUNTAIN MAN"
- Book One: `Creed of the Mountain Man` (chapters 1-31 + Author's Note)
- Book Two: `Guns of the Mountain Man` (chapters 1-32, renumbered from the original chapters 32-63 in the omnibus)
- Combined copyright page: two separate copyright blocks, one per book
- Shared footnotes file with cross-book references
- Single cover image (represents the omnibus, not either individual book)

**Detection signals:**
- In-body Contents page uses `class="toc_entry_part"` dividers; each book's title lives on a dedicated one-line spine file (`<div class="title-part">GUNS OF THE MOUNTAIN MAN</div>`)
- Chapter numbering restarts at 1 for each book (Case 3 restart detection in `chapters.py`)
- Copyright page independently lists each book's title and year separately
- Two independent signals agreeing on the same split point = high confidence for the boundary

## Architecture decisions

### What gets split and what doesn't

**Per-book output includes:**
- Manifest and spine entries for that book's chapters and related assets
- OPF metadata (title, creator, date) tailored to that book
- TOC (NCX and/or nav.xhtml) entries for that book's chapters only
- Title page (cloned from original, title text customized to match that book's title)
- Copyright/legal back matter (extracted from the original, relevant block only)
- Any book-specific images, footnotes, or content files

**Shared content (cloned into each output):**
- Title page (same formatting as original, but with book's title overlaid)
- Copyright blocks (only the ones relevant to each book)
- Front matter preceding the first book (if any: dedication, foreword, etc.) -- *open question, see below*
- Footnotes/endnotes (split by reference mapping, see below)
- Standard back matter (colophon, publisher info, etc.) -- *open question*

**Not included in any output:**
- The omnibus's own metadata (the DC:title is "CREED OF THE MOUNTAIN MAN / GUNS OF THE MOUNTAIN MAN", which belongs only to the original, not either split output)
- The omnibus cover (placeholder or regenerated per book -- *open question*)

### Title page extraction and customization

**Goal:** Make each output's title page look like it was always intended for that book, not like a clipped omnibus edition.

**Process:**
1. Detect the title page in the original (usually one of the first few files)
2. For each book being produced:
   - Clone the title page file (duplicate the XHTML, reuse the images)
   - Extract that book's title from the detected omnibus split point (e.g., from the Contents divider `<div class="title-part">GUNS OF THE MOUNTAIN MAN</div>`)
   - Replace the omnibus title text *within the same XHTML markup* with just that book's title
   - If the original has a subtitle (e.g., "CREED OF THE MOUNTAIN MAN by Louis L'Amour"), keep the subtitle/author line intact and only replace the main title portion

**Edge cases:**
- Original title page is an image (scanned/OCR'd): no text to replace. Keep as-is for now, flagged for manual review if needed.
- Multiple title elements (e.g., `<h1>CREED</h1>` and `<h2>GUNS</h2>` stacked on one page): extract only the portion relevant to the book being produced. *Scoping question: how confident are we in detecting "which line goes with which book"? Conservative approach: flag for review if more than one title word appears.*
- Author/publisher info mixed with title text: preserve the non-title portions (author line, publisher logo, etc.) exactly as-is.

### Copyright block extraction

**Goal:** Each output file should show only the copyright/legal attribution relevant to that book.

**Process:**
1. Detect the copyright page in the original
2. Parse it into individual copyright blocks (one per book, often separated by visual dividers or blank space)
3. For each book:
   - Extract only that book's copyright block
   - Include the copyright year, publisher info, ISBN (if unique to that book -- *open question*)
   - Clone into the output file

**Real example from Mountain Man:**
```
Original copyright page:
  CREED OF THE MOUNTAIN MAN
  Copyright (c) 1999 by Louis L'Amour Enterprises, Inc.
  All rights reserved. Published in the United States of America
  by Pinnacle Books, Inc.
  ...
  
  GUNS OF THE MOUNTAIN MAN
  Copyright (c) 1999 by Louis L'Amour Enterprises, Inc.
  All rights reserved. Published in the United States of America
  by Pinnacle Books, Inc.
  ...
```

Output for Book One: only the CREED block, with blank space trimmed.
Output for Book Two: only the GUNS block, with blank space trimmed.

**Edge cases:**
- No per-book copyright blocks detected (whole page is generic): duplicate the same block into each output. *Open question: is this the right call, or should it be reviewed?*
- ISBN differs per book: include the ISBN for that book. If ISBN is omnibus-only, *open question: omit it, or include with a note?*
- Footnotes: see below, separate concern.

### Footnote/endnote and cross-reference mapping

**Problem:** Footnotes are often bundled into one shared file (e.g., `notes.xhtml` with all note anchors and backlinks), with references scattered across multiple chapters from both books.

**Solution:** Map each footnote back to the chapters that reference it.

**Process:**
1. Scan each book's chapters for `<a href="#note_N">` or similar footnote backlinks
2. Collect the footnote IDs referenced by each book
3. When writing Book 1's output: copy the footnotes file, but *keep only the note entries* referenced by Book 1's chapters
4. Do the same for Book 2, keeping only its referenced notes
5. Renumber footnote anchors/backlinks within each output to maintain contiguous numbering (note 1, note 2, ..., not note 1, note 3, note 7)

*Similar logic applies to any cross-references or endnotes.*

### Metadata per output file

The OPF metadata (`dc:title`, `dc:creator`, `dc:date`, etc.) in the original omnibus edition belongs only to the omnibus, not to either output book.

**Each output's OPF should reflect:**
- `dc:title`: the book's actual title (extracted from the Contents divider or title page)
- `dc:creator`: author name (preserved from original if per-book, or kept from omnibus if shared)
- `dc:date`: publication year for that book (extracted from the copyright block if available, or use omnibus year if identical)
- `dc:identifier`: the ISBN for that book *if available and distinct from the omnibus ISBN*; otherwise, no identifier (or generate a UUID) -- *open question on UUIDs*
- `dc:language`: preserved from original
- Other metadata (publisher, rights, etc.): preserved from original or extracted per-book

### Cover image handling

**Current approach:** The single cover image in the omnibus represents both books visually, but neither individual book gets a "custom" cover.

**Options (open questions, not decided yet):**
1. Reuse the omnibus cover in both outputs (simplest, but both books show the same cover in a reader's library view)
2. Generate a simple text-only cover per book (title + author on solid color -- minimal, but distinct)
3. Mark covers for manual review and let Jacob supply custom covers per book
4. Omit the cover entirely from split outputs and let Calibre/the reader use a default

**Decision pending:** Jacob's preference on effort vs. polish here. For now, assume option 1 (reuse omnibus cover) as the MVP path.

## Detection phase: identifying omnibus structure

Detection runs as part of `EPUBAnalyzer.analyze()`, separate from the repair pipeline. A book with no detected split points is not an omnibus candidate (confidence: NONE).

**Signals to detect:**

1. **Chapter numbering restarts** (already implemented in `chapters.py`):
   - Case 3 detection identifies sequences like: chapters 1..31 (Book One), then 1..32 (Book Two)
   - `_classify` recognizes part boundaries; a numbering restart inside a part is the primary signal
   - Confidence: HIGH when a restart is both detected and corroborated by a Contents divider or title-part page

2. **In-body Contents dividers** (new signal):
   - Scan the Contents page for elements like `<div class="toc_entry_part">GUNS OF THE MOUNTAIN MAN</div>`
   - Match divider text against detected chapter-restart points
   - Confidence: HIGH when the divider's position aligns with where chapter numbering restarts

3. **Copyright block separation** (new signal, forensic):
   - On the copyright page, detect multi-paragraph blocks separated by visual breaks (double newlines, explicit `<hr>`, blank `<p>` elements)
   - Each block should mention one of the book titles already identified by signals 1 or 2
   - Confidence: MEDIUM-HIGH when two blocks independently mention the same titles

**Result of detection:** An `OmnibusAnalysis` object (new, in `analyzer.py`):
```python
@dataclass
class OmnibusAnalysis:
    is_omnibus: bool                           # True if split detected
    confidence: ConfidenceLevel                # HIGH, MEDIUM, LOW, NONE
    split_points: List[OmnibusBook]            # Each book's metadata + boundaries
    
@dataclass
class OmnibusBook:
    title: str                                 # Extracted from Contents/copyright
    author: str                                # Preserved from original or extracted
    chapter_range: Tuple[int, int]             # Start/end chapters (1..31, 32..63)
    first_chapter_href: str                    # First XHTML file for this book
    last_chapter_href: str                     # Last XHTML file for this book
    copyright_block_text: str                  # Extracted from copyright page
    title_page_href: str                       # XHTML file containing title page
    footnotes_referenced: Set[str]             # footnote IDs this book uses
```

**When to flag for review (GUI Review tab):**
- Detected split points exist, but confidence is MEDIUM or below
- Split boundaries detected but copyright blocks don't cleanly align (one book has copyright, other doesn't)
- Unusual structure (more than 2 books, interleaved chapters, etc.)

## Repair phase: producing the output EPUBs

Once an omnibus is detected and the user confirms the split in the GUI Review tab (or via CLI flag `--split-omnibus`), the repair engine produces *N* complete, valid EPUB files.

### Phase 1: Title page and copyright extraction (MVP)

- Clone the original title page per book, customize title text
- Extract and clone copyright blocks per book
- Verify each output's title page and copyright page render correctly (no broken images, valid XHTML)

### Phase 2: Manifest and spine reconstruction

For each book:
- Start with the original OPF manifest and spine
- Retain only the manifest entries for that book's chapters and related images/styles
- Rebuild the spine in the same order, pointing only to chapters in the book's range
- Update the manifest media-types and fallback chains to stay valid
- Rebuild NCX/nav.xhtml to include only that book's chapters

### Phase 3: Metadata and OPF customization

For each book:
- Set `dc:title` to the book's extracted title
- Set `dc:creator` to the extracted author
- Set `dc:date` to the book's publication year (if detected from copyright, else omnibus year)
- Update the unique identifier (`dc:identifier`) if a per-book ISBN was found
- Preserve language, rights, and other shared metadata

### Phase 4: Footnote extraction and renumbering

For each book:
- Identify all footnote IDs referenced by that book's chapters
- Clone the original footnotes file, strip unreferenced notes
- Renumber remaining notes to be contiguous (1, 2, 3, ...)
- Update all backlinks in chapters to point to the new numbering

### Phase 5: Output file generation

For each book:
- Create a new EPUB from scratch (new manifest, spine, OPF, NCX/nav, all cloned and customized content)
- Validate the EPUB (no broken links, correct ZIP structure, `container.xml` and OPF present)
- Save as `{original_filename}_Book1_AuthorTitle.epub`, `{original_filename}_Book2_AuthorTitle.epub`, etc.

**Naming scheme:** `{original_base}_Book{N}_{extracted_title}.epub` (example: `MM5_Complete_Book1_CreedOfTheMountainMan.epub`)

### Phase 6: Full regression testing

- Word-count diffing: sum of outputs' word counts = original (no content loss/duplication)
- Validate each output EPUB independently (ZIP structure, manifest integrity, no broken links)
- Verify each output's title page displays the correct title
- Verify each output's copyright page shows only that book's block
- Idempotency check: running the repair a second time on the split outputs should produce zero changes

## Known gaps and open questions

**Decided:**
- Title page extraction and overlay: YES, as Jacob specified
- Copyright block extraction: YES, per-book only
- Full planning doc: YES, now (this doc)

**Not yet decided:**
1. **Front matter before the first book** (dedication, foreword, generic publisher letter): Include in all outputs, only first, or exclude entirely?
2. **Shared back matter** (colophon, publisher info): Include in all outputs or only first?
3. **ISBN handling for split books**: Keep omnibus ISBN in metadata, omit it, or generate new identifiers?
4. **Cover image**: Reuse omnibus cover (current assumption), generate simple text covers per book, or flag for manual review?
5. **Confidence bar for automatic splitting**: What confidence level should trigger "split is safe enough to do without review" (HIGH only, or MEDIUM-HIGH too)?
6. **More than 2 books in one omnibus**: Current design assumes 2, but could extend. Should we scope 3+ books now, or build for 2 and generalize later?
7. **Already-split omnibus** (some chapters already in separate files): Does `splitter.py`'s existing mixed-state handling cover this, or does the omnibus feature need its own logic?

These will be decided before Phase 1 implementation begins, in a follow-up scoping session with Jacob.

## Implementation order

**If/when this is built:**

1. Expand `analyzer.py` with omnibus detection (signals 1-3 above)
2. Add `OmnibusAnalysis` to the analyzer's output report
3. Wire omnibus findings to the CLI `map-structure` command (display detected split points)
4. Wire omnibus findings to the GUI Review tab (allow acceptance/rejection of split)
5. Build `OmnibusRepair` module (all 6 phases above) in `modules/omnibus_split.py`
6. Register in `engine.py` (if omnibus detected and user approved, run before other repairs)
7. Update CLI/GUI to expose the `--split-omnibus` flag and output-file naming
8. Full regression testing on all sample books + Mountain Man examples (existing + any new test cases)
9. Document the feature in README.md and CLI help text

## Difference from chapter splitting

**Chapter splitting** (`xhtml_recoder_plan.md`):
- Edits one EPUB in place
- Breaks one large chapter file into multiple chapter files within the same EPUB
- Only the TOC (NCX/nav) and manifest change; metadata stays the same

**Omnibus splitting** (this doc):
- Produces multiple *independent* EPUBs from one input
- Each output is a complete, standalone book (new OPF, new manifest, new metadata, new title/copyright pages)
- Each output should be indistinguishable from a real, separate purchase of that book
- More architectural complexity (manifest rebuilding, metadata per-output, file cloning)

## Testing strategy

**Sample books:**
- `MM5_Complete.epub`: the real example (2 books in omnibus)
- `MM5_Incomplete.epub`: if it's already split into chapters, it's a good stress-test for the mixed-state case
- Synthetic test case: build a 3-book omnibus to verify the design generalizes beyond 2 books

**Test cases:**
1. Detect split point correctly (correct chapter range, correct titles)
2. Title page extraction and customization (text replaced, images intact)
3. Copyright block extraction (only relevant block included, formatting preserved)
4. Metadata per output (title, author, date all correct)
5. Footnote extraction and renumbering (footnotes renumbered, backlinks updated)
6. Idempotency (running repair again produces identical files)
7. Word-count diffing (outputs sum to input word count, no loss)
8. Each output validates independently (valid EPUB, no broken links)

**Regression suite:**
- All 18 existing sample books: none should be detected as omnibus (confidence NONE)
- Mountain Man examples: should be detected with HIGH confidence, split correctly, idempotent

---

This file is the source of truth for omnibus splitting. When implementation begins, decisions on open questions (1-7 above) should be documented here as dated notes, before code is written.
