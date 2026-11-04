# Future Plans

Everything that is still open, in one place. Finished work is written up
in `docs/analysis_roadmap.md`. Nothing here is scheduled; Jacob picks
what comes next.

## Decided, not built yet

- **Auto-rename on save, from a filename pattern.** GUI only, off by
  default, with a setting and a checkbox at save time. Never applied to
  a book Calibre manages. Tokens: `%author%`, `%title%`, `%year%`,
  `%series%`, `%series_index%`. Still to settle: what happens when a
  field is empty (no year, say) and how illegal filename characters are
  handled. Waiting on Jacob's decision to build it or drop it.
- **`--log "location.txt"` option for the command line.** Writes the
  full output of a run to a file, color codes handled on purpose.

## Waiting for a real sample

Nothing to build until a real book turns up that shows the problem.

- Ads, newsletter pages and teasers beyond the few in `examples/`.
- The Title/Author search and the series lookup against live Open
  Library on older books (never tested on live data).
- AZW3 fonts and SVG on more books, and two-header MOBI7+KF8 hybrids.
- FB2 tables, citations, code, strikethrough, named styles and a
  body-level image.
- An omnibus whose first book starts at a chapter with no title page,
  and notes shared across books.

## Analyzer ideas, never scoped

Each would be a new check that records its findings once so a repair
module can simply read them.

- **Stray page numbers.** Leftover PDF page numbers sitting inside a
  paragraph or between blocks.
- **Footnote and endnote links.** Markers that point nowhere, or notes
  nothing points to.
- **Fake page-bookmark headings.** Some PDF conversions turn the first
  sentence of every page into a heading (seen in
  `ChaptersMisaligned.epub`, where the ids start with `calibre_pb_`).
  Should be pattern-based, not tied to one converter: a heading that is
  not part of the confirmed chapter sequence and reads like an ordinary
  sentence gets turned back into a paragraph.
- **Doubled letters lost.** Corruption like "walls" becoming "wal s"
  (seen in `BrokenSentences.epub`). It needs a word list to tell it from
  a real word plus "s", and the project has no word list yet.
- **Wrong media types in the manifest.** A declared type that does not
  match what the file really is.
- **Wrong encoding declarations.** A declared encoding that does not
  match the file's actual bytes.
- **Duplicate content.** Repeated boilerplate pages or an accidentally
  doubled chapter.
- **Accessibility gaps.** Missing image descriptions and missing
  language attributes.
- **Language-aware checks.** Now that every book can get a language
  (see the 2026-11-02 entry), checks that depend on it become possible,
  such as quote style and hyphenation rules per language.

## Chapters and contents

Where things stand: books with a contents list or chapter markers are
split safely, books with neither get best-effort candidates that go to
the Review tab, and omnibus books split into separate books.

- **Link the in-body Contents page.** A contents page that lists chapter
  names as plain text (the MM21 case) could have each name linked to its
  chapter. Open question: what to do with a listed name that matches
  nothing cleanly.
- **Image-based chapter markers.** Needs OCR, so it is deferred.
- **Whether the new structure reading replaces `chapters.py`** or keeps
  sitting beside it.

## Interface

Where things stand: the tabs are Overview, Metadata, Review (shown only when
something needs a choice) and Before / After, plus Browse, Fix This Book
and the split page.

- **Bulk mode follow-ups.** Fix a Folder is built (see the 2026-11-04
  entry in `docs/analysis_roadmap.md`). Still open: fixing several books
  at the same time (the books are done one after another today), an
  analyze-only run that lists what a folder needs without changing
  anything, choosing which books in the list to include before it starts,
  saving the results list as a file, and cleaning up the temporary
  working folders it leaves behind (see Session folder cleanup below).
  Calibre covers a library-level series view, so that part is not wanted.
- **Analysis drill-down.** The individual broken links, per-chapter
  whitespace fixes and similar details that `--details` shows on the
  command line, shown in the Overview.
- **Editable identifiers.** Adding and removing several identifiers
  (ISBN and others) in the Metadata tab. Today they are read-only.
- **Session folder cleanup.** Old working folders are never deleted.
  Harmless for one person on one computer, but they pile up.
- **Cover fallback.** When a book has no cover and there is no Calibre
  folder, offer an online lookup.
- **Cover mismatch view.** Show the two covers in the Review tab, or
  leave the comparison to Before / After (likely the latter).
- **Review reduction and series labeling polish.** Both need Jacob's
  real books to judge.

## Known limits

- Series name tidying and the series lookup only know the names in
  `series_names.txt` plus whatever Open Library returns.
- The Whitespace and Paragraph Repair checks favor missing a fix over
  making a wrong one, so a few real problems are left alone on purpose.
- Norwegian text is not recognized by language detection, because it
  overlaps too much with Danish. No answer is safer than a wrong one.
