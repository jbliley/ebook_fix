# Series Metadata -- Planning Doc

**Status:** Not started. This is a planning doc only -- no code yet.
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

## Open questions to resolve when this is picked up
- Write calibre-only, EPUB3-collection-only, or both (leaning both,
  not decided).
- Exact CLI shape for Phase 1 (flags on the existing `repair` command,
  or a new dedicated command, given this isn't really a "fix an
  issue" repair in the same sense as the rest of that command).
- Whether a book that already has series metadata gets it reported
  anywhere in `analyze` output, even before any writing capability
  exists -- a cheap, useful signal to add early and separately from
  the write side, worth considering as a small standalone first step.

## Continuity note
Same as the other planning docs: this file is the source of truth for
where this stands, more reliable than conversation memory across
sessions. Update it at the end of each session that touches this
feature.
