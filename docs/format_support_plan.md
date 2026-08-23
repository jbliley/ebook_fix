# Other Format Support -- Planning Doc

**Status:** Not started. This is a planning doc only -- no code yet.
**Started:** carved out of analysis_roadmap.md's "Noted for the master
plan, not scoped yet" section, at Jacob's request to start actually
planning the MOBI piece while it's on his mind.

## The problem

Every module in this project currently assumes an EPUB's shape: a zip
archive containing an OPF package document, XHTML content files, and
optionally an NCX and/or nav document. `parser.py` is built entirely
around that shape -- manifest, spine, `Chapter.document` as a live
lxml tree, and so on. MOBI (and its newer sibling AZW3/KF8) is a
different format at every level that matters here: not a zip archive,
not XHTML-based in the older MOBI7 case, and not something any of the
existing repair/analysis modules could be pointed at without a real
translation layer in between.

Goal, phrased the same way the XHTML Recoder's problem statement is:
read a MOBI file's structure well enough to report on it the same way
`analyze` already does for EPUB, without pretending it's an EPUB
underneath.

## Why this gets its own planning doc

Same reasoning as `xhtml_recoder_plan.md`: this is a new parser/format
layer, not an extension of the existing one, and it's a bigger, riskier
effort than anything on the regular analysis_roadmap.md candidate
list. Scoped separately so it doesn't get lost as a two-line note
forever, and so picking it back up later doesn't depend on
conversation memory.

## Scope decision already made: analysis first, no repair, no writing

Jacob asked specifically to start with **analysis only** of MOBI
files. Deliberately not scoping repair or writing MOBI files at all
yet -- that's a much larger commitment (MOBI's binary layout is far
less forgiving to hand-edit than EPUB's zip-of-XHTML, and getting a
round-trip write path right is its own project). Analysis-only means:
open a MOBI file, report on what's inside it, change nothing, write
nothing back.

## What a MOBI file actually is (background, not yet verified against
a real file)

Worth writing down now so the next session doesn't have to
re-research it from scratch, but everything below needs confirming
against a real sample once one exists in `examples/`:

- A MOBI file is a PalmDB container (the old Palm OS document
  format), not a zip archive. Reading one means binary/struct parsing
  of a header + record list, not `zipfile`.
- The actual content sits in one or more PalmDOC/MOBI-compressed
  records, plus an EXTH metadata block (title, author, and other
  fields, roughly analogous to what OPF `<metadata>` holds for EPUB)
  embedded in the MOBI header.
- Older MOBI7 content is HTML-like markup with proprietary extensions,
  not real XHTML -- `lxml`'s XHTML-oriented parsing may not apply
  directly.
- Newer AZW3/KF8 files are often a hybrid: a MOBI7 part for backward
  compatibility plus a separate KF8 part that's much closer to real
  EPUB3/XHTML internally. A "MOBI" file handed to this tool could be
  either generation, or both bundled together -- needs detecting,
  not assumed.
- None of this is confirmed by hands-on testing yet. First real step
  of Phase 0 below is getting an actual sample file and checking every
  claim above against it rather than trusting general knowledge alone.

## Tools & resources -- open question

This project deliberately has a minimal dependency list (`lxml`,
`rich`, `tomllib` -- see the project's own conventions). Binary
PalmDB/MOBI parsing is a different kind of problem than anything the
existing dependencies solve. Open question for whenever this is
picked up: hand-roll the binary parsing (more control, more work, zero
new dependencies) versus taking on a MOBI-parsing library (faster to
a working state, but breaks the project's current zero-extra-deps
posture, and any such library would need vetting for whether it's
still maintained). Not decided -- flagging it here so it doesn't get
decided by default just because reaching for a library felt easier in
the moment.

## Rough phased sketch (very unscoped -- expect this to change once
Phase 0 gets a real file to look at)

- **Phase 0 -- Get a real sample and confirm the format basics.**
  Add at least one real MOBI file (and ideally one AZW3/KF8 file
  separately, since they may need different handling) to `examples/`.
  Confirm the background section above against it: is it a pure
  PalmDB container, what does the EXTH metadata block actually
  contain for a real book, is the content MOBI7-only or a KF8 hybrid.
  This phase is mostly research, same spirit as the XHTML Recoder's
  own Phase 0, and should end with this doc's background section
  either confirmed or corrected.
- **Phase 1 -- Minimal container-level parsing.** Open the file,
  identify which generation it is (MOBI7 / KF8 hybrid / pure KF8),
  read the EXTH metadata block into something comparable to what
  `[Book Metadata]` already shows for EPUB (title, author, language,
  identifier, and so on).
- **Phase 2 -- Content-level analysis.** Once the container is
  readable, get at the actual chapter/text content and see how much
  of the existing analysis logic (chapter detection, typography
  counts, whitespace, etc.) can realistically apply to it as-is versus
  needing its own format-specific version. Likely the point where this
  phase list needs to be broken down further, once it's clear how
  different MOBI's actual content shape turns out to be from XHTML.
- **Phase 3 -- Wire into the CLI.** `analyze` command support for a
  `.mobi`/`.azw3` input, producing the same kind of report EPUB
  analysis already does, clearly labeled as MOBI-specific wherever the
  two formats can't produce a directly comparable answer (e.g. no
  meaningful "EPUB Version" line for a format that isn't EPUB at all).

## Non-goals, for now

- No MOBI repair. No MOBI writing/saving. No format conversion
  (MOBI-to-EPUB or the reverse) -- that's an entirely different, much
  larger feature that hasn't been discussed.
- No AZW3/KF8-specific analysis beyond "detect that it's this
  generation" until Phase 0/1 are actually done and it's clear how
  much of Phase 2 applies to it directly versus needing its own path.

## Open questions to resolve when this is picked up
- Hand-rolled binary parsing versus a MOBI-parsing dependency (see
  "Tools & resources" above).
- Whether MOBI analysis becomes its own `ebook_fix.mobi` package
  (mirroring the existing `ebook_fix/` module-per-concern layout) or
  a separate top-level analysis path entirely, given how little of
  the existing EPUB-shaped model (`Book`, `Chapter`, manifest/spine)
  is likely to apply unchanged.
- Whether `cli.py`'s `analyze` command auto-detects the format from
  the file itself (magic bytes / extension) or needs an explicit flag.

## Continuity note
Same as the recoder plan: this file is the source of truth for where
this stands, more reliable than conversation memory across sessions.
Update it at the end of each session that touches this feature.
