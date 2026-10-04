# GUI Redesign -- Planning Doc

**Status:** Phase 1 built (2026-10-03); Phases 2 and 3 not started.
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
2. **Details.** Title, author, series, language, the other metadata
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

## Details tab

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

Not done in this phase, on purpose: checkboxes in the boxes, the Fix
This Book button, auto-saving, and the Details tab.

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

### Phase 3 -- Details tab and polish

Move the metadata form and the cover handling into the Details tab, with
the "Change cover" fold-out. Also in this phase: swap Flask's built-in
server for Waitress when it is installed (falling back to the current
server when it is not) so the "development server" warning goes away, and
add a small tab icon so the browser stops asking for `favicon.ico`. Add the Review count badge and hide the tab
when empty. Remove the old Analysis template and any dead code. Update
`gui_plan.md` with a pointer to this doc as the current design.

Acceptance: a full run on a Calibre-managed test layout and on a
standalone book, covering edit, cover replace, review decision, Fix, and
Replace original in one session.

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
