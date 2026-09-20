# MOBI Conversion -- Planning Doc

**Status:** MOBI7 to EPUB built and verified (2026-09-20). AZW3/KF8 scoped, not started.
**Started:** 2026-09-20.

## The idea

Jacob's reasoning: MOBI is a deprecated format, and this project should be able to open a MOBI book and turn it into an ordinary EPUB by itself, with no other software involved. Once a book is an EPUB, every existing analysis and repair module works on it unchanged, so this is a converter at the front door, not a second set of repair code.

**Decision (Jacob, 2026-09-20):** the converter is built in and fully independent. No Calibre, no `ebook-convert`, no third-party MOBI library. Everything is hand-written, in line with the project's minimal-dependency rule.

This is the MOBI/AZW3 input side of the earlier "convert to EPUB first, then reuse everything" idea in `format_conversion_plan.md` (and the background in `format_support_plan.md`), both removed from the repo on 2026-09-18 but still in git history (`git show 9581afb^:docs/format_conversion_plan.md`). The broader proposal there (other input formats, AZW3 and CBZ as outputs) isn't scoped here. The analysis-only `analyze` command for MOBI/AZW3/FB2 is unchanged and still works as before.

## How it works

```
.mobi/.azw/.prc  ->  reader.py  ->  markup.py  ->  epub_out.py  ->  .epub
                     (open it)     (fix the       (assemble
                                    markup)        the EPUB)
                     convert.py runs all three and reports back
```

- `mobi/palmdb.py`, `mobi/mobi_header.py` (existing): the outer container, the MOBI header, and the EXTH metadata block. `MobiHeader` gained the fields the converter needs (record size, trailing-data flags, NCX index record, HUFF/CDIC record numbers).
- `mobi/decompress.py`: PalmDOC decompression, HUFF/CDIC decompression, and removal of the "trailing entries" a text record can carry.
- `mobi/indx.py`: reads the table of contents (the NCX index: INDX header, TAGX, entries, CTOC heading text).
- `mobi/reader.py`: `read_mobi()` returns the decompressed markup, images, cover, metadata and table of contents, or raises `MobiError` with a message written for a person.
- `mobi/markup.py`: turns MOBI7's sloppy private HTML into well-formed XHTML pages.
- `mobi/epub_out.py`: assembles the EPUB 3 zip.
- `mobi/convert.py`: `convert_mobi_to_epub()` and the printed report.
- CLI: `ebook-fix convert book.mobi [-o out.epub] [--overwrite]`. Default output is the same name with `.epub`, next to the input. An existing file is never replaced without `--overwrite`.

## Output shape

A faithful conversion, not a cleanup. Text, images, cover, metadata, links and table of contents come across as they were; everything that is "fixing" stays with `repair`.

- **EPUB 3.0**, with both a nav document and an NCX when the book has a table of contents (so EPUB 2 readers still work, same as the EPUB 3 Upgrade repair leaves things).
- **One XHTML file per MOBI page.** `<mbp:pagebreak/>` is the file boundary (`text/part0001.xhtml`, ...). A break that would leave an empty page is folded into its neighbor.
- **Links** in MOBI7 point at byte offsets in the text, not at named anchors. Every offset anything points at gets a real `id="fileposN"` at exactly that spot (on the target tag itself when possible), and every link is rewritten to the file that ended up holding it. A link to a place that doesn't exist is kept as plain text and counted in the report.
- **Paragraph spacing and indent** (MOBI's private `height` and `width` attributes), alignment, `<font>` and `<center>` become small shared CSS classes (`m1`, `m2`, ..., most-used first) in `styles/style.css`, not an inline style on every paragraph. `p { margin: 0 }` is the base rule, matching how MOBI7 renders.
- **Repaired markup:** unclosed `<p>`/`<li>`/`<td>`, crossed inline tags, stray `</br>`, nested links, and loose text sitting outside any block (wrapped in a paragraph) are all fixed, so every page parses as strict XML.
- **Images:** only the cover and images the text actually uses are written. The cover is named `cover.jpg` (or `.png`/`.gif`), the name Cover Repair standardizes on, and is declared both ways (`properties="cover-image"` and `<meta name="cover">`). Unreferenced images (the Kindle thumbnail, for one) are left out.
- **Metadata:** title (the "updated title" wins over the header's), every author, publisher, description, date, rights, subjects, contributors, language. Identifiers use `opf:scheme` with the names `identifier_schemes.json` already knows: `uuid` (the book's own UUID when its ASIN field holds one, as Calibre-made books do, otherwise a stable one derived from the book so converting twice gives the same ID), `ISBN`, and `MOBI-ASIN`.
- **No table of contents in the source means none in the output** (no nav, no NCX). That is exactly the state TOC Generation looks for, and it can build a real structure-based one, which a placeholder here would only block. The report says so.
- Output is deterministic: converting the same book twice gives byte-identical files apart from the required `dcterms:modified` stamp.

## Refused on purpose

- **DRM-protected books:** stopped with a clear message. This project doesn't remove DRM.
- **AZW3/KF8:** recognized and stopped with a message saying it's the next planned step. It's a different internal layout, not a variation on MOBI7 (see below).
- **Damaged or cut-off files:** a file that ends before its text does, or whose decompressed text is far short of what its header says, is an error rather than a quietly partial book.

## Verified (2026-09-20)

- **Real sample, `MOBI-Example.mobi`** (MOBI7, PalmDOC, UTF-8, 118 text records, 35-entry table of contents, 6 images): decompressed length matches the header exactly (481,546 bytes); all 35 table-of-contents entries match a reference unpacker's output for label and position; the visible text of the finished EPUB is identical to the reference's; all 37 pages parse as strict XML; every link, image, manifest entry and table-of-contents target resolves.
- **The reference unpacker** (a third-party MOBI unpacking package) was used only as a test oracle to compare against. It isn't a dependency and nothing in the project imports it.
- **Synthetic MOBI files** built for testing: PalmDOC and uncompressed text, trailing-data flags, cp1252 and UTF-8, multiple authors, images with a cover, no table of contents, DRM flag, KF8 flag, not-a-MOBI, truncated, and overwrite protection. PalmDOC compress/decompress round trip on binary, repeated and multibyte data.
- **Markup edge cases:** sloppy and mis-nested tags, loose text, page breaks inside open tags, link targets at a tag start, inside a tag, inside text, inside a multibyte character, at the end of the text and past the end, entities and control characters, `<font>`/`<center>`/`height`/`width`, lists, tables, nested links, unknown tags, duplicate ids.
- **Through the rest of the project:** the converted sample opens in `analyze` with no Cover Repair finding, `repair` runs cleanly (28 chapters detected and marked up), and a second `repair` is a no-op. A converted book with no table of contents gets a nav document and NCX from TOC Generation on `repair`, and a second pass is a no-op.

## Not covered yet (known gaps)

- **AZW3/KF8.** Next step. A real sample exists in `examples/` (`AZW3-Example.azw3`).
- **HUFF/CDIC compression** (some early Mobipocket books) is written from the documented format but no real file has exercised it. The converter checks the decompressed length against the header and refuses, rather than producing a damaged book, if it doesn't match. Swap in a real sample if one turns up.
- **Hybrid files** (a MOBI7 and a KF8 book in one file) are read through their MOBI7 half. Untested for lack of a sample; the report says so when it happens.
- **BMP images** are skipped (not an EPUB image format, and converting them would need an imaging library). The report counts them.
- **Index-label encodings** other than UTF-8/Windows-1252 in the table of contents (the rare "ORDT" scheme) aren't handled.
- **GUI:** opens a MOBI by converting it first (added 2026-09-20, see `docs/gui_plan.md`). The converted EPUB is saved next to the original and never replaces an existing file. For a MOBI inside a Calibre library folder that puts an unregistered EPUB in a Calibre book folder; untested against a real library.
- The output is EPUB 3 only. No EPUB 2 option.
- Not run through `epubcheck` (not available where this was built). Identifiers and authors use the same legacy `opf:scheme`/`opf:role` attributes the rest of the project already writes, so a strict EPUB 3 validator may flag those.

## Next: AZW3/KF8

KF8 keeps the same PalmDB container, header, EXTH, images and PalmDOC text records, so `palmdb.py`, `mobi_header.py`, `decompress.py` and most of `reader.py` carry over. What changes is everything after decompression:

- The text is not one long HTML stream. It is split into a **skeleton** table (the outer HTML of each file), a **fragment** table (the pieces that get inserted into each skeleton), and a **flow** table (`FDST`, which separates the HTML from the CSS and SVG parts). Each is its own INDX-style index.
- Links use `kindle:pos:fid:...:off:...`, images `kindle:embed:...`, and stylesheets `kindle:flow:...`, all needing to be resolved back to real files.
- The table of contents index points at fragment/offset pairs rather than plain byte offsets.
- Embedded fonts and mixed-in SVG can appear.

`indx.py` will need generalizing (the same TAGX/entry decoding, more tag types). `markup.py`'s job (repairing markup) shrinks, since KF8 content is already close to valid XHTML, but link and anchor rewriting stays. `epub_out.py` and `convert.py` are reused as they are.

Suggested order: (1) skeleton/fragment/flow assembly into ordinary XHTML files, (2) link and image resolution, (3) table of contents, (4) CSS flows, fonts and SVG, (5) hybrid files (convert the KF8 half instead of the MOBI7 half when both exist).

## Also on the list

- FB2 conversion, once MOBI/AZW3 are done. `convert` was named generically so it can take other formats later.
