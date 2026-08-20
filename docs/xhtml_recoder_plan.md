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
- **0b -- Define the higher safety bar.** [x] Done, pending your
  sign-off. See `split_safety_bar.md`: proposes per-boundary
  corroboration, a margin-over-runner-up check, a minimum-content
  gate, and a structural-cleanliness check, plus how a book with only
  some weak boundaries should be handled (split what's solid, leave
  the rest bundled and flagged) versus a book with no reliable
  sequence at all (don't split anything). Leaves one open question
  about books with no TOC/anchors to corroborate against.
- **0c -- Design the structure tree shape.** [x] Done. See the new
  `src/ebook_fix/structure.py`: `NodeKind`, `SplitConfidence`,
  `BoundaryEvidence` (one slot per requirement from
  `split_safety_bar.md`, with `.confidence` computed live rather than
  stored, so it can never drift out of sync with the fields behind
  it), `StructureNode` (nestable, for Parts containing chapters), and
  `BookStructure` (the whole-book result, holds the sequence-margin
  stat too).
- **0d -- Skeleton using existing signal only.** [x] Done. Also in
  `structure.py`: `build_structure()` reads chapters.py's
  `BookChapterSummary` and assembles a first-draft tree using only
  sequence membership -- no TOC/anchor corroboration yet, so every
  node comes out as SEQUENCE_ONLY or NONE confidence, never
  CORROBORATED, until 0e/0f exist. Smoke-tested clean against all six
  sample EPUBs. Surfaced a real gap while wiring this up: Part/Book/
  Volume markers never go through chapters.py's sequence search at
  all (only chapter candidates do), so a detected Part boundary today
  has *less* validation behind it than a chapter boundary, not more --
  documented in `chapter_detection_signals.md` and reflected in
  `build_structure()` by never treating a part boundary as
  sequence-confirmed.
- **0e -- TOC cross-check.** [x] Done. Also in `structure.py`:
  `apply_toc_corroboration()` matches confirmed chapters against the
  book's existing NCX/nav TOC and fills in `matched_toc_entry`.
  Matches on a fragment id (checking the candidate's element and its
  nearest ancestors) when the TOC entry has one, or on "first chapter
  in that file" for whole-file links with no fragment. Run against
  all six sample EPUBs first: zero matches everywhere, which turned
  out to be the correct result, not a bug -- every sample book's NCX
  is a stub with a single "Start" entry pointing at the title page,
  the same kind of broken TOC toc.py already flags, so there was
  nothing real to match against. Confirmed the matching logic itself
  works with a synthetic test covering all three cases: fragment
  match, whole-file match, and a deliberate id mismatch that correctly
  stayed unmatched.
- **0f -- Anchor cross-check.** [x] Done. Also in `structure.py`:
  `apply_anchor_corroboration()` scans actual chapter content (not the
  NCX/nav TOC, which 0e already covers) for internal cross-reference
  links -- footnotes, an index, "see Chapter 5" links, anything with
  an in-book `#fragment` -- and matches their targets against a
  candidate's own id the same way 0e matches TOC fragments. Run
  against all seven sample books (Sidewinders included): zero matches
  everywhere, which is the honest, correct result -- none of these
  particular books happen to have any in-body cross-reference links
  at all. Proved the matching logic itself works with a synthetic test
  using real lxml elements: a genuine cross-reference matched
  correctly, an external `http://` link was correctly ignored, and a
  candidate with no id at all correctly stayed unmatched. Deliberately
  does NOT treat the `calibre_pb_N` per-*page* bookmark ids mentioned
  in `analysis_roadmap.md` as corroboration on their own -- those
  exist on every page regardless of chapter boundaries, so matching
  one would be false corroboration; only a link whose target actually
  matches a *candidate's own* id counts.
- **0g -- Confidence scoring.** [ ] Not started.
  Combine all available signals into one per-boundary confidence
  score.
- **0h -- Review command.** [ ] Not started.
  A dry-run command to review detected structure before any file gets
  touched, same manual-review shape as `map-css`
  (`map-structure`? naming TBD). CLI is the near-term step; the
  longer-term goal is a GUI where boundaries -- especially
  uncorroborated ones, see `split_safety_bar.md` -- get decided
  per-boundary by a person. The CLI command's output shape should
  keep that later GUI in mind (structured, not just printed text).

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
