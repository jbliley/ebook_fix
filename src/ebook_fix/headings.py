"""
ebook_fix.headings

One shared answer to "is this element a chapter header?", used by the
detectors that must leave the blank space under a header alone
(ebook_fix.paragraphs and ebook_fix.linebreaks).

Why it exists: many books put a deliberate gap under each chapter title,
as a blank paragraph (<p>&#160;</p>) or a <br/> or two. Jacob's rule is
that this spacing is part of how the book looks and is never removed or
reduced. Removing it changes the look of the whole book.

An element counts as a chapter header when it is:

- a heading tag, <h1> to <h6>;
- an element Chapter Markup has already marked (data-ebookfix-chapter);
- an element the chapter analysis confirmed as a chapter marker. This
  matters because Chapter Markup runs after Paragraph Repair and Stray
  Line Break Removal, so a chapter title written as a styled <p> is not
  yet a heading when those two look at the space below it. The analysis
  has already found it and keeps a live reference to it.
"""
from __future__ import annotations

HEADING_TAGS = frozenset(("h1", "h2", "h3", "h4", "h5", "h6"))
MARKER_ATTR = "data-ebookfix-chapter"


def _local(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def chapter_marker_elements(chapter_summary) -> set:
    """The live elements of the chapters the chapter analysis confirmed
    (its winning sequence). Empty when it found none. A set of the
    elements themselves, never id() values."""
    found: set = set()
    sequence = getattr(chapter_summary, "best_sequence", None)
    if sequence is None:
        return found
    for candidate in sequence.candidates:
        if getattr(candidate, "element", None) is not None:
            found.add(candidate.element)
    return found


def is_chapter_header(el, markers=None) -> bool:
    if el is None or not isinstance(el.tag, str):
        return False
    if _local(el) in HEADING_TAGS or el.get(MARKER_ATTR):
        return True
    return markers is not None and el in markers
