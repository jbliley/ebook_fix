"""
ebook_fix.omnibus

Detects whether a book is actually an omnibus -- two or more complete
books bound into one EPUB, each with its own chapter numbering starting
back at 1 -- and where each one begins and ends. Descriptive only, same
as css.py/fonts.py/frontmatter.py: this module finds the boundaries and
reports a confidence level; splitting the file into separate EPUBs is a
repair module's job, built on top of this analysis (see
docs/omnibus_splitter_plan.md).

Built and verified against five real omnibus files: three Jacob
uploaded for this (not included in examples/ -- see the plan doc for
why) and two already sitting in examples/ under names that didn't say
"omnibus" clearly enough to have been noticed sooner
(OmnibusExample.epub and the Russian-titled First-Mountain-Man file).
Between them these turned out to use three genuinely different
structures, not one. All three are handled, by trying each detection
method in turn rather than picking one and hoping:

- **Nested** (`Tales of Talon Box Set`): each book is already its own
  top-level book.toc entry, with that book's own chapters as its
  children. No restart-scanning needed at all -- the structure already
  says where each book is. See `_detect_nested`.
- **Flat with a restart** (`Ravaged Land... Box Set`, `OmnibusExample`,
  the First Mountain Man file): one flat TOC, "Chapter 1, 2, 3...28"
  followed immediately by "Chapter 1" again. The entry immediately
  before a restart is that next book's own title marker -- UNLESS that
  entry is itself a "Book Two"/"Part Three"/"Volume IV" label, which
  means the restart belongs to a single novel's own internal part
  structure, not a second work (confirmed real and not hypothetical:
  War and Peace restarts chapter numbering 17 times, once per "BOOK
  ONE"/"BOOK TWO"/... division, with nothing else about it resembling
  an omnibus). See `_detect_restart`.
- **Flat with no chapter numbers at all** (`The Complete Tarzan
  Collection`): 25 novels, each its own top-level TOC entry, but
  nothing restarts because nothing was ever numbered in the first
  place -- every entry is just that novel's own title. Only safe to
  read this way when there are several such entries AND each one's
  own span of the book is long enough to be a real book and not a
  stray front-matter page. See `_detect_title_list`.

A book can match more than one of these in principle; `detect()` tries
them in the order above and returns the first real match, since a
structure specific enough to match "nested" or "restart" is a stronger
signal than the title-list fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ebook_fix.chapters import _classify
from ebook_fix.models import TocEntry

# A book's own span has to contain at least this many pages that are
# each individually at least this long to count as a real book for
# _detect_title_list, rather than a cumulative character total across
# the whole span. A cumulative total was tried first and is wrong: a
# run of several short front-matter pages (a cover, an ad, a
# dedication, none longer than a page) can add up past almost any
# single threshold without any of them, or the book they belong to,
# containing a single real chapter -- confirmed a real false positive
# on MM5_Complete.epub, a genuinely single-book file, before this was
# changed to look for several chapter-sized pages instead of a total.
_MIN_SUBSTANTIAL_PAGES = 3
_MIN_CHAPTER_LENGTH = 2000


@dataclass
class OmnibusBook:
    title: str
    start_index: int   # index into book.chapters (spine order); inclusive
    end_index: int      # exclusive -- the next book's start_index, or len(book.chapters) for the last one
    chapter_count: int = 0   # numbered chapters found in this span, where that signal exists; 0 for _detect_title_list


@dataclass
class OmnibusAnalysis:
    is_omnibus: bool = False
    confidence: str = "none"   # "high", "medium", "low", "none" -- same convention as frontmatter.py
    method: str = ""           # "nested", "restart", "title-list", or "" if not detected
    books: list = field(default_factory=list)   # list[OmnibusBook], in spine order
    reason: str = ""            # one line, for a person: why this confidence level


def _href_to_chapter_index(book, href: str) -> int | None:
    target = href.split("#", 1)[0]
    for i, chapter in enumerate(book.chapters):
        if chapter.href == target:
            return i
    return None


def _flatten(entries: list[TocEntry], depth: int = 0) -> list[tuple[int, TocEntry]]:
    out = []
    for entry in entries:
        out.append((depth, entry))
        out.extend(_flatten(entry.children, depth + 1))
    return out


def _chapter_text_length(chapter) -> int:
    if chapter.document is None:
        return 0
    return len("".join(chapter.document.itertext()))


def _own_chapter_sequence(children: list[TocEntry]) -> int:
    """How many of `children`, in order, form a believable "Chapter 1,
    2, 3..." run starting at 1 -- the same counting-up check
    chapters.py's own module docstring describes, just against TOC
    labels instead of in-document text. A Book/Part/Prologue/Epilogue-
    labeled child, or one that isn't a chapter number at all, doesn't
    break the run (it's just not counted) as long as real chapter
    numbers keep counting up around it."""
    expected = 1
    count = 0
    for child in children:
        result = _classify(child.label)
        if result is None:
            continue
        _style, number, _had_label, label_kind = result
        if label_kind not in (None, "chapter"):
            continue
        if number == expected:
            count += 1
            expected += 1
    return count


def _detect_nested(book) -> OmnibusAnalysis | None:
    """Each book is its own top-level book.toc entry, with that book's
    own chapters nested as its children (Tales of Talon Box Set)."""
    candidates = []
    for entry in book.toc:
        if not entry.children:
            continue
        own_chapters = _own_chapter_sequence(entry.children)
        if own_chapters < 2:
            continue
        index = _href_to_chapter_index(book, entry.href)
        if index is None:
            continue
        candidates.append((index, entry.label, own_chapters))

    if len(candidates) < 2:
        return None

    candidates.sort(key=lambda c: c[0])
    books = []
    for i, (start, title, chapter_count) in enumerate(candidates):
        end = candidates[i + 1][0] if i + 1 < len(candidates) else len(book.chapters)
        books.append(OmnibusBook(title=title, start_index=start, end_index=end, chapter_count=chapter_count))
    return OmnibusAnalysis(
        is_omnibus=True,
        confidence="high",
        method="nested",
        books=books,
        reason=(
            f"{len(books)} top-level table-of-contents entries each have their own \"Chapter 1, 2, 3...\" "
            "sequence nested underneath them."
        ),
    )


def _detect_restart(book) -> OmnibusAnalysis | None:
    """One flat book.toc, chapter numbering restarts at 1 partway
    through (Ravaged Land: Eventuality Series Box Set).

    A book's title marker is the single entry immediately before its
    first chapter (e.g. "The Wall" right before that book's own
    "Chapter 1") -- not a multi-entry walk further back than that. Both
    real samples this method is built against never have more than one
    such entry in a row, so a longer walk isn't something this has
    evidence for; everything further back than that immediate
    predecessor (a shared title page, contents, copyright, author's
    note -- all real in this same sample) is shared front matter, not
    part of any one book's own title, and is handled by always
    starting the first book's own span at index 0 rather than at its
    marker's position (see the loop below).

    A restart only counts if the entry immediately before it is
    genuinely unclassifiable -- not itself a "Book Two"/"Part Three"/
    "Volume IV" entry. A single novel divided into numbered parts, each
    restarting its own chapter count (confirmed real: War and Peace,
    "BOOK ONE: 1805" / Chapter I...XXVIII / "BOOK TWO: 1805" / Chapter
    I...), produces exactly this kind of restart without being an
    omnibus at all -- there's no standalone title to use as a marker,
    only the part label chapters.py's own _classify() already
    recognizes as part of a single book's internal structure, not a
    second complete work. Rather than guess at a title in that case,
    the restart is simply not treated as a book boundary."""
    flat = [entry for _depth, entry in _flatten(book.toc)]
    classified = [_classify(entry.label) for entry in flat]

    markers: list[int] = []   # indices into `flat`; the first one is book 1's own title marker, not a "restart"
    last_number = None
    for i, result in enumerate(classified):
        if result is None:
            continue
        _style, number, _had_label, label_kind = result
        if label_kind not in (None, "chapter"):
            continue
        if number == 1:
            if (last_number is None or last_number >= 2) and i > 0 and classified[i - 1] is None:
                markers.append(i - 1)
            elif last_number is None and i == 0:
                markers.append(i)   # the very first TOC entry is itself "Chapter 1" -- no title precedes it at all
        last_number = number

    if len(markers) < 2:
        return None

    books = []
    for i, marker in enumerate(markers):
        index = _href_to_chapter_index(book, flat[marker].href)
        if index is None:
            return None   # a marker's own href doesn't resolve to a real chapter -- don't guess
        next_marker_href = flat[markers[i + 1]].href if i + 1 < len(markers) else None
        next_index = _href_to_chapter_index(book, next_marker_href) if next_marker_href else None
        books.append(
            OmnibusBook(
                title=flat[marker].label,
                start_index=0 if i == 0 else index,   # book 1 also gets whatever comes before its own marker
                end_index=next_index if next_index is not None else len(book.chapters),
            )
        )

    for b in books:
        b.chapter_count = sum(
            1 for d, e in _flatten(book.toc)
            if (idx := _href_to_chapter_index(book, e.href)) is not None and b.start_index <= idx < b.end_index
            and (cls := _classify(e.label)) is not None and cls[3] in (None, "chapter")
        )

    return OmnibusAnalysis(
        is_omnibus=True,
        confidence="high",
        method="restart",
        books=books,
        reason=f"Chapter numbering restarts at 1 {len(books) - 1} time(s) in the table of contents.",
    )


def _detect_title_list(book) -> OmnibusAnalysis | None:
    """One flat book.toc, no chapter numbers anywhere, but several
    top-level entries that each cover a long enough span to be a real
    book rather than a front-/back-matter page (The Complete Tarzan
    Collection).

    Project Gutenberg's own standard license text, appended to the end
    of virtually every Gutenberg book, is long enough on its own to
    pass the span-length check below (confirmed false-positive: "The
    Call of Cthulhu", where it was being counted as a second "book").
    ebook_fix.gutenberg's own detector already knows exactly which
    file that is, so anything at or after it is excluded outright
    rather than taught a second, overlapping length/pattern heuristic
    for the same content."""
    from ebook_fix.gutenberg import analyze_book_gutenberg

    boilerplate_start = None
    gutenberg = analyze_book_gutenberg(book)
    if gutenberg.back_found:
        boilerplate_start = _href_to_chapter_index(book, gutenberg.back.href)

    candidates = []
    for i, entry in enumerate(book.toc):
        if _classify(entry.label) is not None:
            continue   # looks like a chapter/part marker itself -- not a book title
        index = _href_to_chapter_index(book, entry.href)
        if index is None:
            continue
        if boilerplate_start is not None and index >= boilerplate_start:
            continue
        candidates.append((index, entry.label))

    if len(candidates) < 2:
        return None

    candidates.sort(key=lambda c: c[0])
    books = []
    end_cap = boilerplate_start if boilerplate_start is not None else len(book.chapters)
    pending_start = None
    pending_title = None
    for i, (start, title) in enumerate(candidates):
        if pending_start is None:
            pending_start, pending_title = start, title
        is_last = i + 1 == len(candidates)
        next_start = candidates[i + 1][0] if not is_last else end_cap
        substantial_pages = sum(
            1 for c in book.chapters[pending_start:next_start] if _chapter_text_length(c) >= _MIN_CHAPTER_LENGTH
        )
        if substantial_pages < _MIN_SUBSTANTIAL_PAGES:
            if is_last:
                # Nothing left to merge forward into -- this is trailing
                # back matter (an "About the Author" page, a short ad),
                # not a second book. Folds into the last real book found
                # so far, same as any other shared back matter; if no
                # book has been found at all yet, there's nothing to
                # fold it into and it's simply dropped from
                # consideration, same as it would have been anywhere
                # else in the book.
                if books:
                    books[-1].end_index = end_cap
                break
            continue   # not a real book on its own (a title page, a teaser) -- merge forward into the next candidate,
                       # keeping THIS (earlier, so usually the more complete) title rather than the later one
        books.append(OmnibusBook(title=pending_title, start_index=pending_start, end_index=next_start))
        pending_start, pending_title = None, None

    if len(books) < 2:
        return None

    return OmnibusAnalysis(
        is_omnibus=True,
        confidence="medium",
        method="title-list",
        books=books,
        reason=(
            f"{len(books)} top-level table-of-contents entries, each covering a long enough span of the book "
            "to be a real title of its own, but none of them use numbered chapters this module recognizes."
        ),
    )


def detect(book) -> OmnibusAnalysis:
    """Runs every detection method in turn (see the module docstring
    for what each one looks for) and returns the first real match.
    `OmnibusAnalysis.is_omnibus` is False, confidence "none", if the
    book doesn't have a table of contents at all, or none of the three
    methods find a convincing split.

    Whichever method matches, the first book's start_index is forced
    to 0 regardless of where its own marker landed -- a cover page or
    other file with no table-of-contents entry of its own, sitting
    before the first marker any method finds, otherwise falls outside
    every book's range and is silently lost rather than carried along
    as shared front matter (confirmed a real bug, not hypothetical: it
    happened to both The Complete Tarzan Collection's own title page
    and, in a file with an unusually short lead candidate, an actual
    book title was the one that got dropped)."""
    if not book.toc:
        return OmnibusAnalysis(reason="This book has no table of contents to detect book boundaries from.")
    for method in (_detect_nested, _detect_restart, _detect_title_list):
        result = method(book)
        if result is not None:
            if result.books:
                result.books[0].start_index = 0
            return result
    return OmnibusAnalysis(reason="No chapter-numbering restart or repeated book-length structure was found.")
