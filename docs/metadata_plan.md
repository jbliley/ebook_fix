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

- `identifiers.py` — mapping-driven matching/classification. Built and
  wired into the analysis pass (see Session update below). Read-only
  so far; writing normalized values back out still needs a backend.
- `core_fields.py` — title/author/series read (write not built yet).
  Built and wired into the analysis pass. Simpler than identifiers
  since these are direct 1:1 OPF fields, not a scheme-matching
  problem. Main open question is formatting conventions (e.g. author
  "Last, First" vs "First Last") — not yet designed.
- `backends/` — shared file-access layer (Calibre vs lone-EPUB) used by
  both logic modules so neither duplicates file-handling code. Not
  built yet — needed once either module needs to *write*, not just read.
- `review.py` — logging. Not built yet.

Suggested build order (updated):

1. ~~Mapping file + matching/normalization core~~ — done, wired into
   the analysis pass.
2. Calibre-structure detector.
3. Two backends (Calibre read/write via calibredb, EPUB-only via lxml).
4. Review log writer.
5. Dry-run mode as a hard default before anything writes to real files.

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

## Open questions / not yet decided

- Exact scope and design of `core_fields.py` (which fields beyond
  title/author, any formatting-standardization rules).
- Whether author/title normalization needs its own mapping/config file
  similar in spirit to `identifier_schemes.json`.
- Whether this project lives in its own repo or alongside existing
  automation scripts (raindrop_manager.py, check_missing_posters.py, etc.).
- Whether it runs as a manual one-off pass or eventually gets folded into
  scheduled automation.
