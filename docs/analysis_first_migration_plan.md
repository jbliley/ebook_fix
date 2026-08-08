# Analysis-First Migration -- Planning Doc

**Status:** In progress. Four of six repair tools fully converted;
Phase 5 (Whitespace Normalizer) rebuilt and wired to the analysis
cache, config toggle still coarse-grained (see below).
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

### Phase 5 -- Whitespace Normalizer (in progress)
- Full rebuild done, not just wired to the analysis cache yet (see
  below). New `ebook_fix/whitespace.py` replaces the old flat regex
  pass with a real DOM-aware engine:
  - A recursive walker (`iter_text_slots`) tracks protected subtrees
    (pre/code/style/script/svg/math) at any depth, not just an
    element's own tag -- the old code let content nested inside a
    `<pre>` get whitespace-collapsed anyway, since `root.iter()`
    doesn't know about ancestry.
  - Inline-element spacing is handled per edge: leading and trailing
    whitespace on a text/tail node are each independently checked
    against their actual neighbor (`_leading_glue_sensitive` /
    `_trailing_glue_sensitive`), collapsing to a single space instead
    of disappearing wherever an inline element sits on that side.
  - Standalone whitespace-only nodes (nothing but whitespace) always
    collapse to a single space now, never delete outright -- an
    earlier version of this rewrite treated block/block boundaries as
    safe to delete since the tags already force a line break when
    rendered, but that's only true for rendering. `BrokenSentences.epub`
    (a PDF conversion with one printed line per `<p>` tag) exposed the
    real cost: deleting the inter-tag whitespace glued mid-sentence
    line fragments into single words with no space at all, invisible
    on screen but destructive to the actual text content. Caught this
    by comparing total word count before/after across all five sample
    books -- worth repeating that check for any future change here.
  - The old known bug (`([,.;:!?])([A-Za-z])` mangling "3.14",
    "U.S.A.", "example.com", "Mr.Smith") is fixed with a narrower
    rule requiring the actual shape of a missed sentence break, plus
    an explicit abbreviation list. Verified against all four cases
    from the original bug report plus real catches pulled from
    RunTogetherText.epub -- see session notes below for the specific
    test cases if this needs revisiting.
  - Granular category breakdown (leading/trailing indentation,
    repeated whitespace, tabs, space before punctuation, missing
    sentence spacing, whitespace-only nodes, protected nodes skipped)
    now shows in `analyze`'s `[Whitespace]` section, matching the CSS/
    Paragraphs/Images sections.
- `ebook_fix/modules/whitespace.py` rewritten to match the Phase 1-4
  pattern: reads `analysis.whitespace` instead of scanning the book
  itself, applies each issue's precomputed `after` text directly to
  the live element, with a staleness guard (skip if the live
  text/tail no longer matches what analysis saw, in case an earlier
  repair module already touched that exact node).
- Wired into `analyzer.py` (single pass) and `config.py` (new
  `WhitespaceRepairConfig`, currently just an `enabled` toggle).
- What's NOT done yet:
  - Per-category config toggles (e.g. disable "missing sentence
    space" fixes but keep indentation cleanup). The analyzer currently
    bakes all applicable fixes into one `after` string per node, so
    granular toggles would need repair to recompute with a
    config-aware rule set rather than just applying what analysis
    found -- worth deciding whether that's worth the added complexity
    before building it.
  - `--log` output capture (separate item, already on the roadmap).
  - Further inline-spacing edge cases (deeply nested inline runs,
    right-to-left text) haven't been stress-tested beyond the sample
    books and one synthetic DOM test.
- Verified same way as Phase 3/4: ran `analyze` and full `repair`
  against all five sample books, diffed word sequences before/after
  with `difflib` to catch merged/lost words (not just raw counts,
  since counts alone hid the bug above), and directly unit-tested the
  abbreviation/decimal false-positive cases from the bug report.

### Out of scope -- Class Standardize
- Still scans on its own too, but it's a different kind of tool: it
  applies a mapping file you've already reviewed by hand, rather than
  detecting issues itself. Doesn't fit the "analysis finds it, repair
  reads it" shape the other tools do. Leaving as-is unless that
  changes.

## Open questions to resolve when picking this back up
- Whether per-category whitespace config toggles are worth the extra
  complexity (see Phase 5 above) -- no decision made yet.

## Continuity note
This file is the source of truth for where this migration stands --
more reliable than relying on conversation memory across sessions.
Update it at the end of each session that touches this: what phase
got finished, what got decided, what's still open.
