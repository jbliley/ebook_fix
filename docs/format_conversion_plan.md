# Format Conversion -- Planning Doc

**Status:** Planning only. Nothing scoped or built yet -- this doc
captures Jacob's initial proposal plus a feasibility review, with
open questions to resolve before anything gets picked up.
**Started:** Jacob proposed a full input/output format list and
asked whether it's feasible, right after the MOBI/AZW3/FB2 analysis
work and the MM5 truncation-detection work wrapped up.

## The idea

Everything in `ebook_fix` so far (chapter detection, whitespace,
apostrophes, typography, the whole analysis/repair pipeline) works on
one internal shape: an EPUB-like `Book` object. Jacob's proposal is
to lean into that on purpose rather than fight it: read every
supported input format into that same internal shape, then write
back out to a small, deliberately limited set of output formats,
rather than building a direct converter for every input/output pair.

Proposed lists, as given:

**Input:** EPUB, KEPUB, AZW3, CBZ, CBR, MOBI, AZW, PRC, PDB, LIT,
LRF, FB2, DOCX, RTF, TXT
**Output:** EPUB3 (default), AZW3, CBZ
**Explicitly unsupported:** DJVU, PDF, images

## The core architecture call: convert-to-EPUB-first -- agreed, this is right

Reading N formats and writing M formats needs N readers + M writers.
Direct pairwise conversion needs N x M converters, and every one of
them has to duplicate its own chapter detection, its own metadata
handling, its own everything. This project already has a single
internal `Book` shape and a single analysis pipeline built around it
-- leaning on that instead of building direct converters is the same
design Calibre itself uses internally (its own OEB intermediate
representation), for the same reason.

There's a real bonus here beyond "less code," worth calling out
explicitly: once a format's reader produces a real `Book` with real
`chapter.document` trees, EVERY existing analysis module (whitespace,
apostrophes, typography, chapter detection, the new dangling-ending
check, all of it) runs against that format for free. This is
actually already true in miniature: `format_support_plan.md`'s
MOBI/FB2 work stopped at metadata-only analysis (Phase 1) rather than
finishing content extraction (Phase 2) specifically because content
extraction was the harder, unscoped part -- Phase 2 finishing is
functionally the same work as "MOBI/FB2 as a Book-producing reader"
this new proposal needs anyway. That work isn't wasted by this
proposal, it's a direct prerequisite for it.

## Input formats, by how hard this actually is

**Already there or trivial:**
- **EPUB** -- native format, nothing to do.
- **FB2** -- analysis (metadata only) already done, see
  `format_support_plan.md`. Content extraction to a real `Book` is
  the easiest of any format on this list to finish: it's already
  well-formed namespaced XML `lxml` reads directly, no binary
  decompression step at all, unlike every PalmDB-family format below.
- **KEPUB** -- Kobo's own EPUB variant, not a separate format:
  standard EPUB3 plus Kobo-specific `<span class="koboSpan">`
  wrapping around sentences/words (for their reading-progress and
  highlighting features) and usually a `.kepub.epub` double
  extension. The existing EPUB parser can very likely open one
  as-is; the Kobo spans just need to be recognized and either kept or
  stripped rather than mistaken for real content markup. Confirming
  this needs a real sample file, same as every other format claim in
  this project, but the shape of the work is small.
- **TXT** -- trivially readable, but "trivial to read" and
  "trivial to convert well" are different claims here. There's no
  markup of any kind to detect chapters from -- worse off than even
  Case 3 (no chapter markers, no TOC) in `chapters.py`'s three-case
  framework, since Case 3 still has real HTML structure to reason
  about. Chapter/paragraph detection from blank-line-separated raw
  text is a real, separate problem, and quality here can only be
  judged against real sample files, the same as everything else.

**Real but tractable work, mostly already scoped:**
- **MOBI / AZW3 / AZW / PRC** -- container and metadata reading
  (Phase 1) is done, see `format_support_plan.md`. Getting a real
  `Book` out of one needs Phase 2: PalmDOC/LZ77 decompression of the
  text records, then normalizing whatever HTML-with-proprietary-
  extensions MOBI7 content turns out to look like (confirmed
  markup-like but NOT confirmed to be `lxml`-parseable as-is -- see
  that doc's background section) into something `lxml` can build a
  chapter tree from. This is the single biggest chunk of real,
  unstarted work on this whole list.
- **CBZ** -- already just a zip of images, which this project
  already knows how to read (`zipfile`, no new tooling). Converting
  TO an EPUB means wrapping each image in a minimal one-image-per-
  page XHTML file, a well-understood pattern. Converting FROM an
  EPUB back to CBZ only makes sense when the original content was
  already comic/image-based -- see the CBZ output note below.

**Needs a real dependency decision (see `docs/format_support_plan.md`'s
own "hand-rolled vs. dependency" precedent):**
- **DOCX** -- mature, well-documented OOXML format, but its schema
  is genuinely complex (styles, sections, embedded media, tracked
  changes) in a way plain XML formats like FB2 aren't. `python-docx`
  is the standard, actively-maintained library for this -- hand-
  rolling DOCX parsing the way this project hand-rolls MOBI's binary
  layout would be a much bigger, much less justified undertaking,
  since DOCX has nothing like MOBI's "PalmDB is a genuinely small
  fixed-size struct" simplicity going for it. Recommend a real
  dependency here rather than hand-rolling.
- **RTF** -- an old but structurally much simpler format (control-
  word based, not XML/OOXML). Lighter-weight libraries exist
  (`striprtf` for basic text extraction; others vary in how actively
  maintained they are). Worth a real look at current library health
  before committing to one, same diligence this project already
  applies to any dependency decision.

**A new *kind* of dependency, not just a new package -- flagging
before assuming this is like the others:**
- **CBR** -- RAR-compressed comic archives. Python's standard
  library has no RAR support at all (unlike zip), and reading RAR
  data requires either the `rarfile` package or similar -- both of
  which don't implement RAR decompression themselves (RAR's
  compression algorithm is proprietary), they shell out to an
  external `unrar` (or `bsdtar`) binary that has to already be
  installed on the machine running this. That's a materially
  different risk than every dependency this project has taken on so
  far: not "pip install this," but "the person running this tool
  needs a specific external program on their system already." Worth
  Jacob's explicit sign-off before this gets scoped, given the
  project's general minimal-dependency posture.

**Needs clarification before it can even be scoped:**
- **PDB** -- as given, this is ambiguous rather than just hard. PDB
  is the *generic* Palm OS database container -- MOBI, AZW, and PRC
  above are already specific PDB-format files (see `palmdb.py`'s own
  module docstring: "the PalmDB container that wraps every
  MOBI/AZW/AZW3/PRC file"). A `.pdb` file on its own could be: plain
  PalmDOC/AportisDoc text (an even simpler MOBI ancestor -- compressed
  text, no EXTH metadata block at all), or an entirely different
  format that happens to reuse the same outer container (eReader/Palm
  Reader's own format, historically DRM-heavy, is a different real
  possibility). Worth asking Jacob directly what he has in mind here
  before this goes on any roadmap, since "support .pdb files" isn't
  itself a determinate scope the way every other item on this list
  is.

**Recommend dropping from the realistic roadmap:**
- **LIT** (Microsoft Reader) -- Microsoft discontinued Reader and
  the .lit format outright in 2011, with no successor and no
  ongoing use. Beyond just being obsolete: in practice, the large
  majority of real-world `.lit` files still in existence are DRM-
  protected commercial ebooks, not personal/DRM-free files -- the
  handful of historical open-source tools for this format existed
  specifically in the DRM-removal space. That's not a place I'm able
  to help build tooling toward, regardless of any individual file's
  actual protection status, so this isn't just a "low priority"
  judgment call the way LRF below is -- recommend taking it off the
  list entirely.
- **LRF** (Sony BBeB) -- also long-obsolete; Sony's own e-readers
  moved to standard EPUB around 2010. Not DRM-associated the way LIT
  is, so no ethical concern here, but there's essentially no
  remaining user population and no actively-maintained modern
  tooling outside Calibre's own decades-old internal reader for it.
  Recommend deprioritizing indefinitely rather than actively dropping
  it -- revisit only if a real, current reason to support it ever
  comes up.

## Output formats

- **EPUB3** -- already the default, already extensive. Nothing new
  needed here beyond whatever any given input format's reader work
  requires to produce a good `Book` in the first place.
- **AZW3** -- this is a genuinely different kind of task from
  everything MOBI-related done so far. All the `ebook_fix.mobi` work
  to date is a *reader* (parse an existing file); this needs a
  *writer* (construct a valid PalmDB + MOBI header + KF8 content
  structure from scratch). That's a real, mostly-unstarted project on
  its own, not a natural extension of the analysis code. Two
  genuinely different paths worth deciding between before any of this
  is scoped:
  1. Hand-roll a writer, matching this project's existing zero-
     dependency, own-the-whole-format-from-spec approach.
  2. Shell out to Calibre's own `ebook-convert` command-line tool for
     the EPUB-to-AZW3 step specifically. Jacob already has Calibre
     installed for library management, and `calibredb` write-back is
     already planned (`metadata_plan.md`) -- leaning on Calibre's own
     mature, battle-tested converter for just this one output format
     could be a much smaller, much lower-risk lift than hand-rolling
     a binary format writer from nothing. Worth a real decision here
     rather than defaulting to "hand-roll it" purely out of momentum
     from how the reader side was built.
- **CBZ** -- worth confirming this means what it sounds like it
  means: a sensible output for content that was already comic/image-
  shaped (CBZ or CBR in, normalized CBZ out), not a claim that an
  ordinary prose EPUB could reasonably become a CBZ. Turning real
  reflowable text into page images would mean building an actual page
  layout/rendering step, a fundamentally different and much bigger
  problem nobody's discussed and nothing in this project does today.
  Assuming the comic-shaped-in/comic-shaped-out reading is the
  intended one unless told otherwise.

## Unsupported formats -- agreed

DJVU, PDF, and raw images are all fundamentally page-image/fixed-
layout problems (or, for PDF, a layout-based format this project's
own `mobi_analyzer.py` script already only did basic structural
sniffing on, never real conversion). Keeping these out of scope
entirely is consistent with everything else here treating reflowable,
markup-based content as the target.

## Suggested phasing (not decided, just a starting point)

1. **Tier 1 -- fast follow, low risk:** finish FB2 content extraction
   (easiest real format on the list), confirm KEPUB reads via the
   existing EPUB parser against a real sample, CBZ round-trip.
2. **Tier 2 -- real but scoped work:** MOBI-family Phase 2 (PalmDOC
   decompression + content normalization) -- this unlocks MOBI, AZW,
   AZW3, and PRC all at once, since they share the same reader.
   DOCX and RTF input, once the dependency questions below are
   settled.
3. **Tier 3 -- needs an architecture decision first:** AZW3 output
   (hand-roll vs. Calibre shell-out), CBR input (external `unrar`
   dependency sign-off).
4. **Deprioritized/dropped:** LIT (dropped), LRF (deprioritized
   indefinitely), PDB (blocked on clarifying what it actually refers
   to).

## Open questions for Jacob
- AZW3 output: hand-roll a writer, or shell out to Calibre's
  `ebook-convert`?
- PDB: plain PalmDOC/AportisDoc text, eReader/Palm Reader format, or
  something else specifically in mind?
- CBZ output: confirm this is meant for comic-shaped input only, not
  a general prose-to-CBZ path.
- CBR: sign-off on requiring an external `unrar`/`bsdtar` binary on
  the machine, a first for this project.
- LIT/LRF: confirm dropping LIT entirely and deprioritizing LRF
  indefinitely, per the reasoning above.

## Continuity note
This file is the source of truth for the format-conversion effort,
more reliable than relying on conversation memory across sessions.
Nothing here is scoped or built yet -- update as the open questions
above get resolved and real work gets picked up.
