# Missing-Apostrophe Repair -- Planning Doc

**Status:** Phases 1, 2, and 3 done. This feature is considered
complete for now; see Open Questions below for anything left to
revisit later.
**Started:** session following the XHTML Recoder Phase 0 work.

## The problem

Some conversions (OCR passes and certain PDF/print-to-EPUB pipelines
in particular) drop the apostrophe glyph entirely and leave a plain
space where it used to be. A contraction like "don't" comes out as
"don t" -- two separate words where there should be one. Confirmed
this is a real issue already sitting in the sample library, not just
Jacob's book: `GutenbergText-ChapterSplit.epub` contains `IT S A
STRAY DOG!`, which should read `IT'S A STRAY DOG!`.

This is a different failure mode than anything `typography.py`
currently tracks. `typography.py` already counts straight vs. curly
apostrophes to catch *style* inconsistency (a real apostrophe
character that's just the "wrong" shape). This is about apostrophes
that are **missing outright**, with a stray space standing in for
them.

## Two sub-problems of very different risk

**1. Contractions** (don t, isn t, I ll, we ve, let s, y all, ...)
This is a closed set. English has a fixed, well-known list of words
that contract, so this can be checked against a whitelist rather than
guessed at. Low false-positive risk once the whitelist is right.

**2. Possessives** (the dog s bone -> the dog's bone)
This is open-ended -- any noun can take a possessive "'s", so there's
no fixed word list to check against. "the dog s bone" is a safe fix,
but plenty of genuine two-word phrases fit the same shape and would
be wrongly joined. This needs a much higher bar than contractions,
and it's worth treating as a separate, later phase rather than
building it into the same pass at the same confidence level.

Recommendation: build and ship contraction repair first (Phase 1
below), since it's the safer and almost certainly the bigger share of
what's showing up in your book. Treat possessives as a flag-only,
manual-review feature for later (Phase 2), the same cautious posture
the XHTML Recoder takes toward ambiguous chapter boundaries.

## Detection approach (contractions)

A whitelist of known contraction word-pairs, split into the part
before the gap and the fragment after it:
- `n't` fragment (t): don, doesn, didn, isn, aren, wasn, weren,
  hasn, haven, hadn, couldn, wouldn, shouldn, won, can, mustn,
  needn, ain
- `'ll` fragment (ll): I, you, we, they, he, she, it, that, there,
  who, what
- `'ve` fragment (ve): I, you, we, they, could, would, should,
  might, must
- `'re` fragment (re): you, we, they
- `'d` fragment (d): I, you, we, they, he, she, it, who, there
- `'m` fragment (m): I
- `'s` fragment (s), **contraction reading only**: it, that, there,
  here, what, who, let, he, she -- this list needs to stay narrow,
  since a bare `'s` is also the possessive marker (Phase 2's
  problem), and this phase should not touch anything ambiguous
  between the two.
- Fixed idioms: y'all, o'clock, ma'am, 'tis, 'twas

Matching rules, to keep this safe:
- Word-boundary matching, single space only (not "don  t" with two
  spaces, and not across a line break) -- a wider gap is more likely
  a different problem than a missing apostrophe.
- Case-preserving: match case-insensitively (so `IT S` still matches
  against `it` + `s`) but rebuild the joined word using the original
  casing of both halves, so `IT S` becomes `IT'S`, not `It's`.
- Reuse `iter_text_slots` from `whitespace.py`, same as `ellipsis.py`
  already does, so protected content (pre/code/script/style/svg/math)
  is skipped automatically without duplicating that walk.
- Apostrophe character written on repair should match the book's
  existing dominant style rather than a hardcoded default --
  `typography.py` already computes `apostrophe_style` (straight vs.
  curly) per chapter and for the book overall, so repair should read
  that and use whichever style the book already favors. Falls back to
  straight (`'`) for a book with no existing signal either way.

## Architecture (mirrors the Ellipsis Normalizer)

Same shape as `ellipsis.py` / `modules/ellipsis_repair.py`, which is
the closest existing precedent (small, self-contained text-pattern
fix, DOM-aware, analysis-first):

- **New file `src/ebook_fix/apostrophes.py`** -- the whitelist,
  `normalize_apostrophes_text()` (pure, unit-testable, mirrors
  `normalize_ellipsis_text()`), `ApostropheIssue`,
  `ChapterApostropheSummary`, `BookApostropheSummary`,
  `analyze_book_apostrophes()`.
- **New file `src/ebook_fix/modules/apostrophe_repair.py`** -- reads
  `analysis.apostrophes` instead of re-scanning, same
  before/after-guard pattern `EllipsisRepair.repair()` uses so it
  never overwrites text an earlier module in the same run already
  touched.
- **Four wiring touch points**, same as every existing module:
  - `analyzer.py` -- import, add `apostrophes` field to the analysis
    result, populate it in the analysis pass.
  - `config.py` -- new `ApostropheRepairConfig` (`enabled`,
    `target_style` -- "auto" to follow the book's existing style per
    above, or force "straight"/"curly"), registered on `Config`,
    default-TOML block.
  - `engine.py` -- import and register the repair module in the
    pipeline, add an "[Apostrophes]" section to the analysis report
    output alongside the existing "[Ellipsis]" one.
  - `report.py` / existing report plumbing -- no changes expected,
    same `Report.add()` calls as every other module.

## Verification -- one real wrinkle worth flagging up front

Every other text-fixing module in the project gets checked with a
word-count diff before/after (see `analysis_roadmap.md`'s standard
regression baseline). This module is the first one that's *supposed*
to change the word count on purpose -- "don t" (2 words) correctly
becomes "don't" (1 word). A plain word-count diff would flag every
correct fix as a regression.

Plan: verify this module with a **raw character-count diff instead**
(total character count INCLUDING whitespace should stay exactly the
same, since a space is being replaced one-for-one by an apostrophe,
not deleted) rather than a word-count diff, and note this as a
documented exception in the regression-testing notes so it doesn't
get "fixed" back to word-count checking later by mistake.

**[Confirmed during Phase 1 build]** -- verified against
`GutenbergText-ChapterSplit.epub`'s one real match: raw character
count (tags stripped, whitespace included) was identical before and
after, 400,953 either way. The non-whitespace-only count goes UP by
exactly 1 per match instead (328,037 -> 328,038) -- makes sense, a
space was never counted there to begin with, so swapping it for an
apostrophe adds one countable character. Worth remembering: raw
count is the invariant that stays flat, not the non-whitespace count.

## Phased plan

### Phase 1 -- Contraction detection + repair
**[x] Done.**
- Built the whitelist and `normalize_apostrophes_text()` in the new
  `src/ebook_fix/apostrophes.py`, unit-tested against a table of
  cases including the real `IT S A STRAY DOG!` case found while
  scanning the sample library.
- Wired up analysis: `analyze_book_apostrophes()`,
  `BookApostropheSummary`/`ChapterApostropheSummary`/
  `ApostropheIssue`, plus the `analyzer.py` touch point
  (`AnalysisReport.apostrophes`).
- Wired up repair: new `src/ebook_fix/modules/apostrophe_repair.py`
  (`ApostropheRepair`, mirrors `EllipsisRepair`'s before/after-guard
  pattern), new `ApostropheRepairConfig` in `config.py` (`enabled`,
  `target_style` = "auto"/"straight"/"curly"), registered in
  `engine.py`'s pipeline (runs after Ellipsis, before Whitespace, for
  the same stale-check reason Ellipsis runs before Whitespace), plus
  a `[Apostrophes]` section in the analysis report output and a line
  in the Typography Overview.
- `target_style = "auto"` (the default) resolves against
  `typography.py`'s existing per-book straight/curly apostrophe
  counts via `resolve_target_apostrophe_char()`, so a repaired
  contraction matches whatever style the book's other apostrophes
  already use, falling back to straight if the book has no
  apostrophes yet to go on.
- **Real false positive caught and fixed during testing:** `"that
  s/he does not agree"` (real boilerplate text in one of the sample
  books) was initially getting misread as "that" + "s" and turned
  into `"that's/he"`. Fixed with a negative lookahead
  (`(?!/)`) so a slash immediately after the second word blocks the
  match -- that shape means the word is paired with a THIRD word via
  the slash (s/he, and/or, his/her), not a genuine contraction gap.
  Documented in `apostrophes.py`'s regex comment so the reasoning
  doesn't get lost later.
- Verified against all seven sample EPUBs: full `repair` pipeline ran
  clean on every one (no crashes), re-running `analyze` on every
  repaired output came back with zero remaining apostrophe issues,
  and the character-count diff (see above) confirmed the fix behaves
  exactly as expected on the one real match found
  (`GutenbergText-ChapterSplit.epub`).

### Phase 2 -- Possessive detection (flag-only, manual review)
**[x] Done.**
- Added `analyze_book_possessives()` / `BookPossessiveSummary` /
  `ChapterPossessiveSummary` / `PossessiveCandidate` to
  `apostrophes.py`, deliberately kept as a completely separate data
  structure from `BookApostropheSummary` (Phase 1) -- not a shared
  base class, not a shared list with a "confidence" flag on each
  entry. That separation is what actually guarantees no repair module
  can ever reach these and auto-fix one: `ApostropheRepair` only ever
  reads `analysis.apostrophes`, and there is no code path from there
  to `analysis.possessives` at all.
- Wired into `analyzer.py` as `AnalysisReport.possessives`, and into
  `engine.py`'s report output as a `[Possessive Candidates -- Manual
  Review]` section, clearly labeled as not auto-repaired. Each
  candidate is shown with both possible readings (e.g. "dog's" vs.
  "dogs") so a person doesn't have to work that part out themselves.
- Detection: a word of 2+ characters followed by a single space and a
  bare "s", excluding anything already safely handled as a Phase 1
  contraction (so the same split isn't reported twice).
- **Two noise-reduction guards added after testing against the real
  sample books, not just theoretical cases:**
  - Minimum 2-character first word, to filter out letter-spaced
    heading text ("T O M S A W Y E R" was otherwise flooding the list
    with junk like "M's").
  - A bare "s" immediately followed by `./,;:-` or an em/en dash (no
    space) is excluded, since that shape is almost always a middle
    initial or abbreviation ("Michael S. Hart", "in S. Latitude 34°",
    "Gabriela S—Princeton") rather than a possessive marker. **Known
    trade-off:** a sentence that genuinely ends on a possessive right
    before a period ("...it was the dog s.") would also get skipped
    by this guard. Judged the right default for a review list
    specifically -- a list buried in abbreviation noise doesn't get
    read carefully enough to find the real candidates in it.
- **Real finding during testing, logged separately rather than
  chased here:** `BrokenSentences.epub` has an unrelated corruption
  pattern -- a doubled letter loses one copy and gets a space instead
  ("walls" -> "wal s") -- that happens to share the exact same
  "word + space + s" shape this detector looks for, so it shows up as
  noise specifically in that book. Fixing it would need a dictionary
  to tell "wal"+"s" isn't a real word pair, which is out of scope for
  a regex pass and a bigger, separate feature. Logged as its own
  candidate in `docs/analysis_roadmap.md`'s Conversion Artifacts
  section rather than folded into this plan, since it's a genuinely
  different corruption mechanism (a dropped duplicate letter, not a
  dropped apostrophe).
- Verified: full `repair` pipeline still runs clean on all seven
  sample books, and confirmed by direct content check that a flagged
  possessive candidate (`"wal s"` in `BrokenSentences.epub`) is
  byte-for-byte unchanged after a full repair run -- nothing in this
  phase touches book content, by construction.

### Phase 3 -- Edge cases and hardening
**[x] Done.**
- **Leading-apostrophe contractions.** A genuinely different bug
  shape from everything in Phase 1: a dropped apostrophe at the very
  FRONT of a standalone word ("Tis the season" instead of "'Tis the
  season"), not a space in the middle of two words. Added a second,
  independent detection pass in `normalize_apostrophes_text()` for a
  small closed whitelist: tis, twas, twere, twould, tisn't, twasn't,
  gainst. Two candidates were deliberately EXCLUDED after real
  ambiguity was found, not just considered and dismissed
  theoretically:
  - "twill" -- the archaic contraction for "it will", but also the
    name of an ordinary fabric weave (denim). Auto-adding an
    apostrophe to every "twill" in a book that happens to mention
    clothing or textiles would be a real false positive, not a
    hypothetical one.
  - "tween" -- the archaic contraction for "between", but also common
    modern slang for the pre-teen demographic. Same problem.
  - Known remaining gap: this only fixes the LEADING apostrophe. A
    word missing both apostrophes at once (e.g. plain "tisnt" with no
    apostrophe anywhere, instead of "tisn't") isn't recognized -- that
    would need this pass AND a Phase-1-style internal-contraction fix
    to both fire on the same word, and hasn't come up in the sample
    library. Logged here rather than solved speculatively.
- **Multiple instances in one text node.** Stress-tested directly
  (not just spot-checked) with sentences containing three and four
  separate matches back-to-back, including a mix of the space-gap
  pattern and the new leading-apostrophe pattern in the same
  sentence. All resolved correctly -- see the test cases logged
  against this phase's work for the exact strings used.
- **Real bug found and fixed: pipeline-ordering interaction with the
  Ellipsis Normalizer.** This was flagged as a risk in this plan
  before Phase 3 started, and turned out to be a real, reproducible
  bug once actually tested rather than just a theoretical concern.
  Reproduction: a text node containing BOTH an ellipsis artifact and
  a missing apostrophe in the same sentence ("Wait... don t stop").
  Ellipsis Normalizer runs first in the pipeline and correctly fixes
  the ellipsis, but that changes the text node's content -- so when
  Apostrophe Repair ran next, its old guard (`if current_val !=
  issue.before: skip`, the same pattern `EllipsisRepair` itself uses
  against `WhitespaceRepair`) saw the text no longer matched its
  analysis-time snapshot and silently skipped the node entirely,
  meaning "don t" was left completely unrepaired with no error or
  warning.
  - **Fix (in `apostrophe_repair.py` only -- `ellipsis_repair.py` was
    NOT touched, since it's an existing stable module and this
    session didn't need to change it):** instead of skipping the node
    outright when the text has changed since analysis, recompute
    `normalize_apostrophes_text()` fresh against whatever the CURRENT
    text is, rather than trusting the stale snapshot. This is safe
    specifically because `normalize_apostrophes_text()` only ever
    touches its own narrow whitelisted patterns -- it can't collide
    with or undo a change another module already made, since an
    ellipsis fix and a missing-apostrophe fix never occupy the same
    characters. Verified directly: the reproduction case above now
    correctly produces "Wait… don't stop" (both fixes applied)
    instead of "Wait… don t stop" (apostrophe fix silently dropped).
  - This same class of interaction still exists in the OTHER
    direction between Apostrophe Repair and the Whitespace Normalizer
    (Apostrophe Repair runs before Whitespace, so if it changes a
    node, Whitespace's own unmodified stale-check guard would skip
    it) -- that's `whitespace_repair.py`'s existing, already-accepted
    behavior from before this project started using the "skip if
    changed" pattern at all, and wasn't touched here for the same
    reason `ellipsis_repair.py` wasn't: it's a separate, stable,
    already-working module. Worth a future look if it ever turns out
    to matter in practice.
- Verified: full `repair` pipeline still runs clean on all seven
  sample books, re-analyzing every repaired book still comes back
  completely clean of apostrophe issues, and the interaction-bug fix
  was confirmed directly against a constructed reproduction case
  rather than just inferred from code review.

## Open questions to resolve when we pick this back up
- Does Jacob's book have cases this whitelist won't catch (rarer
  contractions, dialect spellings)? Worth checking against the actual
  book once Phase 1 exists, if he's willing to share the file.
- Should Phase 2's possessive candidates eventually get a
  confidence-scoring pass (e.g. corroborating against a common-noun
  wordlist) to cut noise further, similar to the XHTML Recoder's
  boundary evidence? Two guards already handle the noise sources
  found in testing (letter-spaced headings, middle initials); revisit
  if real books surface a noise pattern those two don't cover.

## Continuity note
This file is the source of truth for where this feature stands --
more reliable than relying on conversation memory across sessions.
Update it at the end of each session that touches this feature: what
got built, what got decided, what's still open.
