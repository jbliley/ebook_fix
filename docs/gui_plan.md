# GUI -- Planning Doc

**Status:** Scoping done, not started. This doc is the source of truth
for what the GUI is, why it's shaped this way, and what order it gets
built in.

## Why now

Several things already in the codebase are explicitly waiting on a
GUI before they can go further: cover art / description / author
auto-verification once an identifier is matched (`metadata_plan.md`),
and case 3 chapter-split candidates that need a person's eyes before
anything actually splits (`xhtml_recoder_plan.md`). Both are
"a person needs to look at this and decide" problems that a CLI/CSV
workflow can only handle awkwardly. The engine, analyzer, and repair
pipeline underneath don't change for this -- the GUI is a new way to
drive `engine.py`, not a new way to do the work.

## Shape of v1

- **Single book at a time.** Bulk/library mode is a real future goal,
  but explicitly out of scope for v1 -- it'll be a separate view that
  loops the same screens over a folder once those screens exist and
  are trusted.
- **Local web app, not a native desktop app.** A small Python backend
  (Flask) that runs on your own machine and opens in your browser
  (`localhost`, nothing sent over the internet). Reasons this beat a
  native toolkit (Tkinter/PyQt) for this project specifically:
  - Rich review screens (side-by-side fields, cover thumbnails,
    before/after page rendering) are far easier to build well in
    HTML/CSS than in a Python desktop toolkit.
  - It calls into `engine.py` the exact same way `cli.py` already
    does -- no engine changes needed to support it.
  - No installer/packaging fragility (PyInstaller-style desktop
    builds get brittle fast with a dependency like lxml).
  - Launched by double-clicking a `.bat` file; no install step.

## The three tabs

1. **Metadata.** Editable fields (title, author, language, date,
   description, etc.) pre-filled from analysis. Genuine mismatches
   between the EPUB and the Calibre sidecar are shown side-by-side so
   a value gets picked in the GUI instead of read out of
   `identifier_review.csv`.
2. **Review.** Everything else that needs a person's sign-off:
   case 3 chapter-split candidates (shown with surrounding text, not
   just a confidence label), cover mismatches, and any other
   NEEDS_REVIEW-class finding the analyzer produces. Accept/reject
   per item.
3. **Before / After.** Side-by-side rendered view of the book,
   original vs. what it will look like post-repair, with a
   chapter/page selector so you can browse the book and see each
   change in context rather than trusting a diff summary. This is the
   piece that makes structural changes (splits, running-title removal,
   Gutenberg boilerplate removal, TOC changes) actually inspectable
   before they're applied, not just something the pipeline reports it
   did afterward.

Nothing writes to the actual EPUB until an explicit "Apply" action,
same posture the CLI already has with `--dry-run` / review-gated
splits -- the GUI's job is to make the decision easier, not to change
what's allowed to happen automatically.

**Course correction (2026-09-06):** that last paragraph was the
original intent, but Phase 2 and Phase 3 didn't actually build it that
way -- Metadata's Save and Review's Split Selected each write their
own output immediately, independently, which means using both in one
session doesn't combine (whichever runs second overwrites the first --
flagged honestly in both phases' write-ups above rather than hidden).
Jacob confirmed this is a real problem, not just a rough edge: he
wants metadata changes and the standard repair pipeline to end up in
one output file, not several copies of the same book. Phases 4-6 below
are about actually building the single-Apply design this section
always described, plus two more things Jacob asked for directly: an
editable language field, and an Analysis tab that reads like a report
instead of a terminal transcript.

## Phases

### Phase 1 -- Backend scaffolding
- Minimal Flask app: a launcher script, a file-picker route to load
  one `.epub`, and a route that runs the existing analysis pass and
  returns its findings as JSON.
- No editing yet -- this phase is "can the browser show what
  `analyze` already knows," proving the plumbing works before any UI
  polish goes in.

**Done (2026-09-05).** Built as `src/gui/` (a new top-level package,
alongside `ebook_fix` and `metadata`), launched via `run_gui.py` /
`run_gui.bat` at the repo root -- double-clicking the `.bat` opens a
browser tab automatically. Two routes: `/` (an upload form) and
`/analyze` (accepts the uploaded file, runs the exact same
`Engine.analyze()` the CLI's `analyze` command already calls, shows
the result).

One adjustment from the phase's original description: rather than
JSON, Phase 1 captures analyze()'s existing terminal output (all four
of this project's module-level `rich` Console instances get
temporarily redirected to one buffer for the request, then restored)
and shows it as plain text in a `<pre>` block. This still proves the
full plumbing end-to-end with zero changes to engine.py itself, and
was less work than reshaping analyze() into a JSON contract before
Phase 2/3 exist to say what shape that contract actually needs to be.
Structured JSON is still the right call once the Metadata and Review
tabs need to bind specific fields to specific inputs -- deferred to
those phases, not skipped.

Tested end-to-end (Flask's test client, no server needed): a real
sample book renders its full analysis correctly, no file selected and
a non-`.epub` upload both show a friendly inline error instead of a
crash, and a genuinely corrupt upload shows the same friendly
validation/repair-attempt message the CLI already gives, no traceback.
No leftover temp files after a request (including the sibling
`.ebookfix-analysis.json` cache file `analyze()` writes, which needed
its own cleanup since it isn't visible next to the temp upload the
way it would be next to a real file on disk). `Flask>=3.0` added to
`pyproject.toml`/`requirements.txt`; `ebook-fix-gui` added as a
second `[project.scripts]` entry point alongside `ebook-fix`.

### Phase 2 -- Metadata tab
- Render editable fields from the analysis report.
- Mismatch fields render as a side-by-side picker instead of a plain
  input.
- Wire "Apply" for this tab to the existing metadata writer
  (`calibre_write.py` / `modules/metadata_repair.py`) -- no new
  writing logic, just a new caller.

**Mostly done (2026-09-05).** Introduced a session model to make this
possible at all: uploading now creates a session folder (under the
system temp directory, named by a uuid) so the Analysis and Metadata
tabs -- and Review/Before-After once they exist -- can all work
against the same uploaded book across several requests, `/book/
<session_id>` and `/book/<session_id>/metadata`. Fixed a Phase 1
regression along the way: analysis was defaulting to full detail
view, which is slow to generate and mostly noise for a book with
nothing wrong -- summary is now the default, with a one-click link to
switch to full detail.

The Metadata tab shows title, author, publisher, date, rights, and
description as editable fields, pre-filled with metadata.merge's
already-resolved value; a genuine mismatch shows an EPUB-value and a
Calibre-value button next to the field that fill it in with one
click, so picking a source doesn't mean retyping it, but the field
stays a normal text input the person can also just edit freely.
Language shows read-only, matching the merge logic's own reasoning
(EPUB and Calibre use different, both-correct formats for it, so
there's never anything to resolve). Series name/position are editable
too, via `ebook_fix.series.write()`.

Turned out different from the plan's "wire into calibre_write.py"
sketch above: that writer is deliberately gated to only fire when the
two sources already agree, precisely because it runs with no person
watching. The Metadata tab's whole reason to exist is the opposite
case -- a person looking at a genuine disagreement and choosing. So
"Save Changes" calls `metadata.core_fields.write_core_field()`
directly with whatever the person submitted (which might be either
side's value, or something they typed themselves), then
`ebook_fix.writer.EPUBWriter().save()` to produce a real output file,
with a download link on the page afterward. `calibre_write.py` still
owns the fully-automatic, no-person-involved path (CLI `repair`
/`auto_fix`); this is a second, human-in-the-loop path to the same
underlying fields, not a replacement for it.

Deferred, not forgotten: identifiers aren't editable in this tab yet
(read-only, on the Analysis tab's existing display) -- the Identifiers
list from the metadata-plan work is a different shape of problem (add/
remove/multiple schemes) than a single text field, worth its own pass
rather than bolting onto this one. Session folders also don't get
cleaned up yet -- harmless clutter for a single-user local tool for
now, but worth a real answer (e.g. delete-on-idle) before this goes
much further.

Tested: full upload -> view Metadata tab -> submit changed
title/author/series -> download -> confirmed the downloaded file's
OPF actually contains the submitted values, and that it still passes
`validate` cleanly. Ran the Analysis and Metadata tabs against all 11
sample books with no crashes. Full CLI regression (`analyze` on all
11 samples) still clean, confirming none of this touched the CLI's
own path.

### Phase 3 -- Review tab
- Render case 3 split candidates and other NEEDS_REVIEW findings,
  each with enough surrounding context to judge it.
- Accept/reject per item, feeding the same review-gated repair paths
  that already exist (`split_chapters`, etc.), rather than inventing
  a parallel decision mechanism.

**Mostly done (2026-09-05).** Turned out the plan's assumption above
wasn't quite right: `split_chapters()` isn't actually review-gated
yet -- its own docstring says so plainly ("NOT gated by the full
split-safety-bar corroboration requirement yet... Treat any output
from this command as a mechanics test, not a finished conversion").
It auto-splits anything SEQUENCE_ONLY or better with no person
involved at all, which is exactly the opposite of what a Review tab
is for. Good news: `structure.py`'s `BoundaryEvidence` already tracks
everything a person would need to judge a boundary --
`confidence` (NONE / SEQUENCE_ONLY / NEEDS_REVIEW / CORROBORATED) and
a `notes` list explicitly written, per its own docstring, "to surface
directly in a review command/GUI, not just for debugging." That data
just had nowhere to go before now.

Two small additions, neither touching anything the CLI calls:
- `structure.element_text_preview()`: a short text snippet from a
  candidate's own element, for recognizing the spot without opening
  the book. Same idea as `case3_structural.py`'s private
  `_preview_text`, kept as its own public copy rather than shared,
  since this one needs to work for any `StructureNode`, not just a
  case3 divider.
- `Engine.split_marked()`: applies a split only at the exact
  boundaries given, rather than every eligible one automatically --
  factors the existing split+rewire+write steps `split_chapters()`
  already does into something a caller can point at a person-approved
  subset. `split_chapters()` itself is untouched and still does what
  it always did.

The Review tab groups candidates by file (a file needs 2+ accepted
boundaries to split at all -- one alone has nothing to cut against,
same gate `split_chapters()` already used). CORROBORATED boundaries
are pre-checked, since that's the one level the project's own safety
bar already calls safe without a person watching; SEQUENCE_ONLY and
NEEDS_REVIEW show up too, but require an active choice, each with its
own preview snippet and notes. "Split Selected" applies only the
accepted boundaries and offers the result as a download, same pattern
as the Metadata tab.

One correctness gap worth being upfront about, not silently papered
over: Metadata's "Save" and Review's "Split Selected" each write their
own `output.epub` independently from the *original* upload, so doing
both in the same session doesn't currently combine into one file with
both sets of changes -- whichever runs second simply overwrites the
first's output. Building a single, cross-tab "apply everything" file
is explicitly Phase 5's job (see above); this is worth flagging now so
it doesn't look like an accidental data-loss bug later.

Tested: detected real candidates across 8 of the 11 sample books (the
other 3 have nothing to flag -- clean books with existing chapter
markers and TOC entries that already line up, exactly Jacob's "case
1" from the three-case framework). Accepted a real group of boundaries
in `GutenbergText-ChapterSplit.epub`, confirmed the file count actually
grew (15 -> 32 files) and the result still passes `validate`. Also
confirmed selecting only one boundary in a file, or nothing at all,
shows a clear "nothing was split" message rather than silently doing
nothing or erroring. Ran all three tabs (Analysis/Metadata/Review)
against all 11 sample books with no crashes, plus a full CLI
regression (`analyze` and `split-structure` on all 11) to confirm the
two additions above didn't touch the CLI's own behavior at all.

### Bug fix -- Calibre detection and save location (2026-09-05)

Jacob tried real books from his own Calibre library and found every
one showed as standalone, and that fixed output always went to a
browser-download location rather than back into the book's own
folder. Both traced to the same root cause: uploading a book (the
only way to open one, until now) hands the browser's raw bytes to the
server, which then gets saved into a throwaway session folder --
`book.source` (what `calibre_detect.py` walks up from looking for
`metadata.db` and Calibre's folder-naming convention) was always that
temp copy's path, never the real one. No amount of fixing the
detection logic itself could have helped; the actual book location
was simply gone by the time detection ran. Browsers don't hand over a
real file path on upload at all, for their own users' security --
this isn't an ebook_fix bug to patch around, it's what browser file
inputs are designed to do.

Fix: the upload page now also accepts a **typed/pasted file path** as
an alternative to browsing for a file. Opening a book this way reads
it directly from its real location on disk -- no copy, no session-
folder detour -- so `calibre_detect.py` sees the book's actual folder
structure and works exactly as it already does for the CLI. Once a
book is open this way, saving a fix (Metadata tab or Review tab) also
writes directly back next to the original as `<name>_fixed.epub`,
matching the CLI's own default output convention exactly, with no
browser download step at all -- the file's just already in the right
folder. A book opened by upload still works exactly as before
(temp copy, standalone, browser download for output) since there's
no real location to speak of in that case; the Metadata tab now shows
a plain note when a book displays as standalone, pointing at
"Open by Path" as the fix, so this doesn't look like a silent mystery
the next time someone hits it.

Tested against a constructed fake Calibre library (a book folder named
`Title (42)`, a sibling `metadata.opf` with a deliberately different
title, and a `metadata.db` two levels up) to confirm the whole chain
end to end: opening by path correctly shows the book as Calibre-
managed, correctly flags the title MISMATCH between the EPUB and the
fake sidecar, and both the Metadata tab's Save and the Review tab's
Split Selected write their output directly into that same folder as
`<name>_fixed.epub` rather than offering a download -- confirmed the
resulting file passes `validate` and actually contains the resolved
title. Also confirmed a bad path or wrong extension shows a plain
inline error rather than a crash. Re-ran the full tab regression
(Analysis/Metadata/Review across all 11 sample books via the upload
path) plus a full CLI regression to confirm the upload flow and the
CLI itself are both unaffected by any of this.

A native OS "Browse..." dialog (so path-based opening doesn't mean
hand-typing a long path) would be a nice next step here, but needs
testing on Jacob's own Windows machine before it ships -- there's no
way to verify a real desktop file dialog from this sandbox.

### Follow-up -- single Browse button, path-only (2026-09-05)

Jacob tested the fix above and confirmed it correctly detects both
Calibre-managed and standalone books -- but rejected the two-box
layout (a file picker and a separate typed-path field) outright:
"having two boxes is not gonna work." He wanted one Browse button that
gets the real path itself, the way any normal desktop program's Open
dialog does.

Built the native dialog that was flagged as a next step above: a new
`/browse` route runs a short-lived `python -c` subprocess (kept out of
Flask's own process, since tkinter's event loop doesn't mix well with
Flask's request-handling threads) that opens a real OS file-picker via
tkinter (ships with a standard Python install) and returns the chosen
path as JSON. The homepage is now a single "Browse for a Book..."
button; a small amount of JS calls `/browse`, then submits the
resulting path to `/upload` automatically -- no visible path field at
all in the normal case.

Since path-based opening now works for every book Jacob tried, Calibre
-managed or not, there was no remaining reason to keep the old
browser-upload-by-bytes code path around at all -- removed it
entirely rather than leaving two ways to open a book (one of which,
per the whole point of the earlier fix, never worked as well as the
other). This simplified more than just the homepage: every session is
now guaranteed to have a real file location, so the earlier
"uploaded vs. real-path session" branching throughout `app.py`
(`_is_real_path_session`, the download-vs-save-to-disk fork in what
was `_output_path_for`) collapsed into one path -- `_fixed_output_path()`
always writes "<name>_fixed.epub" next to the real file, full stop.
The `/book/<id>/download` route and its `send_file` import are gone
too, since nothing produces a download-only file anymore.

Tested: re-ran the full fake-Calibre-library scenario from the entry
above against the simplified code and confirmed identical
behavior (detection, MISMATCH flagging, save-to-disk location, and a
valid resulting file). Confirmed `/browse` fails with a clear JSON
error rather than crashing when no display is available to open a
dialog on (this sandbox has none -- the success path itself, an
actual dialog popping up and returning a path, still needs Jacob's
own Windows machine to confirm, same caveat as before). Re-ran the
full tab regression across all 11 sample books via path-based opening,
plus a full CLI regression -- both clean.

### Phase 4 -- Unified Repair tab (Jacob's top priority)
A new tab that's the actual single point of "make the changes real,"
replacing the immediate-write behavior Metadata and Review currently
have:

- **Standard repairs, as checkboxes.** Every toggleable repair module
  already has an `enabled` flag on `Config` (`WhitespaceRepairConfig`,
  `GutenbergRepairConfig`, `CoverRepairConfig`, `IdentifierRepairConfig`,
  `AuthorInitialsConfig`, and so on -- roughly 15 in total). The Repair
  tab reads the project's own `ebook_fix.toml` (same file the CLI
  already reads) and pre-checks each box to match it, rather than
  inventing a second set of defaults the GUI has to keep in sync with
  the config file by hand.
- **Metadata and Review become staging, not writing.** Metadata's
  "Save Changes" and Review's "Split Selected" stop writing a file
  immediately -- they record what was chosen (edited field values;
  accepted split-candidate ids) into the session folder instead, and
  the Repair tab is what actually applies anything, in one pass:
  staged metadata edits, staged split boundaries, and every checked
  standard-repair module, ending in exactly one `EPUBWriter().save()`
  call and one `<name>_fixed.epub` on disk. Both tabs' pages should
  make clear a choice is staged, not yet applied, so this isn't a
  surprise the first time someone hits it.
- **Implementation shape:** `Engine.repair()` itself isn't the right
  thing to call here as-is -- it loads its own fresh copy of the book
  and writes at the end, with no way to hand it a book that already
  has staged edits applied. Rather than reworking `repair()` itself
  (CLI-facing, well-tested, no reason to touch it), the plan is to
  call the same lower-level pieces directly, the same way Phase 2/3
  already do: load the book once, apply staged metadata fields
  (`write_core_field`) and staged split boundaries
  (`Engine.split_marked`'s underlying pieces), then run whichever
  `Engine(config=...).modules` came out of the checkboxes, each via
  its own `.repair(book, analysis_report)`, then write once. This
  reuses every module's real logic; it just orchestrates the call
  order itself instead of going through `repair()`'s own shell.
- **Regression bar:** this is the highest-stakes phase so far, since
  it's the first thing actually composing several kinds of changes
  into one file. Same standard as everything else in this project --
  idempotent, valid, word-count parity where nothing should have
  changed -- but worth being extra thorough here specifically, given
  the combination is new even where each individual piece is already
  tested.

**Done (2026-09-06).** Built essentially as scoped above. A few
specifics worth recording:

- `Engine` gained one more small public method alongside
  `split_marked()`: `run_selected_repairs(book, modules,
  analysis_report, max_passes=5)`, a thin wrapper around the existing
  `_run_repair_passes()` (the CLI's own multi-pass convergence loop --
  re-analyzes and re-runs the module list until a pass changes
  nothing, capped at 5). Reusing this instead of writing a second
  convergence loop means the GUI's Repair tab gets the exact same
  "a fix can reveal a fresh, adjacent issue" handling `repair`/
  `auto_fix` already have, for free.
- Metadata's "Save Changes" is now "Stage Changes," and Review's
  "Split Selected" is now "Stage Selected." Each just writes a small
  JSON file into the session folder (`staged_metadata.json`,
  `staged_review.json`) -- nothing touches the actual book until the
  Repair tab's "Apply Everything." Revisiting either tab shows the
  staged values/selections, not the original analysis, so a staged
  edit doesn't quietly vanish from view.
- The Review tab's staging step still re-validates the "2+ boundaries
  per file" rule immediately (as a preview: "N files will split," not
  silently deferred), even though the real check happens again at
  apply time against a fresh candidate scan -- the boundaries staged
  are re-derived from the book at apply time rather than trusted from
  the moment they were checked, in case anything changed in between.
- Apply order: staged metadata fields, then staged split boundaries
  (via the engine's own `_split_and_rewire`, not `split_marked`, to
  avoid an intermediate write nothing else needs), then a fresh
  `EPUBAnalyzer` pass so the standard-repair modules see the book as
  it actually is right now, then the checked modules via
  `run_selected_repairs()`, then exactly one `EPUBWriter().save()`.
  Both staged JSON files are deleted once applied, so the tabs read as
  "nothing staged" again afterward.

Tested against a constructed fake-Calibre-library book (same fixture
as the earlier Calibre-detection fix): staged a metadata fix (title,
publisher, series) and a chapter split in the same session, applied
both plus every standard repair together, and confirmed the single
resulting file has all of it -- the corrected title/publisher/series
AND the physical split (13 files -> 23) -- and still passes `validate`.
Ran a full "apply every module" pass across all 11 sample books (using
scratch copies, not the real `examples/` folder, so this didn't leave
`_fixed.epub` clutter in the repo): all 11 succeed, all 11 pass
`validate`, and running Apply a second time against each already-fixed
output makes zero further changes on every single one -- full
idempotency held even through this much more complex, multi-source
combined pass. Full CLI regression (`analyze` and `repair` on all 11
samples) stayed clean throughout, confirming none of this touched the
CLI's own path.

### Phase 5 -- Editable language field
Language currently shows read-only on the Metadata tab, because the
EPUB and Calibre sides are never a real disagreement (see
`language_codes.py`) -- there's nothing to *resolve*. Jacob wants it
editable anyway, as a dropdown, for the case where the language is
just wrong outright, not merely formatted differently between the two
sides. Small, self-contained: a dropdown of real language codes/names
(a short well-known list -- doesn't need every ISO 639 code, just
what's actually likely to come up), wired into the same staged-edit
model Phase 4 introduces for the other fields, writing to the
`dc:language` element the same way `write_core_field` already would if
"language" weren't currently excluded from `_EDITABLE_FIELDS`.

**Done (2026-09-06).** Built essentially as scoped above, with a couple
of specifics worth recording:

- `language_codes.py` gained `COMMON_LANGUAGES` (about 50 codes,
  common personal-library languages, not an attempt at ISO 639
  completeness) and `language_options(current_value)`, which turns
  that list into sorted `(code, "Name (code)")` pairs for a `<select>`
  -- and, if the book's own current value isn't one of the common
  codes (a region subtag like `en-GB`, or anything unusual), appends
  it as its own entry rather than dropping it from the list. A book's
  existing value should stay visible and selected until a person
  actively picks something else, not vanish the moment the dropdown
  renders.
- `core_fields.py`'s `_DC_FIELD_MAP` now includes `"language"`, so
  `write_core_field` can write `dc:language` -- but `merge.py`'s
  `_WRITABLE_FIELDS` (what the CLI's fully-automatic `repair`/
  `auto_fix` path is allowed to touch) still excludes it. This is
  deliberate, not an oversight: language only becomes writable through
  the GUI's Metadata tab, where a person is the one choosing the new
  value, never through the automatic merge path.
- The dropdown edits the EPUB's own `dc:language` directly -- it's
  pre-filled from `merged.language.epub_value` (falling back to
  `display_value` if the EPUB's own field is blank), not from whatever
  `MergedField.display_value` would otherwise resolve to, since
  there's no Calibre-side value to prefer here the way there is for
  title/author/etc.
- Staged the same way every other Metadata field already is (Phase
  4's model): "Stage Changes" writes it into `staged_metadata.json`
  under a `"language"` key, and the Repair tab's "Apply Everything"
  writes it via `write_core_field` alongside the other staged fields.
  Read with `.get("language")` rather than `[...]`, so a session
  staged before this phase shipped (no `"language"` key at all) just
  means "nothing to change" instead of a `KeyError` at apply time.
- Added a `select` rule to `base.html`'s shared CSS alongside the
  existing `input[type=text]`/`textarea` rule, so the new dropdown
  matches the rest of the form instead of using the browser default
  style.

Tested: staged a language change on a sample book with no other edits,
applied with every repair module unchecked (isolating just the
language write), confirmed the resulting file's `dc:language` matches
what was picked and still passes `validate`. Ran the Metadata tab
across all 11 sample books, confirming the dropdown renders and
selects the book's actual current value (including `MM21.epub`'s
`en-GB` and `GutenbergText-ChapterSplit.epub`'s blank language) --
then staged/applied each with the language left untouched and
confirmed it comes out unchanged every time. Confirmed
`language_options()` handles an unusual code (not in the common list),
a known code (no duplicate entry), and a blank current value (no
empty-string option added) directly. Full CLI regression (`analyze`
on all 11 samples) stayed clean, confirming `_WRITABLE_FIELDS` still
keeps this out of the automatic path.

### Phase 6 -- Analysis tab rebuild
Replaces the raw captured-CLI-text `<pre>` block from Phase 1 with an
actual HTML report: real structure (headings, lists), and -- Jacob's
specific ask -- **issues kept in their own section, separate from
FYI-only information** like word count or chapter count, rather than
interleaved the way the CLI's terminal-oriented output naturally is.

This phase's real first task is an audit, not a layout exercise: going
through everything `analyze()` currently reports (`summary`,
`chapters`, `typography`, `css`, `frontmatter`, `merged_core_fields`,
`merged_identifiers`, `calibre_context`, etc.) and sorting each piece
into "a problem worth a person's attention" vs. "context, not a
problem" -- that classification doesn't exist anywhere yet, since the
CLI's plain-text report was never designed to make that distinction
structurally. Once that's sorted, this talks to the analyzer directly
for structured data, the same way the Metadata tab already does,
rather than capturing printed text the way Phase 1 does today.

#### Phase 6 audit -- Issue vs. FYI, section by section (2026-09-06)

Went through `Engine.analyze()` (`src/ebook_fix/engine.py`, lines
249-964) line by line, since that's the only place this report is
fully assembled today -- the GUI's Analysis tab currently just
captures its printed text wholesale (see Phase 1). The CLI itself
already separates its output into two top-level comment blocks, "1.
ANALYSIS & OVERVIEW" and "2. ISSUES & FINDINGS", which lines up with
Jacob's Issue-vs-FYI split more closely than expected -- but it's not
a clean 1:1, and a few spots genuinely need a decision before Phase 6
proper starts writing HTML.

**FYI (context, not a problem) -- from "1. ANALYSIS & OVERVIEW":**
- `[Book Metadata]`: Library (Calibre-managed/standalone), and every
  core field line (Title/Author/Language/Publisher/Date/Rights,
  Identifiers, Subjects, Description, Series) *when it isn't flagged
  MISMATCH*. EPUB Version, including the "will be upgraded" note.
- `[File Contents]`: every count (HTML pages, spine entries, TOC
  entries + source, CSS/image/font/audio/video/other file counts,
  total word count, cover status line).
- `[Book Structure Overview]`: Total Paragraphs/Images/Links,
  Divisions/Parts, Chapters Detected, Front/Back Matter counts.
- `[Typography Overview]`: every raw count (quote styles, apostrophe
  styles, dash counts, ellipsis counts, sentence-spacing counts).
- `[CSS Overview]`: stylesheet/rule/`!important`/class counts.
- `[Detailed Chapter Structure]` (the `--details` per-chapter dump):
  entirely drill-down FYI, not findings.

**Issue (worth a person's attention) -- from "2. ISSUES & FINDINGS":**
- `[Structure]`: thin/empty chapters, heading hierarchy issues.
- `[Table of Contents]`: no TOC found, broken TOC links, chapters
  missing from TOC.
- `[Cover Image]`: no cover declared, dangling `<meta name="cover">`,
  declared cover missing from archive, wrong media-type, mismatched
  declarations.
- `[Span Soup]`: nested wrapper chains, empty spans, no-op classes.
- `[Typography]` (the issues block, distinct from the Overview
  counts above): inconsistent/mixed quote or apostrophe styles,
  mojibake, stray BOM, zero-width spaces, soft hyphens, control
  characters, ALL-CAPS runs, repeated punctuation.
- `[Paragraphs]`: junk/watermark paragraphs, empty paragraphs,
  mid-sentence splits.
- `[Images]`: broken image references, manifest entries pointing at
  missing images.
- `[Ellipsis]`: ASCII and spaced-dot ellipsis.
- `[Scene Breaks]`: mid-chapter `<hr>`, chapter-edge `<hr>`.

**A third bucket, not just Issue/FYI -- flagged for manual review
only:**
- `[Possessive Candidates -- Manual Review]` is already its own thing
  in the CLI, on purpose (see the comment right above it in
  engine.py) -- genuinely ambiguous, never auto-repaired, exists so a
  person can review by hand. This should stay a visibly distinct
  third section in the GUI too, not get folded into "Issues" as if it
  were auto-fixable like everything else there.

**Spots that need a decision, not just a classification:**

1. **Metadata mismatches are already owned by the Metadata tab.** A
   MISMATCH on title/author/publisher/date/rights/description/series/
   identifiers/subjects is a real issue, but Phase 2 already gives it
   a dedicated side-by-side picker there. Showing the same mismatch
   again as a duplicate line in the Analysis tab's Issues section
   seems redundant -- more useful might be a single rollup line
   ("N metadata field(s) need review -- see Metadata tab") rather than
   repeating each one. Needs Jacob's call before Phase 6 build starts.
2. **The apostrophe count is reported twice.** `apo.total_match_count`
   ("Missing apostrophes (contraction split by a space)") appears
   once in `[Typography Overview]`'s FYI counts and again as its own
   `[Apostrophes]` issue line lower down -- same number, same book, no
   difference between them. The GUI version should only show this
   once, as an issue, and drop it from the Overview counts.
3. **Project Gutenberg Boilerplate doesn't fit Issue or FYI cleanly.**
   This section only prints at all when `gb.detected` is already
   true, so most of what it says is closer to FYI ("yes, this is
   Gutenberg-sourced, here's what repair will strip") -- except when
   `front_found`/`back_found` comes back false for a book that's
   otherwise Gutenberg-detected, which means the boilerplate is only
   partially findable and repair may not fully clean it. That
   half-found case reads more like an issue. Leaning toward: FYI when
   both halves are found, issue when either half isn't -- but this is
   a judgment call worth Jacob confirming rather than me deciding
   solo.
4. **A few CSS findings are observations about existing, possibly
   intentional CSS, not problems.** "Page-break rules declared,"
   "Forced height/max-height rules," and "Inline `<style>` blocks in
   chapter HTML" (the last of which explicitly counts blocks
   `ebook_fix` itself added) currently sit in `[CSS]` alongside actual
   problems like unbalanced braces or unreadable stylesheets. A book
   can have deliberate page-break CSS and that's not wrong. These
   three probably belong in an FYI/observation bucket rather than
   Issues, but splitting one analyzer section (`css`) across both
   buckets is more surgery than the rest of this audit needed, so
   flagging rather than deciding.
5. **"Protected nodes skipped" isn't a problem, it's a reassurance.**
   Inside `[Whitespace]`, `ws.protected_nodes_skipped_count` is
   explicitly "we deliberately didn't touch these (pre/code/script/
   style/svg/math), here's how many" -- that's FYI, not an issue,
   even though the rest of `[Whitespace]` is a clean Issue list.
6. **`[Module Checks]` isn't analyzer data at all.** Every other
   section reads off `analysis_report`, which is exactly what
   `_load_analysis()` already gives every other GUI tab. This section
   is different: it loops over `self.modules` (the CLI's *configured,
   enabled* repair modules) and calls each one's own `.analyze(book,
   analysis_report)`. The GUI's `_load_analysis()` doesn't build or
   hold a module list the way `Engine` does, and each module's count
   likely just re-derives from the same `analysis_report` fields
   already itemized above anyway. Rather than plumbing a second,
   separate module-instantiation path into the GUI to reproduce this
   section verbatim, Phase 6 should probably just drop it as
   redundant -- but confirming that assumption with Jacob before
   writing the template, since it's the one section this audit
   couldn't map onto anything already covered.

No code changed this session -- next session picks up Phase 6 proper
once the six items above are settled, building the actual template
against `analysis_report` directly (`_load_analysis()`, same pattern
Metadata already uses) instead of capturing `engine.analyze()`'s
printed text.

### Phase 7 -- Before / After tab
- Render a chapter/page from the original EPUB and its post-repair
  counterpart side by side.
- Needs a page-matching approach for the split case specifically:
  when one original file becomes several post-split files, the
  "after" side needs its own chapter selector rather than a strict
  1-to-1 page mapping. Scoping the exact matching logic is this
  phase's first task, not assumed up front.

### Phase 8 -- Final polish and packaging
- Launch script (`.bat`) and a short setup note for running it --
  already exists (`run_gui.bat`/`run_gui.py`), so this is really just
  making sure it still reflects however the app has grown by then.
- Full regression pass across all sample books, same standard as
  every other feature in this project.

## Open questions

- Exact page-matching approach for Phase 7's split case (see above) --
  deferred to that phase rather than guessed at now.
- Whether the Review tab's cover-mismatch item shows the two cover
  images directly in that tab, or defers full visual comparison to
  the Before/After tab -- likely the latter, to avoid building two
  versions of the same comparison view, but not decided yet.
- Phase 4's exact wording/UI for "this is staged, not applied yet" on
  the Metadata and Review tabs -- needs to be clear without being
  annoying on every single visit to those tabs.
- Phase 6's Issue-vs-FYI classification for each analyzer section --
  genuinely not decided yet; that's the phase's own first task, not
  something to guess at in this doc.

## Continuity note
This file is the source of truth for the GUI's scope and phase order,
more reliable than relying on conversation memory across sessions.
Update it as phases get picked up, scoped further, finished, or
changed.
