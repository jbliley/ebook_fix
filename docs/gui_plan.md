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

#### Phase 6 build (2026-09-06)

Jacob's calls on all six audit items: metadata mismatches shown in
full detail (like the Metadata tab does, not a one-line rollup),
apostrophe count dropped from Overview, Gutenberg split FYI/issue on
whether both halves were found, the three CSS observational counts
moved to Overview, "Protected nodes skipped" moved to Overview, and
`[Module Checks]` dropped entirely.

- New `gui/analysis_view.py`: `build_overview()`, `build_issues()`,
  and `build_manual_review()`, each returning a list of `Section`
  (title + plain-text lines) straight from `analysis_report` -- no
  more `Engine.analyze()`/`_captured_output()` involved in this tab at
  all. `book_analysis()` in `app.py` calls these three and hands the
  results to a rebuilt `book.html`: three headed groups (Issues,
  Needs Manual Review, Book Overview), each section rendered as its
  own bordered block, color-coded by bucket (red-tinted for issues,
  amber for manual review, neutral gray for overview) via new
  `.analysis-section`/`.analysis-issue`/`.analysis-manual`/
  `.analysis-fyi` rules in `base.html`.
- Metadata mismatches: `_metadata_mismatch_lines()` walks the same
  core fields, subjects, series, and identifier-conflict groups the
  CLI's own mismatch detection already covers, formatting each as
  "EPUB value vs. metadata.opf value" -- the same two values the
  Metadata tab's picker shows -- under one "Metadata Mismatches" issue
  section, ending with a line pointing over to the Metadata tab to
  actually resolve it. This is informational only; the Metadata tab
  stays the one place that's actually editable, so there's still only
  one surface a person needs to remember to check for a given field.
- Gutenberg: shown in Overview (both halves found, i.e. "repair will
  strip this cleanly") or Issues (either half missing, i.e. "repair
  may not fully strip this") depending on `gb.front_found and
  gb.back_found`, never both.
- The three CSS observations moved to Overview as one consolidated
  count each, combining the plain-stylesheet, embedded-`<style>`, and
  inline-`style=`-attribute variants of "page-break rule" and "forced
  height" into a single number per pair, rather than three separate
  lines -- worth flagging in case Jacob wanted only the plain
  stylesheet-level ones moved and the embedded/inline variants left as
  Issues; easy to split back out if so.
- Dropped the `--details` per-item drill-down this tab used to be able
  to show (individual broken TOC links, individual whitespace fixes
  per chapter, etc.) -- out of scope for what this audit covered, and
  a genuinely separate chunk of work (structured per-category
  drill-down data, not just re-sorting headline counts). Flagging as a
  clear candidate for a future increment rather than silently losing
  it.
- The GUI's Analysis tab no longer calls `Engine.analyze()` at all, so
  it no longer writes the `.ebookfix-analysis.json` cache file next to
  the book the way the CLI's own `analyze` command does -- the
  Metadata and Review tabs already didn't write this either (they've
  used `_load_analysis()` since Phase 2/3), so this actually makes all
  four GUI tabs consistent with each other rather than introducing a
  new gap.

**Follow-up, same session: Repair tab now shows each module's own
issue count and auto-unchecks anything with nothing to do.** Not part
of the original Phase 6 scope, but Jacob asked for it right alongside
the audit answers, and it reuses the exact same
module-instantiate-and-`.analyze()` mechanism the audit's item #6 had
just decided to drop from the Analysis tab -- turns out that
mechanism's real home was the Repair tab's own checkboxes all along,
not a second readout on the Analysis tab.

- New `_REPAIR_MODULE_CLASSES` dict in `app.py`: `_REPAIR_MODULES`'
  attr -> the same module class `Engine._build_modules()` would
  instantiate for it. Deliberately its own dict rather than reusing
  `Engine.modules`, since `Engine` only ever builds the modules
  currently *enabled* in `ebook_fix.toml` -- this needs a count for
  every module regardless of whether it's checked, so a disabled
  module still shows its count if a person considers checking it on.
- `book_repair()` now loads the book's analysis (same
  `_load_analysis()` every other tab uses) and calls each module's own
  `.analyze(book, analysis_report).count` -- the identical call the
  CLI's `[Module Checks]` section made, just per-module now instead of
  a loop building one combined block of text.
- A module starts checked only when it's both enabled in config AND
  found something to do (`count > 0`) -- so a book that's already
  EPUB 3 starts with "EPUB 3 Upgrade" unchecked instead of running a
  no-op pass, same idea generalized to all fifteen modules, not just
  that one. Still toggleable by hand either way; this only changes the
  starting checkbox state, never removes the checkbox.
- `repair.html`'s label for each module now appends "(N issue(s))"
  whenever count > 0, and stays plain when there's nothing to report.

Tested: ran both the Analysis and Repair tabs against all 11 sample
books end to end with no errors. Confirmed against
`GutenbergText-ChapterSplit.epub` specifically that Gutenberg shows in
Overview (both halves found), the apostrophe count appears exactly
once (Issues only, not duplicated in the Typography Overview), the
three CSS observations sit in Overview rather than Issues, and
"Protected nodes skipped" doesn't leak into the Whitespace issue list.
Confirmed the two already-EPUB3 sample books
(`The Call of Cthulhu by H. P. Lovecraft.epub`, `WarPeace-GoodCopy.epub`)
render "EPUB 3 Upgrade" unchecked on the Repair tab, and a book with
real whitespace issues shows the count in the checkbox label (e.g.
"Whitespace Normalizer (290 issues)"). Ran a full metadata-stage +
apply-everything pass afterward to confirm `book_repair()`'s changes
didn't disturb `apply_repair()`. Full CLI `analyze` regression across
all 11 samples stayed clean throughout.

### Phase 7 -- Before / After tab
- Render a chapter/page from the original EPUB and its post-repair
  counterpart side by side.
- Needs a page-matching approach for the split case specifically:
  when one original file becomes several post-split files, the
  "after" side needs its own chapter selector rather than a strict
  1-to-1 page mapping. Scoping the exact matching logic is this
  phase's first task, not assumed up front.

#### Phase 7a -- page-matching scope (2026-09-06)

Went looking for what data is actually available to match an original
page to its post-repair counterpart, rather than assuming a design up
front. Three cases, not two -- a whole chapter file can flat-out
disappear during repair, not just split:

- **Unchanged** (the common case): same href before and after.
  Nothing to match -- direct lookup.
- **Split**: one original href becomes itself (trimmed to segment 0)
  plus one or more new `chapter_NNN.xhtml` files (see
  `splitter.py`'s `generate_split_hrefs`).
- **Removed entirely**: found this case while checking whether it
  could happen at all -- `gutenberg_repair.py`'s
  `_remove_whole_chapter` drops a trailing back-matter file completely
  (out of `book.chapters`, the manifest, the spine, and into
  `book.removed_files` so the writer drops it from the archive too).
  `cover_repair.py` uses the same `book.removed_files` mechanism when
  renaming a cover image. A Before/After tab that only knew
  "unchanged" and "split" would either crash or silently show a blank
  pane for one of these -- it needs to show something like "removed
  by repair" instead.

**How each case gets detected, and why they need different
approaches:**

- **Removed** needs no new plumbing at all. At the point the
  Before/After tab actually renders, both the original file and
  `_fixed.epub` already sit on disk -- loading both and diffing their
  chapter href sets directly tells you everything the "removed" case
  needs, with nothing to capture ahead of time.
- **Split** is the opposite: it genuinely can't be reconstructed after
  the fact. A new `chapter_004.xhtml` file's name alone doesn't say
  which original chapter it came from -- the naming convention is the
  same generic `chapter_NNN` pattern regardless of source file (see
  `generate_split_hrefs`'s docstring), and nothing in the new file's
  own content marks its origin either. The one place this mapping
  genuinely exists is `_split_and_rewire()` in `engine.py`, which
  already builds it internally as `new_hrefs_by_origin` (original href
  -> list of new hrefs) -- it's just never returned to the caller
  today. Chosen approach: add it as a third item in
  `_split_and_rewire()`'s return tuple, and have `apply_repair()`
  persist it as a small new session file (`split_mapping.json`,
  original href -> resulting hrefs) right when a split happens,
  alongside the fixed EPUB it already writes. This is the one actual
  code change this scoping surfaces -- small and additive, returning
  data `_split_and_rewire` already computes rather than computing
  anything new.

**Planned 7b behavior** (not built yet): the tab's chapter selector is
sourced from the *original* book's reading order. For whichever
chapter is selected: if it's in the removed set, show "removed by
repair" instead of an after-pane; if it's a split origin (per
`split_mapping.json`), show segment 0 by default with a small
secondary selector for the additional resulting pages; otherwise, pull
the same href directly from the final book.

**Worth flagging:** `split_mapping.json` only ever gets written by the
GUI's own `apply_repair()` -- the CLI's `split_chapters`/`repair
--case3-boundaries` commands don't go through a GUI session at all, so
this file is GUI-only bookkeeping, same as `staged_metadata.json`/
`staged_review.json` already are, not a new precedent.

#### Phase 7b -- build (2026-09-07)

Built against the 7a scoping above, no changes to the plan itself.

- `_split_and_rewire()` in `engine.py` now returns a 3-tuple
  (`split_count, reports, new_hrefs_by_origin`) instead of 2 -- all
  four call sites updated (three in `engine.py` that don't need the
  mapping, `apply_repair()` in `app.py` that does). `split_marked()`'s
  own public return signature is untouched; nothing calls it from the
  GUI, so it just discards the new value internally.
- `apply_repair()` writes `split_mapping.json` unconditionally after
  every Apply (even an empty `{}` when nothing split), so a re-applied
  session never shows a stale mapping left over from an earlier run.
- New `gui/analysis_view`-style route pair in `app.py`:
  `book_before_after()` (the tab itself) and `book_asset()` (serves
  one file's raw bytes out of either the original or `_fixed.epub`
  archive, for use as an `<iframe src>`). `book_asset()` resolves the
  requested path relative to that archive's own OPF directory -- the
  same `base / href` convention every other href in this project
  already follows -- which is what lets a chapter's own relative
  `<img src="../images/x.jpg">` or `<link href="../css/y.css">`
  resolve back through this same route automatically: the browser
  does that relative resolution against the iframe's current URL
  before ever asking the server for anything, so the route never
  needs to know in advance which images/stylesheets belong to which
  chapter.
- `before_after.html`: a chapter dropdown (sourced from the original
  book's reading order), a second dropdown that only appears for a
  split chapter (choosing among its resulting pages, defaulting to
  the first), and two side-by-side sandboxed `<iframe>`s. A removed
  chapter collapses to a single "before"-only pane with an
  explanation instead of a blank "after" side. Both dropdowns
  auto-submit a plain GET form (`onchange="this.form.submit()"`, same
  idiom already used elsewhere in this project) rather than needing
  any new JS.
- Nav link in `base.html` switched from the "coming soon" disabled
  placeholder to a real link, plus `.before-after-grid`/iframe CSS
  matching the existing card-based styling.

Tested against `GutenbergText-ChapterSplit.epub` specifically, since
it's the one sample book that exercises all three cases in a single
real run: staged and applied a full split (17+18 boundaries across its
two source files) together with every repair module including
Gutenberg Boilerplate Removal, which drops a whole trailing
back-matter file on this book. Confirmed `split_mapping.json` came out
non-empty and correct, confirmed the tab's dropdown correctly labels
the split chapter and the file Gutenberg repair actually dropped,
confirmed the removed-chapter view shows the single-pane explanation
instead of erroring, and confirmed the asset route serves real,
non-empty, and *different* bytes for the before and after sides of an
edited chapter (verified separately against `MM21.epub`, which has no
splits at all, to isolate the plain unchanged-file case). Ran the
before-after tab's "no fixed file yet" state across all 11 sample
books. Verified the OPF-relative path resolution logic directly for
both a root-level `content.opf` (what all 11 samples actually use) and
a synthetic nested `OEBPS/content.opf` case, since none of the samples
exercise a subdirectory OPF on their own. Full CLI regression across
all 11 samples, plus `split-structure` and `auto-fix` specifically (to
exercise the three updated `engine.py` call sites), all stayed clean.

#### Small addition, same session: Select All / Unselect All on the Repair tab (2026-09-06)

Jacob asked for this "in case it ever comes up" -- two small buttons
above the module checklist in `repair.html`, each calling a
`setAllRepairModules(checked)` JS function that walks every
`input[name="modules"]` checkbox and sets it. Plain inline `<script>`,
same style `index.html`'s own browse-button JS already uses -- no
framework, nothing new introduced. Doesn't touch the auto-uncheck
logic from the Phase 6 follow-up at all; it's just a bulk override a
person can apply on top of whatever the page loaded with. Tested
across all 11 sample books (buttons render, no template errors) and
confirmed with a full CLI regression pass that nothing else was
disturbed.

#### Whitespace module expansion -- ripple-effect fixes (2026-09-07)

Jacob added new functionality directly to `whitespace.py` (the
analyzer) and `modules/whitespace.py` (the repair module) outside a
session with me -- three new Unicode cleanup rules folded into the
same `normalize_fragment()` pipeline every other whitespace rule
already runs through: non-breaking spaces, other Unicode whitespace
characters (en/em space, thin space, and similar), and zero-width/
invisible characters (zero-width space, word joiner, a BOM sitting
inside the text itself). Asked to audit what else needed to change as
a result; three concrete gaps, plus one real overlap needing a call
Jacob made this session ("count once, under Whitespace, since it falls
under that more"):

- **`config.py`**: `WhitespaceRepairConfig` didn't have fields for the
  three new rules at all -- the module was falling back to
  `getattr(self.config, "...", True)`, so it worked, but there was no
  way to turn any of them off via `ebook_fix.toml`. Added all three as
  real dataclass fields (default `True`, matching the module's own
  default), added matching entries with comments to the hand-written
  default TOML template right below `collapse_whitespace_only_nodes`,
  and simplified `modules/whitespace.py`'s `_rules()` back to plain
  attribute access now that the fields genuinely exist.
  `_apply_section()`'s TOML loading is fully reflection-based
  (`dataclasses.fields()`), so nothing else needed touching for the
  new options to actually work as real `ebook_fix.toml` switches.
- **`engine.py`'s CLI `[Whitespace]` summary** and **`gui/
  analysis_view.py`'s Whitespace issue section**: both had the same
  gap -- three new counts the analyzer now tracks
  (`nonbreaking_space_count`, `unicode_whitespace_count`,
  `zero_width_whitespace_count`) with no line anywhere in either
  summary to show them. The CLI's own `--details` per-issue dump
  already surfaced them fine (it just prints `issue.category`
  generically), so this was specifically a "headline count" gap, not a
  "the data doesn't exist" gap. Added matching lines to both places.
- **Overlap with `typography.py`**: it already independently tracked
  zero-width spaces (U+200B + U+200C) and stray BOM characters
  (U+FEFF) anywhere in chapter text, for the Analysis tab's Typography
  section -- and it's analysis-only, nothing ever repaired what it
  found. The new whitespace code covers an overlapping-but-not-
  identical set (U+200B + U+2060 + U+FEFF) and, unlike typography.py,
  actually fixes them. Jacob's call: show this once, under Whitespace,
  since it now falls under that more than Typography. Removed the
  "Stray BOM characters"/"Zero-width spaces" lines from Typography's
  issue list in both `engine.py` and `analysis_view.py` -- the
  underlying `typography.py` fields (`chapters_with_bom`,
  `total_zero_width_space`) are untouched, just no longer displayed in
  either summary, so nothing else that might read them breaks. One
  side effect flagged in both places' comments: `typography.py` also
  separately tracks zero-width NON-joiner (U+200C) as part of that
  same combined count, which `whitespace_repair` doesn't touch at all
  -- so U+200C on its own no longer gets its own line anywhere. Worth
  a dedicated line of its own later if that specific character turns
  out to matter in practice; not reintroduced now since that's a
  narrower, different fix than "stop double-reporting the overlap."
- Confirmed `report.py` (no fixed category whitelist -- accepts any
  string, so the three new issue categories and the Repair tab's
  per-module issue counts needed no changes at all) and `serialize.py`
  (fully reflection-based via `dataclasses.fields()`, so the new
  summary fields flow through the analysis cache automatically) needed
  no changes. No module-ordering concerns either -- the new rules run
  inside the same `normalize_fragment()` call every other whitespace
  rule already goes through, in the same pass, at the same point in
  the pipeline.

Tested: confirmed the three new config fields are real, independently
toggleable `ebook_fix.toml` options (set two to `false`, confirmed
`load_config()` honored both while the third stayed at its default).
Confirmed `init-config` writes the new options with their comments.
Full `analyze` regression across all 11 samples confirmed the old
"Zero-width spaces"/"Stray BOM characters" Typography lines are gone
everywhere and the new Whitespace lines appear on the two sample books
that actually have non-breaking spaces
(`ChaptersNotAligned-New.epub`: 52-54 depending on exact count basis,
`WarPeace-GoodCopy.epub`: 5) -- confirmed the same on the GUI's
Analysis tab for both. Ran a real repair against
`ChaptersNotAligned-New.epub`: confirmed "Non-breaking space: 54"
actually gets fixed and counted, then ran a second repair pass against
that output and confirmed zero further changes and byte-identical
archive contents (this project's standard idempotency check). Full
CLI regression (`analyze` and `auto-fix`) across all 11 samples stayed
clean throughout.

### Phase 8 -- Final polish and packaging
- Launch script (`.bat`) and a short setup note for running it --
  already exists (`run_gui.bat`/`run_gui.py`), so this is really just
  making sure it still reflects however the app has grown by then.
- Full regression pass across all sample books, same standard as
  every other feature in this project.

**Done (2026-09-07).** Both items checked, nothing needed changing:

- `run_gui.py`/`run_gui.bat` still work exactly as documented --
  confirmed the "no install step needed" claim in both the script's
  own docstring and the README specifically, by uninstalling the
  `ebook-fix` package entirely and confirming `gui.app` still imports
  cleanly and registers all 13 routes through `run_gui.py`'s own
  `sys.path` insertion alone. README's Web GUI section doesn't
  enumerate individual tabs/features by name, so nothing there went
  stale as tabs were added across Phases 1-7b -- no changes needed.
- Full regression: every one of the 11 sample books through the
  complete GUI workflow in one pass -- Analysis tab, Metadata tab
  (GET and a stage POST), Review tab (GET, plus a stage POST on the
  books with real split candidates), Repair tab (GET for the
  count/auto-uncheck display, then Apply Everything with every
  module checked), Before/After tab, and the asset-serving route.
  Every book produced a loadable, `ebook-fix validate`-clean fixed
  EPUB. This is the same standard every phase before it was already
  held to individually; this pass is the first to exercise all of
  them together, back to back, on every book, in one run.

All eight phases of this plan are done as of this session. What's left
of the GUI isn't a numbered phase so much as it is what's still open
below.

#### Post-plan addition: replace-original-file, and Calibre sync visibility (2026-09-08)

Jacob's repair result wasn't touching metadata.opf or metadata.db and
there was no way to tell why from the GUI -- and separately, he raised
a real workflow gap: the GUI always writes a `<name>_fixed.epub`
alongside the original, which means Calibre (pointed at the original's
exact filename) never sees the fix without a person manually renaming
files themselves. Two related fixes:

- **Calibre sync visibility.** `Engine._sync_metadata_opf()` and
  `._sync_metadata_db()` now return a small status dict (attempted,
  changed_fields, message) instead of only logging internally -- the
  CLI still sees everything via the console, but the GUI has no
  console to read, so `apply_repair()` now surfaces this same status
  directly in the Repair tab's own result banner ("Calibre
  metadata.opf: ...", "Calibre metadata.db: ..."), with a specific
  reason whenever nothing happened (not Calibre-managed, config
  disabled, nothing needed updating, calibredb not found, etc.)
  instead of silence. This should directly answer "why didn't it
  write" the next time it comes up, without needing to dig through
  logs.
- **New "Replace original file" button** on the Repair tab, shown once
  a `_fixed.epub` exists. Does exactly what Jacob proposed: renames
  the untouched original to `<name>_original.epub` (a backup, never
  auto-deleted) and renames the repaired file onto the original's own
  name and location -- two same-filesystem renames, atomic on every OS
  this project supports, not a copy-then-delete. Refuses (rather than
  overwriting) if a backup already exists at that name, since that
  almost always means this book was already replaced once and
  overwriting would lose whichever version came before that.
- The Repair and Before/After tabs both track this via a small
  `replaced.flag` file in the session folder, so neither tab keeps
  pretending the file at the original name is still the pre-repair
  original once it's been swapped -- the Repair tab shows a persistent
  note and hides the button, and Before/After shows a specific message
  instead of its generic "no repair yet" one.

Tested: full round-trip on a throwaway copy -- applied a repair,
confirmed the button appears, clicked it, and verified by hash that
the backup file is byte-identical to the true original and the
original-named file now holds the repaired content. Confirmed a
second replace attempt is refused without touching the existing
backup. Confirmed both tabs show the right persistent messaging
afterward. For the sync-visibility fix, confirmed the "not attempted"
reason renders correctly for a non-Calibre book, and directly
exercised every Calibre-managed branch (no metadata.opf found, no
book id resolved, config disabled, sync off by default, calibredb not
found) with a mocked Calibre context, since none of the 11 sample
books are Calibre-managed. Caught and fixed one small bug of my own
in this pass: `_sync_metadata_db`'s error branch was reporting
`attempted=False` on a calibredb-not-found error even though the sync
had genuinely been attempted (config allowed it, all the gating
checks passed) -- it was reusing `calibredb_write.py`'s own narrower
`attempted` flag (whether a subprocess call was actually made) instead
of its own. Full GUI workflow regression and CLI regression (`analyze`
+ `auto-fix`) across all 11 samples stayed clean throughout.

Jacob's original report (found the description from Calibre, but
metadata.opf/metadata.db weren't touched) still isn't root-caused --
most likely explanation given the defaults: `sync_calibre_db` is off
by default, so metadata.db not changing is expected unless that's
been turned on in `ebook_fix.toml`; the metadata.opf side needs
checking against the actual new result-banner message next time this
runs, since before this fix there was no way to tell whether it was
"nothing needed updating" (the values already agreed) versus
something not firing correctly. Next run's result banner should say
definitively which.

#### Bug fix -- Calibre sync silently gated by the Metadata Sync checkbox (2026-09-10)

Found by Jacob during the first real-library `calibredb` test (see
`docs/metadata_plan.md` Phase 3): `ebook_fix.toml` had
`sync_calibre_opf`/`sync_calibre_db` both `true`, but the Repair tab's
result banner still said "metadata_repair is disabled in config" for
both.

**Root cause:** `apply_repair()` forces `config.metadata_repair.enabled`
to match whether the Metadata Sync checkbox was submitted with the
form, so `run_selected_repairs()` only runs modules the person actually
checked. But `_sync_metadata_opf()`/`_sync_metadata_db()` (engine.py)
read that same `enabled` flag as their own separate gate for whether to
push already-confirmed values out to Calibre. Jacob's test book's EPUB
and `metadata.opf` already agreed on every field, so `MetadataSyncRepair`
found nothing to fix, the checkbox auto-unchecked itself (existing,
correct behavior -- see the Phase 6 follow-up note above), and that
false-looking "unchecked" state fed straight into the Calibre-sync
gate, which has nothing to do with whether this pass found an EPUB-side
issue.

**Fix:** `apply_repair()` now captures `config.metadata_repair.enabled`
as read from `ebook_fix.toml` *before* the checkbox override runs, and
restores it right before calling the two sync methods -- so "did this
pass fix something in the EPUB" and "is Calibre syncing turned on" are
answered independently again, same as they always have been for the
CLI's own `repair()`/`auto_fix()` entry points, which never shared this
bug (they don't have a per-run checkbox to conflict with in the first
place).

Verified with a standalone check confirming the captured value survives
the checkbox loop and is back in place before the sync calls run.
Couldn't verify end-to-end against a real Calibre-managed book here --
that's what Jacob's live Phase 3 testing is already in the middle of.

## Done -- Review tab overhaul, and a sticky top bar (2026-09-12)

Two items picked up from the 2026-09-11 planned batch above.

**Review tab now covers three kinds of finding, not just chapter
splits.** The original v1 plan (see "The three tabs" above) always
said the Review tab should cover "case 3 chapter-split
candidates... cover mismatches, and any other NEEDS_REVIEW-class
finding," but only chapter-split candidates ever got built. Jacob's
ask went further than just listing the others: **be able to see and
pick the correct option from the Review tab for an unresolved conflict
of any kind.** Built exactly that for the two finding types that
actually have a pickable action:

- **Possessive candidates** -- `ebook_fix.apostrophes.PossessiveCandidate`
  now carries a stable `id` and the exact `match_start`/`match_end`
  span of the ambiguous "word s" within its live element's text/tail.
  New `apply_possessive_resolutions(book, resolutions)` applies a
  person's per-candidate choice ("possessive" -> "dog's", "plural" ->
  "dogs"); anything left unpicked stays exactly as it is today, same
  as before this existed. Multiple resolved candidates sharing one
  text/tail slot apply right-to-left, so an earlier edit's changed
  length never shifts a later match's own recorded span out from under
  it.
- **Possible Decorative Color** -- `ebook_fix.color.ColorFinding` now
  carries a stable `id` too (a plain enumeration index per (href,
  location_kind), the same simplicity precedent chapter-split
  candidate ids already used). New `ColorStripRepair.
  apply_review_removals(book, accepted_ids)` removes exactly the
  review-bucket findings a person picked, by id, without touching
  anything else that happens to share the same class or file --
  verified directly against the real book that started this whole
  feature (2026-09-11's Dutch novel): removing just `.wp-kop`'s color
  left `.wp-deel` right next to it, and everything else, untouched.
  Both `repair()` and `apply_review_removals()` now share one
  `_remove_matching()` walk internally so their ids always land on the
  same declarations analysis found.
- **Dangling paragraph endings were deliberately left out of this
  pass** -- flagged in the planning writeup as a different shape of
  problem (no text to fill in automatically no matter what a person
  picks), and that held up under actual building: still Analyze-tab
  only for now, nothing changed here.
- **Select All / Unselect All** added to both the chapter-split and
  decorative-color sections (checkbox-based, so a blanket toggle makes
  sense); not added to possessive candidates, which stayed individual
  -- there's no sensible universal default to mass-apply to a genuine
  per-word judgment call the way "every proposed split here is
  correct" or "strip all of these" can be.

Real bug caught during this build, not by inspection: the first
version of `apply_possessive_resolutions` mutated the live element's
text correctly in memory but never set that chapter's own `.modified`
flag, which `EPUBWriter` actually checks before re-serializing a
chapter from its live tree (see writer.py) -- `book.mark_modified()`
alone isn't enough, the same rule ellipsis_repair.py and whitespace.py
already have to follow. Caught by actually saving and re-reading the
output file rather than trusting the in-memory state looked right;
fixed and re-verified against a real saved file.

Staging and apply flow now covers all three Review sections together:
`save_review()` writes possessive resolutions and accepted color ids
into the same `staged_review.json` alongside chapter-split
`accepted_ids`; `apply_repair()` applies all three before the standard
module pass runs (splits, then possessive resolutions, then color
removals, then a fresh analysis for the modules themselves), and the
Repair tab's staged-item banner and result summary both report all
three counts.

**Sticky top bar**, applied to every tab via one shared change in
`templates/base.html` (`position: sticky`) -- no per-tab special
casing needed. The "Analyze another book" button moved up into that
bar too; it used to live only at the bottom of the Analysis tab (the
only tab that had it at all), so this is also the first time every tab
has had a way back to the upload screen without going via Analysis
first. The filename display shrank to make room for it.

Verified: full sample-suite regression (`analyze`/`repair`/`auto-fix`
across all 13 books) with no crashes and valid XML throughout; a
Flask-test-client run through the actual GUI routes end to end (stage
a possessive resolution and a color removal on the Review tab, apply
everything including the standard module set on the Repair tab, check
the saved output file directly) rather than just checking the pages
render; idempotency of both new repair functions confirmed by diffing
extracted zip contents between two passes, not raw bytes, same
standard as everything else in this project.

## Planned -- Jacob's next batch (2026-09-11)

Written up per Jacob's request, scoped through a round of clarifying
questions before anything gets built -- nothing in this section is
started yet. Each item below is its own independent phase; order
isn't decided.

### Cover replace (upload, or reuse the Calibre folder's own cover)

Two related but separate actions, both landing through the existing
`CoverRepair` pipeline (declaration/manifest handling stays automatic,
same as it already is for every other cover fix) rather than a new
one-off writer:
- **Upload a replacement image** from the Metadata (or a new Cover)
  tab -- a person picks a file, it becomes the book's cover image.
- **Reuse the Calibre folder's own cover**, when the book is
  Calibre-managed -- Calibre libraries keep a `cover.jpg` sitting
  right next to `metadata.opf` in the book's own folder; offer to pull
  that image into the EPUB directly, no upload needed. **One-directional
  only, folder -> EPUB, confirmed with Jacob** -- this does not push a
  newly-uploaded cover back out to the Calibre folder or `metadata.db`
  the way the existing metadata write-back sync does for text fields;
  that direction isn't being built here.

Not yet scoped:
- Whether "reuse the Calibre folder's cover" is offered automatically
  whenever a mismatch is detected (the existing cover-mismatch
  NEEDS_REVIEW finding calibre_detect.py's analysis already surfaces),
  or needs an explicit action a person takes -- likely ties into the
  existing "Open questions" note below about where cover-mismatch
  comparison lives (this tab vs. Before/After).
- Image validation before it's accepted (right dimensions/aspect
  ratio, actually a valid image file, not something already handled
  elsewhere) -- `CoverRepair`'s existing checks are about the
  *declaration*, not the pixel content, so this may need its own
  light validation pass.

### Bug fix (done, 2026-09-12) -- GUI errors if the original file is removed mid-session

Every tab's route eventually called `_source_path()`, which reads the
path recorded in `real_path.txt` and assumed the file was still there.
If the person deleted, moved, or renamed the file on disk while its
GUI session was still open (e.g. after using "Replace original file"'s
own backup/rename dance, or just cleaning up downloads), the next tab
load hit a raw, unhandled `FileNotFoundError` from deep inside
`zipfile.ZipFile()` instead of a real page.

Fixed with a single `_handle_missing_source` decorator in `gui/app.py`
rather than a separate try/except in every route -- it wraps a view
function, catches `FileNotFoundError`, and re-renders `index.html`
(the same "upload a book" page a person sees at `/`) with a plain
message naming the missing file, instead of a 500. Applied to every
`/book/<session_id>/...` route that loads a book off disk: Analysis,
Metadata, Review (both GET and the save-review POST), Repair (both GET
and apply-repair POST), and Before/After. Same posture as the existing
locked-file handling in "Replace original file" (2026-09-10 bug fix
above), just for "gone" instead of "locked."

The Before/After tab needed no separate check beyond the decorator --
it already short-circuits before ever loading the original book
whenever no `_fixed.epub` exists yet, so the missing-file path there
only triggers once a fix has actually been applied and the *original*
is later removed; verified both cases directly. The asset route
(`/book/<session_id>/asset/<side>/<href>`) already checked
`.exists()` and returned a plain 404 before this fix, which is the
right behavior for an `<img>`/`<iframe>` sub-resource rather than a
full tab page, so it was left as-is.

Verified via `app.test_client()`: opened a session against a real
file, confirmed every tab (including Before/After with a `_fixed.epub`
already in place) loads normally, deleted the file out from under the
open session, then confirmed every tab -- GET and POST alike -- now
shows the plain missing-file message instead of a 500. Full CLI
`analyze` regression across all sample books afterward confirmed
nothing else was disturbed.

### Metadata tab: warn before leaving with unsaved changes

A `beforeunload`-style browser guard on the Metadata form specifically,
tracking whether any field has changed since the page loaded (or since
the last successful Save). Confirmed with Jacob this should fire for
**all** ways of leaving the page with unsaved changes -- browser tab
close, window close, and navigating to another tab inside the app --
which conveniently is exactly what a single `beforeunload` listener
already covers uniformly, so this shouldn't need separate handling per
trigger.

### Auto-rename on save, from a filename pattern (GUI only, opt-in)

A config-backed pattern (e.g. `%author% - %title% (%year%)`) applied
to the saved filename. Confirmed scope, narrower than the original
ask:
- **GUI only** -- CLI `-o`/`--output` stays exactly as typed, no
  pattern applied there.
- **Off by default**, opt-in per Jacob -- a config default plus a GUI
  checkbox/control at save time, rather than something that silently
  changes a filename a person didn't ask to have renamed.
- **Never applied to a Calibre-managed book**, full stop, not a
  toggle -- Calibre already owns that book's folder/file naming
  convention, and Jacob does not want this anywhere near that.
- Token set: `%author%`, `%title%`, `%year%`, plus `%series%` and
  `%series_index%` since series support already exists
  (`ebook_fix.series`) -- "just so they're in there" per Jacob, not
  necessarily needed on day one.

Not yet scoped: exact pattern syntax/parsing (a small hand-rolled
`%token%` substitution is plenty, no need for a templating dependency),
what happens when a token's underlying field is empty (e.g. no known
year), and filesystem-illegal-character handling in a resolved
author/title value.

## Open questions

- Whether the Review tab's cover-mismatch item shows the two cover
  images directly in that tab, or defers full visual comparison to
  the Before/After tab -- likely the latter, to avoid building two
  versions of the same comparison view, but not decided yet.
- Phase 4's exact wording/UI for "this is staged, not applied yet" on
  the Metadata and Review tabs -- resolved in the Phase 4 build (the
  `.staged-banner`/no-staged-yet banners on the Repair tab); revisit
  only if it turns out to be more or less noisy than expected in
  practice.
- A structured drill-down view for the Analysis tab (the individual
  broken links, per-chapter whitespace fixes, etc. that `--details`
  used to surface on the CLI) -- deferred out of Phase 6's build, not
  decided against, just not yet scoped.
- Whether the Repair tab's CSS-observation consolidation (page-break/
  forced-height/embedded-`<style>` counts combined across stylesheet,
  embedded, and inline-attribute variants into one number each) is
  what Jacob actually wanted, or whether only the plain
  stylesheet-level counts should have moved to Overview -- flagged in
  the Phase 6 build write-up, not yet confirmed either way.

## Continuity note
This file is the source of truth for the GUI's scope and phase order,
more reliable than relying on conversation memory across sessions.
Update it as phases get picked up, scoped further, finished, or
changed.

#### Bug fix -- replace-original crashed instead of erroring cleanly on a locked file (2026-09-10)

Found in the same session as the fix above, on Jacob's first real
"Replace original file" attempt: Windows refused the rename with
`PermissionError: [WinError 5] Access is denied` (something else --
Calibre's viewer, another reader, an antivirus scan, an Explorer
preview pane -- had the book file open), and that propagated straight
up to a raw 500 page instead of the same kind of clear message
`calibredb_write.py` already gives for a locked Calibre library.

**Fix:** both renames in `replace_original()` are now wrapped in
`try`/`except OSError`, each returning the normal `repair.html` page
with a specific `replace_error` message instead of crashing. If the
first rename (original -> `_original.epub` backup) fails, nothing has
changed yet, so the message says so plainly. If the second rename
(`_fixed.epub` -> original name) fails *after* the first succeeded,
the backup is renamed straight back to the original name before
returning, so a lock that appears mid-operation never leaves the book
missing from its own filename with a stray backup sitting next to it.

Verified with two standalone tests (a fake locked-file rename via
Flask's test client, not a real Windows lock): first-rename failure
leaves both files exactly as they were, no backup created; second-
rename failure restores the original's content and name and leaves no
stray backup, with the `_fixed.epub` still sitting separately.
Real-world confirmation (an actual Windows lock, not a simulated one)
still needs a person hitting it live -- Jacob's original report is
what's actually driving this fix.
