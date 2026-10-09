# GUI Redesign -- Planning Doc

**Status:** All three phases built (2026-10-03, 2026-10-05 and
2026-10-09). The acceptance run for Phase 3 is recorded in its write-up.
Jacob has approved the direction below. This doc is the source of truth for what the redesign
is, why it is shaped this way, and what order it gets built in. It
replaces the Repair-tab-as-everything design from Phase 4 of
`docs/gui_plan.md`; that doc stays as the record of how the GUI got
here.

## Why now

Phase 4 put metadata, cover, and every repair checkbox on one Repair
tab, on the theory that one page would mean fewer steps. In use it did
the opposite. Jacob's words: the GUI is a jumbled mess. Two specific
problems came out of that conversation:

1. **Analysis is a wall of information.** It lists every finding from
   every analyzer, most of which an average person will never need to
   read. There is no quick answer to "is this book in good shape?"
2. **Too many steps for one outcome.** Fixing one book today means
   Browse, Analysis, Review (tick, save), Repair (edit metadata, Save
   Metadata, which reloads), stage a cover (reloads again), Apply
   Everything, then Replace original. Roughly eight clicks across three
   tabs, and three of them (metadata, cover, review) only "stage" a
   choice for later.

The staging steps exist for a historical reason (see the 2026-09-06
course correction in `gui_plan.md`): early on, each tab wrote its own
output file, so choices from different tabs could not combine. Apply
Everything already collects every staged choice into one pass, so the
single save Jacob wants is already possible. Staging is a leftover step
that can go.

## Goals

- **Fewest clicks.** The shortest path to a fixed book is two clicks:
  Browse, then Fix This Book. Editing details or reviewing decisions is
  optional on top of that, never required to reach the button.
- **A high-level answer first.** One summary at the top says what the
  book currently contains and highlights anything bad in red.
- **Everything else is available but out of the way.** Detail sits in
  categorized boxes that start collapsed.
- **No separate "save" steps.** Choices are remembered as they are made.
  Switching tabs never loses anything.
- **Plain language.** No module names (Ellipsis Normalizer, Class
  Standardization) in the main view.

Not goals: changing the engine, the CLI, any repair module, or the
config file. This is a rewrite of what the browser shows and how it
saves, not of what the tool does.

## The tabs

Four tabs, each with one job:

1. **Overview** (replaces Analysis and the repair half of Repair). The
   summary, the categorized boxes, and the one big button.
2. **Metadata.** Title, author, series, language, the other metadata
   fields, the lookup tools, and the cover (current cover on the left,
   fields on the right, "Change cover" as a fold-out underneath).
3. **Review.** Only the decisions that need a person: chapter-start
   candidates, possessives, decorative color and font, front/back
   matter labels. Shows a count badge, and is hidden when there is
   nothing to decide. Content and behavior are the same as today.
4. **Before / After.** Unchanged. The way to check the result.

The sticky top bar already exists. It gains the **Fix This Book**
button, visible from every tab.

## Overview page

```
Sandman Slim - Richard Kadrey                       [ Fix This Book ]
EPUB 2 | 99,661 words | 13 files            [ ] Replace original (keeps a backup)

Cover: yes    Table of contents: BROKEN    Chapters: none found
Title: looks like a filename     Language: English

11 fixes ready, 2 need your review

> Metadata and Cover          2 problems
> Structure and Navigation    4 problems
> Text and Typography         9 fixes
> Styling and Fonts           1 fix
> Images                      nothing found
> Files and Packaging         1 fix
> Advanced: choose individual fixes
```

### The summary strip

Only what the book currently contains, as short facts: format and
version, word count, file count, whether it has a cover, whether it has
a working table of contents, how many chapters were found, whether the
title and author look right, language. Any fact in the "bad" list below
is shown in red. The line under it gives the totals: how many fixes are
ready, how many need a person (linking to the Review tab).

### What counts as red

Red means "will cause trouble in a reader." Cosmetic untidiness is a
plain count with no color; a small blue note marks "needs your review."
First pass, to be adjusted by Jacob:

- No table of contents, or one with broken links or blank labels
- No cover, or the EPUB 2 and EPUB 3 cover declarations disagree
- Title looks like a filename, or the author is stored as "Last, First"
  (Title Cleanup already knows how to tell)
- Metadata disagrees between the EPUB and the Calibre record
- Dead links inside the text (Dead Link Cleanup already counts them)
- An empty file in the reading order (TOC Cleanup already counts them)
- Files the packaging check says are broken or missing from the manifest
- Missing images
- No chapters found
- Possible truncation

Everything above is something the analysis or a module's own `analyze()`
already computes. The exact fields are chosen in Phase 1 from what
exists, and any that need a new check are listed in that phase's
write-up rather than guessed at here.

### The categorized boxes

Six boxes, collapsed until clicked. Each header shows a count and turns
red when it holds a red item. Opening a box shows the findings in plain
English, and the checkbox for the fix that handles them, so analysis and
repair become one view instead of two.

| Box | Analysis sections it absorbs | Repairs behind it |
| --- | --- | --- |
| Metadata and Cover | Book Metadata, Metadata Mismatches, Cover Image | Title Cleanup, Metadata Sync, Identifier Standardize, Author Initials, Cover Repair |
| Structure and Navigation | Book Structure, Structure, Table of Contents | Chapter Markup, Chapter Title Cleanup, TOC Cleanup, Dead Link Cleanup, TOC Generation |
| Text and Typography | Apostrophes, Ellipsis, Whitespace, Typography, Paragraphs, Scene Breaks, Span Soup, Project Gutenberg Boilerplate | Gutenberg Boilerplate Removal, Running Title Removal, Stray Line Break Removal, Paragraph Repair, Scene Break Normalizer, Ellipsis, Apostrophe, Whitespace |
| Styling and Fonts | CSS, Possible Decorative Color, Possible Decorative Font | Color Strip, Font Strip |
| Images | Images | Image Repair |
| Files and Packaging | Files & Packaging, File Contents | EPUB 3 Upgrade |

Findings that need a person (Possessive Candidates, Possible Decorative
Color and Font, Possible Truncation, chapter-start candidates) do not
get a checkbox here. The box says "3 need your review" and links to the
Review tab. This keeps the rule the pipeline already follows: only
unambiguous cases are fixed without someone looking.

### Advanced: choose individual fixes

The full module checklist (today's Repair tab list, with "What's being
fixed?" for each) moves under one collapsed "Advanced" box. Everything
useful starts checked, and anything with nothing to do starts unchecked,
exactly as now. Most people never open it. Unchecking a fix here also
unchecks it inside its category box; the two are the same setting.

### The one button

**Fix This Book** runs the same Apply Everything pass as today: staged
metadata, staged cover, staged review decisions, and the checked
repairs, in one output file. If nothing is checked and nothing was
edited, it is disabled and says "Nothing to fix."

The **Replace original (keeps a backup)** checkbox sits beside it and
folds today's separate "Replace original file" step into the same click:
when checked, the finished file replaces the original, which is kept as
`<name>_original.epub`, exactly what the existing route already does.
The choice is remembered in the browser between sessions. It starts
unchecked, so the first run never touches the original; the backup is
always made when it is used.

## Metadata tab

The metadata form from today's Repair tab, with the cover shown beside
it. Two cover things are kept apart on purpose:

- **Cover Repair** (the module that brings the EPUB 2 and EPUB 3 cover
  declarations into agreement) needs no screen. It lives in the
  Metadata and Cover box on Overview.
- **Replacing the picture** is a choice, so it belongs here: upload a
  file, use the Calibre folder's cover, or fetch from a URL, all under a
  "Change cover" fold-out, with the new cover previewed next to the old.

A cover tab of its own was considered and rejected: one picture does not
justify a whole tab, and putting it beside the fields matches how
Calibre's own edit dialog works.

## Remembering choices without save buttons

Today three forms each have their own save/stage button, and each
reloads the page. After the redesign:

- Each field saves itself when it changes (a short pause after typing, or
  on leaving the field) through a background request, with a small
  "Saved" indicator. No page reload.
- Picking a cover file or URL stages it immediately. Review ticks and
  possessive choices stage as they are clicked.
- The server side does not change shape: the same `staged_metadata.json`,
  `staged_cover.json`, and `staged_review.json` files in the session
  folder remain the storage, and Apply still reads them. Only how they
  get written changes. This is what keeps Phase 2 low-risk.
- The unsaved-changes warning on the Metadata tab goes away, since
  nothing is ever unsaved.

## Build order

Three phases, each leaving a working GUI behind it.

### Phase 1 -- Overview, read-only

Build the Overview page: summary strip, red rules, six collapsed boxes,
the Advanced box (display only). Findings come from the existing
`analysis_view` builders (`build_overview`, `build_issues`,
`build_manual_review`) mapped into the categories above, plus a new
summary builder. Counts per box come from the same per-module
`analyze()` calls the Repair tab already makes; their results are cached
in the session folder so the page opens quickly. The old Analysis tab
and Repair tab stay untouched until Phase 2, so nothing that works today
is at risk.

Acceptance: every example book renders; box counts match what the
Repair tab shows; the red items appear on the books known to have the
problem (Sandman Slim for filename-style title, broken contents, empty
page and dead links; the synthetic cross-reference book for a dead
anchor; a book with no table of contents for the missing-TOC flag).

**Built 2026-10-03.** What shipped, and where it differs from the
sketch above:

- New first tab, **Overview** (`gui/overview_view.py`,
  `templates/overview.html`, route `/book/<id>/overview`). Opening a book
  now lands there. The old Analysis, Review, Repair and Before / After
  tabs are untouched. A short note on the page says repairs are still
  applied from the Repair tab until Phase 2.
- The summary strip shows title and author, format, word count and page
  count, then fact cards: Cover, Table of contents, Chapters, Title and
  author, Language, Library, plus Series and a fixed-layout note when they
  apply. A card turns red for: no cover or a broken cover declaration, no
  table of contents or one with broken links or blank or duplicate
  entries, no chapters found, a filename-style title or a "Last, First"
  author, a Calibre record that disagrees, dead links, empty pages in the
  reading order, broken image references, possible truncation, and
  garbled text. The totals line counts problems, ready repairs, and
  items waiting on the Review tab.
- Six collapsed boxes in the order above. A box header shows a red dot
  and "N problems" when it holds a red card, otherwise "N fixes" or
  "nothing found", plus "N to review". Opening a box shows What was
  found, What the repairs would change (plain-English names, each with a
  "What's being fixed?" fold-out capped at 25 lines with an "and N more"
  note), a link to the Review tab, and About this book (the old
  Overview facts). The Advanced box lists all 22 repairs with their real
  names, a check mark for the ones that will run, and the same fold-outs.
- Repairs the fixed-layout guard would skip are shown as skipped and are
  left out of the "ready" totals, so the page never promises a fix that
  will not run.
- The page's data is cached as `overview_cache.json` in the session
  folder, keyed on the book file and which repairs are turned on. Opening
  Sandman Slim takes about five seconds the first time and is instant
  after; War and Peace (570,000 words) takes about half a minute the
  first time.
- Review counts come from the same lists the Review tab renders, with
  chapter boundaries already pre-checked as safe left out.
- Found while building it and fixed in `analyzer.py`: the analysis counted
  the text inside `<style>`, `<script>` and `<title>` as book text.
  Sandman Slim showed 134,521 words instead of 99,691, because 35,000
  words' worth of CSS is pasted into its pages. This also skewed the
  thin-page count and the quote, apostrophe and hyphen counts. Nothing in
  the repair pipeline reads those numbers, and repairing all 23 example
  books with the old and new analyzer gives identical output files (apart
  from the `dcterms:modified` timestamp).

**Follow-up, same day, after Jacob tried it.** The layout was approved
and the first load took about five seconds. Two changes came out of that:

- The old **Analysis tab is gone.** Every `<li>` line it showed (323 lines
  across seven books, including Sandman Slim, Rules of Prey and the
  largest examples) was checked against the Overview page and all of them
  are there. The tab bar is now Overview, Review, Repair, Before / After.
  The `/book/<id>` address redirects to the Overview so old links still
  work, and `templates/book.html` is no longer used.
- **Suggested buttons on the Metadata form** (in the Repair tab until the
  Metadata tab exists). Jacob noticed the Overview announced "Kadrey,
  Richard - 01 Sandman Slim - Sandman Slim" becoming a clean title, author
  and series, but the Metadata form said nothing, so someone could retype
  what the repair was about to do anyway. When a book has a filename-style
  title, a banner above the form lists the suggested title, author and
  series with a **Use all suggestions** button, and each field gets a
  "Suggested:" button in the style of the existing "Pick a value" buttons.
  Clicking fills the field; Save Metadata keeps it. The banner says the
  Title Cleanup repair does the same thing automatically, so the buttons
  are only for looking at or adjusting the result first. The decision is
  made by one shared function (`suggest_from_filename_title` in
  `metadata/title_cleanup.py`) that Title Cleanup now also uses, so the
  form and the repair cannot disagree. Rules: worked out from the book's
  own title and author; a title or author suggestion disappears once the
  form holds anything else there (someone's own choice is not nagged
  about); a series is only suggested while the series box is empty.

Not done in this phase, on purpose: checkboxes in the boxes, the Fix
This Book button, auto-saving, and the Metadata tab.

Checked on 23 books (the 21 in `examples/` plus Sandman Slim and Rules of
Prey): every Overview renders, the red cards
appear where expected (Sandman Slim shows five: table of contents,
chapters, title and author, dead links, empty pages), and the other tabs
still render.

### Phase 2 -- One action, no staging

Add Fix This Book and the Replace-original checkbox to the sticky bar,
wire the category checkboxes and the Advanced list to the same settings,
and switch metadata, cover, and review to automatic saving. Remove the
Save Metadata, cover staging, and review save buttons and the unsaved
changes warning. The old Repair tab is retired in this phase.

Acceptance: the same selections produce the same output file as the old
two-step path (compared by extracted contents, word counts, and
validation, on every example book); a metadata edit followed by a tab
switch and a Fix keeps the edit; Replace original produces the backup
and the renamed file exactly as today; Fix is disabled when there is
nothing to do.

**Built 2026-10-05.** What shipped, and where it differs from the sketch:

- **Fix This Book** sits in the top bar on every tab, with the **Replace
  original (keeps a backup)** checkbox beside it (remembered in the
  browser, off the first time). It runs the same single pass the old Apply
  Everything did: staged metadata, a staged cover, staged review choices
  and the ticked repairs, into one output file. With the checkbox ticked
  it then replaces the original in the same click (the existing backup
  and rename logic, now shared by the checkbox and the old button on the
  result page). The button turns itself on or off by asking the server in
  the background (`/book/<id>/fix-state`) whether there is anything to do:
  a ticked repair that has something to change on this book, or a waiting
  metadata edit, cover, or review choice. Hovering shows what it will
  apply; with nothing to do it is disabled and says "Nothing to fix". After
  clicking it says "Fixing... this can take a minute".
- **Repair choices live on the Overview.** Every repair in the category
  boxes and in the Advanced list has a checkbox. A repair shown in both
  places is one setting: ticking either ticks both. The choice is saved as
  it is made (`staged_modules.json`, via `/book/<id>/modules`) and is what
  Fix uses from any tab. Until someone changes a checkbox the defaults are
  the old Repair tab's pre-checks (on in the config file, something to do
  on this book) minus anything the fixed-layout guard would skip. The
  totals line follows what is ticked.
- **The Repair tab is gone and the Metadata tab already exists.** The plan
  had the Metadata tab arriving in Phase 3, but retiring the Repair tab
  left the metadata form and cover with nowhere to live, so the old tab
  became the **Metadata** tab now: the metadata form, the ISBN lookup, the
  Suggested buttons and the cover handling, with the repair list and the
  Apply button removed. The tab bar is Overview, Metadata, Review, Before /
  After. (It was first named Details, from the plan; renamed Metadata
  the same week because metadata and the cover are all it holds, and the
  cover counts as metadata, as it does in Calibre.) `/book/<id>/repair` redirects to Details.
- **No save buttons.** Metadata fields save themselves a moment after the
  last keystroke (changes made in quick succession are one save, saves
  never overlap, and anything still waiting is saved before Fix, before a
  cover button, and when leaving the page). The Save Metadata button and
  the unsaved-changes warning are gone. Picking a cover file saves and
  stages it at once (no Upload button). The Review tab saves every tick as
  it is made, shows how many files will split, and no longer has a Stage
  Selected button.
- **Result page.** After Fix, a short page shows what was saved and what
  changed, the Replace original button if the original was not replaced,
  and links to Before / After and back to the Overview.

Two deliberate behavior changes, both so that what is on screen is what
gets applied:

1. Opening the Review tab saves its starting state once, so the
   pre-ticked safe chapter boundaries it shows are really applied by Fix.
   Before, they showed as ticked but did nothing unless someone clicked
   Stage Selected. A book whose Review tab is never opened still gets no
   review-based changes, as before.
2. After a plain Fix the staged metadata, cover, and review choices are
   kept, so a second Fix does not silently drop an edit. They are cleared
   only after the original has been replaced (the file on disk then
   contains them, and the staged review ids refer to a book that no longer
   exists). The old Apply cleared metadata and review choices every time.

Verified: the old pushed Repair-tab path and the new Fix path produce the
same output files for the default choices, for a metadata edit, and for a
Review selection (compared file by file, ignoring the `dcterms:modified`
timestamp); the only difference on Sandman Slim is the scene-opener
markers added since the old copy was taken, and removing them gives back
the old file exactly. Also checked through the app: a subset of repairs
applies only that subset, nothing ticked and nothing staged disables the
button, a metadata edit survives tab changes and is applied (and again on
a second Fix), Replace original makes the backup, renames the repaired
file into place and refuses to overwrite an existing backup, a repaired
book reopens as "Nothing to fix", Review reports the right split counts
from a background save, and a cover upload and clear work. The pages'
scripts were run in a simulated browser with 28 checks (checkbox syncing,
the pause before saving, quick edits collapsing into one save, flushing
before Fix and before a cover change, the leave-the-page beacon, Review
autosave and Select All). All 25 books (21 examples, Sandman Slim, Rules
of Prey and two already-repaired ones) render every tab.

Open question for Jacob: the Overview's "N to review" count leaves out
chapter boundaries that Review pre-ticks as safe, which reads as if they
are automatic, but they are only applied once the Review tab has been
opened (see change 1). Options: apply the safe pre-ticked boundaries
whenever Fix runs, or count them in "to review". Not changed yet.

### Phase 3 -- Metadata tab and polish

What remains after Phase 2 (the Metadata tab itself already exists, see
above):

- Requirement carried over from Jacob's 2026-10-03 feedback: every field
  on the Metadata tab must show what the repairs are going to do to it.
  Where Title Cleanup, Identifier Standardize, Author Initials or Metadata
  Sync will change a field, the field carries a short note ("Fix This Book
  will change this to: Sandman Slim") with a one-click "Use it now"
  button, so no one retypes what will be done anyway. The Suggested
  buttons are the first version of this; Phase 3 generalizes them to every
  metadata field a repair touches.
- Put the cover beside the fields, with "Change cover" as a fold-out.
- Swap Flask's built-in server for Waitress when it is installed (falling
  back to the current server when it is not) so the "development server"
  warning goes away, and add a small tab icon so the browser stops asking
  for `favicon.ico`.
- Add the Review count badge and hide the tab when empty.
- Remove dead code: the unused `metadata.html` template and anything else
  left over. Update `gui_plan.md` with a pointer to this doc as the
  current design.
- Settle the open question above about pre-ticked chapter boundaries.

(All of the above is done; see the Phase 3 write-up below.)

Acceptance: a full run on a Calibre-managed test layout and on a
standalone book, covering edit, cover replace, review decision, Fix, and
Replace original in one session.

**Built 2026-10-09.** What shipped, and where it differs from the sketch:

- **"Fix This Book will change this to" notes on every metadata field.**
  Opening the Metadata tab asks the server (`/book/<id>/metadata-preview`,
  `gui/metadata_preview.py`) what the ticked metadata repairs (Title
  Cleanup, Metadata Sync, Identifier Standardize, Author Initials) are going
  to do. It does this by running those same four repairs, in the engine's
  order, against a throwaway copy of the book that already has the person's
  saved edits applied, and comparing the fields before and after. It has no
  rules of its own, so it cannot disagree with Fix. A blue note appears under
  each field that will change, with the new value, the plain-English name of
  the repair, and a **Use it now** button; a banner at the top counts the
  changes and has **Use all now**. A note disappears as soon as the field
  holds that value, and the notes are recomputed after every autosave, so
  typing your own title removes the title note, and unticking a repair on
  the Overview removes its notes. This replaces the first version (the
  Suggested buttons and banner, which only covered a filename-style title).
  `suggest_from_filename_title` is still the one function Title Cleanup uses.
- **One button fills in a whole repair.** Title Cleanup only fixes the author
  while the title still looks like a filename, so filling in the title by hand
  would have left the author unfixed. A note's button therefore fills in every
  pending change from the same repair, and says so ("this button also fills
  in: author").
- **Cover beside the fields.** The Metadata tab is two columns (one on a
  narrow window): the current cover, with the pending replacement under it,
  stays in view on the left while the fields scroll on the right. **Change
  cover** is a fold-out under the cover holding the Calibre folder's cover,
  the file picker and the web address box. Behavior is unchanged.
- **Waitress and a tab icon.** `run_gui.py` serves the GUI with Waitress when
  it is installed (it is now in `requirements.txt`), which removes the
  "development server" warning, and falls back to Flask's own server with a
  one-line note when it is not. The connection timeout is raised to 15
  minutes so a long Fix on a very large book is never cut off. A small blue
  book icon is served at `/favicon.svg` (and `/favicon.ico`), so the browser
  stops asking for a file that does not exist.
- **Review count badge, hidden when empty.** The Review tab shows the number
  of items that need a decision on every page, and the tab hides itself when
  there is nothing at all to look at (it stays visible while it is the page
  being viewed). The number comes from the cached Overview data and the Fix
  button's background check, so no page waits on an analysis for it.
- **Open question settled: safe chapter boundaries are applied by Fix.** The
  boundaries the Review tab pre-ticks (the ones the split-safety bar calls
  safe to apply without a person looking) are now split at whenever Fix runs,
  whether or not the Review tab was ever opened. Before, they were only
  applied after the Review tab had been opened once, which meant whether a
  book's chapters got their own pages depended on a tab click. Once the
  Review tab has saved anything, its ticks decide, so unticking a boundary
  there still keeps it from being split. The Overview says how many will be
  split, the Fix button's hover text includes them, and the Fix button is
  enabled when they are the only thing to do. The "N to review" count still
  leaves them out, since they need no decision.
- **Dead code removed:** the unused `metadata.html` and `repair.html`
  templates, two unused imports in `app.py`. (`book.html` had already gone.)

## What does not change

`engine.py`, `cli.py`, every repair and analysis module, the config file
and its switches, the Review tab's content and behavior, the Before /
After tab, the Browse dialog, and the MOBI/AZW opening path. The CLI
`analyze` and `repair` commands are re-run as a regression check at the
end of each phase to confirm that.

## Risks

- **Auto-saving and the unsaved-changes guard.** Removing the guard is
  only safe once every field saves itself, so it goes in the same phase
  as auto-save, not before.
- **Overview speed.** Running every module's `analyze()` on each page
  load is slow on big books. Cache the results per session, and
  invalidate only when a Fix runs.
- **Two ways to set a fix (category box and Advanced list).** They must
  be one setting with two views, or they will drift apart. Built that
  way from the start.
- **What "red" should mean.** Too much red and nothing stands out; too
  little and real problems hide. The list above is a first pass and is
  expected to be adjusted after Jacob uses it on real books.

## Open questions

- The exact red list, above. Adjust after Phase 1 shows it on real
  books.
- Whether the Replace original checkbox should default to on once Jacob
  trusts it. Starts off.
- Whether the Before / After tab should move to a button on the summary
  ("See what will change") instead of a tab. Not needed now, but worth
  revisiting once Overview is in use.
