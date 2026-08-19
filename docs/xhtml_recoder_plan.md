# XHTML Recoder -- Planning Doc

**Status:** Not started. This is a planning doc only -- no code yet.
**Started:** session ending with the chapter_markup page_breaks.py cleanup.

## The problem

CSS page breaks (`page-break-after` etc., see `page_breaks.py`) are a
*hint* -- reading systems are allowed to ignore them, and support is
genuinely inconsistent across devices (this is exactly what the
page_breaks.py work was responding to). The only thing every reading
system actually guarantees is a fresh page at a spine boundary --
i.e. a new physical XHTML file.

Goal: split a book's chapters/sections onto their own XHTML files
(each becoming its own spine item), so page breaks are structural
rather than a CSS request a reader can decline.

## Why this needs a Phase 0, before any splitting code

Everything built so far (chapter_markup, class_standardize) is
low-stakes if wrong: a missed or wrong chapter-boundary guess means a
paragraph doesn't get a CSS class it should have, or gets one it
shouldn't. Annoying, easy to spot, easy to fix.

Physically splitting a file is not that. Get a boundary wrong and you
can silently misplace, duplicate, or lose content, permanently reorder
sections, or break every internal link that used to work -- and it's
much harder to notice after the fact than a missing CSS class. The bar
for "confident enough to split on" needs to be meaningfully higher
than the bar chapters.py currently uses for "confident enough to add a
CSS class to."

So before writing any splitting logic: audit/harden the structure
detection itself, ideally corroborating chapters.py's marker-text
detection against other signals already sitting in the book when
they're available (existing NCX/nav TOC entries, heading tags,
existing internal anchors like `calibre_pb_N`) rather than trusting
text-pattern matching alone.

## Phased plan

### Phase 0 -- Structure analyzer audit / hardening
Broken into sub-steps below because Phase 0 alone was too big for one
session. Each sub-step should be completable in a single session on
its own. Status of each tracked here as they're done.

- **0a -- Document current detection signals.** [x] Done. See
  `chapter_detection_signals.md`: what counts as a candidate, how
  scoring works, how the winning sequence gets picked, and a callout
  of what's notably absent from today's confidence bar (no TOC/anchor
  corroboration, no near-miss visibility, no minimum-content gate).
- **0b -- Define the higher safety bar.** [ ] Not started.
  Define what "safe to physically split on" should require, versus
  today's "safe to add a CSS class" bar used by chapters.py/
  chapter_markup.py.
- **0c -- Design the structure tree shape.** [ ] Not started.
  Blueprint only (dataclasses/fields), no detection logic: how a
  book's front matter / chapters / back matter -- possibly nested,
  Parts containing chapters -- gets represented in memory, with a
  confidence + supporting evidence slot per boundary.
- **0d -- Skeleton using existing signal only.** [ ] Not started.
  Assemble a first version of the structure tree (maybe a new
  structure.py, maybe an extension of chapters.py) using just today's
  marker-text detection, wired into 0c's shape. No corroboration yet.
- **0e -- TOC cross-check.** [ ] Not started.
  Corroborate boundaries against existing NCX/nav TOC entries when
  present.
- **0f -- Anchor cross-check.** [ ] Not started.
  Corroborate boundaries against existing internal anchors some
  books already have (e.g. `calibre_pb_N`-style bookmarks).
- **0g -- Confidence scoring.** [ ] Not started.
  Combine all available signals into one per-boundary confidence
  score.
- **0h -- Review command.** [ ] Not started.
  A dry-run command to review detected structure before any file gets
  touched, same manual-review shape as `map-css`
  (`map-structure`? naming TBD).

### Phase 1 -- Single-file splitting mechanics (proof of concept)
- Given one XHTML file + a set of confirmed boundaries, produce N
  standalone XHTML documents (proper doctype/head/body, same
  stylesheet links, images/resources still resolve).
- Register them in the manifest + spine in place of the original
  single entry, correct reading order.
- Automatic integrity check: total text content before and after the
  split should match exactly (word count or similar) -- a concrete,
  cheap safety net worth having from day one.
- No cross-reference rewriting yet. Internal anchors may break
  temporarily -- this phase is purely "does the mechanical split work
  and keep every word."

### Phase 2 -- Cross-reference rewriting
- Every internal `href="...#fragment"` anywhere in the book that
  pointed into a file that just got split needs to be redirected to
  the new file + fragment.
- Covers: NCX/nav TOC entries, in-body footnote/endnote links, any
  other internal cross-references.

### Phase 3 -- NCX / nav TOC generation or repair
- Books that were one big file with `calibre_pb_N`-style internal
  anchors (no real per-file TOC) need a TOC generated from the new
  chapter files.
- Books that already had a working TOC need it re-validated against
  Phase 2's rewritten links.

### Phase 4 -- Edge cases and hardening
- Nested structure (Parts with multiple chapters).
- Front matter / back matter -- split out too, or left bundled?
- Books already partially split (some chapters already separate
  files, some not) -- mixed-state handling.
- Minimum-content gate so a stray short "boundary" (e.g. a
  misidentified scene divider) doesn't trigger a split.

### Phase 5 -- Wire into repair pipeline / CLI
- Same manual-review posture as class_standardize: a dry-run/review
  step before anything is applied, not an automatic split on `repair`.

## Open questions to resolve when we pick this back up
- Exact naming scheme for split files (avoid manifest collisions).
- Where new files live (same directory as the original, to keep
  relative CSS/image paths trivial -- current lean, but not decided).
- Whether Phase 0's structure tree subsumes chapters.py entirely or
  sits alongside it.

## Continuity note
This file is the source of truth for where this feature stands --
more reliable than relying on conversation memory across sessions.
Update it at the end of each session that touches this feature:
what got built, what got decided, what's still open.
