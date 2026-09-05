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

### Phase 3 -- Review tab
- Render case 3 split candidates and other NEEDS_REVIEW findings,
  each with enough surrounding context to judge it.
- Accept/reject per item, feeding the same review-gated repair paths
  that already exist (`split_chapters`, etc.), rather than inventing
  a parallel decision mechanism.

### Phase 4 -- Before / After tab
- Render a chapter/page from the original EPUB and its post-repair
  counterpart side by side.
- Needs a page-matching approach for the split case specifically:
  when one original file becomes several post-split files, the
  "after" side needs its own chapter selector rather than a strict
  1-to-1 page mapping. Scoping the exact matching logic is this
  phase's first task, not assumed up front.

### Phase 5 -- Apply, polish, and packaging
- A single "Apply all accepted changes" action across tabs, using the
  same validation/idempotency guarantees the CLI already has.
- Launch script (`.bat`) and a short setup note for running it.
- Full regression pass across all sample books, same standard as
  every other feature in this project.

## Open questions

- Exact page-matching approach for Phase 4's split case (see above) --
  deferred to that phase rather than guessed at now.
- Whether the Review tab's cover-mismatch item shows the two cover
  images directly in that tab, or defers full visual comparison to
  the Before/After tab -- likely the latter, to avoid building two
  versions of the same comparison view, but not decided yet.
- How much of the analysis report the Metadata/Review tabs need
  reshaped into vs. reused as-is from what `analyzer.py` already
  produces for the CLI.

## Continuity note
This file is the source of truth for the GUI's scope and phase order,
more reliable than relying on conversation memory across sessions.
Update it as phases get picked up, scoped further, finished, or
changed.
