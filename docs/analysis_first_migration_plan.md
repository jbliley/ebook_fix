# Analysis-First Migration -- Planning Doc

**Status:** In progress. Four of six repair tools are converted.
**Started:** session that added `serialize.py` (the analysis cache).

## The problem

The original design was: the analyzer does one full pass over a book
and records everything it finds, and every repair tool reads from
that instead of re-scanning the book itself. Somewhere along the way
this slipped: `repair` never ran the analyzer at all, and every repair
tool went back to scanning the book on its own, sometimes twice (once
during `analyze`, again during `repair`).

Goal: get back to the original design. One analysis pass per run,
saved to a cache file, every repair tool reads from it instead of
looking at the book directly.

## Phased plan

### Phase 0 -- Foundation (done)
- `serialize.py`: saves the analysis report to a JSON cache file next
  to the book, and loads it back. Drops anything that can't be saved
  to a file (e.g. live references to a specific spot in the book,
  which only make sense while that exact parse is still in memory).
- `analyze` saves the cache after building its report.
- `repair` now runs the full analysis once, up front, before any
  repair tool touches the book -- and saves the same cache.
- Every repair tool's `analyze`/`repair` methods now accept the
  analysis as a second argument (falls back to scanning itself if
  none is given, so nothing breaks if called standalone).
- The cache file deletes itself once `repair` is done with it. It
  stays around after a plain `analyze`, since there it's the point.

### Phase 1 -- EPUB 3 Upgrade (done)
- Was checking the book's version itself, twice. Now just reads the
  answer the analysis already worked out. Smallest possible version
  of this migration -- no new analyzer work needed, just swap the
  source of truth.

### Phase 2 -- Chapter Markup (done)
- Was calling `analyze_book_chapters()` itself during repair, on top
  of the analyzer already calling it. Now reads `analysis.chapters`
  instead. Confirmed this is safe even though earlier repair tools
  (Paragraph Repair, EPUB 3 Upgrade) run first and do change the
  book -- chapter markup works off live element references, not
  cached text positions, so it stays correct even after earlier
  tools edit the tree.

### Phase 3 -- Image Repair (done)
- Needed a new `ebook_fix/images.py` first, since the analyzer didn't
  know anything about images before this. It now finds broken `<img>`
  references and manifest entries pointing at missing files, as part
  of the same single pass. `modules/images.py` reads from that
  instead of opening the zip and walking the chapters itself.
- This was the slow one -- new analyzer module, new report section,
  synthetic test book to actually prove the detection works (none of
  the sample books have a broken image), plus double-checking the
  live-element handoff survives a JSON round-trip cleanly. Worth
  knowing the remaining phases with new detection logic (Phase 4)
  will likely take about this long each; the ones that just swap in
  an already-known answer (like Phase 1) are much quicker.

### Phase 4 -- Paragraph Repair (done)
- Needed a new `ebook_fix/paragraphs.py`, same pattern as images. It
  finds empty paragraphs, watermark/junk elements, and mid-sentence
  splits as part of the same single pass.
- Resolved the open question below: the analysis hands over an
  ordered list of "meaningful" paragraphs per chapter (not empty, not
  junk), and repair still walks that list live while merging, since
  a merge can chain (merge B into A, and the result now also looks
  mid-sentence with C). What got saved was the expensive part --
  finding every paragraph and checking each one for empty/junk --
  not the merge-order walk itself, which has to stay live either way.
- Verified this carefully since it's the trickiest handoff so far:
  compared word counts and paragraph counts between the old and new
  code across all five sample books (identical), compared `analyze`
  issue counts book-by-book (identical), and built a synthetic
  three-paragraph chain-merge test to confirm merging still cascades
  correctly (it does -- collapses to one paragraph, same as before).

### Phase 5 -- Whitespace Normalizer (not started, bigger than the rest)
- Also still scans the book itself, twice. But this one shouldn't be
  patched the same way as Phases 1-4 -- it's already flagged
  separately for a full rebuild (a proper structure-aware whitespace
  engine, replacing the current regex approach, which has a known bug
  where it mangles things like "3.14" or "U.S.A."). Wiring it to the
  analysis should happen as part of that rewrite, not before it.
- Treat this as its own project, not a quick swap-in.

### Out of scope -- Class Standardize
- Still scans on its own too, but it's a different kind of tool: it
  applies a mapping file you've already reviewed by hand, rather than
  detecting issues itself. Doesn't fit the "analysis finds it, repair
  reads it" shape the other tools do. Leaving as-is unless that
  changes.

## Open questions to resolve when picking this back up
- Whether Phase 5's rewrite happens before or after some other future
  phase -- no strong reason either way yet.

## Continuity note
This file is the source of truth for where this migration stands --
more reliable than relying on conversation memory across sessions.
Update it at the end of each session that touches this: what phase
got finished, what got decided, what's still open.
