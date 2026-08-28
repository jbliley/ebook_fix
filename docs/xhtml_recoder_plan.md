# XHTML Recoder -- Planning Doc

**Status:** In progress. Phases 0 (structure analyzer audit/hardening),
1 (single-file splitting mechanics), 2 (cross-reference rewriting),
3a (NCX/nav made editable), 3b (rewriting existing TOC/nav entries),
and 3c (generating new NCX entries for chapters with no entry of
their own) are done -- see below.

**2026-08-28 session:** Two things closed today. First, fixed a real
detection bug Jacob found while testing (War and Peace's "First
Epilogue" restart) -- see "Bug fix -- Epilogue/Prologue part
boundaries" below. Second, closed a documented confidence gap: Part/
Book/Volume markers now actually get validated against their own
same-kind neighbors instead of being trusted unconditionally -- see
"Part/Book/Volume sequence validation" below.

**2026-08-27 session:** First slice of case 3 (no chapter markers, no
TOC) built -- detection only, not wired to any actual splitting yet.
See "Case 3 detection -- first slice" below, after the three-case
framework, for what's in and what's still open.

**Priority reordering (2026-08-24):** Jacob's actual goal is two
separate things, done in this order:
1. Every detected chapter physically lives on its own XHTML page.
2. The TOC (existing or generated) correctly points at each one.

Getting every book's chapters onto their own page comes first --
Phase 3d/3e below (TOC consistency/verification) are real, but
deliberately on hold until chapter-separation coverage is further
along. See the priority note after Phase 3e below for what that
actually means against the three-case framework; Phase 3c above
already covers the case where a file has SOME markers detected but no
TOC to extend, and the priority note's first bullet now also covers a
file with markers but *no* TOC coverage at all to extend -- what's
still missing is best-effort splitting when a file has no detected
markers at all (case 3). That comes before TOC verification, not
after.
**Started:** session ending with the chapter_markup page_breaks.py cleanup.

**Jacob's three-case framework** (stated the session Phase 3c was
built), meant to guide this feature going forward:
1. TOC exists + chapter markers exist -> do nothing, leave as-is.
2. Chapter markers exist, no TOC -> eventually generate a TOC and
   split into separate XHTML pages.
3. No chapter markers, no TOC -> attempt best-effort chapter
   splitting, may require user verification if confidence is low.
Priority is preserving a book's own original structure whenever real
chapter headers and TOC entries already exist -- see Phase 3c below,
which inherits a chapter's own detected title rather than inventing
one.

### Bug fix -- Epilogue/Prologue part boundaries (2026-08-28)

Jacob found this by testing, not by reading code: running `map-structure`
against War and Peace, detection looked clean for BOOK ONE through
BOOK FIFTEEN, then just stopped -- no error, no warning, it looked
like the book was over. It isn't; War and Peace ends with a "First
Epilogue" and a "Second Epilogue," each of which restarts its own
chapter numbering back at "CHAPTER I," exactly the way a new
Book/Part/Volume already does.

**Root cause:** `chapters.py`'s `_classify` already recognizes
"Book"/"Part"/"Volume" as a part-boundary label word, but only when
the label word leads ("BOOK ONE," "PART IV"). "FIRST EPILOGUE" and
"SECOND EPILOGUE" put the ordinal first and the label word second --
the reverse order -- so neither ever matched `PART_LABEL_WORDS` at
all. Instead, "FIRST EPILOGUE: 1813 - 20" fell all the way through to
the module's other, unrelated fallback for unlabeled headings like
"FOURTH MACHINATION" (a spelled-out ordinal directly followed by more
title-cased words, with no label word in front), and got classified as
an ordinary chapter candidate instead of a part boundary. An ordinary
chapter candidate never bumps `part_index`, so when "CHAPTER I" showed
up right after it, `_find_best_sequence` saw the count drop from XX
(the last chapter of Book Fifteen) straight to I with no part boundary
between them to license a restart -- an invalid jump under the normal
same-part sequencing rule -- and the winning run simply ended there.
Both epilogues, and every real chapter inside them, were silently
dropped from the confirmed sequence.

**Fix:** new `SUFFIX_PART_LABEL_WORDS = ("epilogue", "prologue")` in
`chapters.py`, plus a new branch in `_classify` checked right after the
existing label-first branch: if the first word is a recognized ordinal
and the second word (first two words only; anything after, like a year
range or subtitle, is dropped the same way the label-first branch
already drops a trailing title) is "epilogue" or "prologue", classify
it as a part boundary the same as "Book"/"Part"/"Volume." A bare
"EPILOGUE" or "PROLOGUE" with no ordinal in front is also recognized,
treated as the first (and possibly only) one of its kind.

Verified against all ten sample books, before/after diffed exactly:
- `WarPeace-GoodCopy.epub`: both epilogues now show up as `(part)`
  boundaries, and every chapter inside each (I through XVI in the
  First Epilogue, I through XII in the Second) is now corroborated by
  its own TOC entry and anchor, all the way to the book's real ending
  -- previously invisible past Book Fifteen entirely.
- `RunTogetherText.epub` picked up one incidental improvement: a bare
  "PROLOGUE" heading before its first real chapter, previously
  unclassified and invisible, now shows up as its own `(part)`
  boundary too. The confirmed chapter sequence itself (FIRST MACHINATION
  onward) is byte-identical to before.
- The other eight sample books: zero change, confirmed by diffing full
  `map-structure` output before and after.
- Full regression pass afterward (`analyze`, `repair`, `split-structure`
  across all ten books): no crashes, `split-structure` output on both
  affected books byte-identical to before the fix (this only changes
  what `_classify` reports, not the splitter itself), and body-text
  word counts after `repair` unchanged from the pre-fix baseline on
  every book.

**Not done, on purpose:** Part/Book/Volume markers (including this new
Epilogue/Prologue case) still aren't run through any sequence-level
check of their own -- see `chapter_detection_signals.md`'s "What's
notably absent" section, a pre-existing gap this fix doesn't touch.
Also out of scope: other structural words that might someday need the
same ordinal-trailing treatment ("First Interlude," "Second Appendix")
-- added only when a real book actually needs it, per this project's
scoped-fix preference, not speculatively.

### Part/Book/Volume sequence validation (2026-08-28)

Jacob's follow-up question after the Epilogue fix above: what else
could raise confidence across the detection/mapping modules? Of the
options surfaced, this was the one he chose to tackle first.
`chapter_detection_signals.md`'s "What's notably absent" section had
flagged it back in the Phase 0a audit: a chapter marker has to survive
a whole scoring/sequence contest to be trusted, but a detected Part/
Book/Volume marker was trusted the instant it was found, with no check
that Parts themselves count up sensibly. A Part boundary was actually
*less* well-grounded than a chapter boundary despite sitting a level
above it structurally -- a single garbled OCR line that happened to
start with "Book" would have been accepted outright.

**Fix:** new `_find_best_part_sequence` in `chapters.py`, run over
`part_candidates` right where `_assign_part_indices` already runs, in
`analyze_book_chapters`. Same increasing-run idea as chapters' own
`_find_best_sequence`, adapted for one structural difference: a book
can have more than one genuinely independent numbering track running
at once -- "BOOK ONE" through "BOOK FIFTEEN" *and* an unrelated "FIRST
EPILOGUE"/"SECOND EPILOGUE" track that starts back at 1 -- and these
must never be forced to count up against each other. New
`_part_division_word` helper groups candidates by which structural
word actually introduced them ("book" vs "volume" vs "epilogue", etc.)
before the increasing-run search runs within each group, so the two
tracks in a book like War and Peace validate independently rather than
looking like a broken 15 -> 1 sequence.

Unlike chapter sequences, a group of exactly one candidate (a single
"Prologue" with no numbered sibling) is trusted on its own label score
alone rather than discarded for falling short of `MIN_SEQUENCE_LENGTH`
-- that floor exists specifically because two bare unlabeled numbers
counting up is easy coincidence, which doesn't apply to an explicitly
labeled structural marker with nothing else of its kind to check
against. A group of two or more still has to actually count up
correctly; an outlier that breaks the count is excluded the same way
chapters.py already excludes one from a chapter run. Confirmed
candidates are marked `.confirmed = True`, same convention as
confirmed chapters, and `structure.py`'s `_evidence_for_part` now
reads that flag into real evidence (`in_winning_sequence`) instead of
hard-coding `False` -- a validated Part boundary now reports
"sequence only" confidence, same baseline a chapter gets before any
TOC/anchor corroboration; an unvalidated one still reports "none."

Verified with two hand-built cases before touching any real book: a
"Book One, Book Two, [garbled] Book Nine, Book Three, Book Four"
sequence correctly excludes only the Book Nine outlier and keeps 1-2-
3-4; a "Book Fourteen, Book Fifteen, First Epilogue, Second Epilogue"
sequence correctly validates both axes independently rather than
treating 15 -> 1 as a broken count. Then verified against all ten
sample books, before/after diffed exactly against last session's
epilogue-fix baseline: only the two books with any detected Part
markers changed at all. `WarPeace-GoodCopy.epub`'s 15 Books and both
Epilogues now all report "sequence only" instead of the previous
unconditional "none," with an updated note explaining the validation
that ran; `RunTogetherText.epub`'s lone "Prologue" (see the Epilogue
fix above) went from "none" to "sequence only" the same way, correctly
treated as a trusted lone marker. No other book, and no chapter-level
line in either of the two changed books, differed at all. Full
regression afterward (`analyze`, `repair`, `split-structure` across
all ten books): no crashes, word counts after `repair` byte-for-byte
matched the pre-fix baseline on every book (repair's own output is
otherwise untouched by this, only `dcterms:modified`'s timestamp
differs run to run as always), and `split-structure` output is
unaffected too, since splitting only ever acts on confirmed chapters,
never on Part nodes.

**Not done, on purpose:** an invalid/unvalidated Part marker still
counts toward `part_index` (i.e. it still permits a chapter-numbering
restart right after it) -- deliberately left alone rather than
changing that too in the same pass, since it's a separate question
(does a marker get to be *trusted as a structural boundary itself*
vs. does it get to *license a restart in the level below it*) and
touching it risks disturbing chapter-restart detection that already
works correctly today. Also not done: extending TOC/anchor
corroboration (today `apply_toc_corroboration`/`apply_anchor_corroboration`
only walk `CHAPTER` nodes via `_walk_chapters`) to also cover `PART`
nodes -- War and Peace's own NCX has a real "BOOK ONE" entry sitting
right there, unused for this. Worth a future session on its own.

### Case 3 detection -- first slice (2026-08-27)

Jacob chose the first signal to build: a bare number immediately
followed by a title, with no label word in front of it ("1. The
Horror in Clay.", as opposed to "Chapter 1"). Structural breaks
(`<hr>`, page-break CSS) were explicitly deferred, not built.

Jacob also decided case 3 splits always require manual review, even
at high confidence -- never auto-applied regardless of how clean the
sequence looks. This is enforced structurally, not just by
convention: `BoundaryEvidence.case3` hard-caps `confidence` at
`NEEDS_REVIEW` (never `CORROBORATED`), and on top of that,
`split-structure` (the only command that currently acts on detected
boundaries) never calls the case 3 pipeline at all today -- so there
is currently no path from case 3 detection to an actual split. That
wiring is intentionally not built yet; see "Still open" below.

**Built:**
- `chapters.py`: `_classify_case3` / `_score_case3_candidate` /
  `extract_case3_candidates` / `analyze_case3_book_chapters`, entirely
  separate from the case 1/2 code path (`extract_candidates` now
  takes optional `classify_fn`/`score_fn` params so both paths share
  one tree-walk instead of two copies of it). Two new `MarkerStyle`
  values: `UNLABELED_NUMBERED_TITLE_ARABIC` /
  `_ROMAN`.
- `structure.py`: `BoundaryEvidence.case3` flag, `analyze_case3_structure`
  (mirrors `analyze_structure` but skips TOC/anchor corroboration,
  since case 3 is defined by having no TOC to corroborate against).
- `engine.py`: `map-structure` now runs the case 3 pipeline as a
  fallback, in its own clearly-labeled section, whenever the normal
  pipeline finds nothing. `analyze`'s "Chapters Detected: None" line
  now points people at `map-structure`.
- Bug fix along the way: `_looks_titleish` (shared helper) was missing
  several common prepositions ("from", "as", "into", "onto", "over",
  "under", "but", "nor") from its list of words allowed to stay
  lowercase in a title, which was silently failing real titles like
  "The Madness from the Sea." Confirmed this only loosens matching
  (never tightens it) and re-ran the full sample suite after the
  change -- no existing book's detected chapter count or style
  changed.
- Verified against `examples/The Call of Cthulhu by H. P. Lovecraft.epub`,
  the one sample book that has real unlabeled numbered sections and
  previously came back "Chapters Detected: None". Full regression run
  across all ten sample books (`analyze`, `map-structure`, and
  `split-structure`) confirmed no change to any other book's chapter
  detection, structure report, or split output.

**Still open (not built this session):**
- Structural-break signals (`<hr>`, page-break CSS) as a second case 3
  detection path.
- Actually wiring a reviewed case 3 sequence through to a real split --
  today this is detection and review-surfacing only. Likely shape,
  following the same pattern as `map-css` -> review -> `repair
  --class-mapping`: a dedicated review/confirm step (not
  `split-structure`, which has no confidence gate at all yet per its
  own docstring) before anything case 3 finds is ever handed to
  `splitter.py`.

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

### Phase 2 -- Cross-reference rewriting -- DONE
- Built `src/ebook_fix/crossref.py`: after a split, every in-body
  `href="...#fragment"` link that pointed into a file that just got
  cut apart is found (`find_links_into`) and redirected to whichever
  new file + fragment its target actually landed in
  (`rewrite_links`). Wired into `engine.py`'s `split_chapters`, run
  once per split so it always sees the full set of files that moved
  in that run rather than checking one split at a time.
- Deliberately in-body links only, as planned -- a footnote, an
  endnote backlink, a "see Chapter 5" cross-reference, anything
  living inside a chapter's own document tree, which is the only
  kind of link this project could mutate at the time this was built.
  NCX/nav TOC entries are explicitly NOT covered here: they were
  still read-only parsed data when this phase was built (see Phase
  3a below, which removes that blocker), so an existing TOC/nav link
  into a split file was a known, left-alone limitation of this phase,
  not a bug. Regenerating/repairing those is Phase 3's job.
- External links (`http://`, `https://`, `mailto:`) are correctly
  left untouched.

### Phase 3 -- NCX / nav TOC generation or repair
Split into small pieces, same reasoning as Phase 2 above -- each one
ends with something real and working, so a session that runs out
mid-phase still leaves solid ground to resume from.

- **Phase 3a -- Make the NCX (and nav.xhtml, if present) editable
  documents.** [x] Done.
  Turned out to be a smaller change than the original scope implied,
  once actually checked: the EPUB3 nav document already gets loaded
  as a normal `Chapter` by `parser.py` (its media_type is
  `application/xhtml+xml`, same as every chapter file), so
  `chapter.document` was already a live, editable tree for it -- the
  only missing piece was a convenient way to find that chapter
  without hunting through `book.chapters` by hand. Added
  `Book.nav_item` and `Book.nav_chapter` properties in `models.py` for
  that.
  The NCX was the real gap, since its media_type
  (`application/x-dtbncx+xml`) never went through the chapter-loading
  path at all -- it was only ever parsed once into the read-only
  `book.toc` list. `parser.py`'s `_read_toc` now keeps the parsed NCX
  tree around on a new `book.ncx_document` field (plus `book.ncx_href`
  for its in-zip location), the same idiom as `book.opf_document`.
  `book.toc` itself is untouched -- still the same read-only,
  already-parsed list anything can read from without caring about
  live trees at all. `writer.py` now serializes `book.ncx_document`
  back out (with a standard NCX doctype, same fixed-doctype idiom the
  chapter serializer already uses) whenever a new `book.ncx_modified`
  flag is set, mirroring `opf_modified`.
  Nothing rewrites anything yet -- this was purely the plumbing Phase
  3b onward depends on. Verified against all ten sample books: an
  untouched book round-trips byte-identical (mimetype through every
  chapter, OPF, and NCX), and a synthetic edit to a live
  `ncx_document` (relabeling a navPoint) writes back correctly,
  reloads cleanly, and touches nothing else in the archive. Also
  re-ran the full existing regression pass (analyze, repair,
  re-analyze, word-count diffing) across all ten sample books to
  confirm this plumbing change didn't disturb anything already
  built -- repaired output was byte-identical to what the
  pre-Phase-3a code produces, aside from the `dcterms:modified`
  timestamp every repair run already stamps.
- **Phase 3b -- Rewrite existing TOC/nav entries whose target file got
  split.** [x] Done.
  Turned out to split into two pieces once actually built, not one:
  the nav half was already covered by Phase 2's existing
  `find_links_into`/`rewrite_links` -- nav.xhtml is a normal `Chapter`
  (see Phase 3a), so its `<a href>` TOC/landmarks/page-list entries
  were already being scanned and rewritten by the exact same in-body
  link code every footnote and cross-reference goes through. Confirmed
  this with a synthetic EPUB3 fixture (a copy of ChaptersNotAligned-New
  with a hand-built nav.xhtml wired in) before writing anything new,
  so Phase 3b's actual new code is the NCX half only:
  `find_ncx_links_into`/`rewrite_ncx_links` in `crossref.py`, walking
  `book.ncx_document`'s `<content src="...">` elements the same way
  `find_links_into` walks `<a href>` elements, and reusing the exact
  same `href_by_id_by_origin` map Phase 2 already builds per split.
  Wired into `engine.py`'s `split_chapters` right after the existing
  cross-reference rewriter, with its own `[NCX Rewriter]` report
  section.
  Found and fixed a real bug in Phase 2's existing code while building
  the synthetic nav fixture above: `rewrite_links` mutated an `<a>`
  element's `href` directly but never flagged the chapter it lived in
  as `modified`, so the writer -- which only re-serializes chapters
  with `chapter.modified = True` -- silently discarded any fix made to
  a chapter that wasn't itself one of the split's own new files. Every
  sample book tested so far happened to avoid this (every touched
  chapter was always either segment 0 or a newly-created split file,
  both already flagged modified by `_wire_into_book`), so it had never
  surfaced. nav.xhtml was the first real case of a fix landing in a
  chapter the split itself never touched, which is exactly what
  exposed it. Fixed by giving `LinkReference` a `chapter` field
  (populated in `find_links_into`, which already has the `Chapter`
  object in scope) and setting `ref.chapter.modified = True` in
  `rewrite_links` whenever a link actually changes.
  Verified against all ten sample books: `split-structure` runs clean
  with zero integrity-check failures, every resulting file still
  re-analyzes as a valid EPUB, and a body-text-only word count (the
  book-wide total already has a documented `<head>`-duplication
  inflation issue from Phase 1, unrelated to this work) matches
  exactly between each original and its split output. Also spot-
  checked every rewritten NCX fragment on ChaptersNotAligned-New by
  parsing the split output back open and confirming each id genuinely
  exists in the file the NCX now points to (21 for 21). Re-ran the
  full repair regression pass (analyze, repair, re-analyze) across all
  ten sample books too, to confirm the `LinkReference` bug fix didn't
  touch anything outside the splitter/crossref path -- repair output
  was unchanged.
- **Phase 3c -- Generate new NCX entries for chapters that split apart
  with no entry of their own.** [x] Done.
  Turned out narrower than the plan doc originally worried once
  actually checked: every split segment already carries its own real,
  detected chapter title (splitter.py's SplitMarker.title, set from
  the structure analyzer's own marker text -- see the eligibility
  check in engine.py's split_chapters), so there was no genuine
  "what do we call an untitled chapter" design problem to solve in
  the normal case. `generate_missing_ncx_entries` in `crossref.py`
  just reuses that title directly, per Jacob's preference to keep as
  much of a book's own original structure as the analysis already
  found, rather than reusing the parent's old TOC label or inventing
  new text.
  Scope is deliberately narrow, matching Jacob's three-case framework
  above: a split whose resulting files have *no* existing NCX
  coverage at all (none of them match any pre-existing <content src>,
  even after Phase 3b's rewriting) is skipped and reported, not
  guessed at -- generating a whole TOC from nothing is case 2 of the
  framework, a separate future piece of work. A split segment with no
  detected title of its own (only possible for the leading,
  untitled chunk of content before a file's very first chapter
  marker -- see splitter.py's split_body_at_markers) is also skipped
  and reported rather than given a fabricated label; confirmed against
  Sidewinders that this is the right call, not a bug -- the flagged
  segment there really was front-matter (title page, publisher info,
  an epigraph) sitting before "Chapter 1," not a real chapter.
  New entries are inserted at the correct position by walking each
  split's resulting hrefs in order and anchoring on whichever existing
  navPoint the split's OWN files already point to (post Phase 3b
  rewriting), inserting any missing ones right after the nearest
  covered neighbor. playOrder is resequenced across the whole NCX
  afterward, but only for books that already used playOrder at all --
  a minimal NCX with no playOrder attributes stays that way. Assumes a
  flat NCX (no nested Parts/sections), the same assumption
  find_ncx_links_into/rewrite_ncx_links already make.
  Verified against all ten sample books via `split-structure`: every
  output still validates (readable, correct ZIP structure,
  container.xml and OPF found), word counts match exactly between
  original and split output on every book (no content lost or
  duplicated), and CrossReferences-Synthetic's generated entries
  (chapter_002.xhtml through chapter_005.xhtml, each correctly labeled
  Two/Three/Four/Five) land in the right position with playOrder
  correctly resequenced 1-5. Also confirmed the two failure-reporting
  paths -- Sidewinders' front-matter case above, and five books
  (BrokenSentences, ChaptersMisaligned, GutenbergText-ChapterSplit,
  RunTogetherText, Watermarks-SmallChapterNumbers) whose splits
  produced no existing NCX coverage at all and were correctly skipped
  rather than guessed at.
- **Phase 3d -- NCX/nav consistency + full regression.** If a book has
  both an NCX and an EPUB3 nav document, keep them in sync with each
  other rather than only fixing whichever one 3b/3c happened to touch
  first. Finishes with the standard full regression pass (word-count
  diffing plus analyze/repair/re-analyze) across every sample book.
  On hold -- see the priority note at the top of this doc; chapter-
  separation completeness (Phase 4's partially-split/no-marker work
  below) comes first.
- **Phase 3e -- Standalone TOC verification, decoupled from
  splitting.** Not started. Jacob's framing (2026-08-24): the real
  goal is (1) every detected chapter on its own page, then (2) the
  TOC correctly pointing at each one -- and today's code only ever
  touches the TOC as a side effect of an actual physical split
  happening in that run (see `split_chapters`'s `if len(nodes) < 2:
  continue` skip). A book that's already fully split, one chapter per
  file, never runs through any TOC logic at all today, correct or
  not. This phase adds a check that runs regardless of whether a
  split happened this run: walk every detected chapter, confirm the
  TOC has a correct entry pointing at it, and fix or generate one
  where it doesn't. On hold for the same reason as 3d -- see the
  priority note at the top of this doc.

### Priority note -- what "chapter separation" still needs before 3d/3e
Per the three-case framework above, Phase 3c only closed part of case
2 (a file with some detected markers gets an entry generated for each
resulting piece, but only once a split already has *something* to
anchor a new entry against). One piece of that gap is now closed:
- [x] Done -- **Generating NCX entries for a split with no existing
  entry to extend at all.** `generate_missing_ncx_entries` in
  `crossref.py` no longer skips this case (it used to report
  "Skipped -- no existing entry to extend" and leave the split with
  no TOC coverage). It now falls back to the book's own reading order
  (`book.chapters`, spine order, since the spine-order bug below is
  fixed) to find the nearest existing navPoint before the split's
  position and anchors new entries after it, or inserts at the very
  start of the navMap if nothing in the book is covered yet either.
  Origins are processed in spine order (not set-iteration order) so a
  run with several uncovered splits still ends up correctly sequenced,
  and a split processed earlier in the same run can itself become the
  anchor a later one falls back on. Verified against all ten sample
  books via `split-structure`: the five books that previously hit the
  skip path (BrokenSentences, ChaptersMisaligned,
  GutenbergText-ChapterSplit, RunTogetherText,
  Watermarks-SmallChapterNumbers) now get real entries generated
  instead, every output still reloads and validates cleanly, and a
  spine-order check across every generated entry in every affected
  book confirmed zero entries land out of order. The "no title to
  use" skip (an untitled leading front-matter segment) is unchanged --
  still reported, not guessed at.
  Still out of scope, and now the actual remaining gap in case 2:
  generating a nav.xhtml/NCX TOC from nothing for a book that has no
  NCX document (or an NCX with zero real entries to anchor against at
  all -- not even a single-entry stub) and no split running in the
  same session to trigger this path. That's Phase 3e's territory
  (standalone TOC verification, decoupled from splitting) more than a
  further extension of this function.
- Best-effort chapter splitting for a file with no detected markers at
  all (case 3), likely needing user verification when confidence is
  low, per Jacob's framework. Not started.

### Phase 4 -- Edge cases and hardening
- Nested structure (Parts with multiple chapters).
- Front matter / back matter -- split out too, or left bundled?
- Books already partially split (some chapters already separate
  files, some not) -- mixed-state handling. Related to the priority
  note above: case 3 of Jacob's framework (no detected markers at all)
  likely lives here too, since best-effort splitting needs the same
  kind of "what's already fine, what needs work" judgment this bullet
  describes.
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
