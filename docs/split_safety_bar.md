# Defining "Safe to Split" (Phase 0b)

**Status:** Proposal, written for review. Core requirements below are
still open for feedback; the no-corroboration question has been
decided (see that section). This sets the bar Phase 0d onward will be
built against.

`chapter_detection_signals.md` (Phase 0a) documented today's bar:
a chapter marker is trusted only by being part of the single
highest-scoring run of markers counting upward through the book. That
bar was only ever built for "safe enough to add a CSS class to a
paragraph" -- a mistake there is easy to spot and cheap to fix.
Physically splitting a file is not that. A wrong boundary can lose,
duplicate, or misplace content and it's much harder to notice after
the fact. This doc proposes what should be required before a boundary
is trusted enough to actually cut a file in two.

## The core idea

Today's system asks one question per book: "what's the best sequence
I can find?" and then trusts every marker in it equally. The higher
bar needs to ask a second, separate question per *boundary*: "how sure
am I about this specific cut point?" A book can have an excellent
overall sequence with one or two individually shaky boundaries inside
it (a damaged page, an unusual chapter title, an OCR glitch) -- the
split needs to be able to trust most of a book while still refusing
to cut at the boundaries it isn't sure about, rather than an
all-or-nothing decision for the whole book.

## Proposed requirements, before a boundary is split-eligible

1. **It has to be part of the winning sequence.** No change from
   today -- this is the floor, not the whole bar.

2. **It needs corroboration from something outside the marker text
   itself.** As 0a noted, today's scoring never checks anything else
   already sitting in the book. For splitting, a boundary should need
   at least one supporting signal beyond the marker text and its
   score: a matching existing table-of-contents entry, or a matching
   existing internal bookmark (like `calibre_pb_N`). This is what
   Phase 0e and 0f will build. A book with no TOC and no existing
   bookmarks at all would need a different, explicitly weaker fallback
   -- see "Open question: books with no corroborating signal" below.

3. **Its winning run needs a healthy margin over the runner-up.**
   0a noted that near-miss sequences are found internally but never
   surfaced. If the best-scoring sequence and the second-best are
   close in score, that's a sign the book itself is ambiguous, and
   splitting on a coin-flip decision is exactly the kind of silent,
   hard-to-notice mistake this phase exists to prevent. Propose:
   require the winning sequence's score to beat the runner-up by a
   clear margin (exact number TBD once we have more sample books to
   test against, but meaningfully more than "just barely ahead").

4. **Each resulting piece needs a minimum amount of real content.**
   0a flagged that a one-line "chapter" can be part of a confirmed run
   today with no check that it actually contains a chapter's worth of
   content. A boundary that would produce a sliver of a file (a
   misidentified scene divider, a stray heading) shouldn't be
   split-eligible on its own -- it should fold into the section it
   sits within instead.

5. **The split point has to be structurally clean, not just textually
   convincing.** A marker being confirmed by chapters.py's scoring
   doesn't by itself guarantee it's sitting somewhere a file can
   actually be cut -- e.g. not in the middle of a table, a footnote
   block, or some other structure that would end up broken across the
   two resulting files. This needs its own check, separate from the
   text-based confidence score.

6. **Whole-book coverage has to add up.** Before/after every boundary,
   the book's total content has to be accounted for -- nothing
   silently dropped between confirmed boundaries. (This overlaps with
   Phase 1's "word count before and after must match exactly" safety
   net, but it belongs here too, as a pre-check before splitting is
   even attempted, not just a post-check after.)

   **Done (2026-08-29):** see `structure.py`'s `apply_coverage_check`
   and the write-up in `docs/analysis_roadmap.md`. A mismatch blocks
   every boundary in the book from being split-eligible, not just the
   one nearest the gap -- see that write-up's "not done" note for what
   this does and doesn't catch.

## What happens when a book doesn't clear the bar

Two different situations, and they should be handled differently:

- **The whole book's sequence is unreliable** (best sequence barely
  edges out the runner-up, or there's no sequence at all) -- don't
  split anything. Report why, same as any other analysis finding.
- **Most of the book is reliable but a specific boundary isn't** (one
  weak or uncorroborated marker in an otherwise solid sequence) --
  split at every boundary that clears the bar, leave the unclear one
  un-split (that section stays bundled with its neighbor), and flag it
  clearly in the review output so it's a visible decision, not a
  silent gap.

## Decided: books with no corroborating signal at all

Requirement 2 above needs *some* books to still be handled even when
they have no existing TOC and no existing bookmarks -- plenty of
plain-text-style conversions have neither.

**Decision: never auto-split these.** A book with no corroborating
signal always stops at "reviewed, not split" and needs a person's
sign-off per boundary -- no scoring workaround that lets it through
automatically. This fits where the project is headed: the eventual
goal is a GUI where a person looks at exactly these ambiguous cases
and decides per-boundary, so building an automatic fallback here would
be solving a problem the GUI is meant to solve anyway. Automatic
handling of the no-corroboration case is explicitly deferred, not
planned for now -- worth revisiting only once there's a mature enough
review interface that "ask the person" is already cheap and natural,
at which point there may be nothing left to build here at all.

## What this doc deliberately does not cover yet

- The actual data shape for storing a boundary's confidence + evidence
  (that's Phase 0c).
- How TOC/anchor corroboration actually gets checked (0e/0f).
- Front matter and back matter handling -- split out too, or left
  bundled? (Already flagged as an open question in the main plan doc;
  still open.)

## Next step

Phase 0c: design the actual structure-tree shape (front matter /
chapters / back matter, nested Parts) with a slot for a boundary's
confidence and the evidence backing it, based on the requirements
above.
