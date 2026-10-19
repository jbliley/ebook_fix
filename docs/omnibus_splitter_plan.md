# Omnibus Splitter -- Planning Doc

**Status:** Detection done and verified (2026-10-01). Splitting built (2026-10-14) as the `split-omnibus` command and wired into the GUI the same day. Loose ends (per-book ISBN, copies of shared front/back matter, note renumbering) built and tested 2026-10-16. A Table of Contents page for books that have none built and tested 2026-10-18.
**Started:** 2026-09-17 (scoping session). Detection built 2026-10-01 after Jacob supplied three real
omnibus files to test against, which also led to finding two more already sitting in `examples/`
under names that didn't say "omnibus" (`OmnibusExample.epub`, and a Russian-titled First Mountain Man
file) -- five real samples in total, covering three genuinely different structures. See "Detection
phase" below for what's actually built, and the 2026-10-01 entries throughout this doc for what
changed from the original scoping.

## The idea

An EPUB omnibus edition bundles multiple complete books into one file, each restarting its own chapter numbering and with its own distinct title, copyright, and often metadata. The goal is to split it into separate, complete, valid EPUB files -- one per included book -- where each output looks like a standalone publication, not a cannibalized chapter extraction.

This is fundamentally different from the chapter-splitting work in `xhtml_recoder_plan.md`. That work edits *one EPUB in place*, reorganizing chapters within the same file. This feature *produces multiple independent EPUBs* from a single input, each with its own complete manifest, spine, metadata, OPF document, and cover/title/copyright pages.

Jacob's actual motivation: for a 50+ book series still expanding, he wants each entry as its own file in his Calibre library, not bundled 2-to-a-file omnibus editions. Making each output "look like it was always a single book" ensures the split output is indistinguishable from having purchased each book separately -- users won't see "this looks like it came from a different edition" when they open the book.

## Real examples (2026-10-01)

The original version of this section described `MM5_Complete.epub` as a two-book "Creed/Guns of the
Mountain Man" omnibus with `class="toc_entry_part"` dividers and a `title-part` div per book. That
file no longer matches that description -- it's `Law of the Mountain Man`, a single novel, already
repaired by this project's own tooling (its `contents.xhtml` carries `ebookfix-chapter-wrapper`
markup). Whether it was swapped out at some point or the original description was simply wrong isn't
known; either way, nothing in `examples/` matched it when this was checked. The real two-book
"Creed/Guns" omnibus does still exist in `examples/`, just under a different name:
`OmnibusExample.epub`.

Five real omnibus files were examined in total -- three Jacob uploaded for this (not kept in
`examples/`, since he didn't want them added permanently; ask him again if a permanent fixture is
wanted later) and two already in `examples/` under names that didn't say "omnibus" clearly enough to
have been noticed sooner. Between them they use three genuinely different structures, not the one
structure (TOC dividers + split copyright blocks) this doc originally assumed:

- **`OmnibusExample.epub`** (`examples/`) -- "Creed of the Mountain Man" / "Guns of the Mountain Man",
  2 books. Flat table of contents; chapter numbering restarts from 1 at "Guns"; the entry immediately
  before the restart ("GUNS OF THE MOUNTAIN MAN") is that book's title. No TOC divider class, no split
  copyright blocks -- just the restart itself, with a title immediately before it.
- **A Russian-titled First Mountain Man file** (`examples/`) -- "The First Mountain Man: Absaroka
  Ambush" / "Courage of the Mountain Man", 2 books. More complex: *each* book is itself divided into
  "BOOK ONE"/"BOOK TWO"/"BOOK THREE" parts, each restarting its own chapter count -- restarts that
  are **not** book boundaries, since nothing separates them but a part label, not a title. The real
  boundary between the two actual books has no restart signal driving it at all (TOC source).
- **Ravaged Land: Eventuality Series Box Set** (Jacob's upload, not kept) -- "The Wall" / "The
  Outside", 2 books. Same flat-restart shape as OmnibusExample, plus real shared front matter (a title
  page, a copyright page with no book-specific text at all, an ad page, an author's note) before the
  first book's own title.
- **Tales of Talon Box Set** (Jacob's upload, not kept) -- 4 books, 3 main plus a short bonus novella.
  Different shape entirely: the table of contents is *hierarchical*, each book's own chapters nested
  as children of that book's own top-level entry. No restart-scanning needed at all here -- the
  structure already says where each book is directly.
- **The Complete Tarzan Collection** (Jacob's upload, not kept; also the stress test for scale -- 659
  spine files, 7.4 MB) -- 25 full novels. No chapter numbers anywhere in the table of contents at all;
  each novel is its own flat top-level entry with nothing to restart. The only way to tell a real book
  entry from a stray front-matter one here is span length: a real novel's entries span tens of
  thousands of characters, a front-matter page a few hundred. One of the per-novel "title pages" here
  is a scanned pulp-magazine cover image with no text at all (`<title>Unknown</title>`) -- confirms the
  "title page might be an image" edge case this doc already anticipated.

**What this changed about the plan:** the CSS-divider-based and copyright-block-based detection this
doc originally specified doesn't match any of the five real files above -- not one of them has a
`toc_entry_part`/`title-part` class, and only `OmnibusExample.epub` even has a copyright page that
mentions book titles at all (and it mentions neither book by name there). The book's own table of
contents turned out to be the one signal present, in some form, in every real sample -- see "Detection
phase" below for what actually got built instead.

## Architecture decisions

**2026-10-01 note:** the title-page "clone + overlay text" mechanism described below turned out to be
largely unnecessary against real samples -- see finding 9 under "Known gaps and open questions" further
down. The open questions in this section (ISBN handling, cover image, shared front/back matter) were
also resolved the same day -- see the decisions list below this section for the actual answers. What
follows is kept as the original 2026-09-17 scoping, for the parts (copyright-block edge cases, the
general shape of per-book metadata) that are still relevant.

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

## Detection phase: identifying omnibus structure -- done, 2026-10-01

Built in `ebook_fix/omnibus.py` as `detect(book) -> OmnibusAnalysis`, descriptive-only in the same
style as `fonts.py`/`frontmatter.py`/`css.py` -- it finds boundaries and a confidence level; it
doesn't write anything. Works off the book's own already-parsed `book.toc` (which the parser builds
identically whether the source is an NCX or an EPUB3 nav document), not off CSS classes or copyright
text, since the real samples showed that's the one signal every omnibus structure actually carries:

- **`_detect_nested`** -- each book is already its own top-level `book.toc` entry, with that book's
  own "Chapter 1, 2, 3..." sequence nested as its children (reusing `chapters.py`'s own `_classify()`
  to recognize chapter numbers in a TOC label). No restart-scanning needed; the structure already says
  where each book is. Confidence: high. Matches Tales of Talon.
- **`_detect_restart`** -- one flat `book.toc`, chapter numbering restarts at 1 partway through. A
  restart only counts as a book boundary when the entry immediately before it is genuinely
  unclassifiable (a real standalone title) -- not itself a "Book Two"/"Part Three" label, which means
  the restart belongs to a single novel's own internal part structure, not a second work. (Confirmed
  real, not hypothetical: War and Peace restarts chapter numbering 17 times, once per "BOOK ONE"/
  "BOOK TWO"/... division, and was a real false positive before this check was added.) Confidence:
  high. Matches OmnibusExample and Ravaged Land.
- **`_detect_title_list`** -- one flat `book.toc`, no chapter numbers anywhere, but several top-level
  entries that each cover a long enough span to be a real book: at least 3 pages within that span each
  at least 2000 characters of visible text, not a cumulative total across the span (a cumulative total
  was tried first and is wrong -- a run of several short front-matter pages, none individually long,
  can add up past almost any single threshold without any of them, or the book they belong to,
  containing a single real chapter; confirmed a real false positive on the already-single-book
  MM5_Complete.epub before this was changed to require several chapter-sized pages instead).
  Project Gutenberg's own standard license text is long enough on its own to pass as a "book" this way
  (confirmed false positive on "The Call of Cthulhu"), so anything at or after the file
  ebook_fix.gutenberg's own detector already flags as Gutenberg back matter is excluded. Confidence:
  medium (a real title match, but no chapter-numbering signal to corroborate it). Matches The Complete
  Tarzan Collection and the First Mountain Man file's own two-book split (which has no restart signal
  of its own, see above).

detect() tries the three in that order (a structural signal is stronger evidence than a title-length
heuristic) and returns the first real match. Whichever matches, the first book's own span always
starts at spine index 0, regardless of where its own title marker sits -- shared front matter (a
cover, a title page, a copyright page, an author's note) goes to Book One, per Jacob's decision below,
and a naive implementation of this turned out to have a real bug: a file with no marker at all before
the first detected title (The Complete Tarzan Collection's own cover/title-page files, which have no
table-of-contents entry of their own) silently lost that content outside every book's range entirely,
rather than merely mis-attributing it.

**Verified:** all five real samples detect with the right method, the right number of books, and the
right titles; every book's range is contiguous with its neighbors and the whole set spans exactly
[0, len(book.chapters)) with nothing lost or overlapping; and a full scan of every other file in
examples/ (18+ single-book EPUBs, including War and Peace and The Call of Cthulhu, both of which
were real false positives at an earlier point in building this) produces zero false positives.

**Not yet wired anywhere:** detect() isn't called from analyzer.py, the CLI, or the GUI yet -- that
and the actual splitting/output-writing are the repair phase below, not started.

## Repair phase: built, simple version (2026-10-14)

Built as `ebook_fix/omnibus_split.py` and the `split-omnibus` command (not a pipeline module, since it writes several files from one input). Usage: `ebook-fix split-omnibus BOOK.epub [-o FOLDER] [--dry-run] [--allow-medium]`. The original is never changed. `--dry-run` lists the books and file names only. High confidence splits automatically; medium confidence stops with the list and needs `--allow-medium` (the GUI Review step for medium is not built yet).

What each output gets, following the 2026-10-01 decisions:
- Only its own book's pages. Shared front matter goes to Book One, shared back matter to the last book.
- The omnibus cover picture and cover declaration stay in every output; only Book One keeps the cover page.
- Its own title (an ALL CAPS contents label becomes Title Case) and one new unique ID (a UUID built from the source file and the book number, so the same split gives the same ID). The omnibus ISBN and sort title are dropped. Author, language, publisher, date, rights and series name are carried over; a series position is dropped, because the omnibus's number is not any one book's.
- Contents (NCX and nav) trimmed to its own entries. Pictures nothing uses are removed.
- File names: `{original}_Book{N}_{TitleInCamelCase}.epub`, with a long original name cut to 50 characters for Windows.
- Each output is checked after writing (manifest files exist, reading order valid, pages well-formed, contents entries resolve) and any problem is printed.

Found while building, not in the original scoping:
- The omnibus's own HTML contents page lists every book, so Book One would still list Book Two. On a contents-style page (three or more links to other books, each a short label) those entries are removed; any other link to another book becomes plain text.
- Finding 8 above was wrong for `OmnibusExample.epub`: it has ONE notes page serving both books (notes 1-10 from Creed, 11-23 from Guns), placed at the very end. A page that three or more links from a book's chapters point at is "used" by that book. Used by one other book only: it moves there. Used by several: each keeps its own copy, trimmed to the notes that book uses. Note numbers are not renumbered, so Book Two's notes start at 11.

Verified: `OmnibusExample.epub` (high, 2 books) and the Russian-titled First Mountain Man file (medium, needs `--allow-medium`) split into the right books with zero broken links or anchors, every page's text unchanged except the contents and notes pages, and all outputs repair without errors. The other 20 examples report no omnibus. The three files Jacob uploaded earlier (Ravaged Land, Tales of Talon, the Tarzan collection) are not in `examples/`, so splitting was not tried on them (Tarzan, 25 books and 659 pages, is the scale test still to run).

Still not built at that point: GUI wiring, per-book copies of shared front/back matter, a per-book ISBN from a copyright page, note renumbering, and a contents page for Book Two and later (only Book One gets the omnibus's own). GUI wiring was done 2026-10-14 (see the end of this doc); the rest was done 2026-10-16 (see "Splitter loose ends" at the end). A contents page for Book Two and later was built 2026-10-18 (see "Contents page for books that have none" at the end of this doc).

## Repair phase: original design (kept for reference)



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

**Decided (2026-09-17):**
- Title page extraction and overlay: YES, as Jacob specified
- Copyright block extraction: YES, per-book only
- Full planning doc: YES, now (this doc)

**Decided (2026-10-01) -- Jacob confirmed every recommendation below as-is:**
1. **Front matter before the first book** (dedication, foreword, generic publisher letter): include
   only in Book One's output, not duplicated into every book.
2. **Shared back matter** (colophon, publisher info): include only in the last book's output.
3. **ISBN handling**: use a per-book ISBN if found; otherwise a generated UUID, not the omnibus's own
   ISBN reused on two separate files.
4. **Cover image**: reuse the omnibus cover for the MVP; revisit later if it's a problem in practice.
5. **Confidence bar for automatic splitting**: high only auto-splits; medium routes to the GUI Review
   tab; low (i.e. not detected at all) offers nothing.
6. **More than two books**: build for N books generally. Already true of the detection phase above --
   none of its three methods assume exactly two.
7. **Already-split omnibus**: out of scope for v1 unless a real sample turns up.

Jacob's own words on 1 and 2: this is deliberately the simple version for now -- "We may end up
recreating the front matter and copying it to the other books in the future, and same with the back
material, if only for the look and feel. Once we get this working and reliable, we can go back and
tweak it." So the repair phase below should build the simple version (shared matter goes to one book
only) rather than the original title-page-cloning/overlay design, and treat per-book front/back matter
as a later refinement once the basic split is proven reliable.

**Found while building detection, not anticipated by the original scoping:**
8. **Footnote/cross-reference splitting** (originally planned as Phase 4 of the repair phase): none of
   the five real samples examined has shared footnotes referenced across book boundaries -- the two
   self-published box sets and the Mountain Man westerns have none at all, and Tarzan's are Gutenberg-
   sourced public-domain fiction with none either. Deferred until a real sample actually needs it,
   rather than built against no evidence at all.
9. **Title page cloning and text overlay** (originally the core of "Title page extraction and
   customization" above): turned out to be largely unnecessary against real samples. Every book found
   by `_detect_restart` or `_detect_nested` already has its own standalone title/divider page as real
   spine content immediately at its own start index (e.g. Ravaged Land's "The Wall / Ravaged Land:
   Eventuality Book One by Kellee L. Greene", or Tarzan's own pulp-cover-image page) -- which the
   detection phase's own span-slicing already includes as that book's first page, with no text
   replacement needed. The one case genuinely needing something like the original cloning idea is a
   book found by `_detect_title_list` whose own "title entry" is actually a real title but whose span
   starts immediately at a chapter file with no dedicated title page of its own (not yet seen in a real
   sample either) -- worth building if and when one turns up, not before.

These two findings substantially shrink the repair phase from the original design: Phase 1 (title/
copyright extraction) and Phase 4 (footnotes) below are both mostly unneeded for v1; Phase 2
(manifest/spine reconstruction), Phase 3 (metadata), and Phase 6 (verification) are the real work.

## Implementation order

Steps 1-2 are done (as `ebook_fix/omnibus.py`'s standalone `detect()`, not as an expansion of
`analyzer.py` itself -- matching how `fonts.py`/`frontmatter.py`/`css.py` are each their own module
that `analyzer.py` calls into, not inline code within it). Remaining:

5-7 (the simple split, as a command rather than an engine module). Steps 3, 4, 8 and 9 remain.

1. ~~Expand `analyzer.py` with omnibus detection~~ -- done differently: `ebook_fix/omnibus.py`,
   called from `analyzer.py` the same way every other analysis module is.
2. ~~Add `OmnibusAnalysis` to the analyzer's output report~~ -- done; `OmnibusAnalysis`/`OmnibusBook`
   live in `omnibus.py` itself.
3. Wire `detect()`'s result into the CLI `map-structure` command (display detected split points)
4. Wire it into the GUI Review tab (medium confidence) and a plain report (high confidence)
5. Build `OmnibusRepair` module in `modules/omnibus_split.py` -- per the 2026-10-01 decisions above,
   the simple version first (shared front/back matter to one book only, no title-page cloning or
   footnote splitting, both deferred for lack of real evidence they're needed)
6. Register in `engine.py` (if omnibus detected and user approved, run before other repairs)
7. Update CLI/GUI to expose a `--split-omnibus` flag and output-file naming
8. Full regression testing -- see "Testing strategy" below, already partly done for detection
9. Document the feature in README.md and CLI help text

## Difference from chapter splitting

**Chapter splitting** (`xhtml_recoder_plan.md`):
- Edits one EPUB in place
- Breaks one large chapter file into multiple chapter files within the same EPUB
- Only the TOC (NCX/nav) and manifest change; metadata stays the same

**Omnibus splitting** (this doc):
- Produces multiple *independent* EPUBs from one input
- Each output is a complete, standalone book (new OPF, new manifest, new metadata; title/copyright
  pages carried through as part of each book's own span, not cloned/edited -- see the 2026-10-01 note
  on title-page handling above)
- Each output should be indistinguishable from a real, separate purchase of that book
- More architectural complexity (manifest rebuilding, metadata per-output)

## Testing strategy

**Sample books (updated 2026-10-01):**
- `OmnibusExample.epub` (in `examples/`): "Creed/Guns of the Mountain Man", 2 books, detected via
  `_detect_restart`. The real fixture this doc originally meant by "MM5_Complete.epub".
- The Russian-titled First Mountain Man file (in `examples/`): 2 books, detected via
  `_detect_title_list` -- also the regression case for "a restart that isn't a book boundary" (its own
  internal BOOK ONE/TWO/THREE parts).
- Three files Jacob uploaded for this and did not want kept in `examples/`: Ravaged Land (2 books,
  restart), Tales of Talon (4 books, nested), The Complete Tarzan Collection (25 books, title-list, and
  the scale stress-test at 659 spine files / 7.4 MB). Ask Jacob again if any should become permanent
  fixtures once the repair phase needs real files to split, rather than just detect against.
- `MM5_Incomplete.epub`: not an omnibus (confirmed, detection's own regression scan) -- still useful as
  a mixed-state stress test once the repair phase exists.
- *War and Peace* and *The Call of Cthulhu* (both already in `examples/`): not omnibuses, but real
  false positives at an earlier point in building detection (an internal part structure, and Project
  Gutenberg's own license text respectively) -- keep these in the regression set specifically for that
  reason, not just as generic single-book coverage.

**Test cases, detection (done):**
1. Detect split point correctly (correct chapter range, correct titles) -- verified on all 5 real
   samples
2. Zero false positives across every other file in `examples/` -- verified

**Test cases, repair (not started):**
3. Metadata per output (title, author, date all correct)
4. Idempotency (running repair again produces identical files)
5. Word-count diffing (outputs sum to input word count, no loss, no duplication)
6. Each output validates independently (valid EPUB, no broken links)
7. Shared front/back matter lands in the right one book per the 2026-10-01 decisions, not duplicated

---

This file is the source of truth for omnibus splitting. Decisions and what was actually found while
building are documented here as dated notes (see the 2026-09-17 and 2026-10-01 entries throughout).

## GUI wiring: built (2026-10-14)

- The Overview tab shows an omnibus card when a book is detected as an omnibus (high or medium confidence), with the list of books it would make.
- High confidence: one button, "Split into N separate books".
- Medium confidence: the same button, but only after ticking "These titles look right".
- New books go in a folder named `<book name> (split)` next to the original. The original is never changed.
- A result page lists each new book, its page and word counts, and any problems the built-in output check found.
- Not built yet: opening a new book straight from the result page (use Browse for now). Per-book ISBNs, per-book copies of shared front/back matter and note renumbering were added 2026-10-16 (below).

## Splitter loose ends: built (2026-10-16)

Item 2 of Jacob's 2026-10-15 list. All three changes are in `omnibus_split.py`; the only change to `omnibus.py` is that each detected book now records `title_index`, the spine position of the page that carries its own title (for Book One that is where its shared front matter ends).

**Per-book ISBN.** Each book's own pages are searched for an ISBN written next to the word "ISBN" on a copyright-style page, with a valid check digit (an ISBN-10 is converted to ISBN-13). It is used only when exactly one qualifies. Never used: the omnibus's own ISBN, anything on the shared front or back matter, a number found in more than one book, and a page that names two or more of the omnibus's books (that is the omnibus's own copyright page). If a book has several candidates, one marked as the e-book edition wins; otherwise none is written. It goes in as a second identifier (`opf:scheme="ISBN"` on EPUB 2, `urn:isbn:` on EPUB 3); the generated UUID stays the package ID, so a book with no ISBN of its own still gets only the UUID, as decided 2026-10-01.

**Copies of shared front and back matter.** Front matter is what sits before Book One's own title page; back matter is the run of author-level pages at the very end ("Books by", "About the author", mailing list or newsletter, colophon). Each is copied into every book that does not already hold the original, in the same place (front copies first, back copies last) and listed in that book's contents. A copy is a new file next to the original with `_copy` added to its name, so its links and pictures still work. Not copied: the omnibus's own title page (including one split over several files), its contents page, and the notes page (which is trimmed or moved instead).

**Notes renumbered.** On a notes page a book keeps, the visible numbers count 1, 2, 3 again, both next to the note in the text and on the notes page. Only the visible numbers change; links and anchors are untouched. All or nothing per page: if a note mark has no plain number, a number repeats, or the numbers are out of order, that page is left alone.

Results (checked with scratch test scripts that are not part of the repo): OmnibusExample (Guns now has notes 1 to 13; they were 11 to 23, every note mark matches its entry), Ravaged Land (4 front and 3 back pages copied), the First Mountain Man file (medium) and the 25-book Tarzan collection (13 seconds) all split with zero problems from the built-in check; broken links in the outputs equal the original's (Tarzan's 20 are the placeholder X links, a separate fix); splitting twice gives byte-identical files; the normal repair runs on sample outputs with no errors and a second repair changes nothing; the other 19 single-book examples report no omnibus. Per-book ISBNs were proven on a synthetic copy of OmnibusExample (one ISBN-10 ending in X, one ISBN-13); none of the real files has a per-book ISBN, and the First Mountain Man file correctly gets none because its only ISBN is the omnibus's.

Known limits:
- The omnibus's own copyright page is a front page like any other, so it is copied as written, including the omnibus's ISBN and the list of every title in it. Say if you would rather have it left in Book One only.
- A publisher's foreword ("Dear Readers" in the Mountain Man books) is copied into every book.
- A contents page for Book Two and later was built 2026-10-18 (see "Contents page for books that have none" below).

## Placeholder X links (2026-10-16)

Item 3 of Jacob's 2026-10-15 list. The links are in the original omnibus too, so the splitter only carries them along; the fix lives in Dead Link Cleanup (`modules/dead_link_repair.py`), which now relinks them to the chapter whose heading matches the link text instead of removing them. 18 of the 20 in the Tarzan collection relink; the 2 in Book 2 (a chapter that starts partway down another page) are still removed with their text kept. See `docs/analysis_roadmap.md`, "Placeholder X links relinked to their chapters", for the rules and results.

## Contents page for books that have none (2026-10-18)

The last item left from the splitter loose ends. Book One usually keeps the omnibus's own contents page (with the other books' entries removed), but Book Two onward had only the contents list a reading app shows in its menu, with no contents page to turn to. Checked on the three real omnibus files that split into books: the 25 Tarzan books all already have their own contents page, so nothing changes there; the second book of Ravaged Land and the second book of OmnibusExample had none.

**What it does.** After a book is cut out and its contents list is rebuilt, the splitter checks whether the book has a contents page: a short page headed "Contents" or "Table of Contents", or a page that is mostly a list of links to the book's other pages. The EPUB3 nav document does not count, since it is a menu, not a page. If there is none, and the book has at least three entries to list, a new page is added:

- Titled "Table of Contents", in a new file `table_of_contents.xhtml` next to the book's own title page, using the same stylesheet links so the fonts match.
- Placed just before the book's own title page, after the copied copyright page and the like. That is where Book One's own contents page sits in OmnibusExample.
- It lists the entries of the book's current contents list, nested the same way, each linked to the real page or heading. Entries that point at pages before it (cover, copyright) are left off, and the front and back pages that follow it are kept.
- It is also added to the book's contents list (NCX and nav document), just before the first entry that comes after it.
- Its look is set inline (no bullets, a centered heading), so it does not depend on any class in the book's own stylesheet.

**Settings.** On by default. `--no-contents-page` on the `split-omnibus` command (or `contents_page=False` in code) turns it off. The result page and the command's output say when a page was added and how many entries it has.

**Results.** Ravaged Land Book Two (33 entries) and OmnibusExample Book Two (34 entries) get the page; Book One of each and all 25 Tarzan books are unchanged. Every link on the new pages resolves to a real file and heading. The built-in output check reports no problems, and the official EPUB checker (epubcheck) reports nothing on the new pages that it did not already report on every other page of the same book (the DOCTYPE note on EPUB 2 books, which is the writer's and not this change's). Also tested on a copy of OmnibusExample whose own contents page was blanked (Book One then gets a page too, placed after its copyright page) and on an EPUB3 book with a nav document (the entry is added to the nav list).

Known limits:
- The page lists what the book's contents list holds. A book whose list is just a single "whole book" entry is left without a page (fewer than three entries), as before.
- A heading such as "Contents" inside the first 120 characters of a short story page would count as an existing contents page and no new one would be made.
