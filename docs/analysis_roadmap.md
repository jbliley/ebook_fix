# Analysis Roadmap -- Planning Doc

**Status:** Active. Front/back matter classification, NCX/nav label
parsing + reuse + link validation, and Project Gutenberg boilerplate
detection + removal are done (see below). TOC generation when a book
has none is the next item picked up from here.
**Started:** session that closed out the analysis-first migration
(see "Carried over" below).

## The idea

The analysis-first migration (now mostly finished, see
`analysis_first_migration_plan.md` before it's deleted) fixed *how*
analysis and repair talk to each other -- one pass, one shared report,
repair reads instead of re-scanning. This doc is the opposite kind of
list: *what else* is worth having the analyzer notice in that one
pass, so a future repair module never has to scan for it itself.

Each item below is a candidate for a new (or extended) analyzer
module, in the same spirit as `images.py`/`paragraphs.py`/
`whitespace.py` -- record the facts once, let repair (whenever it
gets written) just read them. Nothing here is scoped or ordered yet;
this is a holding pen, not a commitment.

## Candidates

### Structure & navigation
- **NCX/nav label parsing, reuse, and link validation -- done, see
  below.** Generating a TOC from scratch when a book has none is the
  remaining piece -- see "Next" below.
- Front/back matter classification -- identify title page, copyright,
  dedication, TOC page, etc. by pattern, so "thin chapter" doesn't
  lump them in with actually broken content. **Done, see below.**
- Cover image detection -- confirm a manifest item is properly marked
  as the cover and that it exists.

### Conversion artifacts
- Stray page-number remnants -- leftover PDF page numbers sitting
  mid-paragraph or between blocks.
- Hyphenation break artifacts -- words split by a line-break hyphen
  that were never rejoined ("con-\ntinue").
- Footnote/endnote link integrity -- reference markers that don't
  resolve to a matching note, or notes nothing points to.
- Span soup -- excessive, purposeless nested `<span>` wrapping left
  behind by conversion tools.
- Fake page-bookmark headings -- found while looking into
  `ChaptersMisaligned.epub` (a `pdftohtml` conversion, one file per
  original PDF page). Chapter detection itself is fine on this book;
  it correctly finds all 24 real "-N-" markers via sequence
  validation and ignores everything else. The actual defect is that
  `pdftohtml` stamps the first sentence of *every* page into an
  `<h2 class="calibre4" id="calibre_pb_N">` as a page-bookmark label,
  and that class happens to render oversized/bold, so random
  mid-chapter sentences look like headings. Not a chapter-detection
  problem, a leftover-heading-markup problem. Likely candidate: flag
  any heading element that isn't part of `chapters.py`'s confirmed
  sequence and doesn't look like a real title (ordinary sentence-case
  prose rather than a short label), then a small repair module to
  unwrap it back to a `<p>`. The `calibre_pb_` id is a reliable
  fingerprint for this specific converter, but the general check
  (unconfirmed heading + prose-shaped text) should catch other
  converters too -- this is expected to recur, not a one-off (worth
  keeping in mind for scoping: it should be pattern-based rather than
  id-based, so it isn't Calibre/pdftohtml-specific).

### Files & packaging
- Orphaned zip files -- files sitting in the archive that aren't
  referenced in the manifest at all (the reverse of the existing
  missing-image check).
- Manifest media-type mismatches -- declared media-type doesn't match
  what the file actually is.
- Font embedding gaps -- `@font-face` references a font that isn't
  embedded, or an embedded font nothing uses.
- Encoding declaration mismatches -- declared XML/HTML encoding
  doesn't match the file's actual bytes.

### Content quality
- Duplicate/near-duplicate content -- repeated boilerplate pages or
  accidentally duplicated chapters.
- Metadata completeness -- missing or malformed title, author,
  language code, identifier.
- Accessibility gaps -- missing `alt` text, missing `lang`
  attributes, images with no text alternative.

## Carried over from the analysis-first migration doc

These were on that doc's radar but never got their own phase before
the migration was declared essentially done. Keeping them here so
they aren't lost when that file goes away:

- `--log "location.txt"` flag to optionally write full CLI output to
  a file (Option B: capturing everything including `rich` console
  output; ANSI color codes to be handled intentionally). Discussed,
  not yet implemented.
- Per-category config toggles were the open question for Phase 5 and
  did get resolved (see the migration doc's Phase 5 writeup) -- listed
  here only as a pointer in case that doc is gone by the time this is
  read.
- Class Standardize was ruled out of scope for the migration itself
  (works off a hand-reviewed mapping file, not analyzer findings) --
  not a candidate for this list either, for the same reason.
- Image-based chapter markers deferred to a future OCR phase.
- TOC-dependent structural checks deferred until chapter detection is
  more reliable (TOC data expected to be wrong in many target books)
  -- revisit for "Generate when missing" below, since a generated TOC
  is only as good as chapter detection is.
- Physical chapter splitting assessed as substantially harder than
  anything on this list -- see `xhtml_recoder_plan.md` for that one;
  it has its own planning doc since it's a bigger, riskier effort.

## Done: Front/back matter classification

First item off this list, picked up right after this doc was written.

- New `ebook_fix/frontmatter.py`, feeding into the same single
  analyzer pass (`analyzer.py` now runs chapter and frontmatter
  analysis before its per-chapter loop instead of after, since the
  loop's thin-chapter check needed the zone/label answer).
- Zones (front/back/main) are anchored on `chapters.py`'s own
  confirmed chapter sequence -- if no sequence was confirmed at all,
  every entry falls back to zone "unknown" rather than guessing from
  position alone, on purpose (see the module's own docstring on why).
- Labels (title page, copyright page, dedication, epigraph, table of
  contents, acknowledgments, afterword, about-the-author, colophon)
  come from two independent signals per spine entry: the page's own
  text (trusted first) and its filename (a fallback hint, since
  filename conventions vary by conversion tool). Falls back to a
  zone-only label ("front matter" / "back matter") when neither
  signal fires but a zone is known.
- Immediate payoff realized: `AnalysisReport` now carries both
  `thin_chapters` (unchanged, still every thin chapter) and
  `unexplained_thin_chapters` (thin chapters NOT explained by being
  classified front/back matter). `engine.py`'s "Thin/Empty Chapters"
  issue count now uses the unexplained list, with the explained count
  called out separately instead of silently dropped. Verified across
  all five sample books: BrokenSentences, GutenbergText-ChapterSplit,
  RunTogetherText, and Watermarks-SmallChapterNumbers each went from
  reporting 1-2 "thin chapters" that were actually just a title page
  and/or copyright page, to reporting 0 unexplained (with the
  explained ones still visible in `--details`). ChaptersMisaligned
  correctly still flags its 3 genuinely thin main-content chapters as
  unexplained, while its title page and 5 back-matter pages are
  explained.
- `book.chapters` loads in manifest order, not spine order (the same
  bug noted under "physical chapter splitting" below) -- this module
  builds its own spine-ordered list locally via `book.spine` rather
  than waiting on that fix, since zone detection needs real reading
  order.
- Not done yet: no repair module built on top of this -- it's
  analysis only for now, per the "picked up next" plan. TOC/nav
  consistency (above) is the natural next consumer of this data.

## Done: NCX/nav label parsing, reuse, and TOC link validation

Found while investigating a bug report (real-world book, `Deathwatch`
by Robb White, libgen conversion) -- it turned out to be two bugs with
the same root cause, and Jacob confirmed the fix should go further
than just patching those two bugs (see the three goals below).

**Bug report:** running `repair` on a book whose EPUB reader showed
correct chapter names in its table of contents (Cover, Title Page,
Copyright, Dedication, Contents, Chapter 1...17, pulled straight from
the book's `toc.ncx`) resulted in every single entry showing the
same generic label ("Deathwatch," the book title) after repair.

**Root cause, confirmed against the actual file:**

1. `book.toc` was never populated -- `parser.py` had no code anywhere
   that read a book's `toc.ncx` or nav document, so none of a book's
   real, already-correct labels were available in memory for anything
   else to reuse.
2. `epub3_upgrade.py`'s `_chapter_label()` -- which builds the labels
   for the brand-new `nav.xhtml` an EPUB2->3 upgrade generates --
   checked a chapter's own `<head><title>` text FIRST, before checking
   its headings. `Deathwatch`'s converter (a common pattern) stamped
   the same generic `<title>Deathwatch</title>` into every single
   page, cover through chapter 17, so step 1 always found *something*
   and never got to step 2, which would have actually found the right
   answer (every chapter has its own numbered `<h1>` in the body).

**What shipped, covering all three goals confirmed at the planning
stage (reuse first, validate when present, generate when missing --
that last one still open, see "Next" below):**

- `models.py` gained a real `TocEntry` (label, href, nested children)
  and `Book.toc: list[TocEntry]` plus `Book.toc_source` ("ncx", "nav",
  or "" for neither).
- `parser.py` now actually parses the NCX (`navMap`/`navPoint`,
  recursively) and/or the EPUB3 nav document's `<nav epub:type="toc">`
  list (also recursively, for nested `<ol>` sub-sections), preferring
  the NCX when both exist and both parse to something -- an NCX
  navLabel is usually hand-authored or carried over from a real
  source, where a per-page `<title>` is frequently just the book
  title stamped onto every file by a conversion tool. Every href gets
  resolved to the same OPF-relative form the rest of the codebase
  already uses (matching `Chapter.href`), regardless of which
  directory the NCX/nav file itself lives in.
- Fixed the other latent bug this depended on: `book.chapters` loaded
  in manifest order, not spine (reading) order, which `frontmatter.py`
  had already worked around locally rather than waiting on. Chapters
  now load in real spine order directly in `parser.py`; any xhtml file
  the manifest declares but the spine doesn't read (an out-of-flow nav
  document, an orphaned page) still loads, just after every real
  chapter, so nothing silently disappears. Verified this was a
  no-op on all six sample books (manifest order already happened to
  match spine order in each), but it's now correct regardless.
- **Reuse:** `epub3_upgrade.py`'s nav-document generator now checks
  the book's own NCX/nav label for a chapter FIRST, ahead of that
  chapter's own `<head><title>`. Verified against `RunTogetherText`:
  its NCX has exactly one real entry ("Start" -> titlepage.xhtml,
  everything else undetailed), and the repaired `nav.xhtml` now
  carries that exact label through instead of overwriting it, while
  every other chapter (not covered by the source NCX) still falls
  back to the old per-page-title/heading logic same as before --
  confirms the fix reuses real data without fabricating labels that
  don't exist.
- **Validate:** new `ebook_fix/toc.py`, same "analysis, not repair"
  pattern as `css.py`/`paragraphs.py`/`whitespace.py`. Flattens the
  parsed TOC (all nesting levels) and checks each entry's href against
  real chapter hrefs (a "missing file" issue) and, if it has a
  `#fragment`, against the actual `id` attributes present in that
  chapter's document (a "missing anchor" issue). Also reports which
  main-content chapters (per `frontmatter.py`'s zone) aren't
  referenced by the TOC at all. Wired into the single analyzer pass
  (`AnalysisReport.toc`) and into `analyze`'s CLI output: real
  "TOC entries: N (from ncx/nav)" in `[File Contents]` (previously
  always read 0), plus a new `[Table of Contents]` findings section
  for broken links / missing coverage / no-TOC-at-all.
- Regression-verified across all six sample EPUBs: word counts,
  paragraph counts, and chapter-detection results are all unchanged
  from before this work (confirmed by diffing against the old
  manifest-order parser output directly). Ran a full `repair` on all
  six with no failures, and confirmed the already-EPUB3 Cthulhu book
  (which has its own nav document) doesn't get a second one generated.
  None of the sample books' own NCX data has broken links, so the
  "broken TOC links" path is implemented but not yet exercised against
  a real broken example -- worth testing against a book that actually
  has one, next time a good candidate turns up.

## Done: Project Gutenberg boilerplate detection

New `ebook_fix/gutenberg.py`, same "analysis, not repair" pattern as
`frontmatter.py`/`toc.py`. Picked up after looking into two example
books already in `examples/` and finding the two Gutenberg conversion
eras don't look alike at all:

- **Modern "Ebookmaker" format** (`The Call of Cthulhu by H. P.
  Lovecraft.epub`) -- the front disclaimer is a `<header
  class="pg-boilerplate" id="pg-header">` with a `*** START OF THE
  PROJECT GUTENBERG EBOOK ... ***` marker inside it, the back license
  is a `<footer class="pg-boilerplate" id="pg-footer">` with a
  matching `*** END OF ... ***`. The footer is its own whole spine
  file; the header sits at the top of the same file as the real title
  page and story.
- **Older plain-text style** (`GutenbergText-ChapterSplit.epub`, a Tom
  Sawyer text) -- same marker text, but as ordinary untagged `<p>`
  text, and the back matter isn't confined to one file either: the
  `*** END OF...` marker lands mid-file, and the "Small Print" legal
  text carries on into further spine files with no marker of their
  own at all.

What shipped:

- Detection keys off the marker TEXT first (`*** START OF [THIS|THE]
  PROJECT GUTENBERG EBOOK ... ***` / `*** END OF ... ***`, tolerant of
  asterisk/spacing variation), since that's the only signal the older
  conversions have. The modern semantic tags (`pg-header`/`pg-footer`
  ids, `pg-boilerplate` class) are checked as a fast path, and each
  tagged candidate is still classified by the marker text found inside
  it rather than by the class alone -- Ebookmaker stamps the same
  class value on both the header and the footer, so the class by
  itself can't tell front from back.
- `BookGutenbergSummary` records a `front`/`back` `GutenbergMarker`
  (href, detection method, matched marker text, and a live element
  reference for a future repair module to anchor on) plus
  `trailing_back_matter_hrefs` -- every spine entry after the file
  containing the `*** END OF...*** ` marker, folded in as more back
  matter even with no marker of its own. Safe to assume for a
  PG-sourced book: PG's own plain-text source never puts anything but
  its own license after that line.
- Wired into the single analyzer pass (`AnalysisReport.gutenberg`) and
  a new `[Project Gutenberg Boilerplate]` findings section in
  `analyze`'s CLI output (front/back href + detection method,
  `--details` adds the matched marker text and the trailing-file
  list).
- Deliberately does NOT anchor on `frontmatter.py`'s zone
  classification -- confirmed while investigating that Cthulhu is an
  un-chaptered short story, so `frontmatter.py` reports "not
  classified" for it (no confirmed chapter sequence to anchor zones
  on) and would never have caught this on its own.
- Verified across all six sample EPUBs: correctly detects both
  Gutenberg books (front/back, right href, right method for each
  era) and stays silent -- no false positives -- on the four
  non-Gutenberg ones. Confirmed the JSON analysis cache round-trips
  clean (the live `element` reference is dropped by serialize.py the
  same way chapters.py's candidates already are).
- Not covered: the very old (pre-1997) "*END*THE SMALL PRINT!" style
  header some early PG texts use -- noted in the module's own
  docstring as a deliberate scope cut, not an oversight.
- Repair module built on top of this in the next session -- see
  "Done: Project Gutenberg boilerplate removal" below.

## Done: Project Gutenberg boilerplate removal

New `ebook_fix/modules/gutenberg_repair.py`, reading the front/back
`GutenbergMarker` the detector above already found rather than
re-scanning. Also picked up two things the writer/model layer needed
that didn't exist yet, and one correctness fix in the detector itself.

Bug fixed in the detector first: `trailing_back_matter_hrefs` was
sweeping up `toc.xhtml` on the Cthulhu book, treating the book's own
EPUB3 nav document as leftover Gutenberg license text. Cause: the nav
document isn't in `book.spine` at all (manifest-only), but the local
spine-ordering helper still tacks manifest-only entries onto the end
of its list, and the trailing-matter sweep didn't know the difference.
Fixed by restricting that sweep to hrefs that are actually in
`book.spine`.

Removal logic, by case:

- **Front matter** -- always a subtree cut, since both eras put it at
  the top of a file that also holds real content. Walks up from the
  marker/wrapper element to whichever ancestor is a direct child of
  `<body>`, removes that ancestor and everything before it under
  `<body>`. One deliberate guard: if a heading (h1-h6) shows up in
  that leading run, the sweep stops there and leaves it alone. Needed
  because of what turned up testing against the Tom Sawyer example --
  calibre stamps a real `<h1>` book title in from the OPF metadata
  *before* the Gutenberg boilerplate paragraphs even start, and a
  blind "everything before the marker" sweep would have deleted the
  actual book title along with the license text. Verified: the title
  survives, every boilerplate paragraph around it doesn't.
- **Back matter** -- whole-file drop when the marker's file is
  nothing but the license (checked directly: does `<body>` have any
  non-whitespace content besides the marker/wrapper element), subtree
  cut in the other direction otherwise (marker's ancestor and
  everything AFTER it under `<body>`, no heading guard needed since
  nothing legitimate follows an END marker by definition).
  `trailing_back_matter_hrefs` are always whole-file drops.
- Verified against a real edge case worth knowing about: Tom Sawyer's
  back-matter file has the real "CONCLUSION" chapter sitting *before*
  the END marker in the same file. Subtree cut correctly preserved it
  and only removed the trailing "Small Print" text after the marker.

What the writer/model layer needed (didn't exist before this):

- `book.removed_files` (a new field on `Book`) plus matching support
  in `EPUBWriter.save()` -- previously there was no way to drop a file
  from the saved EPUB at all. Editing `book.manifest`/`book.spine` to
  no longer reference a file did nothing to stop the writer from
  copying that file's bytes through untouched, since it iterates the
  *source* zip's own file list. Caught this by inspecting the output
  zip directly rather than trusting a clean `repair` exit code.
- Whole-file removal now also cleans up: the live OPF `<manifest>`/
  `<spine>` elements, `book.manifest`/`book.spine`/`book.chapters`,
  and any `<a href>` in the book's own EPUB3 nav document pointing at
  the removed file (verified: Cthulhu's `toc.xhtml` no longer
  references the dropped footer file after repair).

Verified across both Gutenberg example books: `repair` completes
clean, `validate_epub` passes on both outputs, a word-count diff on
Tom Sawyer shows ~2,367 words removed (matches the size of the
stripped boilerplate), and re-running `analyze` on each repaired book
confirms `gutenberg.detected` is now `False`. Also reran the full
`analyze`/`repair` pass across all six sample EPUBs -- no regressions,
no crashes.

Known, deliberate limitation, not a bug: a legacy NCX (`toc.ncx`)
isn't cleaned up. Confirmed on both repaired books -- `analyze`
correctly reports a "Broken TOC links" entry for each removed file,
always the NCX entry (`THE FULL PROJECT GUTENBERG™ LICENSE` on
Cthulhu, both trailing Tom Sawyer files), never the nav document,
which is cleaned. Cause: `toc.ncx` isn't loaded as an editable
document anywhere in the project yet (only `application/xhtml+xml`
manifest items become `Chapter` objects with a `.document` to edit --
see `parser.py`'s `_load_resources`), so there's nothing to edit
against without adding raw-bytes NCX parsing/editing support, which
is bigger than this module's job. Worth its own follow-up if legacy
NCX books turn out to matter for the collection this gets run against.

## Next: TOC generation when missing

The one goal from the original three-part plan not yet built: if a
book has no NCX and no nav document at all (`toc_source == ""`),
eventually generate one from `chapters.py`'s confirmed chapter
sequence instead of leaving the book without navigation.

Not scoped in detail yet. Known considerations for whenever this is
picked up:
- `epub3_upgrade.py`'s `_add_nav_document()` already builds a nav.xhtml
  from the spine whenever a book is upgraded to EPUB3 -- but only when
  `needs_upgrade` is true. An EPUB3 book that's simply missing its nav
  document (invalid per spec, but a possible real-world find) wouldn't
  currently trigger it. Worth deciding whether "generate when missing"
  is its own repair step, or a small extension of that existing one.
- EPUB2 books needing an NCX generated (as opposed to EPUB3's nav)
  aren't handled by anything yet either.
- `analyze_book_toc()`'s `chapters_missing_from_toc` list (new, see
  "Done" above) is already halfway to being the input list a
  generator would need -- it's the same "main content chapters with
  no TOC entry" answer either way.
- Depends on chapter detection actually having confirmed a sequence
  (`chapters.py`'s `best_sequence`) -- if it hasn't, there's nothing
  reliable to build labels from, so this would need its own fallback
  (probably just numbering chapters "Chapter N" off spine position) or
  a decision to skip generation entirely in that case.

## Open questions
- Whether NCX-label parsing lands as its own module or folds into
  `frontmatter.py` -- moot now; it landed in `parser.py` (population)
  + new `toc.py` (validation), with `frontmatter.py` untouched.

## Continuity note
This file is the source of truth for "what's next" on the analysis
side, more reliable than relying on conversation memory across
sessions. Update it as items get picked up, scoped, finished, or
dropped.
