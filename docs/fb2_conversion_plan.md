# FB2 Conversion -- Planning Doc

**Status:** built and verified against all three of Jacob's samples (2026-09-22).
**Started:** 2026-09-22, right after MOBI conversion (`docs/mobi_conversion_plan.md`).

## The idea

Same direction as MOBI: convert to EPUB first, fully independent of Calibre or any other tool, so every existing analysis and repair module works on the result unchanged. `ebook-fix convert` already existed for MOBI; this extends it to `.fb2`, dispatching by file extension.

FB2 (FictionBook 2.0) is a much easier format than MOBI: it's a real, fully-documented XML standard, already well-formed, with no reverse-engineered container or compression to work out. Most of the effort here went into mapping FB2's specific vocabulary (sections, epigraphs, poems, footnote bodies, genre codes) onto sensible EPUB/XHTML, not into recovering data from a hostile format the way MOBI needed.

## How it works

```
.fb2  ->  reader.py  ->  markup.py  ->  epub_builder.py  ->  .epub
         (parse the      (render the    (assemble the
          XML)            tree to        EPUB -- shared
                          XHTML)         with the MOBI
                                         converter)
         convert.py runs all three and reports back
```

- `fb2/reader.py`: `read_fb2()` parses the XML with lxml and returns metadata, decoded image binaries, and the book's `<body>` elements as lxml elements ready for `markup.py` to walk. Refuses (with a message meant for a person) anything that isn't well-formed XML or doesn't look like a FictionBook document.
- `fb2/markup.py`: `Fb2Renderer`, a recursive tree-to-XHTML walker (much simpler than MOBI's `markup.py` since there's no sloppy markup to repair), plus `assign_missing_section_ids()` and `collect_toc()`/`collect_ids()` for id and table-of-contents handling.
- `fb2/genres.py` + `fb2/genres.json`: FB2 genre code -> human-readable `dc:subject` label, editable data file, same convention as `src/metadata/schemes/identifier_schemes.json`.
- `fb2/convert.py`: `convert_fb2_to_epub()` and the printed report; orchestrates the above.
- `fb2/analyzer.py`: the project's existing FB2 *analysis* support (unchanged, just relocated -- see "A naming collision" below).
- CLI: `ebook-fix convert book.fb2 [-o out.epub] [--overwrite]` -- the same `convert` command MOBI uses, now dispatching by extension. GUI: the same MOBI/AZW upload flow now also accepts `.fb2`.
- `ebook_fix/epub_builder.py` (moved from `ebook_fix/mobi/epub_out.py`): the EPUB-assembly step is shared between the MOBI and FB2 converters rather than duplicated, since neither converter's version of it was actually MOBI- or FB2-specific. Gained series metadata (`calibre:series`/`belongs-to-collection`, matching what `ebook_fix.series` already reads and writes) and a shared `normalize_isbn()` helper, both used by the FB2 converter and now also by the MOBI one.

## A naming collision (fixed before it caused real damage)

The repo already had an `ebook_fix/fb2.py` -- the project's existing MOBI/AZW3/FB2 *analysis* support (`FB2_EXTENSIONS`, `analyze_fb2`, `print_fb2_report`, imported in `cli.py`). Turning `fb2` into a package (`ebook_fix/fb2/`) for the new reader/markup/convert modules would silently shadow that file: Python prefers a package over a same-named module, so the old analysis code would stop being reachable at all under its own import path, with no error, just wrong/missing behavior. Fixed by moving the file to `fb2/analyzer.py` and re-exporting the same three names from `fb2/__init__.py`, so `cli.py`'s existing `from ebook_fix.fb2 import FB2_EXTENSIONS, analyze_fb2, print_fb2_report` line didn't need to change at all. Worth remembering for any future format: check for an existing same-named module before adding a package.

## Output shape

Faithful conversion, matching the MOBI converter's posture: convert what's there, let `repair` do the cleanup and enhancement afterward.

- **EPUB 3.0**, nav + NCX, cover page, images, table of contents, and OPF `<guide>`/landmarks, same as MOBI's output.
- **One XHTML file per top-level `<section>`** (a direct child of the book's main `<body>`). Nested subsections stay inline in that same file as headings (`<h2>`, `<h3>`, ... one level per nesting depth, capped at `<h6>`), same "only split where the source's own structure says to" rule the MOBI converter follows for pages. Unlike MOBI, FB2's own section nesting is fully known upfront (it's a real parsed tree, not a linear byte stream), so **every nested subsection also gets its own table-of-contents entry** at the matching depth, not just the top-level parts -- a real, structurally-grounded nested TOC, not a heuristic-detected one.
- **A body-level title/epigraph** (a book-level dedication or title page before the first named part, seen in `FB2-Example.fb2` -- Harper Lee's own dedication and the Charles Lamb epigraph) becomes its own leading page.
- **A dedicated cover page** is created and put first in the spine. FB2's cover is pure metadata (`<coverpage>`) with no equivalent inline page the way a MOBI book's body sometimes already has one, so this converter builds one, matching ordinary EPUB convention.
- **Footnotes/endnotes** (any `<body>` after the first -- confirmed against `FB2-ForeignLanguage.fb2`'s "notes" body, 37 entries) become one page holding every entry wrapped in an EPUB3 `<aside epub:type="footnote">`, and every `<a type="note" href="#id">` in the main text is rewritten to `epub:type="noteref"` pointing there. The footnote's own number/label is folded into the start of its first paragraph ("**1.** actual footnote text...") rather than given a paragraph of its own -- see "Two real bugs found by testing" below for why.
- **FB2's own blank-line marker** (`<empty-line/>`) is rendered as a real `<hr/>`, so the project's existing Scene Break Normalizer classifies and standardizes it (real break vs. chapter-edge artifact, target glyph "* * *") automatically -- see below for why this replaced an earlier attempt.
- **Poems** (`<poem>`/`<stanza>`/`<v>`), **epigraphs**, **citations** (`<cite>`), and **tables** map onto `<div>`/`<blockquote>`/`<table>` with matching CSS classes in `styles/style.css`.
- **Images**: only the cover and images actually referenced (block-level or inline, e.g. an `<image>` nested inside `<strong>`, seen in `FB2-Images.fb2`) are written, sniffed from the actual bytes rather than trusted from the binary's own declared `content-type` (same posture as the rest of the project -- see `ebook_fix.cover.sniff_image_media_type`, now reused here instead of a second hand-rolled check).
- **Metadata:** title, every author and translator (assembled from FB2's separate first/middle/last-name fields), language, genres (mapped to readable subjects via `genres.json`, with an ungraceful-but-safe fallback for any code not in the table -- nothing is ever silently dropped), annotation (as plain-text `dc:description`, not markup), date, publisher, ISBN (validated, via the same `normalize_isbn()` MOBI now also uses), and series (`<sequence>`, written in both conventions `ebook_fix.series` reads). The book's own id (`document-info/id`, when it's a real UUID) is used directly as `dc:identifier opf:scheme="uuid"`; otherwise a stable one is derived from title/author/ISBN so converting the same book twice gives the same id.

## Two real bugs found by testing, not by inspection

Both of these were real, non-obvious interactions with the existing repair pipeline that only surfaced by actually running `repair` on the converted output and reading what it did, not by reading the converter's own code:

1. **Footnote numbers were being misread as book chapters.** The first version rendered each footnote's number as its own paragraph (`<p><strong>1</strong></p>`). `chapters.py`'s chapter-marker detection matches a block whose *entire* text is a bare number -- exactly what a strictly-rising footnote sequence (1, 2, 3, ... 37) looks like. Chapter Markup dutifully wrapped all 37 footnotes as "chapters" on the notes page. Fixed by folding the number into the start of the footnote's first paragraph instead of giving it a block of its own; inline text ("1. actual text...") never whole-string-matches a bare-number marker.
2. **A blank-line marker was being silently deleted.** The first version rendered `<empty-line/>` as `<p class="empty-line">&nbsp;</p>`, reasoning that a non-breaking space would keep the paragraph from looking empty. Paragraph Repair's own emptiness check strips text the same way Python's `str.strip()` does, which treats `\xa0` as strippable whitespace too -- so these were being read as ordinary empty paragraphs and removed. Fixed by rendering as a real `<hr/>` instead, which is also the more correct fix: FB2's blank-line marker is usually exactly the kind of deliberate pause `ebook_fix.scene_breaks` already exists to classify and standardize, so this hands it to that existing, already-tested machinery instead of inventing a second, competing representation.

Both are now confirmed fixed: `repair` on all three sample conversions makes only real, sensible changes (typography, EPUB3 upgrade, and now correctly-classified scene breaks) and a second `repair` pass is a no-op on all three.

## Verified (2026-09-22)

- **All three of Jacob's samples** (`FB2-Example.fb2` -- *To Kill a Mockingbird*, two-level Part/Chapter nesting, body-level dedication/epigraph, cover; `FB2-ForeignLanguage.fb2` -- a Russian book, 38 flat chapters plus a 37-entry footnote body, poems with footnote references mid-verse, non-breaking spaces throughout; `FB2-Images.fb2` -- a single untitled section, inline images including one nested inside `<strong>`) convert cleanly: strict-XML pages, every link/image/manifest/TOC entry resolves, and every GUI tab (Analysis, Repair, Review, Before/After) opens without error.
- **`repair` run on all three:** picks up real, sensible fixes on the first pass (chapter markup, non-breaking-space normalization already present in the *source* text -- confirmed byte-for-byte identical counts between the source FB2 and the converted EPUB, so nothing was introduced by conversion --, and now-correct scene-break classification) and converges to zero changes on a second pass.
- **A synthetic FB2** built to exercise everything none of the three real samples happen to use: `<table>` (with `align`/`colspan`), `<cite>`/`<text-author>`, `<code>`/`<strikethrough>`/`<sub>`/`<sup>`, a translator, an unrecognized genre code (confirmed falls back to a readable title-cased label rather than being dropped), and `<sequence>` (series name + number, confirmed round-trips correctly through `ebook_fix.series.read()`).
- Full regression: every file in `examples/` still runs through `analyze` without error; the MOBI converter, after the `epub_builder.py` move and its two small shared-helper changes, still converts `MOBI-Example.mobi` and `AZW3-Example.azw3` (refused, as before) identically to its own prior test suite.

## Not covered yet (known gaps)

- **`<table>`, `<cite>`, `<code>`, `<strikethrough>`, `<sub>`/`<sup>`, and `<sequence>`** are implemented from the FB2 2.0 specification (a stable, fully public standard, unlike MOBI's reverse-engineered internals) and confirmed against the synthetic test file above, but not yet against a real book that uses them. Lower risk than MOBI's equivalent gaps (HUFF/CDIC, KF8) precisely because the format itself is documented rather than guessed at, but still worth swapping in a real sample if one turns up.
- **`<style name="...">`** (FB2's inline named-style element) becomes `<span class="fb2-NAME">` with no actual CSS generated for that name -- there's nothing to map it to without seeing what styles real books actually declare.
- **A body-level `<image>`** (a body's own leading illustration, separate from the coverpage) is rendered if present, but no sample exercised it.
- **`src-lang`** (the language a translated FB2 was translated *from*) isn't carried into the EPUB; there's no Dublin Core field for it and it isn't the book's actual reading language.
- **The GUI's cover-image detection** for a converted FB2 depends on the same "properties=cover-image" manifest convention MOBI conversion already relies on, not specifically re-tested for FB2 beyond what the sample sweep already covers.
- **Not run through `epubcheck`** (not available where this was built), same caveat as the MOBI converter.
