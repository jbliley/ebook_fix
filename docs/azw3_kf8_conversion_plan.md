# AZW3/KF8 Conversion -- Planning Doc

**Status:** planning, phased. Phase 1 in progress. Not yet building end-to-end conversion.
**Started:** 2026-09-24, continuing straight from `docs/mobi_conversion_plan.md`'s "Next: AZW3/KF8" section.

## Why this is its own plan doc, not a MOBI7 addendum

The MOBI7 plan doc's original sketch assumed KF8 was "MOBI7 plus a few extra header fields and a different link scheme." Spending a day with the three real sample files (`AZW3-Example.azw3`, `AZW3-Newer.azw3`, `AZW3-Older.azw3`) showed that's wrong: KF8 keeps the same outer container, header, EXTH, and image records, but a book's actual text is not one continuous stream the way MOBI7's is. It's broken into small interleaved pieces ("skeletons" and "fragments") reassembled through a set of custom, sparsely-documented index tables. That's a genuinely different, harder problem than MOBI7's "decompress and find page breaks," closer to reverse-engineering a second file format than extending the first one -- so it gets its own phased plan rather than a short "AZW3/KF8" section tacked onto MOBI7's doc.

Jacob's call (2026-09-24): keep going, but in incremental, independently-checkable phases rather than one long push, specifically to make this more manageable.

## The three samples

- **`AZW3-Example.azw3`** ("Dead Line" by C.J. Box) -- 212 records, pure KF8 (no legacy MOBI7 half), PalmDOC-compressed text, 190 text records, 70 fragments / 127 skeleton pieces / a 2-entry guide-like index / a 68-entry chapter TOC index.
- **`AZW3-Newer.azw3`** ("The Ballad of Songbirds and Snakes") -- 340 records, same shape as Example, larger book, confirmed to have the identical 4-index-pair layout (see "Reference: what's confirmed so far" below).
- **`AZW3-Older.azw3`** (*Frankenstein*) -- 129 records, also `file_version == 8` (a real KF8 header), but its text is **HUFF/CDIC-compressed**, not PalmDOC -- the compression scheme flagged as "written from the spec, no real sample to verify against" in `mobi_conversion_plan.md`. Also has an EXTH 121 ("KF8 boundary") record present, which turned out to be a false trail -- see Phase 1.

None of the three are a genuine two-header hybrid (a real MOBI7 header *and* a separate KF8 header in the same file, the case `mobi/reader.py` already has an untested code path for). If Jacob comes across one, that code path finally gets a real sample; until then it stays flagged unverified.

## Phases

Each phase should be independently buildable, testable against the real samples, and reviewable on its own before moving to the next -- that's the whole point of splitting it up this way.

### Phase 1: Fix what the real samples already broke in the existing code (in progress)

Two bugs surfaced just from reading these three files with the *existing* MOBI7 code, before any new KF8-specific code was written:

1. **HUFF/CDIC decompression is close but wrong.** Running the existing `HuffCdicReader` (written from the public spec for MOBI7, never tested against a real file) against `AZW3-Older.azw3`'s text records decompresses to 440,336 bytes against a header-declared 439,246 -- off by 1,090 bytes (0.25%), consistently a little short on most records and drastically over on the last one. Close enough to confirm the overall algorithm shape is right, wrong enough to need real debugging rather than a guess. This directly benefits MOBI7 conversion too, not just AZW3 -- any *classic* MOBI file using this older compression scheme was equally unverified before now.
2. **The "KF8 boundary" (EXTH 121) check has a false-positive.** `AZW3-Older.azw3` has an EXTH 121 record (value 117), which `mobi/reader.py`'s existing `has_boundary` check treats as "this is a two-header hybrid file, read the MOBI7 half." But record 117 is actually this file's *own* HUFF dictionary record, not a second MOBI header -- there is only one MOBI header in the whole file (confirmed by scanning every record for a second `MOBI` tag; there isn't one). The EXTH value looks like stale or misleading metadata from whatever tool produced this file, not a real second header pointer. Needs hardening: confirm a genuine second header actually exists at the referenced record (a real `MOBI` tag, sane header length) before treating a file as hybrid, rather than trusting the EXTH record's mere presence.

Both are bugs in already-shipped code, found by pointing it at new real files -- fixed here rather than filed separately, since fixing them is a prerequisite for reading `AZW3-Older.azw3` at all in later phases.

### Phase 2: Flow separation (FDST)

KF8 splits the decompressed text-record stream into "flows": flow 0 is the main HTML text; later flows hold embedded CSS and SVG. The `FDST` record (found and located the same way `first_image_record` already is -- confirmed empirically against all three samples) gives the byte ranges for each flow within the decompressed stream. Phase 2 is just: decompress the text records (Phase 1's fixed decompression, reused as-is), read FDST, and correctly slice out flow 0 from whatever else is there. Verified by confirming flow 0's boundaries make sense (starts with `<?xml`/`<html>`, ends at `</html>`) against all three samples.

### Phase 3: Skeleton and fragment indices

The two INDX-family tables that describe how flow 0 (a single interleaved stream of "skeleton" template pieces and "fragment" content pieces) reassembles into the book's actual pages. `indx.py`'s existing generic INDX/TAGX/entry decoder (built for MOBI7's NCX) is structurally reusable here -- confirmed it can already walk both tables and produce raw tag values -- but the *meaning* of each table's specific tag numbers needs to be pinned down against real content, not assumed from memory of how the format is documented elsewhere. This is the phase most likely to need real iteration: the working theory (skeleton entries mark template byte ranges with an insertion point; fragment entries carry the content that gets substituted in) needs to be checked by literally reconstructing a few real pages and confirming they read correctly, not just that the numbers look plausible.

### Phase 4: Page reassembly

Using Phases 2 and 3's output, produce complete, well-formed XHTML pages -- one skeleton (with its fragment(s) substituted at the right point) per output file. Verified by: every byte of flow 0 accounted for exactly once (no gaps, no overlaps), every reassembled page valid XML, and the page count matching the fragment index's own declared entry count.

### Phase 5: Real structure -- guide and table of contents

Two more labeled indices exist alongside skeleton/fragment (confirmed present in all three samples, each with human-readable CTOC labels: one small one that looks like guide-style landmarks -- "Copyright Page", "Table of Contents" -- and a larger one that looks like the book's real chapter-by-chapter TOC -- "Title Page", "Dedication", chapter names). Unlike MOBI7, KF8 books already carry a real, structurally-grounded table of contents here, so this phase should produce a proper nested TOC without any of MOBI7's heuristic-detection uncertainty -- genuinely easier than the MOBI7 equivalent once the index-reading mechanics from Phase 3 are solid.

### Phase 6: Links and images

Resolve `kindle:pos:fid:...:off:...` (internal links), `kindle:embed:...` (images), and `kindle:flow:...` (stylesheets) references within the reassembled pages to real hrefs in the finished EPUB. Images and cover handling reuse MOBI7's existing machinery (same EXTH fields, same record layout) -- confirmed unchanged from MOBI7 by inspection, not yet exercised end-to-end on a KF8 file.

### Phase 7: Assembly, CLI/GUI wiring, full verification

Wire into `ebook_fix.epub_builder` (the shared assembler MOBI and FB2 already use), extend the `convert` CLI command and GUI upload flow to stop refusing `file_version >= 8` files, and run the same battery of checks the MOBI7 and FB2 converters got: strict-XML validity, every link/image/TOC entry resolving, `repair` making only sensible changes and converging to zero on a second pass, full example sweep with no regressions.

### Phase 8 (stretch, only if a sample turns up): fonts, embedded SVG, genuine two-header hybrids

Not blocking a working converter. Flagged as unverified/deferred rather than built speculatively, matching how HUFF/CDIC was originally handled for MOBI7.

## Reference: what's confirmed so far (from exploration, not yet implemented)

Kept here so a future session doesn't have to re-derive it from the raw bytes again.

- **KF8 header fields**, confirmed by cross-referencing against real magic-tagged records rather than assumed from memory: `first_image_record` (the same MOBI7 field, same offset) correctly locates the first image in `AZW3-Example.azw3` (record 203). A block of fields further out in the header (past MOBI7's 232-byte length -- KF8 headers run 264 bytes) holds the FDST record number and count, and separately the record numbers of four INDX-family tables, each empirically confirmed by cross-checking against real `INDX`-tagged records rather than trusted from memorized offsets.
- **Four INDX-family tables per book** (confirmed present, in this order, in both `AZW3-Example.azw3` and `AZW3-Newer.azw3`): a larger one with no CTOC labels (127 entries in the Example file -- skeleton candidate), one whose entry ids look like `SKELnnnnnnnnnn` (70 entries -- fragment candidate, referencing which skeleton each fragment belongs to), a small one with plain-English CTOC labels like "Copyright Page" / "Table of Contents" (2 entries -- guide candidate), and a larger one with real chapter-title CTOC labels (68 entries -- TOC candidate). Tag-level semantics within each are not yet pinned down with full confidence -- that's Phase 3's job.
- **Flow 0 is not simply a sequence of complete pages.** Tried treating each complete `<html>...</html>` span found by direct text search as one output page (70 such spans found in `AZW3-Example.azw3`, matching the fragment index's entry count almost too neatly) -- ruled out by checking that the spans only account for about 4.5% of flow 0's bytes. The real chapter content lives in the much larger gaps between those spans, which is exactly the content the skeleton/fragment system exists to reassemble. No shortcut around Phases 3-4.
- **`AZW3-Older.azw3` is not a real two-header hybrid**, despite carrying an EXTH 121 record that would normally suggest one -- see Phase 1.
