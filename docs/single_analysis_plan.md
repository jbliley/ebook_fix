# Single Analysis Plan

Written 2026-11-05. The plan for making one analysis of the whole book the
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

### Stretch 7: Links and ids (M)

Index of links, ids and targets per page. Moves Dead Link Cleanup, TOC
Cleanup and Page Titles, and the link part of Cover Repair.

### Stretch 8: Text lines (M)

Index of each page's paragraphs and lines as text. Moves Mid-Word Hyphen
Repair, Quote Style, Div Paragraphs, Extra Page Break Removal and Chapter
Title Cleanup.

### Stretch 9: Scene and header steps (L)

Moves Scene Opener Markers, Scene Break Markers (and the book's own scene
marker it reuses), Chapter Header Spacing and Extra Content Removal.
Split point: the two scene steps first, then the other two.

### Stretch 10: Metadata and package steps (S)

Title Cleanup, Identifier Standardize, Author Initials, Junk File Removal,
EPUB 3 Upgrade, Metadata Sync and Image Repair read through the store.
Final check that every Group A step only reads findings.

## Phase 3: Retire the repeats

### Stretch 11: Switch the Fix to the dirty check (M)

Only after the shadow check has agreed on every test book through Stretch
10. Remove the settling step and the confirming analysis. Keep the cap on
passes as a safety net. Run all the examples plus a second-run check, and
ask Jacob to try the books that gave trouble before (Tarzan, War and
Peace, Sidewinders and others he picks).

### Stretch 12: Clean-up and write-up (S)

Delete dead code (refresh tables, `PageFindingCache` if nothing uses it),
update `README.md` to match its existing style without em-dashes, record the
final timing table, and mark this plan finished in the roadmap. After that
the plan file itself can be removed under the docs clean-up rule, with its
text kept in git history.

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
  Moves in Stretch 8.
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
| Gutenberg Boilerplate Removal | A | 2, 10 | 1.0 | classes, files removed, links/ids, attributes, package, page list, tags, text | |
| Extra Content Removal | B | 9 | 5.7 | files removed, package, page list, resources | |
| Running Title Removal | A | 2, 10 | 0.9 | classes, links/ids, attributes, tags, text | |
| Div Paragraphs | C | 8 | 4.0 | tags, CSS | |
| Stray Line Break Removal | A | 2, 10 | 0.0 | classes, links/ids, attributes, tags, text | |
| Paragraph Repair | A | 2 | 1.0 | classes, links/ids, attributes, tags, text | |
| Chapter Markup | A | 2 | 1.0 | classes, links/ids, attributes, tags, text | |
| Chapter Title Cleanup | C | 8 | 8.0 | nothing seen | |
| TOC Cleanup | C | 7 | 3.0 | nothing seen | |
| Dead Link Cleanup | C | 7 | 9.7 | classes, links/ids, attributes, tags, text | |
| EPUB 3 Upgrade | A | 10 | 7.5 | files edited, package | |
| TOC Generation | A | 2, 10 | 4.0 | NCX, links/ids, files edited | |
| Chapter Header Spacing | B | 9 | 9.0 | classes, links/ids, attributes, tags, text | |
| Scene Break Normalizer | A | 2 | 1.0 | classes, links/ids, attributes, tags, text | |
| Scene Opener Markers | B | 9 | 4.0 | nothing seen | |
| Scene Break Markers | B | 9 | 4.0 | classes, links/ids, attributes, tags, text | |
| Body Paragraph Look | B | 5 | 7.0 | classes | ✓ |
| Paragraph Spacing | B | 6 | 3.0 | CSS | ✓ |
| Line Height | C | 5 | 4.0 | CSS | ✓ |
| Reader Settings | B | 6 | 15.0 | classes, text, CSS | ✓ |
| Text Size | C | 5 | 10.0 | nothing seen | ✓ |
| Extra Page Break Removal | C | 8 | 2.0 | nothing seen | |
| Image Repair | A | 10 | 0.0 | classes, links/ids, attributes, tags, text | |
| Cover Repair | A | 7, 10 | 9.2 | files removed, links/ids, files edited, package | |
| Color Strip | A | 2 | 4.0 | CSS | |
| Font Strip | A | 2 | 5.0 | files removed, attributes, package, resources, CSS | |
| Embedded Stylesheet Consolidation | C | 5 | 2.0 | nothing seen | ✓ |
| Class Cleanup | C | 4 | 6.0 | classes | ✓ |
| Span Cleanup | C | 4 | 8.0 | classes, links/ids, attributes, tags, text | ✓ |
| Unused CSS | C | 4 | 7.0 | classes, files removed, links/ids, attributes, package, tags, text, resources, CSS | ✓ |
| Junk File Removal | C | 10 | 3.0 | files removed, links/ids, package, resources | |
| Mid-Word Hyphen Repair | C | 8 | 2.0 | text | |
| Ellipsis Normalizer | A | 1 | 0.0 | text | |
| Apostrophe Repair | A | 1 | 0.0 | text | |
| Quote Style | C | 8 | 5.7 | text | |
| Page Titles | C | 7 | 2.0 | text | |
| Whitespace Normalizer | A | 1 | 0.0 | text | |
| Title Cleanup | C | 10 | 0.0 | package | |
| Metadata Sync | A | 10 | 0.0 | nothing seen | |
| Identifier Standardize | C | 10 | 0.0 | package | |
| Author Initials | C | 10 | 0.0 | package | |
| Class Standardize (automatic mapping) | B | 6 | 4.0 | classes, CSS | |

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
These times only compare with other times from the same machine. Fill in
the "now" column at Stretches 3, 6, 9 and 11.

| Measure | Baseline | Target | Now |
| --- | --- | --- | --- |
| Full analyses per Fix (book settling in 2 passes) | 2 | 1 | 2 (Stretch 3) |
| Whole-book repair runs per Fix | every step twice, 9 steps a third time (settling) | once each | 93 runs of 42 steps, the same (Stretch 3) |
| Fix time, Tarzan | 33.4s | measure, no promise | 35.6s (same machine and day: 34.5s before Stretch 3) |
| Fix time, War and Peace | 13.7s | measure, no promise | 13.9s (13.4s before Stretch 3) |
| Fix time, Images-PageNumbers | 6.5s | measure, no promise | 6.6s (6.5s before Stretch 3) |
| Steps that detect on their own | 26 of 42 | 0 | 26 of 42 (Stretch 3) |
| Repair runs a dirty check could skip, with one whole-book fingerprint | 0% | most of the confirming pass | 4.7% (110 of 2,356 runs on 26 books; 0 wrong predictions) |

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

Open: nothing right now.

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
