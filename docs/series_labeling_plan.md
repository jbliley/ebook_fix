# Series Labeling: Plan and Ideas

**Status:** First step built (2026-10-13). Second step built (2026-10-17): spelled-out and roman numbers in tags, tag removal when the series is already set, series labels on split omnibus books. The rest is a list of ideas, nothing decided or started.

Jacob sees series labeling becoming a bigger feature over time. This note records what exists, what was learned, and the ideas raised so far, so the next session starts from here.

## What exists today

- **Storage:** `ebook_fix/series.py` reads and writes both conventions (Calibre `calibre:series` and `calibre:series_index`, and EPUB 3 `belongs-to-collection`). It never overwrites a series a book already has.
- **From a filename-style title:** Title Cleanup reads "Author - 01 Series - Title" (needs the author in the title).
- **From a series tag in the title (new):** Title Cleanup also reads a tag written into an ordinary title, with an explicit marker: `Title (Series #3)`, `Title (Series, Book 3)`, `Title [Series Vol. 3]`, `Title (Book 3 of the Series)`, `Series #3: Title`, `Series, Book 3 - Title`. The tag comes out of the title and becomes the series. Setting: `read_embedded_series` under `[title_cleanup]`. The Metadata tab shows it as a "Fix This Book will change this to" note like every other repair.
- **From Open Library (new):** the lookup now suggests a series name and position when Open Library has one for the edition.

## Second step (2026-10-17)

- **Spelled-out and roman numbers:** a marked tag may now use a number word or a capital roman numeral: `Title (Series, Book Three)`, `Title [Series Vol. IV]`, `Series, Book II: Title`. Numbers up to fifty. A roman numeral must be in capitals and stay small, so words such as "Mix" or "Civil" are never read as numbers. A bare spelled-out number with no marker is still ignored.
- **Tag already matches the book's series:** when the book already has a series and its title still ends with a tag naming that same series, only the tag is taken out of the title. The series is not touched. A tag naming a different series is left alone.
- **Omnibus splitter:** each split book now carries its series. With a series name given (`split-omnibus --series NAME [--series-start N]`, or the two fields on the Overview split form) the books are labeled with it and numbered in order. A series tag in a book's own title sets that book's position itself. With no name given the omnibus's own series name is carried over as before, with no position, because the omnibus's number says nothing about where each book sits.

## Third step (2026-10-17)

- **Series name tidying:** Jacob's rule is that "The Dark Tower" and "Dark Tower" are one series, kept with the leading "The" (Calibre ignores it when sorting). New `ebook_fix/series_names.py` matches a name against known proper names, ignoring case, a leading The/A/An, punctuation and a trailing "Series". Known names come from `series_names.txt` next to `ebook_fix.toml` (one per line, wins) and from the series already in the Calibre library the book is in (read-only from `metadata.db`). With no match, only basic tidying is done: spacing, quotes, a trailing "Series" dropped, ALL CAPS to Title Case. A leading "The" is never invented. Applied to series read from a title, series suggested by the Open Library lookup, and the series name given to `split-omnibus`. A series typed in by hand is never changed.

## Deliberately not done yet

- A tag with no marker, such as `Title (Series 3)`, is ignored. A bare number could be an edition or part of the title.
- A series name with no number (`Title (Deathlands)`) is ignored, since it could be a subtitle.
- A book that already has a series is never changed, even when its title still carries the tag.
- Open Library has series on only some editions, in free text. Expect gaps.

## Ideas raised or implied (undecided)

- Series from the filename or Calibre folder name when the title has nothing.
- A library-level view (group books by series, show gaps): set aside, since ebook_fix works on one book at a time and has no multi-book screen yet.
- Other online sources for series, if Open Library proves too thin.

## Open questions for Jacob

- Should a series tag be removed from the title automatically (as now), or only suggested?
- Is the library-level view something he wants inside ebook_fix, or does Calibre already cover it well enough?
