# Other Format Support -- Planning Doc

**Status:** Phase 0 and Phase 1 done for MOBI7 and pure KF8/AZW3
(hybrid KF8 still unconfirmed -- no real hybrid sample). Phase 3 (CLI
wiring) also done alongside Phase 1. FB2 support added separately --
see "FB2 (FictionBook) support" below; it's a much simpler format
than MOBI so it didn't need its own multi-phase plan.
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

## What a MOBI file actually is (confirmed against a real sample)

Confirmed against `examples/MOBI-Example.mobi` -- "Law of the Mountain
Man" by William W. Johnstone, a real Calibre-exported MOBI7 file, 132
PalmDB records, 29 EXTH metadata tags:

- A MOBI file is a PalmDB container (the old Palm OS document
  format), not a zip archive. Reading one means binary/struct parsing
  of a header + record list, not `zipfile`. Confirmed -- header and
  record-offset layout matched the documented spec exactly.
- The actual content sits in one or more PalmDOC/MOBI-compressed
  records, plus an EXTH metadata block (title, author, and other
  fields, roughly analogous to what OPF `<metadata>` holds for EPUB)
  immediately following the MOBI header inside record 0. Confirmed --
  EXTH parsed cleanly into 29 real tags: title, author, publisher,
  ISBN, ASIN, description (as HTML), nine separate `subject` (105)
  tags, a cover-image reference, and several Kindle/Calibre-internal
  numeric tags. Subject is genuinely multi-valued (repeats the tag
  once per subject) rather than one comma-separated field.
- The EXTH tag table in the general documentation the old
  `mobi_analyzer.py` draft used had at least one real error: it
  labeled tag 504 as "language," but the real sample's language tag
  is 524, not 504. `mobi_header.py`'s `EXTH_TYPES` table uses 524 and
  has been corrected accordingly.
- The cover reference is two EXTH tags, not one: tag 201 gives an
  offset that's added to the MOBI header's `first_image_record` field
  to get the actual PalmDB record index, not an absolute record
  number by itself. Confirmed by reading that record and finding a
  real JPEG (`\xff\xd8\xff` magic bytes) at the resulting index.
- PalmDOC compression (type 2, confirmed on this sample) doesn't mean
  every byte is compressed -- LZ77-style compression has nothing to
  back-reference yet at the very start of a record, so a compressed
  record's opening bytes can still look like plain readable text (this
  sample's first content record visibly starts `<html><head><guide>`
  despite being marked PalmDOC-compressed). Worth remembering later,
  during Phase 2 content-level work, so this doesn't get mistaken for
  a sign the file is actually uncompressed.
- Older MOBI7 content is HTML-like markup with proprietary extensions,
  not real XHTML -- confirmed by the visible `<html><head><guide>`
  opening tag above being plain HTML, not XML-declared. `lxml`'s
  XHTML-oriented parsing likely won't apply directly once Phase 2
  gets to actual content, as suspected.
- **Confirmed for pure KF8/AZW3, still not confirmed for the hybrid
  case:** a second real sample, `examples/AZW3-Example.azw3` ("Arliss
  Cutter 7: Dead Line" by Marc Cameron, `file_version` 8, no EXTH 121
  tag), confirmed the pure-KF8 detection path -- metadata extraction
  (title, author, publisher, ISBN, ASIN, cover) worked identically to
  the MOBI7 sample with no changes needed. Newer AZW3/KF8 files are
  documented as *sometimes* being a hybrid instead -- a MOBI7 part for
  backward compatibility plus a separate KF8 part closer to real
  EPUB3/XHTML internally, detectable via EXTH tag 121 (a "KF8
  boundary" record index). That hybrid path is still unconfirmed;
  this sample simply didn't have one, so `_detect_generation()` in
  `analyzer.py` still labels its own output "unconfirmed" only when
  it detects the hybrid case specifically, not for plain KF8 anymore.

## Tools & resources -- decided

Hand-rolled binary parsing, using Python's built-in `struct` module
only. Zero new dependencies -- matches this project's existing
posture (`lxml`, `rich`, `tomllib`, and nothing else). Confirmed
workable: the PalmDB/MOBI/EXTH parsing in `ebook_fix/mobi/` is all
`struct.unpack_from` calls against known byte offsets, same approach
Jacob's own draft `mobi_analyzer.py` script already used.

## Rough phased sketch (very unscoped -- expect this to change once
Phase 0 gets a real file to look at)

- **Phase 0 -- Get a real sample and confirm the format basics. DONE**
  for MOBI7 (`examples/MOBI-Example.mobi`) and pure KF8/AZW3
  (`examples/AZW3-Example.azw3`). Not done for the KF8 hybrid case --
  no hybrid sample exists yet; that specific sub-case is still
  unconfirmed (see background section above).
- **Phase 1 -- Minimal container-level parsing. DONE.**
  `ebook_fix/mobi/palmdb.py` reads the PalmDB container, `mobi_header.py`
  reads the MOBI header, generation detection, and EXTH metadata into
  a `MobiMetadata` object comparable to what `[Book Metadata]` already
  shows for EPUB (title, author, language, publisher, date, ISBN,
  ASIN, subjects, description). Cover-image detection (which PalmDB
  record holds it, and its actual image format via magic bytes,
  reusing `ebook_fix.cover.sniff_image_media_type`) came along with
  this phase too, since the EXTH data needed to find it was already
  being read.
- **Phase 2 -- Content-level analysis. Not started.** Once the
  container is readable, get at the actual chapter/text content and
  see how much of the existing analysis logic (chapter detection,
  typography counts, whitespace, etc.) can realistically apply to it
  as-is versus needing its own format-specific version. This means
  PalmDOC/LZ77 decompression of the text records first, which hasn't
  been written yet -- Phase 1 confirmed the compression type but
  didn't need to actually decompress anything to read EXTH metadata.
  Likely the point where this phase list needs to be broken down
  further, once it's clear how different MOBI's actual content shape
  turns out to be from XHTML.
- **Phase 3 -- Wire into the CLI. DONE**, done alongside Phase 1
  rather than held back separately. `ebook-fix analyze` auto-detects
  `.mobi`/`.azw`/`.azw3`/`.prc` by file extension and routes to
  `ebook_fix.mobi.analyzer` instead of the EPUB `Engine` pipeline,
  printing a `[File]` / `[Book Metadata]` / `[File Contents]`-style
  report in the same plain-text format EPUB analysis uses. Deliberately bypasses
  `Engine`/`Config` entirely for this path -- there's no repair
  config to load for an analysis-only format, and MOBI's binary
  layout has nothing in common with the EPUB-shaped pipeline it would
  otherwise be flowing through.

## Non-goals, for now

- No MOBI repair. No MOBI writing/saving. No format conversion
  (MOBI-to-EPUB or the reverse) -- that's an entirely different, much
  larger feature that hasn't been discussed.
- No AZW3/KF8-specific analysis beyond "detect that it's this
  generation" for the still-unconfirmed hybrid case (see background
  section) until a real hybrid sample exists.

## Open questions -- resolved this session
- Hand-rolled binary parsing versus a MOBI-parsing dependency: hand-rolled,
  using only `struct` (see "Tools & resources" above).
- Own package versus a separate top-level path: `ebook_fix.mobi`, a
  new subpackage mirroring the existing `ebook_fix/` module-per-concern
  layout (`palmdb.py`, `mobi_header.py`, `analyzer.py`).
- Auto-detect versus explicit flag: auto-detect, by file extension
  only (`.mobi`, `.azw`, `.azw3`, `.prc`) checked in `cli.py`. Not
  magic-byte sniffing at the `cli.py` level -- `analyzer.py` itself
  still validates the actual PalmDB signature internally and reports
  a clear error if a `.mobi`-named file isn't really one.

## Still open
- No real KF8-hybrid sample exists yet (a file with both a MOBI7 part
  and a KF8 part bundled together), so that specific sub-case's
  detection and handling remain unconfirmed (see background section
  above). Plain MOBI7 and plain KF8/AZW3 are both confirmed now.
- Phase 2 (actual PalmDOC/LZ77 decompression and content-level
  analysis) hasn't been scoped in any detail yet -- the phased sketch
  above is still a rough placeholder for it.
- FB2: only the FictionBook 2.0 namespace has been tested. A
  2.1-namespaced file, or a zipped `.fb2.zip`, would need a real
  sample before trusting either case -- see "FB2 (FictionBook)
  support" below.

## FB2 (FictionBook) support

Added in the same session the AZW3 sample confirmed the MOBI KF8
path, after Jacob added `examples/FB2-Example.fb2` (Harper Lee's "To
Kill a Mockingbird") to the repo. Much simpler than MOBI, so this
didn't get its own phased plan -- it's one module
(`ebook_fix/fb2.py`), confirmed and done in one pass rather than
staged.

**What FB2 actually is (confirmed against the real sample):** a
single well-formed XML document, not a zip archive and not a binary
format -- `lxml` parses it directly with no container layer in front
of it, the same library this project already depends on for EPUB's
OPF. Metadata lives under a namespaced `<description><title-info>`
block (title, author, language, genre, annotation), roughly
analogous to OPF `<metadata>`. Chapters are nested `<section>`
elements under `<body>` (this sample: 2 top-level parts, 33 sections
total once nested chapters are counted). A cover image, when present,
is referenced from `<title-info><coverpage>` by an XLink `href` and
stored inline as base64 inside a matching `<binary>` element,
confirmed by decoding it and finding a real JPEG at the resulting
bytes.

**What's confirmed:** the FictionBook 2.0 namespace
(`http://www.gribuser.ru/xml/fictionbook/2.0`) only, which is what
this one sample declares. FB2 2.1 is documented as using a different
namespace URI -- `analyze_fb2()` still attempts the parse and reports
the namespace it actually found either way, but flags the result
"untested" whenever it isn't the confirmed 2.0 URI, rather than
refusing outright or assuming 2.1 behaves identically.

**Not covered:** `.fb2.zip` (a zipped FB2, which does show up in the
wild) isn't handled -- `analyze_fb2()` expects a plain XML file on
disk. Would need its own real sample and a small amount of
`zipfile` handling in front of the existing XML parsing if this comes
up later. No repair or writing, same analysis-only scope as MOBI.


Same as the recoder plan: this file is the source of truth for where
this stands, more reliable than conversation memory across sessions.
Update it at the end of each session that touches this feature.
