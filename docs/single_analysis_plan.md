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
  and which aspect of the page (its text, its structure and classes, its
  styles, the package files).
- Pages are fingerprinted by aspect when a finding is asked for. Repairs edit
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
- Output on all 25 example books must be identical to the previous version
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

- Build the store: findings declared with their scope and aspects, aspect
  fingerprints per page, refresh on read. It replaces `PageFindingCache` with
  the same behavior.
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
- Split point: chapters and front matter first (2a), then the rest (2b).
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

### Stretch 5: Class and style usage, part 2 (M)

Moves Line Height, Text Size, Embedded Stylesheet Consolidation and Body
Paragraph Look onto the same index.

### Stretch 6: Class and style usage, part 3 (L)

Moves Reader Settings, Paragraph Spacing and Class Standardize with the
automatic class mapping (its class profiles are rebuilt every pass today).
Split point: Reader Settings and Paragraph Spacing first, then Class
Standardize.

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
passes as a safety net. Run all 25 examples plus a second-run check, and
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

| Step | Group | Planned stretch | Done |
| --- | --- | --- | --- |
| Gutenberg Boilerplate Removal | A | 2, 10 | |
| Extra Content Removal | B | 9 | |
| Running Title Removal | A | 2, 10 | |
| Div Paragraphs | C | 8 | |
| Stray Line Break Removal | A | 2, 10 | |
| Paragraph Repair | A | 2 | |
| Chapter Markup | A | 2 | |
| Chapter Title Cleanup | C | 8 | |
| TOC Cleanup | C | 7 | |
| Dead Link Cleanup | C | 7 | |
| EPUB 3 Upgrade | A | 10 | |
| TOC Generation | A | 2, 10 | |
| Chapter Header Spacing | B | 9 | |
| Scene Break Normalizer | A | 2 | |
| Scene Opener Markers | B | 9 | |
| Scene Break Markers | B | 9 | |
| Body Paragraph Look | B | 5 | |
| Paragraph Spacing | B | 6 | |
| Line Height | C | 5 | |
| Reader Settings | B | 6 | |
| Text Size | C | 5 | |
| Extra Page Break Removal | C | 8 | |
| Image Repair | A | 10 | |
| Cover Repair | A | 7, 10 | |
| Color Strip | A | 2 | |
| Font Strip | A | 2 | |
| Embedded Stylesheet Consolidation | C | 5 | |
| Class Cleanup | C | 4 | |
| Span Cleanup | C | 4 | |
| Unused CSS | C | 4 | |
| Junk File Removal | C | 10 | |
| Mid-Word Hyphen Repair | C | 8 | |
| Ellipsis Normalizer | A | 1 | |
| Apostrophe Repair | A | 1 | |
| Quote Style | C | 8 | |
| Page Titles | C | 7 | |
| Whitespace Normalizer | A | 1 | |
| Title Cleanup | C | 10 | |
| Metadata Sync | A | 10 | |
| Identifier Standardize | C | 10 | |
| Author Initials | C | 10 | |
| Class Standardize (automatic mapping) | B | 6 | |

Stretch 1 adds two columns to this table, "reads" and "changes", and may
move steps between stretches.

## Measures

Baseline taken on 2026-11-05 after Page Reuse, in the development sandbox.
These times only compare with other times from the same machine. Fill in
the "now" column at Stretches 3, 6, 9 and 11.

| Measure | Baseline | Target | Now |
| --- | --- | --- | --- |
| Full analyses per Fix (book settling in 2 passes) | 2 | 1 | |
| Whole-book repair runs per Fix | every step twice, 9 steps a third time (settling) | once each | |
| Fix time, Tarzan | 33.4s | measure, no promise | |
| Fix time, War and Peace | 13.7s | measure, no promise | |
| Fix time, Images-PageNumbers | 6.5s | measure, no promise | |
| Steps that detect on their own | 26 of 42 | 0 | |

## Risks and how each is handled

- **A finding is reused when it should have been made again.** Every finding
  declares what it reads, a missed input is a bug in that declaration, and the
  default when unsure is to make the finding again. The shadow check and the
  identical-output rule catch the rest.
- **Elements held by findings after an edit.** Page Reuse found that a removed
  element still names its old document. The store checks that an element is
  still attached by walking up to the top of its own branch.
- **The examples miss a rare branch.** Output on 25 books cannot prove every
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

Open, with the default if Jacob does not say otherwise:

- Early stretches show little speed gain. Default: go ahead.
- Shadow check messages go to the Fix log only, not the GUI. Default: log only.
- Which of Jacob's own books to add to the test set for Stretch 11. Default:
  only the 25 examples plus any book Jacob attaches in chat.

## Status log

| Stretch | Date finished | Result |
| --- | --- | --- |
| Plan written | 2026-11-05 | This file |
