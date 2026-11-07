"""
ebook_fix.analysis_store

The one place an analysis keeps what it found, so the same findings can be
asked for again at any time without reading the whole book again.

Why this exists: repairs change the book, and a finding made before a repair
can be out of date after it. Until now each place that needed a finding to be
current worked that out for itself (the refresh tables in `engine.py`, the
confirming analysis at the start of each pass). The store does it once, in one
place: asking for a finding always gives the answer for the book as it is
right now, and only the pages that changed since it was last made are read
again. See `docs/single_analysis_plan.md` for where this is heading.

Two kinds of finding are stored today:

- Findings that depend on one page's markup and nothing else (whitespace,
  ellipsis, apostrophes and possessive candidates). Each is kept per page,
  with a fingerprint of the page, by PageFindingCache (see
  `ebook_fix.finding_cache` for exactly when a kept finding is trusted).
- Whole-book findings (chapters, front matter so far). Each is kept with a
  fingerprint of the whole book's state and the numbers of the findings it
  is made from, and is reused only while all of those are unchanged and
  every element it points at is still on a page. A fingerprint that covers
  the whole book is coarse on purpose: any change anywhere makes the finding
  again, so a kept answer can never be out of date. Finer inputs per finding
  come later in the plan, one finding at a time.

Chapters, front matter, paragraphs, line breaks, scene breaks, color and
fonts are stored this way. A finding made from other findings always asks the
store for them, so it is made from the book as it is now.

The analysis report carries its store (`report.store`), so a Fix that starts
from an analysis already made, such as the Overview's, keeps what that
analysis found. The store is not part of the report's saved form.
"""

from __future__ import annotations

import dataclasses
import hashlib

from lxml import etree

from ebook_fix.apostrophes import analyze_book_apostrophes, analyze_book_possessives
from ebook_fix.chapters import analyze_book_chapters
from ebook_fix.class_map import build_class_profiles
from ebook_fix.class_usage import analyze_book_class_usage
from ebook_fix.color import analyze_book_color
from ebook_fix.ellipsis import analyze_book_ellipsis
from ebook_fix.finding_cache import PageFindingCache, _root_of, page_fingerprint
from ebook_fix.fonts import analyze_book_font_usage
from ebook_fix.frontmatter import analyze_book_frontmatter
from ebook_fix.headings import chapter_marker_elements
from ebook_fix.linebreaks import analyze_book_linebreaks
from ebook_fix.paragraphs import analyze_book_paragraphs
from ebook_fix.scene_breaks import analyze_book_scene_breaks
from ebook_fix.toc_corroborated import find_corroborated_titles
from ebook_fix.whitespace import analyze_book_whitespace

# name -> the function that makes the whole-book finding for it. Every one of
# these depends on a single page's markup and nothing else, and takes the
# page cache as `cache=`. The name is also the attribute it is stored under
# on the analysis report.
PAGE_FINDINGS = {
    "whitespace": analyze_book_whitespace,
    "ellipsis": analyze_book_ellipsis,
    "apostrophes": analyze_book_apostrophes,
    "possessives": analyze_book_possessives,
    "class_usage": analyze_book_class_usage,
}


def _make_chapters(book, store, overrides):
    """Chapter findings as EPUBAnalyzer.analyze has always made them: the
    chapter starts, plus (when the book has no confirmed boundaries) the
    "1. TITLE" chapters the book's own contents page confirms."""
    chapters = analyze_book_chapters(book)
    if not chapters.confirmed_boundaries:
        try:
            chapters.corroborated_titles = find_corroborated_titles(book)[0]
        except Exception:
            chapters.corroborated_titles = []
    return chapters


def _make_frontmatter(book, store, overrides):
    return analyze_book_frontmatter(
        book, chapter_summary=store.findings("chapters", book), overrides=overrides
    )


def _make_paragraphs(book, store, overrides):
    return analyze_book_paragraphs(
        book,
        frontmatter_summary=store.findings("frontmatter", book),
        chapter_markers=chapter_marker_elements(store.findings("chapters", book)),
    )


def _make_linebreaks(book, store, overrides):
    return analyze_book_linebreaks(
        book,
        frontmatter_summary=store.findings("frontmatter", book),
        chapter_markers=chapter_marker_elements(store.findings("chapters", book)),
    )


def _make_scene_breaks(book, store, overrides):
    return analyze_book_scene_breaks(
        book,
        chapter_summary=store.findings("chapters", book),
        frontmatter_summary=store.findings("frontmatter", book),
    )


def _make_color(book, store, overrides):
    chapters = store.findings("chapters", book)
    frontmatter = store.findings("frontmatter", book)
    return analyze_book_color(
        book,
        chapter_summary=chapters,
        frontmatter_summary=frontmatter,
        class_profiles=store.class_profiles(book, chapters, frontmatter),
    )


def _make_fonts(book, store, overrides):
    chapters = store.findings("chapters", book)
    frontmatter = store.findings("frontmatter", book)
    return analyze_book_font_usage(
        book,
        chapter_summary=chapters,
        frontmatter_summary=frontmatter,
        class_profiles=store.class_profiles(book, chapters, frontmatter),
    )


# Whole-book findings: name -> (function making it, the findings it is made
# from). The function gets (book, store, overrides). What each reads from the
# book is covered by the book-state fingerprint (every page, the package
# files and everything this tool added or removed), so an answer is reused
# only when none of that has changed and none of the findings it is made
# from has been made again. The third entry says whether the person's own
# front matter choices (`overrides`) are an input to it.
BOOK_FINDINGS = {
    "chapters": (_make_chapters, (), False),
    "frontmatter": (_make_frontmatter, ("chapters",), True),
    "paragraphs": (_make_paragraphs, ("chapters", "frontmatter"), True),
    "linebreaks": (_make_linebreaks, ("chapters", "frontmatter"), True),
    "scene_breaks": (_make_scene_breaks, ("chapters", "frontmatter"), True),
    "color": (_make_color, ("chapters", "frontmatter"), True),
    "fonts": (_make_fonts, ("chapters", "frontmatter"), True),
}


def _elements_in(value, found, seen):
    """Collect every lxml element held anywhere inside a finding."""
    if isinstance(value, etree._Element):
        found.append(value)
        return
    if id(value) in seen:
        return
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        seen.add(id(value))
        for field in dataclasses.fields(value):
            _elements_in(getattr(value, field.name, None), found, seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        seen.add(id(value))
        for item in value:
            _elements_in(item, found, seen)
    elif isinstance(value, dict):
        seen.add(id(value))
        for item in value.values():
            _elements_in(item, found, seen)


def _branch_top(element):
    top = element
    parent = top.getparent()
    while parent is not None:
        top = parent
        parent = top.getparent()
    return top


def _hash_bytes(*parts) -> bytes:
    digest = hashlib.blake2b(digest_size=16)
    for part in parts:
        if isinstance(part, str):
            part = part.encode("utf-8", "replace")
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.digest()


def book_state(book) -> bytes:
    """A short digest of everything a whole-book finding can read: every
    page's markup, the package and contents documents, the book's own
    metadata, spine and contents list, and every file this tool has added,
    changed or removed."""
    parts = []
    for chapter in book.chapters:
        document = getattr(chapter, "document", None)
        parts.append(getattr(chapter, "href", ""))
        parts.append(page_fingerprint(document) if document is not None else b"")
    for document in (getattr(book, "opf_document", None), getattr(book, "ncx_document", None)):
        parts.append(etree.tostring(document) if document is not None else b"")
    parts.append(repr(getattr(book, "metadata", None)))
    parts.append(repr(getattr(book, "spine", None)))
    parts.append(repr(getattr(book, "toc", None)))
    parts.append(repr((book.toc_source, book.toc_generated, book.package_path, book.version)))
    for name in sorted(book.new_files):
        data = book.new_files[name]
        parts.append(name)
        parts.append(data if isinstance(data, (bytes, bytearray)) else repr(data))
    parts.append(repr(sorted(book.removed_files)))
    return _hash_bytes(*parts)


class AnalysisStore:
    """Findings kept for one book, current whenever they are asked for."""

    def __init__(self):
        self._pages = PageFindingCache()
        self._books = {}  # name -> (what it was made from, serial number, finding)
        self._serial = 0
        self.book_reused = 0
        self.book_recomputed = 0
        self.profiles_reused = 0
        self.profiles_recomputed = 0
        # The person's own front matter choices (Review tab) that the latest
        # analysis was asked to use; every finding made from front matter
        # uses them until an analysis is given others.
        self.overrides = None
        # The book's state is worked out once per request for a finding, not
        # once per finding it is made from (nothing changes the book while a
        # finding is being made).
        self._depth = 0
        self._state = None
        # Class profiles kept by (book state, the chapter and front matter
        # findings they were made from); a few are kept because a repair asks
        # with the report's findings while the analysis asks with the store's.
        self._profiles = {}

    def findings(self, name, book):
        """The finding called `name` for `book` as it is now. A page that has
        not changed since the finding was made for it keeps that answer; any
        other page is read again. A whole-book finding keeps its answer only
        while the book, the findings it is made from and `overrides` (the
        person's own front matter choices, `self.overrides`) are unchanged."""
        if name in PAGE_FINDINGS:
            return PAGE_FINDINGS[name](book, cache=self._pages)
        self._depth += 1
        try:
            return self._book_finding(name, book)
        finally:
            self._depth -= 1
            if self._depth == 0:
                self._state = None

    def _book_finding(self, name, book):
        make, needs, uses_overrides = BOOK_FINDINGS[name]
        overrides = self.overrides if uses_overrides else None
        if self._state is None:
            self._state = book_state(book)
        # Ask for what it is made from first, so their serial numbers are current.
        for dep in needs:
            self.findings(dep, book)
        wanted = (
            self._state,
            tuple(self._books[dep][1] for dep in needs),
            tuple(sorted(overrides.items())) if overrides else None,
        )
        entry = self._books.get(name)
        if entry is not None and entry[0] == wanted and self._still_attached(entry[2], book):
            self.book_reused += 1
            return entry[2]
        self.book_recomputed += 1
        value = make(book, self, overrides)
        self._serial += 1
        self._books[name] = (wanted, self._serial, value)
        return value

    def class_profiles(self, book, chapter_summary, frontmatter_summary):
        """The class profiles (see ebook_fix.class_map) made from the given
        chapter and front matter findings, for the book as it is now. Asked
        again for the same book state and the same two finding objects, the
        kept profiles are returned instead of walking every page again. The
        profiles hold no elements and the repairs only read them."""
        state = self._state if self._depth else book_state(book)
        key = (state, id(chapter_summary), id(frontmatter_summary))
        entry = self._profiles.get(key)
        if entry is not None:
            self.profiles_reused += 1
            return entry[0]
        self.profiles_recomputed += 1
        profiles = build_class_profiles(
            book, chapter_summary=chapter_summary, frontmatter_summary=frontmatter_summary
        )
        # The two findings are kept with the profiles so their ids cannot be
        # given to other objects while the entry exists.
        self._profiles[key] = (profiles, chapter_summary, frontmatter_summary)
        while len(self._profiles) > 4:
            self._profiles.pop(next(iter(self._profiles)))
        return profiles

    @staticmethod
    def _still_attached(value, book) -> bool:
        """True when every element a finding points at is still part of one of
        the book's pages."""
        roots = set()
        for chapter in book.chapters:
            document = getattr(chapter, "document", None)
            if document is not None:
                roots.add(id(_root_of(document)))
        found = []
        _elements_in(value, found, set())
        return all(id(_branch_top(element)) in roots for element in found)

    @property
    def reused(self) -> int:
        """How many page lookups were answered from what was kept."""
        return self._pages.reused

    @property
    def recomputed(self) -> int:
        """How many page lookups had to read the page."""
        return self._pages.recomputed
