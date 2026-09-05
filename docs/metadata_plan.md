# Ebook Metadata Standardization Tool — Project Plan

## Goal

Standardize and clean up ebook metadata across the library — starting with
identifier codes (ISBN, ASIN, etc.), then expanding to core fields like
title and author. Handle both lone EPUB files and books managed through
Calibre, keeping both in sync when Calibre is present. Everything should
be reviewable and editable through plain config files for now, with an
eye toward a future GUI that edits the same files directly.

## Core problem being solved

Book identifier metadata is often missing, malformed, or inconsistently
labeled (wrong/garbled `opf:scheme` attributes, scheme names crammed into
the value text, inconsistent formatting). The tool should:

1. Read whatever identifier data exists in a book.
2. Recognize known identifier types (ISBN, ASIN, Goodreads ID, etc.) even
   when messily labeled, and rewrite them into clean, correctly-scoped
   Dublin Core `<dc:identifier>` entries.
3. Fall back to a bare, unscoped `<dc:identifier>` (honest plain DC) when
   no known scheme confidently matches — never guess wrong.
4. Log anything that fell back to bare DC to a review file, so patterns
   can be spotted and promoted into the mapping file later.
5. Do the same eventually for other core fields (title, author, series).

## Where books are found / dual-mode operation

Two supported storage contexts, auto-detected per book:

- **Calibre-managed**: book folder has a sibling `metadata.opf` and lives
  under a `metadata.db`-containing library structure. In this case, read
  and write through both the per-book `metadata.opf` sidecar *and*
  `metadata.db` (via `calibredb`, not raw sqlite writes, to avoid
  corrupting the db while Calibre-Web has it open) — keep both in sync.
- **Lone EPUB file**: no Calibre sidecar found. Read/write directly
  against the EPUB's internal `content.opf`.

Both contexts are handled through a shared backend interface so the
higher-level logic (identifier matching, field normalization) doesn't
need to know which one it's talking to.

## Project structure

Dedicated project root, not folded into the general automation-scripts
collection, since this is expected to grow into its own multi-module tool
(and eventually a GUI):

```
ebook_metadata_tool/
  src/
    metadata/
      schemes/
        identifier_schemes.json      ← identifier standards (built)
        (room for more standards files later, e.g. author name formatting)
      identifiers.py                  ← identifier matching/normalization logic
      core_fields.py                  ← title/author/series read+write (not yet designed)
      backends/
        base.py                       ← shared interface both logic modules call
        calibre_backend.py            ← calibredb + metadata.db path
        epub_backend.py               ← lone-EPUB content.opf path
      review.py                       ← unmatched-identifier logging
    cli.py                            ← manual "run this" entry point for now
  tests/
```

Standards/schema files live inside `metadata/` bundled with the code that
consumes them, but as plain JSON — so a future GUI can edit them directly
without any code changes.

## The identifier mapping file (`identifier_schemes.json`) — built

Editable, per-scheme JSON config. Each entry defines:

- `enabled` — on/off switch; disabling a scheme means matches fall through
  to the bare-DC fallback instead of being tagged.
- `aliases` — strings that count as this scheme in an `opf:scheme` attribute.
- `match_prefix_regex` — strips a leading label from raw text when the
  scheme attribute is missing/garbage, so the remainder can be checked.
- `value_regex` — validates the candidate value after stripping/normalizing;
  rejects the scheme as a candidate if it doesn't match.
- `normalize` — transform applied before writing back (`strip_dashes_spaces`,
  `digits_only`, `uppercase`, `lowercase`, `trim_only`).
- `output_scheme` — exact string written into `opf:scheme` on success.
- `notes` — free text, no functional effect.

Schemes currently defined (18 total):

- **Enabled by default**: ISBN, ASIN, MOBI-ASIN, ISSN, DOI, LCCN, OCLC,
  Goodreads, Hardcover, Google Books, OpenLibrary, LibraryThing, UPC, UUID, URI
- **Off by default** (defined but toggled off — flip on if ever needed):
  VIAF, Douban, Overdrive

Goodreads and Hardcover were prioritized since those are metadata sources
already in use. URI is a deliberate low-confidence catch-all that still
routes through the review log rather than being trusted outright.

## Processing logic (identifiers)

1. Check `opf:scheme` attribute first — if it cleanly matches a known
   scheme's aliases, normalize the value per that scheme's rule and
   rewrite with the canonical `output_scheme`.
2. If the scheme attribute is missing or doesn't match anything known,
   try `match_prefix_regex` against the raw text to detect an embedded
   label (e.g. `"ISBN: 978-1-234..."`).
3. Validate the extracted value against `value_regex` — don't force a
   match that doesn't actually fit the shape.
4. No match at all → bare-DC fallback: strip whitespace/junk, drop any
   bogus `opf:scheme`, leave as a plain `<dc:identifier>`.
5. Dedupe identical values after normalization.

## Review log

Every identifier that falls back to bare DC gets a row logged (CSV or
similar) with: book path (+ Calibre book ID if applicable), title/author,
original raw identifier text + original scheme attribute if any, and what
it was normalized to. Purpose: spot patterns (e.g. 40 books with the same
unmatched prefix) and promote them into the mapping file instead of
hand-fixing books one at a time.

## Module breakdown

Not one monolithic script — one module per metadata *concern*, one shared
layer for file *access*:

- `identifiers.py` — mapping-driven matching/classification, plus
  writing the result back out (`rewrite_identifiers`). Built.
- `core_fields.py` — title/author/series read and write
  (`apply_core_field_updates`, `write_core_field`, `write_subjects`).
  Built. Author "Last, First" vs "First Last", language ISO 639-1 vs
  639-2 conventions, and title formatting/subtitle handling are all in
  `merge.py` (see the 2026-09-03 and 2026-09-04 sessions below), not
  here -- this module only applies whatever value `merge.py` already
  decided is safe.
- `title_match.py` — recognizes a purely-cosmetic title difference
  (whitespace, smart punctuation, ALL CAPS) as the same title, and
  flags (without resolving) a likely subtitle/series-annotation
  difference. Built.
- `backends/` — shared file-access layer (Calibre vs lone-EPUB) used by
  both logic modules so neither duplicates file-handling code.
  `calibre_backend.py` reads metadata.opf (identifiers + core fields)
  and its `OpfShim` doubles as the write target `calibre_write.py`
  uses for the sidecar. A lone EPUB just uses the real `Book` object
  directly for both reading and writing — no separate `epub_backend.py`
  ended up being needed, since `core_fields.py`/`identifiers.py`'s
  functions are already duck-typed to accept either one.
- `calibre_write.py` — writes confidently-resolved core fields and
  cleaned-up identifiers back into a metadata.opf sidecar. Built.
  metadata.db (via `calibredb`) is still NOT touched — needs a real
  Calibre library with `calibredb` installed to test against, not
  available in this environment.
- `merge.py` — built. Reconciles EPUB-side and Calibre-side results,
  flags genuine disagreement (never silently picks a winner), resolves
  known non-issues (language convention, reversed author name)
  automatically, and hands back exactly what's safe to write via
  `epub_updates()`/`calibre_updates()`.
- `review.py` — built. Logs unmatched identifiers and genuine field
  mismatches to `identifier_review.csv`, wired into the `analyze`
  command only (not `repair`, to avoid piling up duplicate rows on
  repeated runs).

Suggested build order (updated):

1. ~~Mapping file + matching/normalization core~~ — done.
2. ~~Calibre-structure detector~~ — done.
3. ~~Calibre backend (read side) + merge~~ — done.
4. ~~Review log writer~~ — done.
5. ~~Language/author-name convention handling~~ — done (2026-09-03).
6. ~~The writer: core fields + identifiers, both the EPUB and the
   metadata.opf sidecar~~ — done (2026-09-04).
7. metadata.db sync via `calibredb` — still open, needs a real Calibre
   library to test against.

## Future / not being planned yet

- GUI with separate text boxes for editing metadata standards directly —
  explicitly deferred; not being designed yet, but the JSON-based mapping
  approach is intended to make it a natural fit later.

## Session update (2026-09-02): identifier/core-field reading migrated into analysis

The identifier and title/author/etc. reading that used to live inline
in `analyzer.py` has been moved into the `metadata` package, run as
part of the same analysis pass rather than a separate step:

- `metadata/identifiers.py` — `analyze_book_identifiers(book)` reads
  every `<dc:identifier>` directly off `book.opf_document` (bypassing
  the old single-value `book.metadata.identifier`, which only ever
  kept the first identifier `parser.py` happened to find) and
  classifies each one against `identifier_schemes.json` using the
  matching logic described above. Read-only for now — it records what
  each identifier is, it doesn't rewrite the book. Matching happens in
  three stages, tried in order: `opf:scheme` attribute match, embedded
  text-prefix match, then (for schemes with no prefix at all, like
  URI, whose value already carries its own recognizable shape such as
  `urn:uuid:...`) a direct value-shape match. Anything none of the
  three catch falls back to bare DC as designed.
- `metadata/core_fields.py` — `analyze_book_core_fields(book)` groups
  title/author/language/publisher/date/rights/description/subjects/series
  into one result. For now this is a pass-through over what
  `ebook_fix.parser` and `ebook_fix.series` already extract; no new
  normalization logic yet (still an open question below).
- `AnalysisReport` gained two fields: `core_fields` and `identifiers`,
  holding the full detail. `AnalysisReport.summary`'s existing flat
  fields (`title`, `author`, `identifier`, etc.) are now populated
  *from* these instead of reading `book.metadata` directly, so nothing
  else reading `analysis_report.summary` needed to change.
- `engine.py`'s `analyze` command now lists every recognized
  identifier under `[Book Metadata]`, not just one — confirmed against
  the sample books that this surfaces a real improvement: MM21.epub
  carries an ISBN plus two Calibre UUIDs, and the ISBN is now
  correctly chosen as the primary identifier shown, instead of
  whichever identifier happened to be listed first in the OPF.

Regression run across all 11 sample books: `analyze` and `repair` both
run clean, repair output is byte-identical on a second pass
(idempotent), and all XHTML/OPF/NCX in the repaired output still
parses as strict, well-formed XML.

Known gap, not fixed here: `ebook_fix.parser._read_metadata` still
uses `.find()` (not `.findall()`) for `dc:identifier`, so
`book.metadata.identifier` itself remains a single flattened value if
anything else ever reads it directly. `metadata.identifiers` avoids
this by reading `book.opf_document` itself, the same way
`ebook_fix.series` already does — but flagging this here in case
`parser.py` or `models.py` ever need a real fix for it later.

## Session update (2026-09-02, part 2): Calibre-structure detector

`metadata/calibre_detect.py` — `detect(epub_path) -> CalibreContext`.
Given a book's file path, walks up from its folder looking for the two
things a real Calibre library always has: a `metadata.opf` sitting
right next to the book, and a `metadata.db` somewhere above it (up to
6 parent directories, comfortably past the normal
`Library Root/Author/Title (id)/` depth). Both need to be present --
either alone could be coincidence. When both are found, also extracts
the Calibre book id from the folder name's trailing `(id)` (Calibre's
own on-disk convention, e.g. `Sidewinders (12)`), and reports the
library root.

Tested against three synthetic fixtures (lone EPUB, a decoy folder
with a metadata.opf but no metadata.db anywhere above it, and a real
Calibre-shaped layout) -- all three classified correctly.

Wired into `engine.py`'s `analyze` command for visibility: a new
`Library: Calibre-managed (id N, root: ...)` or
`Library: standalone EPUB (no Calibre library detected)` line now
prints at the top of `[Book Metadata]`, so this can be verified
directly against the real library rather than only against synthetic
fixtures. All 11 sample books (none Calibre-managed) still correctly
report standalone; a copy placed in a synthetic Calibre-shaped folder
correctly reports Calibre-managed with the right id and root.

This module only detects and reports context -- it doesn't read or
write metadata.opf/metadata.db contents itself. That's the next piece
(the Calibre and EPUB-only backends).

## Session update (2026-09-02, part 3): bug fix from real-world testing

Testing against a real book (Fellowship of the Ring, from the actual
Calibre library) surfaced two real bugs, both now fixed:

1. **`calibre` scheme was conflated with `UUID`.** The real
   `metadata.opf` sidecar has `<dc:identifier opf:scheme="calibre">6130</dc:identifier>`
   -- a plain integer database row id, not a UUID. Split this into its
   own `CALIBRE` scheme entry (aliases: `calibre`, value shape: plain
   digits). Confirmed against the real file: id 6130 matched, which is
   the book's actual Calibre library id -- also independently
   cross-validated against the id the folder-name detector in
   `calibre_detect.py` already extracted from the same book (both
   agreed: 6130).

2. **More general bug: `digits_only` normalization could manufacture a
   false match.** The EPUB's own internal OPF (not the metadata.opf
   sidecar) has `<dc:identifier opf:scheme="calibre">3f7e59de-1169-4c76-810b-b6fa4e2d026c</dc:identifier>`
   -- here "calibre" is mislabeling what's actually a UUID. Since
   `digits_only` deletes every non-digit character, that UUID
   collapsed into a 21-digit string that passed a bare `^\d+$` check.
   This wasn't unique to CALIBRE -- any scheme using `digits_only`
   (OCLC, GOODREADS, HARDCOVER, VIAF, DOUBAN, UPC) could be fooled the
   same way by garbage containing a scheme's matching attribute name
   but a value shaped like something else entirely. Fixed with a
   raw-shape guard: before `digits_only` runs, the raw value must
   already look like digits with only whitespace/dashes as decoration
   (`^[\d\s-]+$`); anything else is rejected outright rather than
   normalized into looking valid. Other normalizers here (strip
   dashes/spaces, upper/lowercase, trim) don't delete
   distinguishing characters, so they can't manufacture a match this
   way and didn't need the guard.

Both confirmed fixed against the real book: the mislabeled UUID now
correctly falls back to bare DC instead of masquerading as a fake
Calibre id, and the real `metadata.opf` CALIBRE/uuid/ISBN/AMAZON→ASIN/
GOOGLE identifiers all classify correctly. Full 11-sample-book
regression (`analyze` + `repair`, idempotency, strict XML validity)
stayed clean throughout.

**Also surfaced, not yet acted on:** this same real book showed the
metadata.opf sidecar and the EPUB's own internal OPF genuinely
disagreeing -- different ISBN (9780007887668 vs 9780007322497),
different publisher (Ballantine Books vs HarperCollins), and series
metadata (Lord of the Rings, index 1) that exists *only* in
metadata.opf, completely absent from the EPUB's own internal file.
Right now `analyze` only ever reads the EPUB's internal OPF, even when
`calibre_detect` confirms the book is Calibre-managed -- it doesn't
read metadata.opf's content at all yet, only detects that the file
exists. Reconciling these two sources is exactly the job of the
Calibre backend (next up in the build order) and needs its own design
decision: which source wins on conflict, or are both recorded
separately.

## Session update (2026-09-02, part 4): Calibre backend + merge, decided by the user

Discussed with the user whether metadata.opf or the EPUB's own internal
file should win on conflict (real example: Fellowship of the Ring has
a different ISBN and publisher in each, plus series info that exists
only in metadata.opf). Decided: neither wins automatically -- record
both, flag disagreement, let a person (or eventually the GUI) decide
per field. This matches the project's existing posture toward
ambiguous decisions (e.g. Case 3 chapter splitting waiting on the
GUI rather than guessing).

**Fixed first, since it directly affects whether this merge means
anything**: `ebook_fix.parser._read_metadata` had `Metadata.date`,
`.rights`, `.description`, and `.subject` defined on the dataclass but
never actually populated -- confirmed by testing against the sample
books: The Call of Cthulhu has a real public-domain rights notice and
subject tags that `analyze` was silently showing as "(none found)".
Fixed by mirroring the existing extraction pattern for title/creator/
etc. Confirmed as a real improvement, not just a refactor, across the
sample-book regression.

**`metadata/calibre_backend.py`** -- `read_metadata_opf(path)` parses a
standalone metadata.opf file directly (it isn't inside an EPUB
container, so it can't go through `ebook_fix.parser`'s normal
book-loading pipeline) and returns identifiers (reusing
`identifiers.extract_identifiers_from_opf`, refactored out of
`identifiers.py` so both the EPUB and metadata.opf paths share the
exact same classification rules) plus core fields (a small local dc:
field reader, since metadata.opf isn't a `Book` object `series.read()`
et al. expect -- a minimal shim object exposing just `.opf_document`
lets `series.read()` work against it unmodified). Read-only, matching
the rest of the metadata package so far.

**`metadata/merge.py`** -- reconciles an EPUB-side and Calibre-side
result:
- Identifiers: same-scheme entries from both sources are combined into
  one list, each tagged with which source(s) it came from
  (`sources: ["epub"]`, `["calibre_opf"]`, or both if they agree
  exactly). Same scheme, different value -- e.g. two different ISBNs
  -- surfaces as a flagged conflict via `.conflicts()`, not a silent
  overwrite.
- Core fields: each field becomes a `MergedField(epub_value,
  calibre_value)` with a `.mismatch` flag when both sides have data
  and disagree, and `.display_value` for the common case where they
  agree or only one side has data at all.
- When a book isn't Calibre-managed (or metadata.opf can't be read),
  the Calibre side of every comparison is simply empty and nothing is
  ever flagged -- callers don't need a separate code path for the
  standalone-EPUB case.

**Wired into `analyzer.py`**: `AnalysisReport` gained `calibre_context`
(computed once here via `calibre_detect.detect(book.source)`, so
`engine.py` no longer needs its own separate detection call),
`merged_identifiers`, and `merged_core_fields`. `analyze`'s
`[Book Metadata]` display now shows `-- MISMATCH` inline wherever the
two sources disagree (title, author, language, publisher, date,
rights, description, series, series index, subjects, and same-scheme
identifier conflicts), and a clean single value otherwise.

**Tested against the real Fellowship of the Ring** (both the EPUB and
its actual metadata.opf, placed in a synthetic but structurally real
`Library Root/Author/Title (id)/` layout): every genuine disagreement
correctly flagged (title, author, language, publisher, date, ISBN),
the mislabeled UUID under `opf:scheme="calibre"` correctly stayed
"unrecognized" rather than false-matching (confirming the earlier
digits_only fix holds through the full merge path), and series
correctly appeared from metadata.opf alone since the EPUB side has
none. Full 11-sample-book regression (`analyze` + `repair`, strict XML
validity) stayed clean throughout, plus a repair pass on the real
Fellowship book itself -- confirmed idempotent by comparing extracted
contents directly (whole-zip-file hashes differ only by each file's
embedded modification timestamp, which is expected and not a content
difference).

## Session update (2026-09-02, part 5): review log writer

`metadata/review.py` -- appends rows to a running CSV
(`identifier_review.csv`, defaults to the current working directory,
matching how `ebook_fix.toml` also defaults to cwd) whenever `analyze`
finds something it couldn't confidently resolve on its own:

- `log_unmatched_identifiers()` -- one row per identifier that fell
  back to bare DC (the original documented purpose of this file).
- `log_merge_conflicts()` -- one row per same-scheme identifier
  conflict and per core-field mismatch between the EPUB and a
  metadata.opf sidecar (the mismatch flags added in part 4). This
  wasn't in the original plan for this file, but it's the same kind
  of "needs a person's eyes" signal, so it goes to the same log rather
  than inventing a second mechanism.

Wired into the `analyze` command only (not into `repair`'s internal
analysis pass), so re-running repair on a book you've already reviewed
doesn't pile up duplicate rows for something already seen. `analyze`
prints how many rows were logged and where, but only when there's
something to report -- clean books stay quiet.

Confirmed against the real Fellowship of the Ring: 7 rows logged (the
mislabeled UUID, the ISBN conflict, and mismatches on title, author,
language, publisher, and date) -- matching exactly what the terminal
display already showed. Confirmed the 11 standalone sample books log
nothing, and the file only grows by the rows a given run actually
found (no false positives, no silent duplication).

This is a plain append log, not a deduplicated state file -- running
`analyze` on the same book twice adds the same rows again with a new
timestamp. That's intentional for now (an audit trail, not a to-do
list), but worth revisiting once there's a GUI that can mark a
conflict as resolved.

## Session update (2026-09-03): language and author convention handling

Two of the mismatches `merge.py` was flagging on every Calibre-managed
book turned out not to be real disagreements at all -- both are now
recognized and resolved automatically instead of going to the review
log.

**Language** -- Calibre stores `dc:language` as a three-letter ISO
639-2 "bibliographic" code (`eng`), while EPUB's own convention is the
two-letter ISO 639-1 code (`en`). Neither side is wrong; they're just
each format's native shape for the same language. New module
`metadata/language_codes.py` holds the full ISO 639-1 <-> 639-2/B
table (plus the handful of terminological codes, e.g. `deu`/`ger`,
that differ from the bibliographic form) and a `codes_equivalent()`
check that also tolerates a region subtag (`en-US`). `merge.py`'s new
`_language_field()` uses it: still a real `MISMATCH` if the codes
genuinely differ, otherwise `MergedField.equivalent=True` with a note
explaining why, and never logged to `identifier_review.csv`.

**Author name order** -- the other recurring, non-substantive
disagreement is "Last, First" on one side and "First Last" on the
other -- same person, just reversed. New module
`metadata/author_names.py` detects this mechanically (exactly one
comma; rearranging the comma side's own words matches the other side
word-for-word) and returns the canonical "First Last" form. Unlike the
language case there *is* a preferred value here, so `merge.py`'s new
`_author_field()` sets `MergedField.normalized_value` to it --
`display_value` now returns that corrected form automatically. A
genuinely different name (not just reordered) still falls through to
the normal mismatch path untouched.

`MergedField` gained `equivalent`, `normalized_value`, and `note` to
carry this: `.mismatch` returns `False` whenever `equivalent` is set,
regardless of the raw string comparison, and `.display_value` prefers
`normalized_value` when one was determined. `engine.py`'s
`[Book Metadata]` display appends the note in brackets after a
resolved field, so the reasoning stays visible even though it's no
longer flagged.

Verified with a synthetic Calibre-managed copy of the Sidewinders
sample (real EPUB, hand-written `metadata.opf` sidecar with
`Johnstone, William W.` / `eng` against the EPUB's own `William W.
Johnstone` / `en`): both resolved cleanly with the expected note, a
genuine date mismatch on the same book still flagged normally, and
nothing spurious added to `identifier_review.csv`. Full regression
across all sample EPUBs in `examples/` stayed clean.

## Session update (2026-09-04): the metadata writer

Everything up to this point only ever reported what was wrong --
nothing actually got written back anywhere. This session adds the
writer, but deliberately scoped to the one case where it's safe to run
without a person or a GUI reviewing each change first: a Calibre-
managed book, where the metadata.opf sidecar sitting right next to the
EPUB acts as a second source confirming the value.

**What writes, and when.** A new repair module,
`modules/metadata_repair.py`'s `MetadataSyncRepair`, only ever acts
when `analysis.calibre_context.is_calibre_managed` is true -- a
standalone EPUB is untouched, same as before (still just flagged for
review). Even then, it only ever writes a field metadata.merge has
already ruled unambiguous: the two sides already agree (nothing to
write), one side was simply empty (an unambiguous backfill either
direction), or the two sides are the same value in a different
rendering (the reversed-author-name case). A genuine disagreement
(`MergedField.mismatch`) is never written -- it stays in
identifier_review.csv exactly as before. Language is never touched at
all, since both codes are already correct for their own format.

**Both sides get updated, not just the EPUB.** Writing into the EPUB
alone would let it drift right back out of sync with metadata.opf the
next time Calibre touches it. `MergedCoreFields` gained
`epub_updates()`/`calibre_updates()` (plus `subjects_for_epub()`/
`subjects_for_calibre()` for backfilling an empty subject list) --
each returns exactly the fields that side is missing or has stale,
computed from the same non-mismatch resolution `.display_value`
already uses. `metadata/core_fields.py` gained the actual write
functions (`write_core_field`, `write_subjects`,
`apply_core_field_updates`), written duck-typed against anything with
an `.opf_document` so they work unmodified against both a real Book
and a bare metadata.opf file. `series`/`series_index` are dispatched
to `ebook_fix.series` instead of a plain dc: element, since it already
knows how to write both the calibre and EPUB3 collection conventions.

**metadata.opf itself.** New module `metadata/calibre_write.py`
parses the sidecar, applies whatever `calibre_updates()` says needs
correcting, and writes it back out (temp-file-then-replace, the same
atomic convention as every other on-disk write in this project).
`metadata/calibre_backend.py`'s existing `_OpfShim` (used to let
`ebook_fix.series.read()` work against a bare metadata.opf) was
promoted to a public `OpfShim` with `.opf_modified`/`.mark_modified()`
added, so the same shim now works for writing too, not just reading.
metadata.db (via `calibredb`) is still NOT touched -- syncing the live
Calibre database needs its own testing against a real library with
`calibredb` installed, which isn't available here, so it stays future
work rather than shipping unverified.

**Respecting --dry-run.** The module itself only ever mutates the
in-memory `Book` object, exactly like every other repair module --
that alone keeps `--dry-run` safe. The metadata.opf sidecar write is a
real disk write outside that mechanism, though, so it's handled
separately: a new `Engine._sync_metadata_opf()`, called from `repair`/
`auto_fix` only *after* the repaired EPUB has actually been saved to
its output path (never during a `--dry-run`, which returns before
that point).

**Config.** New `[metadata_repair]` section (`enabled`,
`sync_calibre_opf`), both defaulting to `true` like every other module.

Verified against a synthetic Calibre-managed copy of the Sidewinders
sample: a reversed author corrected on the metadata.opf side (EPUB's
own copy was already right), a publisher/series backfilled into the
EPUB from metadata.opf, a genuine date disagreement left untouched on
both sides, and `--dry-run` leaving the sidecar's checksum unchanged
and producing no output file at all. `auto-fix` exercised separately,
backfilling a missing metadata.opf date from the EPUB in the other
direction. Full regression across every sample in `examples/` (all
standalone, no Calibre) stayed clean -- the module correctly does
nothing for any of them.

## Session update (2026-09-04, part 2): identifier rewriting -- the writer's second piece

The very first goal in this doc ("rewrite them into clean,
correctly-scoped `<dc:identifier>` entries") was still open even after
the core-field writer -- identifiers.py only ever read and classified,
never rewrote anything. This closes that gap, exactly per the
"Processing logic (identifiers)" rules above: normalize the value, set
`opf:scheme` to the matched scheme's canonical `output_scheme` (or
drop `opf:scheme` entirely for an honest bare-DC fallback), then drop
an exact duplicate that results after normalization.

**Doesn't need a Calibre-managed gate.** Unlike the core-field writer,
this doesn't need metadata.opf as a second source to be confident -- a
scheme's own `value_regex` (already the confidence check for reading)
is enough on its own. `identifiers.py` gained `rewrite_identifiers()`,
duck-typed the same way `core_fields.py`'s write functions are, so it
runs unmodified against a real Book or a bare metadata.opf via
`OpfShim`. New repair module `modules/identifier_repair.py`'s
`IdentifierStandardizeRepair` runs on every book, Calibre-managed or
not.

**The one real safety guard:** an identifier element carrying its own
`id="..."` attribute is never removed as a duplicate, even when
another element normalizes to the exact same value -- something
elsewhere in the OPF (most commonly the package's own
`unique-identifier` reference) may point at that id, and there's no
reliable way to check every possible reference. It's still
normalized/rescoped in place like any other identifier, just never
deleted outright. Confirmed with a synthetic test: an id-bearing
duplicate survives regardless of whether it's the first or second
occurrence.

**`analyze` previews exactly what `repair` would do**, by construction
rather than a hand-maintained parallel dry-run path:
`IdentifierStandardizeRepair.analyze()` runs the identical
`rewrite_identifiers()` against a `copy.deepcopy()` of the OPF tree
instead of the real one, so the preview can never drift out of sync
with the real thing.

**metadata.opf gets the same cleanup**, via a new
`calibre_write.clean_metadata_opf_identifiers()`, called from
`Engine._sync_metadata_opf()` alongside the core-field sync -- same
timing (only after the repaired book is actually saved, never during
`--dry-run`), same atomic-write convention, but its own independent
config toggle (`[identifier_repair]`) since it doesn't depend on
`MergedCoreFields` at all.

**A real, previously-documented bug surfaced again during testing, and
confirmed already fixed correctly:** a UUID mislabeled
`opf:scheme="calibre"` (see the 2026-09-02 bug-fix entry above)
correctly rewrites to a bare, unscoped identifier -- the rewrite logic
just enacts the classification that was already fixed, rather than
needing anything new. Verified this on a hand-built test case using
the exact UUID from that earlier bug report.

Verified against all 11 sample books: `repair --dry-run` runs clean on
every one with no errors, several find and correctly fix real,
previously-unnoticed identifier issues -- MM21.epub's own two Calibre
UUIDs (mentioned in the 2026-09-02 session note above) both get
corrected (one mislabeled `opf:scheme="calibre"` stripped to bare DC,
the genuinely bare `urn:uuid:...` on GutenbergText-ChapterSplit gets
tagged `opf:scheme="URI"`). Full repair + a second pass on the output
confirmed idempotent (nothing left to fix), and the output still
passes `validate`. End-to-end test against a synthetic Calibre-managed
book with a dashed `opf:scheme="isbn10"` value and an embedded
`"calibre:5"` label confirmed both the EPUB and metadata.opf sidecar
end up correctly cleaned (`opf:scheme="ISBN"`/`"CALIBRE"` with
normalized values) alongside the core-field sync from the previous
session, in the same run.

## Session update (2026-09-04, part 3): title -- formatting normalization, subtitle flagged not resolved

The remaining open question from the core-fields writer session --
"whether title needs its own normalization rules the way author and
language now do" -- is resolved: it does, but narrower in scope than
author/language, because title carries a real risk the other two
didn't. Reversing "Smith, John" and recognizing "eng"/"en" both have
exactly one unambiguous right answer. A title difference doesn't
always -- sometimes one side genuinely has a subtitle, or an appended
series annotation, and dropping or keeping that is an editorial call,
not a formatting bug.

**What's auto-resolved (never flagged at all):** purely cosmetic
differences -- extra/irregular whitespace, straight vs "smart"
punctuation (quotes, apostrophes, dashes, ellipses), and one side being
ALL CAPS (a source-data artifact, essentially never a deliberate
title style). New module `metadata/title_match.py`'s
`titles_equivalent()` recognizes these by comparing a folded/
case-insensitive form of both sides, and picks the "cleaner" rendering
to standardize on (not shouting, whitespace already collapsed;
ties go to metadata.opf, matching the existing tie-break convention
elsewhere). `merge.py`'s new `_title_field()` wires this in exactly
like `_language_field()`/`_author_field()` -- `title` was already in
`_WRITABLE_FIELDS`, so the existing writer, `MetadataSyncRepair`, and
the metadata.opf sync all pick this up for free with no other changes
needed anywhere.

**What's flagged, but with a note instead of a bare MISMATCH:** a
likely subtitle ("Sidewinders" vs "Sidewinders: A Western Novel") or
trailing parenthetical, often a series annotation ("Sidewinders" vs
"Sidewinders (Sidewinders, #1)") -- `detect_subtitle_relationship()`
recognizes the shorter title as a normalized prefix of the longer one
and names the added part, but never resolves it; `_title_field()` only
attaches the note when the difference *isn't* already a formatting
match. `engine.py`'s `[Book Metadata]` display now shows a mismatch's
note in brackets (previously only shown for a resolved,
non-mismatched field), and `review.py`'s CSV row now carries the same
note in its `note` column instead of always leaving it blank -- so
`identifier_review.csv` tells you at a glance "these are probably the
same book, here's what's different" instead of just two raw strings.

Explicitly NOT done, on purpose: guessing which of two different
title-casing *styles* (Title Case vs sentence case, as opposed to ALL
CAPS vs either) is "more correct," and auto-resolving the subtitle/
series-annotation case at all -- both are real editorial judgment
calls this project's "repair only unambiguous cases" principle says
to leave to a person.

Verified: unit-tested `titles_equivalent()`/`detect_subtitle_relationship()`
against a battery of cases (ALL CAPS, smart quotes/ellipsis, whitespace
on both sides at once -- caught and fixed a real bug here, where tying
on two equally-dirty renderings returned the raw uncollapsed string
instead of a cleaned one -- genuinely different titles, and both kinds
of subtitle pattern). End-to-end test against a synthetic Calibre book
(EPUB title clean, metadata.opf's `ALL CAPS  ` with trailing
whitespace) resolved correctly and synced the clean form back to the
sidecar. Full regression across all 11 samples stayed clean, no
Traceback/Error, `analyze` unaffected.

## Session update (2026-09-05): review-log timing, and quieting the routine display

Feedback from actually running this against a few real library books,
two rough edges:

**identifier_review.csv was appearing after a plain `analyze`.**
`analyze` is just looking -- it shouldn't leave a file behind. Moved
the logging call (`Engine._log_metadata_review()`, previously inline
in `analyze()`) so it only fires from `repair`/`auto_fix`, the same
role a class-mapping file plays for `map-css`/`repair
--class-mapping`: a file that shows up specifically because a repair
was requested and something couldn't be resolved automatically.
Verified: `analyze` on a book with a genuine date mismatch shows the
MISMATCH inline but writes no CSV; `repair --dry-run` on the same book
does. `analyze` remains the way to just look without leaving anything
on disk.

**The routine `[Book Metadata]` display was showing the full
explanation for every resolved field, every single run.** Useful the
first time you see a book carries `eng` vs `en`, much less so on the
thousandth already-clean book. `engine.py`'s `_field_line()` now only
appends a resolved (non-mismatched) field's note with `--details` --
the same convention this project already uses everywhere else for
"more than the headline number." A genuine mismatch's note (e.g. the
subtitle-relationship one) still always shows, since that one's still
asking for a decision, not just explaining why there's nothing to do.
Default output is now just `Language: eng`, `Author: William W.
Johnstone`, etc. -- the interesting cases only spend words when
they've earned it.

Also worth capturing here, from that same feedback, for later (not
started -- all bigger pieces of their own):

- **What those extra UUIDs in "Other identifiers found" actually are,
  and whether they can be cross-checked against each other.** A
  Calibre-managed book routinely carries several: its own EPUB-native
  UUID (the `unique-identifier` a reader/reading-system uses),
  Calibre's own row id (`opf:scheme="calibre"`), and often a genuine
  `urn:uuid:...` the original publisher/converter baked in -- three
  different, legitimate things, not duplicates of each other, so
  there's no "pick the right one" call to make. Where this *could* get
  more useful: if an ASIN or a Google Books id is also present,
  fetching that source's own record and comparing title/author against
  it would be a real, external cross-check -- worth scoping once
  there's an approach for outside lookups at all (see the date-lookup
  idea right below, same underlying need).
- **Looking up a canonical publish date from an ASIN (or ISBN) to
  resolve a genuine date MISMATCH with real evidence**, instead of
  just flagging two dates and letting a person guess which is right.
  Needs a real external data source and a plan for handling that
  source being wrong, unavailable, or rate-limited -- not something to
  wire in casually.
- **A description standardizer** -- missing descriptions, and
  descriptions that are far too long, both came up. Likely its own
  small module once there's a sense of what "too long" and "how to
  shorten without just truncating mid-sentence" should actually mean.
- **Whether other fields (description, cover art, author) could be
  auto-verified/overwritten once an identifier is confidently
  matched**, the same way core fields already are for a
  Calibre-managed book. Cover art and description both go well beyond
  "does this match a second source" into "is this actually the right
  content," which is a much bigger trust question than anything built
  so far -- explicitly flagged as needing a visual review step (the
  planned GUI) before going further, not a `repair` module.

## Open questions / not yet decided

- Syncing metadata.db itself (via `calibredb`, not raw sqlite writes)
  once there's a real Calibre library with `calibredb` installed to
  test against -- the writer currently only ever touches the EPUB and
  the metadata.opf sidecar.
- Whether this project lives in its own repo or alongside existing
  automation scripts (raindrop_manager.py, check_missing_posters.py, etc.).
- Whether it runs as a manual one-off pass or eventually gets folded into
  scheduled automation.
