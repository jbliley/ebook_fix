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

**Input:** EPUB, KEPUB, AZW3, CBZ, CBR, MOBI, AZW, PRC, FB2, DOCX,
RTF, TXT
**Output:** EPUB3 (default), AZW3, CBZ
**Explicitly unsupported:** DJVU, raw images
**Narrow exception:** PDF -> CBZ (image-only, no OCR/reflow -- see below)

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
  needs a specific external program on their system already."

  **"CBR is upgradeable to CBZ" is exactly the right way to think
  about this, not a mistaken one.** CBR and CBZ hold identical
  content (the same images, in the same order) -- the only
  difference is which compression container they're wrapped in. RAR
  is proprietary and needs that external tool; ZIP is what Python
  already handles natively and what every reader supports. So a
  CBR-to-CBZ "conversion" is really just decompress-and-rezip, not a
  transformation that could lose or alter anything -- a genuine,
  lossless upgrade to a more universally-supported container, not a
  format change with trade-offs.

  **Effort estimate, since Jacob asked directly:** the actual code
  here is small -- detect a RAR archive (magic bytes, not just the
  `.cbr` extension, same principle already used for MOBI detection),
  extract via `rarfile` or a subprocess call to whatever RAR-capable
  tool is available, sort the resulting images into page order, and
  write them into a normal zip as a `.cbz`. That part is closer to an
  hour or two of real implementation than a multi-day project --
  genuinely one of the smaller items on this whole list, code-wise.

  The real uncertainty isn't the code, it's the external tool: this
  needs *something* on Jacob's Windows machine that can actually
  extract RAR archives. The standalone freeware `UnRAR.exe` (from
  RARLab, separate from full WinRAR) or 7-Zip's command-line tool
  (`7z.exe`, free and open-source, and a very commonly already-
  installed Windows utility) both work -- either is a small, one-time
  install if not already present, not an ongoing burden. Given Jacob
  said he doesn't know how often he'd actually use this, a reasonable
  approach: hold off building it until an actual `.cbr` file shows up
  that needs converting, at which point it's a short, low-risk task
  to pick up -- no real cost to waiting, and no wasted effort either
  way, unlike something like AZW3 output where the underlying design
  decision (hand-roll vs. Calibre) matters more the earlier it's made.

**Dropped / deprioritized -- confirmed with Jacob:**
- **LIT** (Microsoft Reader) -- dropped entirely, not just
  deprioritized. Microsoft discontinued Reader and the .lit format
  outright in 2011, with no successor and no ongoing use. Beyond just
  being obsolete: in practice, the large majority of real-world
  `.lit` files still in existence are DRM-protected commercial
  ebooks, not personal/DRM-free files -- the handful of historical
  open-source tools for this format existed specifically in the
  DRM-removal space. That's not a place I'm able to help build
  tooling toward, regardless of any individual file's actual
  protection status.
- **LRF** (Sony BBeB) -- deprioritized indefinitely, not dropped
  outright, though nothing currently points to this changing. Also
  long-obsolete; Sony's own e-readers moved to standard EPUB around
  2010. Not DRM-associated the way LIT is, so no ethical concern
  here, but there's essentially no remaining user population and no
  actively-maintained modern tooling outside Calibre's own decades-
  old internal reader for it. Revisit only if a real, current reason
  to support it ever comes up.
- **PDB** -- removed from the input list. Confirmed with Jacob this
  was an accidental addition, not something he actually needs
  supported -- MOBI, AZW, and PRC above are already specific PDB-
  format files (see `palmdb.py`'s own module docstring: "the PalmDB
  container that wraps every MOBI/AZW/AZW3/PRC file"), so nothing
  real is lost by dropping the separate line item.

## Output formats

- **EPUB3** -- already the default, already extensive. Nothing new
  needed here beyond whatever any given input format's reader work
  requires to produce a good `Book` in the first place.
- **AZW3** -- **no rush, per Jacob -- he reads EPUB himself, this is
  purely for other users down the line.** Still worth writing down
  now while the tradeoff is fresh, so it's not re-derived from
  scratch whenever it does get picked up. This is a genuinely
  different kind of task from everything MOBI-related done so far.
  All the `ebook_fix.mobi` work to date is a *reader* (parse an
  existing file); this needs a *writer* (construct a valid PalmDB +
  MOBI header + KF8 content structure from scratch). That's a real,
  mostly-unstarted project on its own, not a natural extension of the
  analysis code. Two genuinely different paths worth deciding between
  whenever this gets picked up:
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
- **CBZ** -- confirmed with Jacob: comic-shaped in, comic-shaped
  out (CBR/CBZ normalized to CBZ), not a claim that an ordinary prose
  EPUB could reasonably become a CBZ. Turning real reflowable text
  into page images would mean building an actual page layout/
  rendering step, a fundamentally different and much bigger problem
  nobody's discussed and nothing in this project does today.

## Unsupported formats -- agreed

DJVU and raw images are fundamentally page-image/fixed-layout
problems. Keeping these out of scope entirely is consistent with
everything else here treating reflowable, markup-based content as the
target. PDF gets a narrow, image-only exception -- see below.

## PDF -> CBZ, image-only, no OCR (2026-09-11)

Raised by Jacob as a much smaller ask than full PDF support: skip
reflowable conversion entirely (the actual reason PDF was excluded
above -- OCR and page-layout reconstruction is a fundamentally
different, much bigger problem) and instead treat a PDF exactly like
the CBZ output case already agreed above -- page-images-in,
page-images-out, just packaged as a CBZ archive instead of read back
into an EPUB `Book`. This sidesteps the original objection rather than
arguing around it: nothing here tries to make PDF content reflow.

Confirmed working already: Jacob supplied a working script
(`pdf_cbz.py`) using PyMuPDF (`fitz`) -- renders every page to a JPEG
via `page.get_pixmap()` at a 2x render matrix (roughly 150-200 DPI
equivalent), writes each as `page_NNNN.jpg` into a zip, renames to
`.cbz`. This approach works uniformly whether the PDF is a scanned
image stack or a vector/text-based document, since it rasterizes the
rendered page rather than trying to extract or distinguish embedded
images -- confirmed with Jacob rather than restricting to
already-image-based PDFs only.

**New dependency: PyMuPDF (`fitz`).** A real departure from this
project's hand-roll-where-feasible philosophy, called out plainly
rather than glossed over -- but justified here, unlike everywhere else
that philosophy applies: rendering arbitrary PDF pages to images isn't
a hand-rollable problem the way binary parsing elsewhere in this
project is (see `palmdb.py`, `mobi_analyzer.py`, etc.), and Jacob has
already used this specific library successfully for this specific
task, unlike the still-open, unconfirmed `unrar`/`bsdtar` question for
CBR below.

Ebook-fix's own `ebook_fix.mobi_analyzer`-adjacent reader work still
doesn't touch PDF content at all -- there's no PDF `Book` reader
planned or needed here, since the point is explicitly to avoid
building one. This is a standalone converter (PDF file in, CBZ file
out), not a new input format for the analysis/repair pipeline the way
MOBI/FB2/etc. are.

**DJVU: dropped**, not deprioritized. Jacob raised it only as an
afterthought alongside the already-working PDF script, and confirmed
it's not worth the real complexity it'd add -- there's no lightweight
way to hand-roll a DJVU decoder (its own compression and IW44 image
encoding), so supporting it would mean a second new dependency (an
external `djvulibre` binary or Python bindings around it) for a format
Jacob doesn't actually have files for. Revisit only if a real DJVU
file shows up needing conversion, same posture as CBR below.

Not yet scoped, worth deciding before this gets built:
- Where the actual conversion code lives -- a new top-level module
  (e.g. `ebook_fix/pdf_cbz.py`) rather than anywhere in the `Book`
  reader/writer/repair pipeline, since this never touches a `Book`
  object at all.
- CLI shape: a new subcommand (`ebook-fix pdf-to-cbz input.pdf
  -o output.cbz`)? Doesn't fit `repair`/`auto-fix`/`analyze`'s
  existing shape (open-a-book-then-fix-it), since there's no `Book`
  to analyze.
- Render setting (the 2x matrix / JPEG quality 85 in Jacob's script)
  as a fixed default vs. a config option -- likely fine as a fixed
  default matching what Jacob already confirmed looks right, unless a
  real case for tuning it per-book comes up.
- requirements.txt: PyMuPDF's own license (AGPL, with a commercial
  option) is worth a conscious note in the repo somewhere once this
  actually gets added, given how different that is from lxml/rich/
  ebooklib/Flask's licensing.

## Suggested phasing (not decided, just a starting point)

1. **Tier 1 -- fast follow, low risk:** finish FB2 content extraction
   (easiest real format on the list), confirm KEPUB reads via the
   existing EPUB parser against a real sample, CBZ round-trip.
2. **Tier 2 -- real but scoped work:** MOBI-family Phase 2 (PalmDOC
   decompression + content normalization) -- this unlocks MOBI, AZW,
   AZW3, and PRC all at once, since they share the same reader.
   DOCX and RTF input, once the dependency questions below are
   settled.
3. **Pick up on demand, not tiered:** CBR input -- small, self-
   contained (~an hour or two of real code) whenever an actual `.cbr`
   file shows up that needs converting; no benefit to front-loading
   it before then. PDF -> CBZ (see above) fits the same "pick up on
   demand" posture -- small, self-contained, and already has a
   confirmed working approach, just needs wiring into a real command.
4. **No rush -- Jacob reads EPUB himself, this is for other users
   eventually:** AZW3 output. Worth deciding hand-roll vs. Calibre
   shell-out whenever it does get picked up, but nothing time-
   sensitive about when.
5. **Dropped/deprioritized:** LIT (dropped), LRF (deprioritized
   indefinitely). PDB removed from the input list entirely
   (accidental addition, not a real need).

## Open questions for Jacob -- all resolved this session
- ~~AZW3 output: hand-roll vs. Calibre shell-out?~~ Still an open
  design choice whenever this gets picked up, but no longer time-
  sensitive -- see "no rush" note above.
- ~~PDB?~~ Accidental addition, removed from the list.
- ~~CBZ output: comic-shaped only, not general prose-to-CBZ?~~
  Confirmed.
- ~~CBR: sign-off on an external `unrar`/`bsdtar`/7-Zip dependency?~~
  Effort estimate given above; Jacob's holding off on a firm
  commitment until an actual `.cbr` file needs converting, given low
  certainty about how often that'll come up.
- ~~LIT/LRF: confirm dropping/deprioritizing?~~ Confirmed.

## Continuity note
This file is the source of truth for the format-conversion effort,
more reliable than relying on conversation memory across sessions.
Nothing here is scoped or built yet -- update as the open questions
above get resolved and real work gets picked up.
