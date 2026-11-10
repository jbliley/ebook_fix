# Single Analysis Plan

Written 2026-11-05. All 12 stretches were built by 2026-11-10; what is still open is listed under Decisions and in the status log at the bottom. The plan for making one analysis of the whole book the
only place anything is detected, with every repair working from it. What
was built so far is in `docs/analysis_roadmap.md`; this file is the
roadmap for what is left, so nothing gets lost between sessions. Update
the status log at the bottom at the end of every stretch.

## The goal

Jacob's rule from the start of the project: the book is analyzed once,
and every repair acts on that analysis. A repair never goes looking for
problems on its own.

The end state:

- One full analysis per book, whether it is started from the Overview tab,
  Fix This Book, Fix a Folder or the command line.
- Every repair reads the analysis for what to fix. The Overview's per-repair
  checks (what each repair would change) read the same findings.
- When a repair changes a page, only that page's findings are made again,
  and only when something reads them.
- The Fix ends with a cheap check that nothing is left, instead of a whole
  second pass.

## Where things stand (2026-11-05)

Done so far, all in the roadmap: Fix reuses the Overview's analysis, repairs
settle inside one pass, the front matter and Gutenberg checks are cheaper,
and unchanged pages keep their whitespace, ellipsis and apostrophe findings.
These cut repeats but did not change who detects what.

Measured on 2026-11-05 across six example books, the 42 repair steps (41
modules plus automatic class mapping) fall into three groups:

- **Group A, 16 steps: already work from the analysis.** Gutenberg
  Boilerplate Removal, Running Title Removal, Stray Line Break Removal,
  EPUB 3 Upgrade, Image Repair, Ellipsis Normalizer, Apostrophe Repair,
  Whitespace Normalizer, Metadata Sync, Color Strip, Font Strip, Cover
  Repair, Paragraph Repair, Scene Break Normalizer, TOC Generation,
  Chapter Markup. (Cover Repair also reads pages itself to fix links.)
- **Group B, 8 steps: use only the chapter and front matter summary, then
  scan pages for their own subject.** Reader Settings, Chapter Header
  Spacing, Scene Break Markers, Scene Opener Markers, Class Standardize,
  Extra Content Removal, Body Paragraph Look, Paragraph Spacing.
- **Group C, 18 steps: ignore the analysis and scan the book themselves.**
  Chapter Title Cleanup, Text Size, Span Cleanup, Unused CSS, Dead Link
  Cleanup, Line Height, Class Cleanup, Quote Style, TOC Cleanup, Div
  Paragraphs, Extra Page Break Removal, Embedded Stylesheet Consolidation,
  Junk File Removal, Page Titles, Mid-Word Hyphen Repair, Title Cleanup,
  Identifier Standardize, Author Initials. Three of these (Title Cleanup,
  Identifier Standardize, Author Initials) read no pages at all.

So 26 of 42 steps do some detecting of their own. The page reads were
counted as accesses to a page's markup, so they rank the steps but are not
exact scan counts. Heaviest readers, in page accesses per page: Cover
Repair 7.8, Reader Settings 5.0, Chapter Title Cleanup 4.0, Text Size 3.6,
Span Cleanup 3.0, Chapter Header Spacing 3.0, Unused CSS 2.5, Dead Link
Cleanup 2.4.

The grouping came from watching which findings each step touched while it
ran. It does not cover rarely used branches, so Stretch 1 checks it again.

## Why not simply analyze once and repair from that

Repairs change the book, so findings made before the repairs go stale. That
staleness is the reason for the confirming pass, the settling step and the
hand-written refresh tables in `engine.py`, and it has caused real loops
(Chapter Header Spacing and Paragraph Repair undoing each other). A plan
that ignores it would either miss problems the repairs uncover or break
the rule that a second run changes nothing.

## The design in short

One analysis that keeps track of which pages have changed.

- Each finding is stored with what it reads: a single page or the whole book,
  and which part of the book (the text of a page, its structure and classes,
  the styles, the package files). Stretch 1 stores the page findings by the
  whole page only. Finer fingerprints per part are added in the stretch that
  first needs them (Stretch 2 or later), because today's four findings all
  depend on the page's whole markup.
- Pages are fingerprinted when a finding is asked for. Repairs edit
  the pages directly, so there is no place to hook a "page changed" signal;
  fingerprinting every page costs about 0.04s on Tarzan (659 pages).
- Asking for a finding whose inputs changed makes just that finding again,
  for just the pages that changed. This replaces the hand-written refresh
  tables.
- Repairs, the Overview's per-repair checks and the Review tab all read the
  same store.
- The Fix ends by asking the store what is still dirty and has findings,
  instead of repeating the whole analysis and every repair.
- The new end-of-Fix check first runs beside the old confirming pass, only
  writing to the log, until the two have agreed on every test book. Only
  then is the old pass removed.

What does not change: the repair logic itself, confidence gating, the
Review tab, and the rule that a second repair run changes nothing.

## Ground rules for every stretch

A stretch is one usage interval of work that ends with a ZIP Jacob can paste
into the repo.

- Pull the latest repo first; Jacob may have changed files.
- Say up front roughly how big the stretch is.
- Output on all example books (26 since the Bite Back box set was added,
  `examples/BiteBack-BoxSet.epub`) must be identical to the previous version
  (compared by unpacking the files, ignoring the modified time in the
  package file), and fixing an already fixed output must change nothing.
- Run old code against new code at the same time to keep the checks short.
  At most two full runs of the examples per stretch.
- Test the new pieces directly where a book alone would not show a fault
  (the Page Reuse work found a real flaw that way).
- Each migrated step keeps its old path until the new one is proven
  identical, so the program always works between stretches.
- Add a dated entry to `docs/analysis_roadmap.md`, update the checklist and
  status log in this file, and name the ZIP with a few words.
- No extra files: only project files, in the repo's folder structure.

Sizes are guesses, to be corrected after each stretch: **S** is about half
an interval, **M** is one interval, **L** is one interval that may spill
into a second. An L stretch lists its split point so the first half still
ships as a working ZIP.

## Phase 1: The tracked analysis (the foundation)

### Stretch 1: Store and inventory (M)

- Build the store: findings declared with their scope, refresh on read, one
  whole-page fingerprint per page (part-by-part fingerprints wait until a
  finding needs them). It is built on `PageFindingCache` with the same
  behavior.
- Move whitespace, ellipsis, apostrophe and possessive findings onto it.
- Fill in the checklist below for all 42 steps: what each reads, what it
  changes, and which shared finding could replace its own scan. This fixes
  the batches for Phase 2.
- Done when: examples identical, the three page checks do no more reads than
  today, checklist columns filled.

### Stretch 2: Findings that depend on other findings (L)

- Move chapters, front matter, paragraphs, line breaks, scene breaks, color
  and fonts onto the store. Several need the chapter and front matter
  findings first, so their inputs include those.
- Remove `_REFRESH_BEFORE`, `_SETTLE_REFRESH` and `_fresh_findings` once the
  store does the same job.
- Split point: chapters and front matter first (2a, done 2026-11-06), then
  the rest (2b, done 2026-11-06: paragraphs, line breaks, scene breaks,
  color, fonts). 2a keeps
  whole-book findings behind a whole-book fingerprint, which is coarse on
  purpose (any change anywhere makes them again). 2b adds the finer inputs
  where a finding gains from them, and removes the refresh tables.
- Done when: examples identical, same or fewer passes, no hand-written
  refresh left in `engine.py`.

### Stretch 3: Everything reads the store, plus the shadow check (M)

- The Overview, Fix This Book, Fix a Folder and the command line all use
  the store. Review tab choices feed it. The Overview's per-repair checks
  read it too.
- Add the shadow check: after the last pass, record what the dirty check
  says is left and compare it with what the real confirming pass finds.
  Disagreements go to the Fix log only.
- Record the first "now" numbers in the measures table below.
- Done when: examples show zero disagreements and the first "now" numbers
  are recorded.

## Phase 2: Move the steps that scan for themselves

Each stretch builds one shared index (a set of findings recorded once
per page) and moves the steps that use it. Which steps belong to which index
is the working guess from today's measurements; Stretch 1 settles it.

### Stretch 4: Class and style usage, part 1 (M)

Index of classes, inline styles and spans per page. Moves Class Cleanup,
Unused CSS and Span Cleanup.

Done 2026-11-06. The index is `class_usage.py` (`PageClassUsage`, one record
per page, kept by the store). The three steps read it for the used classes,
the span classes, the pages that have a span they could remove, the pages
that have a style block and the book's scripts, and look through only the
pages that can have something to do. Inline style counts are not in the
index yet, because none of these three steps reads them. Stretch 5 adds them
when Text Size and Line Height need them.

### Stretch 5: Class and style usage, part 2 (M)

Moves Line Height, Text Size, Embedded Stylesheet Consolidation and Body
Paragraph Look onto the same index.

Done 2026-11-06. The page record (`class_usage.py`) now also holds the classes
on non-inline elements and under headings (Line Height), whether any element
has an inline line height or an inline font size with text, the text each page
adds to the book's size totals (Text Size), and the classes of one-class
paragraphs with text (Body Paragraph Look). Text Size skips a page that has no
sized class and adds that page's totals from the record, in the same order, so
ties between sizes break the way they always did. `_book_rules` (shared with
Header Spacing and Paragraph Indent) can take the records for the style blocks.
The inline style counts planned for this stretch turned out to be needed only
as the two flags above, so no per-element counts were added.

### Stretch 6: Class and style usage, part 3 (L)

Moves Reader Settings, Paragraph Spacing and Class Standardize with the
automatic class mapping (its class profiles are rebuilt every pass today).
Split point: Reader Settings and Paragraph Spacing first, then Class
Standardize.

Stretch 6a (done): the page records also hold the classes on elements that are
not paragraphs or inside a heading, the classes on the body, whether a
paragraph or body has an inline style, and whether another element sets a
font family inline. The class profiles (`class_map.py`) are built once per book
state by the store and shared by the color finding, the font finding, Reader
Settings, Paragraph Spacing, Body Paragraph Look and the automatic Class
Standardize, through `class_profiles_for`. Reader Settings and Paragraph
Spacing open only pages whose record says something can change.

Stretch 6b (done): Class Standardize counts and renames only on pages whose
record lists a mapped class, and opens other pages only when they have a style
block or a class already named like a chapter heading's new name. Its class
profiles already came from the shared build in 6a. Stretch 6 is complete.

### Stretch 7: Links and ids (M)

Index of links, ids and targets per page. Moves Dead Link Cleanup, TOC
Cleanup and Page Titles, and the link part of Cover Repair.

Done 2026-11-08. The new page record (`link_index.py`, kept by the store as
the `links` finding) holds each page's ids and anchors, whether it is the
navigation document, the links that could be dead, the files its links go to,
the first few text blocks as heading keys with their anchors, the book paths
its links reach (for TOC Cleanup's check that nothing links to an empty page),
whether the body is empty, the text of its `<title>`, and the picture and
link addresses Cover Repair looks at. Dead Link Cleanup opens only pages whose
records show a link that goes to a missing file or a missing spot, and builds
its heading index from the records. TOC Cleanup reads the empty-page flag and
the link paths from the records. Page Titles opens only pages whose title is
missing or a placeholder. Cover Repair's stale cover links, the picture
rewrite after a rename and the unused-image check read the records. Cover
Repair's duplicate page search is left as it was: it looks at no more than
nine pages, so it is not a whole-book scan. The package part of Cover Repair
stays with Stretch 10.

### Stretch 8: Text lines (M)

Index of each page's paragraphs and lines as text. Moves Mid-Word Hyphen
Repair, Quote Style, Div Paragraphs, Extra Page Break Removal and Chapter
Title Cleanup.

Done 2026-11-08. The new page record (`text_lines.py`, kept by the store as
the `text_lines` finding) holds each page's words and hyphen pairs (for the
Mid-Word Hyphen join decision), its quote counts, its count of text
paragraphs and its text-only divs with their classes, whether any element
forces a page break, its leading headings with the separator each numbered
title uses, and the files its links go to as a whole. Each step adds up the
records for its book-wide decision (the word and pair counts, the quote
totals, the div plan, the separator style) and opens only the pages whose
record shows something that can change: a page holding one of the pairs to
join, a straight quote of a kind being converted, a div of a class in the
plan, a forced page break, or a heading whose separator differs from the
chosen one. Chapter Title Cleanup also reads the navigation page flag from
the link records and looks up whole-file links only when a contents entry has
passed every other check for a false chapter. Mid-Word Hyphen Repair's own
cost moved here.

### Stretch 9: Scene and header steps (L)

Moves Scene Opener Markers, Scene Break Markers (and the book's own scene
marker it reuses), Chapter Header Spacing and Extra Content Removal.
Split point: the two scene steps first, then the other two.

Done 2026-11-09, both halves in one stretch. The new page record
(`scene_index.py`, kept by the store as the `scene_index` finding) holds, for
each page, the facts the book-wide scene opener checks add up (paragraphs
with text, uses of each paragraph class and wrapper element, paragraphs that
open with capitals) and whether the page has any opener candidate at all;
whether any paragraph could be a text scene break marker or the book's own
marker line; and whether any element carries a chapter marker stamp. Scene
Opener Markers adds up the records and opens only a page with an opener
candidate. The book's own scene marker (`house_marker_text`, used by Scene
Break Normalizer, Paragraph Repair, Scene Opener Markers and Scene Break
Markers) and Scene Break Markers open only a page with a marker candidate.
Chapter Header Spacing opens only a page that has a stamp or holds one of
the chapter analysis's marker elements. Extra Content Removal needed no move:
it reads only the front matter labels and a handful of front and back pages
(never the story), so there is no whole-book scan in it to remove.

### Stretch 10: Metadata and package steps (S)

Title Cleanup, Identifier Standardize, Author Initials, Junk File Removal,
EPUB 3 Upgrade, Metadata Sync and Image Repair read through the store.
Final check that every Group A step only reads findings.

Done 2026-11-10. Of the seven steps, only Junk File Removal had a whole-book
scan to move. Title Cleanup, Identifier Standardize, Author Initials, Metadata
Sync and EPUB 3 Upgrade (which only reads the package file) read no pages, and
Image Repair works from the analysis it is given, so there was nothing to
change in them. The new page record (`file_refs.py`, kept by the store as the
`file_refs` finding) lists, for each page, the files it points at (pictures
and other files, including stylesheet `url(...)` values). Junk File Removal
adds up the records to decide which files are still in use and rescans only
the pages whose record names a file it is about to change. The final check
found two more leftovers: Color Strip and Font Strip walked every page for
inline colors, font families and `<font>` tags. The class usage record now
also notes whether a page has an inline color, an inline font family, a
`<font>` tag with a color or face, or a style block, and both steps open only
pages where one is present. The other Group A steps that touch pages act on
elements the analysis already found, or on a handful of front and back
pages.

## Phase 3: Retire the repeats

### Stretch 11: Switch the Fix to the dirty check (M)

Only after the shadow check has agreed on every test book through Stretch
10. Remove the settling step and the confirming analysis. Keep the cap on
passes as a safety net. Run all the examples plus a second-run check, and
ask Jacob to try the books that gave trouble before (Tarzan, War and
Peace, Sidewinders and others he picks).

Done 2026-11-10 (Jacob's trial on his own books still to come). The settling
step is gone: `_SETTLE_AFTER` and `_SETTLE_REFRESH` are removed, and the
repairs that used to run a second time inside a pass now simply run in the
next pass. A repair that found nothing to do, when the book is unchanged
since, is skipped (`DirtyTracker.is_clean`), and the pass ends when nothing
changed. The per-text findings of the three text repairs are still made again
just before they run. The analysis made at the start of a later pass reads
only the pages that changed, because of the store. The cap on passes stays
as the safety net. The tracker no longer predicts: it skips, and its Fix log
line reads "[Dirty check] N repair runs, M skipped as unchanged".

### Stretch 12: Clean-up and write-up (S)

Delete dead code (refresh tables, `PageFindingCache` if nothing uses it),
update `README.md` to match its existing style without em-dashes, record the
final timing table, and mark this plan finished in the roadmap. After that
the plan file itself can be removed under the docs clean-up rule, with its
text kept in git history.

Done 2026-11-10 (the plan file stays until Jacob's trial on his own books is
finished). A scan for code nothing calls found five helpers and one import,
all removed: `filter_contents_page_candidates` (`contents_filter.py`),
`is_marked_header` (`headings.py`), `extract_isbn_from_epub`
(`isbn_lookup.py`), `load_report` (`serialize.py`), `_text_from`
(`structure.py`) and the unused `Table` import in `report.py`. The refresh
tables are already gone except `_REFRESH_BEFORE` in `engine.py`, which is now
only three names (Ellipsis, Apostrophe, Whitespace) and is still used, so it
stays. `PageFindingCache` stays too: the store is built on it and every page
index goes through it. The README has a short paragraph on the one-analysis
idea, the checklist is ticked and the "now" column of the measures is filled
in. The measures show that the plan's two numbers about repeats were not met
(see Decisions).

Rough total: 12 stretches, about 11 intervals if nothing spills over (two
stretches are half size), up to 14 if all three L stretches spill over.
The program works after every stretch, and each one ships. Jacob can stop
after Stretch 3 and still have the foundation, or after Stretch 6 and have
the heaviest scanners moved.

## Parked, not forgotten

Speed work that is separate from this plan and waits until it is done, or
until Jacob wants it earlier:

- Faster whitespace analysis. It is the largest single cost on Tarzan
  (2.3s of 8.3s per analysis) and is still worked out in full three times
  per Fix (four before Page Reuse).
- Per-page reuse for paragraphs, typography, color and front matter
  (about 3s together on Tarzan). Mostly absorbed by Stretch 2.
- Mid-Word Hyphen Repair's own cost (about 1 to 2s per run on Tarzan).
  Done in Stretch 8.
- About a quarter of a Fix on Tarzan is neither analysis nor repair (opening
  the book, saving the analysis cache, writing the result). Not broken down
  yet.
- Fixing several books at the same time, and the other items in
  `docs/future_plans.md`, are not part of this plan.

## Checklist of repair steps

Group is today's group (A already reads the analysis, B uses only the
summary and scans for its subject, C scans on its own). A tick means the
step reads only the store and the checks above passed.

| Step | Group | Planned stretch | Reads | Changes | Done |
| --- | --- | --- | --- | --- | --- |
| Gutenberg Boilerplate Removal | A | 2, 10 | 1.0 | classes, files removed, links/ids, attributes, package, page list, tags, text | ✓ |
| Extra Content Removal | B | 9 | 5.7 | files removed, package, page list, resources | ✓ |
| Running Title Removal | A | 2, 10 | 0.9 | classes, links/ids, attributes, tags, text | ✓ |
| Div Paragraphs | C | 8 | 4.0 | tags, CSS | ✓ |
| Stray Line Break Removal | A | 2, 10 | 0.0 | classes, links/ids, attributes, tags, text | ✓ |
| Paragraph Repair | A | 2 | 1.0 | classes, links/ids, attributes, tags, text | ✓ |
| Chapter Markup | A | 2 | 1.0 | classes, links/ids, attributes, tags, text | ✓ |
| Chapter Title Cleanup | C | 8 | 8.0 | nothing seen | ✓ |
| TOC Cleanup | C | 7 | 3.0 | nothing seen | ✓ |
| Dead Link Cleanup | C | 7 | 9.7 | classes, links/ids, attributes, tags, text | ✓ |
| EPUB 3 Upgrade | A | 10 | 7.5 | files edited, package | ✓ |
| TOC Generation | A | 2, 10 | 4.0 | NCX, links/ids, files edited | ✓ |
| Chapter Header Spacing | B | 9 | 9.0 | classes, links/ids, attributes, tags, text | ✓ |
| Scene Break Normalizer | A | 2 | 1.0 | classes, links/ids, attributes, tags, text | ✓ |
| Scene Opener Markers | B | 9 | 4.0 | nothing seen | ✓ |
| Scene Break Markers | B | 9 | 4.0 | classes, links/ids, attributes, tags, text | ✓ |
| Body Paragraph Look | B | 5 | 7.0 | classes | ✓ |
| Paragraph Spacing | B | 6 | 3.0 | CSS | ✓ |
| Line Height | C | 5 | 4.0 | CSS | ✓ |
| Reader Settings | B | 6 | 15.0 | classes, text, CSS | ✓ |
| Text Size | C | 5 | 10.0 | nothing seen | ✓ |
| Extra Page Break Removal | C | 8 | 2.0 | nothing seen | ✓ |
| Image Repair | A | 10 | 0.0 | classes, links/ids, attributes, tags, text | ✓ |
| Cover Repair | A | 7, 10 | 9.2 | files removed, links/ids, files edited, package | ✓ |
| Color Strip | A | 2 | 4.0 | CSS | ✓ |
| Font Strip | A | 2 | 5.0 | files removed, attributes, package, resources, CSS | ✓ |
| Embedded Stylesheet Consolidation | C | 5 | 2.0 | nothing seen | ✓ |
| Class Cleanup | C | 4 | 6.0 | classes | ✓ |
| Span Cleanup | C | 4 | 8.0 | classes, links/ids, attributes, tags, text | ✓ |
| Unused CSS | C | 4 | 7.0 | classes, files removed, links/ids, attributes, package, tags, text, resources, CSS | ✓ |
| Junk File Removal | C | 10 | 3.0 | files removed, links/ids, package, resources | ✓ |
| Mid-Word Hyphen Repair | C | 8 | 2.0 | text | ✓ |
| Ellipsis Normalizer | A | 1 | 0.0 | text | ✓ |
| Apostrophe Repair | A | 1 | 0.0 | text | ✓ |
| Quote Style | C | 8 | 5.7 | text | ✓ |
| Page Titles | C | 7 | 2.0 | text | ✓ |
| Whitespace Normalizer | A | 1 | 0.0 | text | ✓ |
| Title Cleanup | C | 10 | 0.0 | package | ✓ |
| Metadata Sync | A | 10 | 0.0 | nothing seen | ✓ |
| Identifier Standardize | C | 10 | 0.0 | package | ✓ |
| Author Initials | C | 10 | 0.0 | package | ✓ |
| Class Standardize (automatic mapping) | B | 6 | 4.0 | classes, CSS | ✓ |

"Reads" is the most page accesses per page the step made itself, over the
26 example books (it ranks the steps and is not an exact scan count; steps
that use a shared finding show 0 or 1). "Changes" lists what the step edited
anywhere in those books ("nothing seen" means no example triggered it, so
Stretch 11 still has to treat it as unknown). The inventory moved no steps
between stretches. It showed the real page readers are heavier than the
first estimate: Reader Settings 15, Text Size 10, Dead Link Cleanup 9.7,
Cover Repair 9.2, Chapter Header Spacing 9.0, Chapter Title Cleanup 8.0,
Span Cleanup 8.0. Seven steps changed nothing on any example: Chapter Title Cleanup, TOC
Cleanup, Scene Opener Markers, Text Size, Extra Page Break Removal, Embedded
Stylesheet Consolidation and Metadata Sync. Six of them do their own
scanning, so the examples cannot check their move onto the shared index and
Stretches 4 to 9 need synthetic tests for them.

## Measures

Baseline taken on 2026-11-05 after Page Reuse, in the development sandbox.
These times only compare with other times from the same machine (the Stretch 12 times were taken on a different machine from the baseline, so they were compared with the code from before Stretch 11 on that machine). Fill in
the "now" column at Stretches 3, 6, 9 and 11.

| Measure | Baseline | Target | Now |
| --- | --- | --- | --- |
| Full analyses per Fix (book settling in 2 passes) | 2 | 1 | 3 (Stretch 12): the first one and two later ones that read only the pages that changed. It was 2 before Stretch 11 |
| Whole-book repair runs per Fix | every step twice, 9 steps a third time (settling) | once each | 112, 121 and 117 runs on the three timing books, with 14, 5 and 9 more skipped as unchanged (Stretch 12) |
| Fix time, Tarzan | 33.4s | measure, no promise | 50.2s (41.1s with the code from before Stretch 11, same machine and day; 3 passes against 2) |
| Fix time, War and Peace | 13.7s | measure, no promise | 19.7s (16.7s before Stretch 11, same machine and day) |
| Fix time, Images-PageNumbers | 6.5s | measure, no promise | 8.7s (8.0s before Stretch 11, same machine and day) |
| Steps that detect on their own | 26 of 42 | 0 | 0 of 42 walk the whole book on their own (checked step by step as each moved in Stretches 4 to 10; the page-read counter was not run again). Cover Repair's duplicate page search and Extra Content Removal still look at a handful of pages |
| Repair runs a dirty check could skip, with one whole-book fingerprint | 0% | most of the confirming pass | 4% to 11% skipped on the three timing books (5 to 14 of 126 runs; Stretch 12). Not counted again across all examples |

## Risks and how each is handled

- **A finding is reused when it should have been made again.** Every finding
  declares what it reads, a missed input is a bug in that declaration, and the
  default when unsure is to make the finding again. The shadow check and the
  identical-output rule catch the rest.
- **Elements held by findings after an edit.** Page Reuse found that a removed
  element still names its old document. The store checks that an element is
  still attached by walking up to the top of its own branch.
- **The examples miss a rare branch.** Output on 26 books cannot prove every
  path. The Stretch 11 trial on Jacob's own books is there for this, and
  any book that shows a difference gets added as a test.
- **A stretch runs over.** L stretches have a split point, and nothing ships
  half-migrated.
- **Jacob changes code between stretches.** The first step of every stretch
  is pulling the repo, and the store's interface stays small and stable.
- **Little speed gain early.** Expected. Stretches 1 to 3 build the
  foundation; the savings arrive as steps move (Phase 2) and the confirming
  pass goes (Stretch 11).

## Decisions

Made:

- The GUI is the main way the program is used, so the plan is built around it
  (the command line follows).
- One analysis per book is the top priority, and doing it in phases is fine.
- A deliverable at the end of every stretch, sized to fit one interval.
- The analysis cache file is for the program, not for reading, so a compact
  format is fine.
- Stretches 1 to 3 may show little speed gain, because the savings arrive in
  Phases 2 and 3. Speed is not the focus until this plan is complete.
- Shadow check messages go to the Fix log only, not the GUI.
- The test set is the examples folder plus any book Jacob attaches in chat.
  The Bite Back box set (Mark Henwick, three books in one file) is added
  now as `examples/BiteBack-BoxSet.epub`.

Open after Stretch 12:

- Jacob's trial of Tarzan, War and Peace, Sidewinders and other books of his own, which is the check for rare cases the examples miss.
- Every repair now reads the store, but the Fix still looks at the book more times than before. With the settling step gone, a repair that used to run twice inside one pass now runs in the next pass, so the three timing books take 3 passes instead of 2, and 9% to 22% longer on this machine. The later passes read only the pages that changed, so the extra cost is small per pass, but the target of one full analysis per Fix is not met and Stretch 11 made the time worse. The cause of the extra pass has not been investigated yet. This is the first thing to look at when work on speed starts.

## Status log

| Stretch | Date finished | Result |
| --- | --- | --- |
| Plan written | 2026-11-05 | This file |
| Open decisions answered | 2026-11-05 | Decisions section updated. Bite Back box set added to the examples (a Fix runs cleanly in 12.6s and 2 passes before any of this work starts) |
| Stretch 1: Store and inventory | 2026-11-06 | `analysis_store.py` added; whitespace, ellipsis, apostrophe and possessive findings read through it, and the analysis report carries it. Examples identical (26 books, only the OPF modified time differs), second run changes nothing, 17 direct store tests pass. Checklist filled in |
| Stretch 2a: Chapters and front matter on the store | 2026-11-06 | `chapters` and `frontmatter` come from `AnalysisStore.findings`, reused only while the book's state, the findings they are made from and the person's front matter choices are unchanged and every element they point at is still on a page. `_refresh_chapters` removed from `engine.py`. Examples identical (26 books, only the OPF modified time differs), second run changes nothing. Direct tests found and fixed one flaw before delivery (front matter choices were wrongly counted as an input to chapters) |
| Stretch 2b: The rest on the store | 2026-11-06 | `paragraphs`, `linebreaks`, `scene_breaks`, `color` and `fonts` come from the store, always made from the chapters and front matter as the book is now. The engine's refresh tables now only name which finding to ask the store for (`_refresh_paragraphs` and `_fresh_findings` removed). Examples identical (26 books, only the OPF modified time differs), second run changes nothing, direct tests pass. Not faster yet: Tarzan 34 to 35s, within the run-to-run noise (a whole-book fingerprint is taken for each request). The savings come from Stretches 4 to 10 |
| Stretch 3: Shadow check | 2026-11-06 | New `dirty_check.py`, `DirtyTracker`, watched by `_run_repair_passes`; every repair run is compared with the prediction "nothing changed since this repair last found nothing to do". Results go to the Fix log only (`[Shadow check]` lines). The command line `repair` keeps the store when it analyzes again after a split. Examples identical (26 books, only the OPF modified time differs), second run changes nothing, tracker tests pass. Over 26 books: 0 wrong predictions and 0 unreported book changes, but only 4.7% of runs skippable, because one fingerprint for the whole book makes every step dirty after any change anywhere. The skip rate is the number Phase 2 has to raise, by giving each step its own inputs |
| Stretch 4: Class and style usage, part 1 | 2026-11-06 | New `class_usage.py` and a per-page `class_usage` finding in the store. Class Cleanup, Span Cleanup and Unused CSS read it instead of walking every page, and open only pages that can have something to do. Examples identical (26 books, only the OPF modified time differs), second run changes nothing. A direct comparison of the old and new steps on 48 cases (24 books, each with injected edge cases: empty `class=""`, a span with an id, empty spans, a style block, a script) gave identical pages, files and counts. Shadow check unchanged: 0 wrong predictions in 2,356 runs |
| Stretch 5: Class and style usage, part 2 | 2026-11-06 | `class_usage.py` records extended; Line Height, Text Size, Embedded Stylesheet Consolidation and Body Paragraph Look read them. Examples identical (26 books, only the OPF modified time differs), second run changes nothing. Old against new on 24 books in 5 variants each (120 cases, including books made to trigger Text Size's two findings, a repeated embedded stylesheet, inline line heights and body paragraph variants): identical pages, files and counts. Shadow check unchanged: 0 wrong predictions in 2,356 runs |
| Stretch 6a: Reader Settings and Paragraph Spacing | 2026-11-07 | Page records extended; class profiles built once and shared (`class_profiles_for`). Reader Settings, Paragraph Spacing and Body Paragraph Look read them. Examples identical (26 books, only the OPF modified time differs), second run changes nothing. Old against new on 120 cases (24 books, 5 variants, including one that triggers Paragraph Spacing): identical. Shadow check unchanged: 0 wrong predictions in 2,356 runs. Tarzan time unchanged (within noise) |
| Stretch 6b: Class Standardize | 2026-11-07 | `class_standardize.py` reads the page records for both its count and its repair, so a page with no mapped class and no style block is not walked. Examples identical (26 books, only the OPF modified time differs), second run changes nothing. Old against new on 48 cases (24 books, plain and with injected edge cases: mapped classes in style blocks, with inline colors and page breaks, a scene break marker, an empty class; 36 changed the book): identical pages, files and counts, and a second pass finding nothing. Shadow check unchanged: 0 wrong predictions per book. Stretch 6 complete |
| Stretch 7: Links and ids | 2026-11-08 | New `link_index.py` and a per-page `links` finding in the store. Dead Link Cleanup, TOC Cleanup, Page Titles and the link part of Cover Repair read it and open only pages that can have something to do. Examples identical (26 books, only the OPF modified time differs), second run changes nothing. Old against new on 572 cases (26 books with injected dead links, placeholder titles, empty and linked-to empty pages, blank and repeated contents entries, stale cover links; analysis with and without a store) plus 40 cases on 20 books made to hold a duplicate cover page: identical pages, files, counts and report lines. Shadow check unchanged: 0 wrong predictions in 2,356 runs |
| Stretch 8: Text lines | 2026-11-08 | New `text_lines.py` and a per-page `text_lines` finding in the store. Mid-Word Hyphen Repair, Quote Style, Div Paragraphs, Extra Page Break Removal and Chapter Title Cleanup read it and open only pages that can have something to do. Examples identical (26 books, only the OPF modified time differs), second run changes nothing. Old against new on 1,820 cases (26 books with injected hyphen pairs, mixed quotes, text-only divs, forced page breaks and mixed title separators; analysis with and without a store) plus a made-up false chapter, one merged and one blocked: identical pages, files, counts and report lines. Shadow check unchanged: 0 wrong predictions in 2,356 runs. Time on the examples is unchanged (226s against 233s for all 26) |
| Stretch 9: Scene and header steps | 2026-11-09 | New `scene_index.py` and a per-page `scene_index` finding in the store. Scene Opener Markers, the book's own scene marker (and Scene Break Markers) and Chapter Header Spacing read it and open only pages that can have something to do; Extra Content Removal had no whole-book scan to move. Examples identical (27 books, only the OPF modified time differs), second run changes nothing. Old against new on 30 books (the 27 examples plus three made-up books with scene openers, with the book's own marker line, and with neither): identical opener findings, marker text and marker lists. Shadow check unchanged: 0 wrong predictions in 2,397 runs. Time not compared (old and new ran side by side) |
| Stretch 10: Metadata and package steps | 2026-11-10 | New `file_refs.py` and a per-page `file_refs` finding in the store. Junk File Removal reads it; Color Strip and Font Strip read new inline color and font facts on the class usage record and open only pages that have one. The other five steps read no pages, so nothing moved. Examples identical (27 books, only the OPF modified time differs). Color Strip and Font Strip with and without the store compared on 27 examples plus 5 made-up books: identical pages and reports. Time not compared |
| Stretch 11: Switch the Fix to the dirty check | 2026-11-10 | Settling step removed; unchanged repairs skipped in later passes; log line renamed to Dirty check. Examples identical (27 books, only the OPF modified time differs); fixing 8 of the fixed outputs again changed nothing. Skipped runs on the examples: 1 to 35 of 27 to 119 per book. Time not compared; Jacob's trial on Tarzan, War and Peace and Sidewinders still to do |
| Stretch 12: Clean-up and write-up | 2026-11-10 | Five unused helpers and one unused import removed; `PageFindingCache` and `_REFRESH_BEFORE` kept because they are still used. README paragraph added, checklist ticked, measures filled in. Examples identical (27 books, only the OPF modified time differs, old code against new); fixing 8 of the fixed outputs again changed nothing. Time compared with the code from before Stretch 11 on the same machine: 9% to 22% slower and one extra pass, recorded under Decisions |
