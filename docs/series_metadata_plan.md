# Series Metadata -- Planning Doc

**Status:** Phase 1 done (2026-08-31). Manual series metadata writer
shipped as a new `series` CLI command. Phase 2 (automatic detection)
and Phase 3 (GUI) are still just the sketch below, not started.
**Started:** at Jacob's request, while thinking through what he wants
this project to eventually cover, ahead of any GUI version existing.

## The idea

Jacob wants a reliable way to add series information (series name +
position within the series) to a book's metadata. He's heard this can
be done reliably via metadata, isn't sure whether it can be done
automatically, and is fine with this being a manually-entered field
for now -- even if the first real interface for it ends up being a
future GUI's input field rather than anything in this CLI.

Checked all ten current sample books' OPF metadata directly: **none
of them have any series or collection metadata at all**, calibre
conversions included. So there's no existing real-world example in
`examples/` to build against yet -- worth adding one (or using
Jacob's own library) once this is picked up for real.

## How series info actually gets stored (background, needs no new
research -- this is a settled, well-known convention)

Two independent mechanisms exist. A book can carry either, or both:

- **Calibre's convention** -- `<meta name="calibre:series"
  content="Series Name"/>` and `<meta name="calibre:series_index"
  content="3"/>` in the OPF `<metadata>` block. Not part of the actual
  EPUB spec, but it's the de facto standard: calibre itself, and most
  reading apps/devices that bother to show series info at all,
  recognize this exact pair. This is almost certainly what Jacob's
  "I've heard we can reliably add series info" refers to -- it's the
  practical answer, not the spec-correct one.
- **EPUB3's actual standard** -- the `belongs-to-collection`
  mechanism (a `<meta property="belongs-to-collection">` element with
  `id`, refined by `<meta refines="#id" property="collection-type">
  series</meta>` and `<meta refines="#id" property="group-position">
  3</meta>`). Spec-correct, but real-world reader support is
  noticeably less consistent than calibre's convention -- plenty of
  apps that show series info at all only look for the calibre tags.

Likely answer once this is scoped for real: write **both**, same
posture `cover.py` already takes toward EPUB2's `<meta name="cover">`
vs EPUB3's `properties="cover-image"` -- support both signals rather
than picking one and leaving readers that only check the other
signal unable to show it. Not a certainty yet, just the pattern this
project already has for "two competing real-world conventions, same
underlying fact."

## Where this plugs into the existing architecture

Nothing new needs inventing here. `Book.opf_document` is already a
live, editable lxml tree (see `models.py`), with `Book.opf_modified`
already the existing flag the writer checks before re-serializing the
OPF -- `epub3_upgrade.py` and `gutenberg_repair.py` already edit this
same tree for other reasons. Adding/updating a `<meta>` element on it
is a small, well-precedented operation, not a new mechanism.

## Scope shape: closer to class_standardize than to a normal analyzer

This isn't something `analyze` would ever "find" -- there's no
existing signal in a book's own content that reliably says what
series it belongs to (see "automatic detection" below for why that's
a stretch goal, not the plan). This is closer in spirit to
`class_standardize.py`: **a hand-supplied answer, not an analyzer
finding**, that a repair step then applies. The person (Jacob, or
eventually a GUI's input field) provides the series name and index;
the tool's job is just to write it into the right metadata field(s)
correctly and idempotently (updating an existing series tag rather
than duplicating it, if one's already there).

## Rough phased sketch

- **Phase 1 -- Manual series metadata writer.** A way to supply a
  series name + index for one book (a CLI flag to start, e.g.
  something like `--series "Name" --series-index 3`, since there's no
  GUI yet and this project's interfaces are all CLI-based today) and
  a small module that writes/updates the calibre `<meta>` pair (and
  possibly the EPUB3 collection block too -- decide during this
  phase, see "How series info actually gets stored" above) on
  `book.opf_document`. Idempotent: running it again with a different
  series name updates the existing tag rather than adding a second
  one. Verified via `analyze` before/after showing the new field and
  nothing else in metadata changed, plus the standard full regression
  pass.
- **Phase 2 (stretch, not the core ask) -- Automatic detection.**
  Jacob explicitly said he doesn't know if this can be automatic and
  isn't asking for it yet. If ever picked up: a title like "Rage of
  the Mountain Man" gives no reliable signal on its own, but some
  books' own title/subtitle text spells it out directly ("Mountain
  Man, Book 4"). Any such detection should land as a **suggestion
  only**, never an automatic write -- same posture as the possessive-
  apostrophe candidates, which are flag-only and never auto-repaired,
  since a wrong guess here (wrong series, wrong position) is worse
  than saying nothing.
- **Phase 3 (later, GUI-dependent only for the entry point) -- Wire
  into a future GUI.** Whatever Phase 1 builds should already be the
  right shape for this: a GUI series field would just call the same
  underlying write logic instead of a CLI flag. Not a reason to wait
  on Phase 1 -- the CLI flag is a real, usable interface on its own
  in the meantime.

## Done: Phase 1 -- manual series metadata writer (2026-08-31)

Built as a new `series` command (`series.py` for the read/write
logic, `Engine.set_series` in `engine.py`, wired into `cli.py`), not
a flag on `repair` -- resolves the open question below in favor of a
dedicated command, since setting series info is a hand-supplied fact
rather than a detected issue being fixed.

- `series.py`: `read()` and `write()` against `book.opf_document`,
  writing **both** calibre's `calibre:series`/`calibre:series_index`
  meta pair and EPUB3's `belongs-to-collection` block (resolves the
  other open question below in favor of both, same posture as
  `cover.py`). `write()` is idempotent -- updates existing tags in
  place, confirmed by running it twice on the same book and diffing
  the OPF. `index` is a `float`, so `3.5` works for bonus/novella
  entries; `format_index()` renders whole numbers without a trailing
  `.0`.
- `ebook-fix series "book.epub" --name "..." --index 3` runs
  immediately if both are given; leaving either off prompts for it
  interactively in `cli.py` rather than erroring, since Jacob's
  typing this by hand rather than scripting it.
- `analyze` now shows existing series info read-only under Book
  Metadata (the small standalone win called out below), via a new
  `series`/`series_index` pair on `BookSummary` populated from
  `series.read(book)` in `analyzer.py`. This works independently of
  the `series` command ever having been run -- it just reports
  whatever's already on the book.
- Tested: full 10-book regression via `analyze` (no crashes, no
  unrelated changes), a `series` run followed immediately by
  `repair` on the same book to confirm the new meta tags don't
  interfere with the rest of the pipeline, and an idempotency check
  (name/index updated by re-running, no duplicate tags).
- README updated with a new "Series Metadata" section and a
  `series` entry in the Command Cheat Sheet.

## On the horizon: bulk series numbering

Jacob asked about this, not urgent. Because Phase 1's write logic
(`series.write(book, name, index)`) only cares about one name/index
pair per book, adding bulk support later shouldn't need any rework
here -- it'd mean a new small entry point (something like a CSV or
config file mapping EPUB filenames to name/index pairs) that loops
over a folder and calls the same `write()` once per book. Worth
revisiting once there's an actual multi-book batch to run it against.

## Continuity note
Same as the other planning docs: this file is the source of truth for
where this stands, more reliable than conversation memory across
sessions. Update it at the end of each session that touches this
feature.
