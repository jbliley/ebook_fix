# Chapter Detection: What chapters.py Actually Looks At (Phase 0a)

**Status:** Reference doc, written for the XHTML Recoder Phase 0 audit.
See `xhtml_recoder_plan.md` for how this fits into the bigger plan.

This document exists to answer one question before any file-splitting
code gets written: **exactly what does chapters.py currently trust,
and how sure is it?** Everything below describes today's behavior
only -- no proposed changes.

## The big picture

chapters.py never looks at the whole book at once. It works in four
stages:

1. **Find candidates.** Scan every page for short bits of text that
   might be a chapter marker ("Chapter Four", "IV", "- 4 -", "Fourth").
2. **Classify each candidate.** Figure out what kind of number it is
   and score how convincing it looks in isolation.
3. **Merge repeated headers.** Fold running headers that repeat across
   many pages (common in PDF-converted books) into a single candidate.
4. **Find the winning sequence.** Look for the longest, highest-scoring
   run of candidates that count upward in a believable way, in the
   order they actually appear in the book. Only candidates in that
   winning run get marked "confirmed."

Only step 4's output -- the confirmed run -- is what the rest of the
project currently acts on (adding a CSS class via chapter_markup.py).
Nothing outside that winning run is used for anything today.

## Stage 1: What counts as a candidate at all

A candidate has to be a short, isolated bit of text sitting in a
paragraph, heading, or span tag (not a `<div>`, which usually wraps a
whole chapter's worth of prose and would just be noise here). "Short"
means 8 words or 60 characters, whichever comes first -- generous
enough for "Chapter Twenty-Seven: The Long Way Home" but not a whole
sentence. If the tag contains a nested candidate tag inside it (a
heading wrapping a span, for example), only the inner one counts, so
the same text isn't reported twice.

## Stage 2: How a candidate gets classified and scored

**What number styles are recognized:** plain numbers ("4"), numbers
wrapped in dashes ("- 4 -"), Roman numerals ("IV"), and spelled-out
numbers or ordinals ("Four", "Fourth", "Twenty-Seven"). Roman numerals
get double-checked by converting back to text and comparing, so
invalid forms like "IIII" are rejected.

**What label words are recognized:** "Chapter" and "Section" mark an
ordinary chapter. "Book," "Part," and "Volume" mark a bigger division
that chapter numbering commonly restarts under (see below). Text can
also qualify with no label word at all, if it's just a bare number by
itself ("IV" sitting alone in a paragraph).

**How a candidate's score is built.** Every candidate starts at zero
and picks up or loses points based on how convincing each of its
traits is:

| Trait | Points |
|---|---|
| Sits in an actual heading tag (`<h1>`-`<h6>`) | +3 |
| Has a label word ("Chapter", "Part", etc.) | +2.5 |
| Its CSS class or id mentions chapter/title/heading | +1.5 |
| Roman numeral or dash-wrapped number | +1.5 |
| Spelled-out number or ordinal | +1.0 |
| Bare plain number with nothing else backing it up | -0.5 |
| Short text (3 words or fewer) | +0.5 |

The bare-number penalty is deliberate: an unlabeled "4" in a paragraph
is the single easiest thing to mistake for a chapter marker when it's
actually a page number, footnote, or something mentioned in the story.
It only survives into a winning sequence if enough of its neighbors
also count up convincingly.

## Stage 3: Collapsing repeated running headers

PDF-to-EPUB conversions often produce one file per *printed page*
rather than one per chapter, with the chapter title repeated at the
top of every page as a running header. Left alone, that would make one
real chapter look like dozens. Before sequence-checking runs, any
consecutive candidates (in book order) that share the same normalized
text, number, and style get folded into one, keeping a count of how
many pages repeated it and which files it appeared in.

## Stage 4: Deciding which candidates form the real sequence

This is where "confident enough" actually gets decided today, and
it's the part most relevant to raising the bar for physical splitting.

- Candidates are grouped by number style (Roman, Arabic, spelled-out,
  etc.) -- a book is expected to stick to one style throughout.
- Within a style, in book order, the algorithm looks for the run where
  each number is higher than the last (allowing a gap of up to 2, to
  tolerate an unlabeled or skipped chapter), maximizing **total score**
  rather than raw run length.
- Scoring by total score rather than length matters: a long run of
  weak, unlabeled numbers (like printed page numbers counting up
  through nearly every file) could easily be longer than the real,
  well-labeled chapter run, and length alone would pick the wrong one.
- Crossing into a new "Book"/"Part"/"Volume" is allowed to reset the
  count back down near 1 without breaking the run, since many classic
  novels restart chapter numbering inside each part.
- A run has to reach at least 3 candidates to count as a believable
  sequence at all -- two numbers counting up in a row is too easy to
  be coincidence.
- Whichever style's run scores highest overall wins. Every candidate
  in that winning run is marked "confirmed"; everything else,
  including runs that almost won, is discarded.

## What "confidence" means today, in one sentence

**A candidate is trusted only by being part of the single highest-
scoring, longest-enough, style-consistent, monotonically-increasing
run in the whole book** -- there is no separate per-boundary confidence
number, no corroboration from anything outside the book's own running
text, and no way today to see the runs that almost won or to review
the decision before it's acted on.

## What's notably absent (relevant to Phase 0b and beyond)

- **No corroboration from outside the marker text itself.** Existing
  NCX/nav table-of-contents entries and existing internal anchors
  (like `calibre_pb_N` bookmarks some books already have) aren't
  consulted at all right now, even when they're sitting right there in
  the book.
- **No visibility into near-misses.** `other_sequences` is tracked
  internally but nothing surfaces it -- there's no way today to see
  "the runner-up sequence scored almost as high" as a signal that the
  book is ambiguous.
- **No minimum-content gate.** A very short "chapter" (a one-line
  interlude, a misidentified scene divider) can be part of a confirmed
  run today with nothing checking that it actually contains a
  reasonable amount of chapter content.
- **The bar is calibrated for a low-stakes outcome.** Today a
  confirmed boundary only results in a CSS class being added to a
  paragraph -- easy to spot and fix if wrong. None of the scoring
  above was designed with "this boundary will physically split a file
  into two" as the cost of being wrong.

These gaps are exactly the raw material for Phase 0b (defining the
higher bar) and 0e/0f (adding the corroborating signals).
