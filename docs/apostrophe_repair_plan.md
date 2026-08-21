# Missing-Apostrophe Repair -- Planning Doc

**Status:** Phase 1 done. Phases 2/3 not started.
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
- Detect the "word + space + s" shape without a closed whitelist to
  check it against.
- Given the false-positive risk, this phase should **only report**
  candidates for review, not auto-repair them -- same posture as
  `class_standardize`'s dry-run review step. Whether this eventually
  gets a confidence-scoring pass similar to the XHTML Recoder's
  boundary evidence (e.g. corroborating against a dictionary of common
  nouns, or checking surrounding punctuation) is an open question to
  revisit once Phase 1 is done and we can see how much of what you're
  hitting is actually possessives vs. contractions.

### Phase 3 -- Edge cases and hardening
- Contractions at a sentence start after a dropped leading quote
  mark (e.g. `'Tis` vs `Tis`).
- Multiple consecutive instances in the same text node/sentence.
- Interaction with the Whitespace Normalizer and Ellipsis Normalizer
  pipeline ordering, since all three now touch text nodes -- likely
  needs to run before the Whitespace Normalizer for the same
  stale-check reason Ellipsis does (documented in
  `analysis_roadmap.md`'s pipeline-ordering notes).

## Open questions to resolve when we pick this back up
- Should the "'s" contraction fragment list (it/that/there/here/
  what/who/let/he/she) be trimmed further, or is there a safe way to
  widen it without bleeding into Phase 2's possessive territory?
- Does Jacob's book have cases this whitelist won't catch (rarer
  contractions, dialect spellings)? Worth checking against the actual
  book once Phase 1 exists, if he's willing to share the file.

## Continuity note
This file is the source of truth for where this feature stands --
more reliable than relying on conversation memory across sessions.
Update it at the end of each session that touches this feature: what
got built, what got decided, what's still open.
