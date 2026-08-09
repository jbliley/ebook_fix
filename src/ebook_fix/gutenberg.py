"""
ebook_fix.gutenberg

Detects the standard Project Gutenberg disclaimer/license text bundled
at the front and back of a PG-sourced book. Descriptive only, same
"analysis, not repair" pattern as frontmatter.py/toc.py -- this finds
and records the boundary, a future repair module does the actual
removal (see docs/analysis_roadmap.md).

Two conversion eras, two shapes
--------------------------------
Investigated against two books already in examples/ and they don't
look alike at all:

- Modern "Ebookmaker" conversions (The Call of Cthulhu) wrap the front
  disclaimer in a `<header class="pg-boilerplate" id="pg-header">` and
  the back license in a `<footer class="pg-boilerplate" id="pg-footer">`,
  each containing a `*** START OF...`/`*** END OF...` marker line. The
  footer is usually its own whole spine file (clean, a future repair
  step can just drop the file); the header sits at the top of the
  SAME file as the real title page and story (needs a subtree
  removal, not a file exclusion).
- Older, plainer conversions (GutenbergText-ChapterSplit.epub, a Tom
  Sawyer text) carry the identical `*** START OF...`/`*** END OF...`
  marker text but as ordinary untagged `<p>` text, no wrapper element
  at all. Worse, the back matter isn't confined to one file either --
  the END marker lands mid-file, and the "Small Print" legal text
  that follows it carries on into a whole separate spine file with no
  marker of its own.

Because of that last point, this module keys off the marker TEXT
first -- present in both eras, load-bearing for the older one -- and
only uses the semantic tags as a confidence boost / fast path when
they're present. Note that the pg-boilerplate CSS class alone doesn't
tell front from back (Ebookmaker stamps the same class value on both
the header and the footer), so tag-based detection still classifies
each candidate by the marker text found inside it, falling back to
the id (pg-header/pg-footer) only if that search comes up empty.

Once the file containing the END marker is found, every spine entry
after it is treated as more back matter too, even without a marker of
its own -- safe to assume for a PG-sourced book, since PG's own
plain-text source never puts anything but its own license after that
line.

Scope / limitations
--------------------
Pattern-based, not exhaustive, same spirit as frontmatter.py's own
docstring on that. This covers the "*** START OF [THIS|THE] PROJECT
GUTENBERG EBOOK ... ***" / "*** END OF ..." marker format used from
the late 1990s onward, which is the overwhelming majority of the PG
catalog. The very old (pre-1997) "*END*THE SMALL PRINT!" style header
some early texts use is NOT covered here -- if that becomes worth
handling, it belongs in its own follow-up rather than folded into
these regexes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Tolerant of the exact number of asterisks and spacing, since not
# every era/tool formats the line identically.
_START_MARKER_RE = re.compile(
    r"\*{2,}\s*START OF (?:THIS|THE) PROJECT GUTENBERG EBOOK\b[^*]*\*{2,}",
    re.IGNORECASE,
)
_END_MARKER_RE = re.compile(
    r"\*{2,}\s*END OF (?:THIS|THE) PROJECT GUTENBERG EBOOK\b[^*]*\*{2,}",
    re.IGNORECASE,
)

# Semantic markup the modern "Ebookmaker" conversion pipeline uses --
# a fast path / confidence boost, never the only signal on its own
# (see module docstring for why the marker text has to carry the
# older conversions unassisted).
_BOILERPLATE_CLASS = "pg-boilerplate"
_HEADER_ID = "pg-header"
_FOOTER_ID = "pg-footer"


@dataclass
class GutenbergMarker:
    href: str = ""
    method: str = ""        # "tag" (semantic pg-boilerplate wrapper found) or "text" (bare marker line)
    marker_text: str = ""   # the actual matched START/END line, kept as evidence
    element: object = None  # live lxml reference to the wrapping tag (method "tag") or the
                             # marker's own paragraph (method "text") -- descriptive-only
                             # consumers can ignore it; a future repair module needs it to
                             # find the removal boundary without re-scanning the book.


@dataclass
class BookGutenbergSummary:
    detected: bool = False
    front: GutenbergMarker | None = None
    back: GutenbergMarker | None = None
    # Spine entries after the file the END marker lands in, folded in
    # as more back matter even though they carry no marker of their
    # own -- see module docstring. Always in spine order.
    trailing_back_matter_hrefs: list = field(default_factory=list)

    @property
    def front_found(self) -> bool:
        return self.front is not None

    @property
    def back_found(self) -> bool:
        return self.back is not None


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _spine_ordered_chapters(book):
    """Same local workaround frontmatter.py uses -- book.chapters isn't
    guaranteed spine order yet (see docs/analysis_roadmap.md), and
    telling "the real end marker" apart from a coincidental later match
    depends on actual reading order, so this builds its own list rather
    than trusting book.chapters' existing order."""
    by_id = {c.id: c for c in book.chapters}
    ordered = []
    seen = set()
    for idref in book.spine:
        chapter = by_id.get(idref)
        if chapter is not None:
            ordered.append(chapter)
            seen.add(id(chapter))
    for chapter in book.chapters:
        if id(chapter) not in seen:
            ordered.append(chapter)
    return ordered


def _real_spine_hrefs(book):
    """hrefs that are actually read in the spine (real reading order),
    as opposed to a manifest-only extra like a nav document that isn't
    part of the reading order at all. Needed because a manifest-only
    entry can still end up tacked onto the end of _spine_ordered_chapters
    -- see the trailing-back-matter sweep below, which must not treat
    the book's own nav/TOC file as leftover Gutenberg license text."""
    by_id = {c.id: c for c in book.chapters}
    hrefs = set()
    for idref in book.spine:
        chapter = by_id.get(idref)
        if chapter is not None:
            hrefs.add(chapter.href)
    return hrefs


def _element_class_list(el):
    return (el.get("class") or "").split()


def _tagged_candidates(tree):
    """Elements in this chapter carrying the modern conversion's
    semantic boilerplate markup: a pg-header/pg-footer id, or the
    pg-boilerplate class."""
    if tree is None:
        return []
    found = []
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        el_id = el.get("id") or ""
        if el_id in (_HEADER_ID, _FOOTER_ID) or _BOILERPLATE_CLASS in _element_class_list(el):
            found.append(el)
    return found


def _find_marker_element(tree, pattern):
    """Finds the most specific (innermost) element whose own text
    contains the marker line -- the older conversion's bare-paragraph
    case, where there's no wrapper tag to anchor on directly. Several
    ancestors up the tree will also technically match (their text
    includes the same substring), so this picks the one with the
    shortest total text as a stand-in for "narrowest containing
    element" rather than doing a full ancestor-exclusion walk."""
    if tree is None:
        return None, ""
    best = None
    best_text = ""
    best_len = None
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        text = "".join(el.itertext())
        match = pattern.search(text)
        if not match:
            continue
        if best_len is None or len(text) < best_len:
            best = el
            best_text = match.group(0).strip()
            best_len = len(text)
    return best, best_text


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

def analyze_book_gutenberg(book) -> BookGutenbergSummary:
    summary = BookGutenbergSummary()
    ordered = _spine_ordered_chapters(book)

    front = None
    back = None

    # Fast path: modern semantic markup. Each candidate is classified
    # by the marker text found inside it, falling back to its id only
    # when no marker text turns up (the class alone can't tell front
    # from back, see module docstring).
    for chapter in ordered:
        for el in _tagged_candidates(chapter.document):
            text = "".join(el.itertext())
            start_match = _START_MARKER_RE.search(text)
            end_match = _END_MARKER_RE.search(text)
            el_id = el.get("id") or ""
            if front is None and (start_match or el_id == _HEADER_ID):
                front = GutenbergMarker(
                    href=chapter.href,
                    method="tag",
                    marker_text=start_match.group(0).strip() if start_match else "",
                    element=el,
                )
            if back is None and (end_match or el_id == _FOOTER_ID):
                back = GutenbergMarker(
                    href=chapter.href,
                    method="tag",
                    marker_text=end_match.group(0).strip() if end_match else "",
                    element=el,
                )

    # Fallback: bare marker text with no wrapper tag (older conversions).
    if front is None:
        for chapter in ordered:
            el, matched = _find_marker_element(chapter.document, _START_MARKER_RE)
            if el is not None:
                front = GutenbergMarker(href=chapter.href, method="text", marker_text=matched, element=el)
                break

    if back is None:
        for chapter in ordered:
            el, matched = _find_marker_element(chapter.document, _END_MARKER_RE)
            if el is not None:
                back = GutenbergMarker(href=chapter.href, method="text", marker_text=matched, element=el)
                break

    summary.front = front
    summary.back = back
    summary.detected = front is not None or back is not None

    if back is not None:
        back_idx = next((i for i, c in enumerate(ordered) if c.href == back.href), None)
        if back_idx is not None:
            real_spine_hrefs = _real_spine_hrefs(book)
            summary.trailing_back_matter_hrefs = [
                c.href for c in ordered[back_idx + 1:]
                if c.href in real_spine_hrefs
            ]

    return summary
