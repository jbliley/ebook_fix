# Series Labeling: Plan and Ideas

**Status:** First step built (2026-10-13). The rest is a list of ideas, nothing decided or started.

Jacob sees series labeling becoming a bigger feature over time. This note records what exists, what was learned, and the ideas raised so far, so the next session starts from here.

## What exists today

- **Storage:** `ebook_fix/series.py` reads and writes both conventions (Calibre `calibre:series` and `calibre:series_index`, and EPUB 3 `belongs-to-collection`). It never overwrites a series a book already has.
- **From a filename-style title:** Title Cleanup reads "Author - 01 Series - Title" (needs the author in the title).
- **From a series tag in the title (new):** Title Cleanup also reads a tag written into an ordinary title, with an explicit marker: `Title (Series #3)`, `Title (Series, Book 3)`, `Title [Series Vol. 3]`, `Title (Book 3 of the Series)`, `Series #3: Title`, `Series, Book 3 - Title`. The tag comes out of the title and becomes the series. Setting: `read_embedded_series` under `[title_cleanup]`. The Metadata tab shows it as a "Fix This Book will change this to" note like every other repair.
- **From Open Library (new):** the lookup now suggests a series name and position when Open Library has one for the edition.

## Deliberately not done yet

- A tag with no marker, such as `Title (Series 3)`, is ignored. A bare number could be an edition or part of the title.
- A series name with no number (`Title (Deathlands)`) is ignored, since it could be a subtitle.
- A book that already has a series is never changed, even when its title still carries the tag.
- Open Library has series on only some editions, in free text. Expect gaps.

## Ideas raised or implied (undecided)

- Strip the tag from the title when the book already has the same series set.
- Spelled-out numbers ("Book One", "Volume Two") and roman numerals.
- Series from the filename or Calibre folder name when the title has nothing.
- Series name tidying: "The Dark Tower" vs "Dark Tower", "Series" suffix, capitalization, so one series is spelled one way across the library.
- A library-level view: group books by series, show gaps (have 1, 2, 4), and spot books that look like they belong to a series but have none.
- Other online sources for series, if Open Library proves too thin.
- Omnibus splitter: label each split book with its series position.

## Open questions for Jacob

- Should a series tag be removed from the title automatically (as now), or only suggested?
- Is the library-level view something he wants inside ebook_fix, or does Calibre already cover it well enough?
