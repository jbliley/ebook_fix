# XHTML Recoder -- Planning Doc

**Status:** Not started. This is a planning doc only -- no code yet.
**Started:** session ending with the chapter_markup page_breaks.py cleanup.

## The problem

CSS page breaks (`page-break-after` etc., see `page_breaks.py`) are a
*hint* -- reading systems are allowed to ignore them, and support is
genuinely inconsistent across devices (this is exactly what the
page_breaks.py work was responding to). The only thing every reading
system actually guarantees is a fresh page at a spine boundary --
i.e. a new physical XHTML file.

Goal: split a book's chapters/sections onto their own XHTML files
(each becoming its own spine item), so page breaks are structural
rather than a CSS request a reader can decline.

## Why this needs a Phase 0, before any splitting code

Everything built so far (chapter_markup, class_standardize) is
low-stakes if wrong: a missed or wrong chapter-boundary guess means a
paragraph doesn't get a CSS class it should have, or gets one it
shouldn't. Annoying, easy to spot, easy to fix.

Physically splitting a file is not that. Get a boundary wrong and you
can silently misplace, duplicate, or lose content, permanently reorder
sections, or break every internal link that used to work -- and it's
much harder to notice after the fact than a missing CSS class. The bar
for "confident enough to split on" needs to be meaningfully higher
than the bar chapters.py currently uses for "confident enough to add a
CSS class to."

So before writing any splitting logic: audit/harden the structure
detection itself, ideally corroborating chapters.py's marker-text
detection against other signals already sitting in the book when
they're available (existing NCX/nav TOC entries, heading tags,
existing internal anchors like `calibre_pb_N`) rather than trusting
text-pattern matching alone.

## Phased plan

### Phase 0 -- Structure analyzer audit / hardening
Broken into sub-steps below because Phase 0 alone was too big for one
session. Each sub-step should be completable in a single session on
its own. Status of each tracked here as they're done.

- **0a -- Document current detection signals.** [x] Done. See
  `chapter_detection_signals.md`: what counts as a candidate, how
  scoring works, how the winning sequence gets picked, and a callout
  of what's notably absent from today's confidence bar (no TOC/anchor
  corroboration, no near-miss visibility, no minimum-content gate).
- **0b -- Define the higher safety bar.** [x] Done, pending your
  sign-off. See `split_safety_bar.md`: proposes per-boundary
  corroboration, a margin-over-runner-up check, a minimum-content
  gate, and a structural-cleanliness check, plus how a book with only
  some weak boundaries should be handled (split what's solid, leave
  the rest bundled and flagged) versus a book with no reliable
  sequence at all (don't split anything). Leaves one open question
  about books with no TOC/anchors to corroborate against.
- **0c -- Design the structure tree shape.** [x] Done. See the new
  `src/ebook_fix/structure.py`: `NodeKind`, `SplitConfidence`,
  `BoundaryEvidence` (one slot per requirement from
  `split_safety_bar.md`, with `.confidence` computed live rather than
  stored, so it can never drift out of sync with the fields behind
  it), `StructureNode` (nestable, for Parts containing chapters), and
  `BookStructure` (the whole-book result, holds the sequence-margin
  stat too).
- **0d -- Skeleton using existing signal only.** [x] Done. Also in
  `structure.py`: `build_structure()` reads chapters.py's
  `BookChapterSummary` and assembles a first-draft tree using only
  sequence membership -- no TOC/anchor corroboration yet, so every
  node comes out as SEQUENCE_ONLY or NONE confidence, never
  CORROBORATED, until 0e/0f exist. Smoke-tested clean against all six
  sample EPUBs. Surfaced a real gap while wiring this up: Part/Book/
  Volume markers never go through chapters.py's sequence search at
  all (only chapter candidates do), so a detected Part boundary today
  has *less* validation behind it than a chapter boundary, not more --
  documented in `chapter_detection_signals.md` and reflected in
  `build_structure()` by never treating a part boundary as
  sequence-confirmed.
- **0e -- TOC cross-check.** [x] Done. Also in `structure.py`:
  `apply_toc_corroboration()` matches confirmed chapters against the
  book's existing NCX/nav TOC and fills in `matched_toc_entry`.
  Matches on a fragment id (checking the candidate's element and its
  nearest ancestors) when the TOC entry has one, or on "first chapter
  in that file" for whole-file links with no fragment. Run against
  all six sample EPUBs first: zero matches everywhere, which turned
  out to be the correct result, not a bug -- every sample book's NCX
  is a stub with a single "Start" entry pointing at the title page,
  the same kind of broken TOC toc.py already flags, so there was
  nothing real to match against. Confirmed the matching logic itself
  works with a synthetic test covering all three cases: fragment
  match, whole-file match, and a deliberate id mismatch that correctly
  stayed unmatched.
- **0f -- Anchor cross-check.** [x] Done. Also in `structure.py`:
  `apply_anchor_corroboration()` scans actual chapter content (not the
  NCX/nav TOC, which 0e already covers) for internal cross-reference
  links -- footnotes, an index, "see Chapter 5" links, anything with
  an in-book `#fragment` -- and matches their targets against a
  candidate's own id the same way 0e matches TOC fragments. Run
  against all seven sample books (Sidewinders included): zero matches
  everywhere, which is the honest, correct result -- none of these
  particular books happen to have any in-body cross-reference links
  at all. Proved the matching logic itself works with a synthetic test
  using real lxml elements: a genuine cross-reference matched
  correctly, an external `http://` link was correctly ignored, and a
  candidate with no id at all correctly stayed unmatched. Deliberately
  does NOT treat the `calibre_pb_N` per-*page* bookmark ids mentioned
  in `analysis_roadmap.md` as corroboration on their own -- those
  exist on every page regardless of chapter boundaries, so matching
  one would be false corroboration; only a link whose target actually
  matches a *candidate's own* id counts.
- **0g -- Confidence scoring.** [x] Done. Also in `structure.py`:
  requirement 3 (a healthy margin over the runner-up sequence) is now
  read live in `BoundaryEvidence.confidence` itself -- a weak margin
  routes straight to NEEDS_REVIEW even with corroboration, since a
  weak margin means the book's overall sequence is ambiguous, which
  one corroborated boundary doesn't fix. `apply_content_length_check`
  fills in requirement 4 (minimum content per resulting slice) and
  `apply_structural_cleanliness_check` fills in requirement 5 (not
  cutting inside a table/list/footnote block). Both walk from a
  chapter's own marker up to the *next* boundary, including cases
  where that next boundary lives in a different file -- lxml's
  `.//text() | following::text()` union (kept in document order by
  libxml2) does the actual range extraction, since a plain
  `.iter()`-based walk silently drops an ancestor's tail text that
  falls after the marker. `score_confidence()` runs both, and a new
  `analyze_structure(book)` chains the whole Phase 0 pipeline
  (0d through 0g) into one call. Verified with synthetic tests: a weak
  margin correctly downgrades an otherwise-corroborated boundary,
  table/footnote ancestors are correctly caught, and a synthetic
  three-file range (with content both before and after the marker,
  including an ancestor-tail case) counts exactly the words it should.
  Also caught a real case on the sample books: `Watermarks-
  SmallChapterNumbers.epub`'s "chapters" are actually running
  page-number watermarks with nothing but more watermarks between
  them -- the minimum-content check correctly flags every one of them
  as too short, exactly the false-positive pattern requirement 4 was
  written to catch.
- **0h -- Review command.** [x] Done. `engine.map_structure()` /
  `ebook-fixer map-structure <epub>` -- a dry-run pass, same posture
  as `map-css`: nothing is written or modified. Calls
  `analyze_structure()` and prints `format_structure_report()`'s plain
  -text tree (indented for nested Parts, one line per node with its
  confidence label, corroboration source, and any notes). The
  underlying `BookStructure` object stays the structured result;
  `format_structure_report` is just one way of presenting it, so a
  later GUI can render the same tree differently without touching
  `structure.py`. One formatting gotcha worth remembering for future
  CLI output: engine output goes through `rich`'s `console.print`,
  which treats `[text]` as a markup tag and silently swallows it --
  the report uses `(chapter)`-style parentheses instead of
  `[chapter]` for exactly that reason.

### Phase 1 -- Single-file splitting mechanics (proof of concept) -- DONE
- Built `src/ebook_fix/splitter.py`: given one XHTML file + a set of
  confirmed boundaries, it produces N standalone XHTML documents
  (same doctype/head/body, same stylesheet links, images/resources
  still resolve). Segment 0 keeps the original file's href; every
  other segment is a new file.
- Naming: `chapter_NNN.xhtml`, numbered by detected chapter number
  when there is one, or by position among the newly-created files
  when there isn't -- same directory as the original file. Collisions
  fall back to a `_2`, `_3`, ... suffix, same idiom as
  `modules/epub3_upgrade.py`'s `_unique`.
- Registered in the manifest + spine in place of the original single
  entry, correct reading order -- both the live OPF XML and
  `book.manifest`/`book.spine`/`book.chapters` are kept in sync, same
  four touch points `modules/gutenberg_repair.py` and
  `modules/epub3_upgrade.py` already update for a removed/added file.
- Automatic integrity check: total word count before and after the
  split is compared before any manifest/spine change is made. A
  mismatch raises `SplitError` and puts every moved element back
  where it came from first, so a failed split can't half-apply.
- No cross-reference rewriting yet, as planned -- that's Phase 2.
- A real structural case turned up during testing and is now handled:
  some conversions (calibre in particular) wrap a book's *entire*
  content in one `<div>` directly under `<body>`, sometimes several
  layers deep, before any chapter content starts. The splitter sees
  through any such single-child wrapper chain automatically
  (`_effective_container` / `_wrapper_chain`) and rebuilds the same
  wrapper levels in each new file, rather than refusing the whole book.
  Caught two bugs building this: the first version refused to split
  any wrapped book at all; the fix for that then nested an extra
  `<body>` tag inside itself instead of the real wrapper `<div>` --
  found with a synthetic doubly-nested test case before it ever
  touched a real book.
- Regression tested against all 6 sample books that have a
  multi-chapter file (17-24 chapters each) -- every one splits
  cleanly, every word-count check passes exactly, every output is a
  valid, structurally correct EPUB.
- Added a `split-structure` CLI command (Engine.split_chapters) so
  this can be tried by hand, same posture as `map-structure`'s dry
  run except this one actually writes a file. Deliberately **not**
  gated by the split-safety-bar's corroboration requirement yet --
  it only requires SEQUENCE_ONLY confidence or better, since none of
  the sample books currently have a CORROBORATED boundary (see Phase
  0e/0f) and a command gated that strictly would have nothing to
  test against today. Proper gating to CORROBORATED-only is Phase 5's
  job, once cross-reference rewriting and NCX handling exist too --
  until then, treat this command's output as a mechanics test, not a
  finished conversion.
- Side finding, not a splitter bug: `analyzer.py`'s book-wide
  `total_word_count` counts each chapter file's entire document
  (`tree.itertext()`), including `<head>` content like `<title>` text
  and inline `<style>` blocks -- not just `<body>`. Splitting a file
  that has an inline stylesheet copies that `<head>` into every new
  file (correctly -- the new files need it to render right), which
  makes the book-wide word count look inflated afterward even though
  the actual chapter content is provably unchanged (verified directly
  against raw body text). Worth a separate look at some point; out of
  scope for this phase.

### Phase 2 -- Cross-reference rewriting
- Every internal `href="...#fragment"` anywhere in the book that
  pointed into a file that just got split needs to be redirected to
  the new file + fragment.
- Covers: NCX/nav TOC entries, in-body footnote/endnote links, any
  other internal cross-references.

### Phase 3 -- NCX / nav TOC generation or repair
Split into small pieces, same reasoning as Phase 2 above -- each one
ends with something real and working, so a session that runs out
mid-phase still leaves solid ground to resume from.

- **Phase 3a -- Make the NCX (and nav.xhtml, if present) editable
  documents.** Right now both are parsed once into a read-only
  `book.toc` list, purely for display/validation (see toc.py) -- no
  code path anywhere edits them back. This step loads them as live,
  mutable trees on the Book model, the same way `chapter.document`
  already works, and teaches the writer to serialize changes back
  out. Nothing rewrites anything yet; this just removes the blocker
  everything else in this phase depends on. Testable by confirming an
  untouched book round-trips with zero diff.
- **Phase 3b -- Rewrite existing TOC/nav entries whose target file got
  split.** Reuses the same `href_by_id`/`current_href_origin`
  machinery Phase 2 already built for in-body links, just pointed at
  NCX `<content src="...">` and nav `<a href="...">` entries instead.
  This is the "already had a working TOC" half of this phase's
  original two-line scope. Testable against ChaptersNotAligned-New
  (real NCX, real multi-chapter-per-file split) and
  CrossReferences-Synthetic (already has a toc.ncx entry to exercise).
- **Phase 3c -- Generate new TOC entries for chapters that split apart
  with no entry of their own.** The harder, more design-heavy half:
  when one file with one TOC entry splits into five chapter files,
  only one (or zero) of them had a real entry to begin with. This
  phase inserts entries for the others -- this is where "TOC/nav label
  reuse during chapter mapping" (see the open bug list this doc's
  continuity note points back to) belongs. Likely to need further
  slicing once actually in progress, since the label-reuse question
  alone (what to call a newly-split chapter that never had its own
  TOC entry) has more than one reasonable answer.
- **Phase 3d -- NCX/nav consistency + full regression.** If a book has
  both an NCX and an EPUB3 nav document, keep them in sync with each
  other rather than only fixing whichever one 3b/3c happened to touch
  first. Finishes with the standard full regression pass (word-count
  diffing plus analyze/repair/re-analyze) across every sample book.

### Phase 4 -- Edge cases and hardening
- Nested structure (Parts with multiple chapters).
- Front matter / back matter -- split out too, or left bundled?
- Books already partially split (some chapters already separate
  files, some not) -- mixed-state handling.
- Minimum-content gate so a stray short "boundary" (e.g. a
  misidentified scene divider) doesn't trigger a split.

### Phase 5 -- Wire into repair pipeline / CLI
- Same manual-review posture as class_standardize: a dry-run/review
  step before anything is applied, not an automatic split on `repair`.

## Open questions to resolve when we pick this back up
- Whether Phase 0's structure tree subsumes chapters.py entirely or
  sits alongside it.

## Continuity note
This file is the source of truth for where this feature stands --
more reliable than relying on conversation memory across sessions.
Update it at the end of each session that touches this feature:
what got built, what got decided, what's still open.
