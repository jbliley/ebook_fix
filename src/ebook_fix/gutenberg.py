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
docstring on that. Covers both the "*** START OF [THIS|THE] PROJECT
GUTENBERG EBOOK ... ***" / "*** END OF ..." marker format used from
the late 1990s onward (the overwhelming majority of the PG catalog)
and the older pre-1997 "Small Print" style some early texts use, which
has no start/end marker LINE at all -- see _SMALL_PRINT_END_RE/
_OLD_ETEXT_END_RE and the leading-front-matter sweep below (2026-09-13,
raised by Jacob against GutenbergText-HRule.epub; see
docs/analysis_roadmap.md for the full writeup).
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

# Older, pre-1997 conversions (GutenbergText-HRule.epub) don't use the
# "*** START/END OF ... EBOOK ***" marker line at all -- the front
# matter runs straight from a plain "The Project Gutenberg Etext of
# ..." paragraph through a donation appeal through the full "Legal
# Small Print" license text, with nothing marking its *start* any
# differently from an ordinary paragraph. What IS distinctive and
# stable across that era is how the Small Print block -- always the
# last thing before the real story -- ends, and how the book's own
# end is announced in plain prose rather than asterisked shouting.
# Tolerant of the exact asterisk placement/spacing PG's own texts
# don't agree on ("*END*THE SMALL PRINT!" vs "*END THE SMALL
# PRINT!"), same reasoning as the modern markers' own tolerance above.
_SMALL_PRINT_END_RE = re.compile(
    r"\*+\s*END\b[^*]{0,40}SMALL PRINT",
    re.IGNORECASE,
)
_OLD_ETEXT_END_RE = re.compile(
    r"\bEnd of (?:(?:this|the)\s+)?Project Gutenberg('?s)?\s+(?:E-?text|EBook)\b",
    re.IGNORECASE,
)

# Used only to vet a WHOLE spine file for the leading-front-matter
# sweep below -- much lower bar than the marker regexes above (this
# alone would false-positive constantly used against an arbitrary
# paragraph), safe here only because it's paired with the heading
# check: a file with no heading at all AND text that mentions Project
# Gutenberg by name is never going to be a real title page or story
# content by coincidence.
_MENTIONS_GUTENBERG_RE = re.compile(r"Project Gutenberg", re.IGNORECASE)

# Classifies a HEADING's own text (not a whole file's) as boilerplate
# rather than a real title -- "Information about Project Gutenberg"
# and "The Legal Small Print" both match, "Goldsmiths Friend Abroad
# Again" and "Adventures of Tom Sawyer, By Twain, Complete" (the
# calibre-injected real titles in the two example books) don't. Used
# by the leading-front-matter sweep to tell "a whole file whose only
# heading is itself a boilerplate section title" apart from "a whole
# file that happens to have a real title ahead of more boilerplate."
_BOILERPLATE_HEADING_RE = re.compile(r"project gutenberg|small print", re.IGNORECASE)

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
    # Whole spine files strictly before the file the front marker
    # lands in, still safe to treat as more front matter even though
    # they carry no marker of their own -- see the leading-front-matter
    # sweep in analyze_book_gutenberg. Always in spine order.
    leading_front_matter_hrefs: list = field(default_factory=list)
    # Leading files that DO have what looks like a real title heading
    # (so the whole file is kept, unlike leading_front_matter_hrefs
    # above) but where everything AFTER that heading is still more
    # Gutenberg boilerplate -- a list of GutenbergMarker (method
    # "leading_partial", element is the heading to keep, everything
    # after it under <body> is what a repair module should remove).
    leading_front_matter_partial: list = field(default_factory=list)
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


_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _element_class_list(el):
    return (el.get("class") or "").split()


def _first_heading(tree):
    """The first heading (h1-h6) in document order, and its own text
    -- or (None, None) if the document has none. Used by the leading-
    front-matter sweep to classify a whole file (see
    _BOILERPLATE_HEADING_RE above) rather than gutenberg_repair.py's
    own node-by-node heading guard, which walks backward from an
    actual marker instead of scanning a whole file for its first one."""
    if tree is None:
        return None, None
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag.split("}")[-1].lower() in _HEADING_TAGS:
            return el, "".join(el.itertext()).strip()
    return None, None


def _text_after_top_level(tree, el):
    """Text of everything after el's own top-under-body ancestor --
    mirrors gutenberg_repair.py's own _ancestor_under_body walk
    (kept separate since this module is analysis-only, never mutates
    the tree), used only to sanity-check that what follows a leading
    file's real-looking title heading is actually more Gutenberg
    boilerplate before agreeing to trim it away, rather than assuming
    so just because the file happens to come before the front
    marker's own file."""
    if tree is None or el is None:
        return ""
    body = tree.find(".//{*}body")
    if body is None:
        return ""
    node = el
    top = None
    while node is not None:
        parent = node.getparent()
        if parent is None:
            return ""
        if parent is body:
            top = node
            break
        node = parent
    if top is None:
        return ""
    parts = []
    sib = top.getnext()
    while sib is not None:
        parts.append("".join(sib.itertext()))
        sib = sib.getnext()
    return "".join(parts)


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
    element" rather than doing a full ancestor-exclusion walk.

    Also checks each element's own TAIL text (lxml's term for text
    that follows a self-closing or childless element but still
    precedes the next sibling) -- some markers turn out to be bare
    text directly under <body> with no wrapping tag at ALL, not even
    a <p>: GutenbergText-HRule.epub's "End of this Project Gutenberg
    Etext..." line sits as an <hr/>'s tail. That text isn't part of
    ANY element's own itertext() (tail text belongs to the parent's
    flow, not the preceding element's own subtree), so the loop above
    would only ever match on body/html themselves -- returning one of
    those as "the element to anchor a removal on" is useless, since
    _ancestor_under_body can't walk anything up from body itself.
    Returns immediately on a tail match rather than competing on the
    shortest-text heuristic above: the owning element is already about
    as specific an anchor as this ever gets."""
    if tree is None:
        return None, ""
    best = None
    best_text = ""
    best_len = None
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        tail_match = pattern.search(el.tail or "")
        if tail_match:
            return el, tail_match.group(0).strip()
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

    # Further fallback: pre-1997 style, no "*** START/END OF ... EBOOK
    # ***" line anywhere in the book at all -- see module docstring
    # and the two regexes' own comments above. Tried last since it's
    # the least specific of the three tiers.
    if front is None:
        for chapter in ordered:
            el, matched = _find_marker_element(chapter.document, _SMALL_PRINT_END_RE)
            if el is not None:
                front = GutenbergMarker(href=chapter.href, method="small_print", marker_text=matched, element=el)
                break

    if back is None:
        for chapter in ordered:
            el, matched = _find_marker_element(chapter.document, _OLD_ETEXT_END_RE)
            if el is not None:
                back = GutenbergMarker(href=chapter.href, method="old_etext_end", marker_text=matched, element=el)
                break

    summary.front = front
    summary.back = back
    summary.detected = front is not None or back is not None

    if front is not None:
        front_idx = next((i for i, c in enumerate(ordered) if c.href == front.href), None)
        if front_idx is not None:
            # Whole files strictly before the marker's own file.
            # Confirmed against GutenbergText-HRule.epub, where the
            # first two of its three files are nothing BUT boilerplate
            # (a front disclaimer page, then a donation-appeal page)
            # -- but each one has its OWN heading (calibre stamps one
            # in at every physical page-break point in this
            # conversion, not just at a real chapter/title start), so
            # a plain "no heading = safe to drop" rule isn't enough on
            # its own here the way it was for gutenberg_repair.py's
            # single-file, marker-anchored sweep. Three-way split per
            # file instead:
            real_spine_hrefs = _real_spine_hrefs(book)
            leading_drop = []
            leading_partial = []
            for chapter in ordered[:front_idx]:
                if chapter.href not in real_spine_hrefs:
                    continue
                heading_el, heading_text = _first_heading(chapter.document)
                text = "".join(chapter.document.itertext()) if chapter.document is not None else ""

                if heading_el is None:
                    # No heading at all -- confidently boilerplate only
                    # if it also mentions Project Gutenberg by name,
                    # same bar as everywhere else in this module.
                    if _MENTIONS_GUTENBERG_RE.search(text):
                        leading_drop.append(chapter.href)
                    continue

                if _BOILERPLATE_HEADING_RE.search(heading_text):
                    # The file's only heading is itself a boilerplate
                    # section title ("Information about Project
                    # Gutenberg"), not a real one -- whole file drops.
                    leading_drop.append(chapter.href)
                    continue

                # Heading looks like a real title (e.g. "Goldsmiths
                # Friend Abroad Again") -- keep the file AND the
                # heading, but only trim what follows it if that
                # remainder itself still mentions Project Gutenberg;
                # otherwise leave the whole file alone rather than
                # guess, same "unambiguous cases only" restraint as
                # everywhere else in this project.
                trailing_text = _text_after_top_level(chapter.document, heading_el)
                if _MENTIONS_GUTENBERG_RE.search(trailing_text):
                    leading_partial.append(GutenbergMarker(
                        href=chapter.href, method="leading_partial",
                        marker_text=heading_text, element=heading_el,
                    ))

            summary.leading_front_matter_hrefs = leading_drop
            summary.leading_front_matter_partial = leading_partial

    if back is not None:
        back_idx = next((i for i, c in enumerate(ordered) if c.href == back.href), None)
        if back_idx is not None:
            real_spine_hrefs = _real_spine_hrefs(book)
            summary.trailing_back_matter_hrefs = [
                c.href for c in ordered[back_idx + 1:]
                if c.href in real_spine_hrefs
            ]

    return summary
