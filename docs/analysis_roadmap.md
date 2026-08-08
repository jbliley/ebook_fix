# Analysis Roadmap -- Planning Doc

**Status:** Not started. This is a planning doc only -- no code yet.
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
- Internal link/anchor validation -- flag `href="#..."` links
  (footnotes, cross-references, TOC entries) that point at an id
  which doesn't exist anywhere in the book.
- TOC/nav consistency -- compare the NCX or nav document's entries
  against the actual chapter structure to catch missing, orphaned, or
  mislabeled entries.
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
  -- worth revisiting once "TOC/nav consistency" above exists, since
  that's effectively the same work from the other direction.
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

## Open questions
- None yet -- nothing on this list has been scoped in detail.

## Continuity note
This file is the source of truth for "what's next" on the analysis
side, more reliable than relying on conversation memory across
sessions. Update it as items get picked up, scoped, finished, or
dropped.
