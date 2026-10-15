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
- an element the chapter analysis confirmed as a chapter marker (a
  numbered chapter, an unnumbered one such as a Prologue or Epilogue, or a
  part marker). This
  matters because Chapter Markup runs after Paragraph Repair and Stray
  Line Break Removal, so a chapter title written as a styled <p> is not
  yet a heading when those two look at the space below it. The analysis
  has already found it and keeps a live reference to it.
"""
from __future__ import annotations

HEADING_TAGS = frozenset(("h1", "h2", "h3", "h4", "h5", "h6"))
MARKER_ATTR = "data-ebookfix-chapter"
# Stamped on the header block a person-approved split cut at (the Review
# tab's ticked boundaries). The chapter analysis can fail to find that
# header again once the book is split (a repeated word in the copyright
# page can win instead), so the split records its own answer.
SPLIT_MARKER_ATTR = "data-ebookfix-split"

_BLOCK_TAGS = HEADING_TAGS | frozenset(("p", "div", "li", "blockquote"))


def _local(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def chapter_marker_elements(chapter_summary) -> set:
    """The live elements of every chapter start the chapter analysis
    confirmed: the winning numbered sequence, plus unnumbered chapters
    such as a Prologue or Epilogue, plus part markers. (Looking only at
    the numbered sequence missed a Prologue, which is a chapter start the
    analysis confirms but that is not one of "Chapter One" to "Chapter
    Seventeen".) Empty when it found none. A set of the elements
    themselves, never id() values."""
    found: set = set()
    groups = []
    sequence = getattr(chapter_summary, "best_sequence", None)
    if sequence is not None:
        groups.append(sequence.candidates)
    for name in ("confirmed_boundaries", "unnumbered_chapters", "parts", "corroborated_titles"):
        groups.append(getattr(chapter_summary, name, None) or [])
    for candidates in groups:
        for candidate in candidates:
            element = getattr(candidate, "element", None)
            if element is not None:
                found.add(element)
                # A title inside a styled <p> ("<p><span>1. TITLE</span></p>") has
                # its paragraph as the real header block, and the detectors compare
                # whole blocks, so the block counts too.
                found.add(split_header_block(element))
    return found


def split_header_block(element):
    """The nearest block (heading, p, div, li, blockquote) at or above
    `element`: a marker is often an inline <strong> or <span> inside the
    real header paragraph."""
    node = element
    while node is not None:
        if isinstance(node.tag, str) and _local(node) in _BLOCK_TAGS:
            return node
        node = node.getparent()
    return element


def stamp_split_header(element) -> bool:
    """Record that a split was made at this header. Returns True if the
    attribute was added."""
    block = split_header_block(element)
    if block is None or not isinstance(block.tag, str) or block.get(SPLIT_MARKER_ATTR):
        return False
    block.set(SPLIT_MARKER_ATTR, "true")
    return True


def carries_split_stamp(element) -> bool:
    node = element
    while node is not None:
        if isinstance(node.tag, str) and node.get(SPLIT_MARKER_ATTR):
            return True
        node = node.getparent()
    return False


def is_marked_header(el) -> bool:
    """True for an element Chapter Markup or a split has marked."""
    return el is not None and isinstance(el.tag, str) and bool(
        el.get(MARKER_ATTR) or el.get(SPLIT_MARKER_ATTR)
    )


def is_chapter_header(el, markers=None) -> bool:
    if el is None or not isinstance(el.tag, str):
        return False
    if _local(el) in HEADING_TAGS or el.get(MARKER_ATTR) or el.get(SPLIT_MARKER_ATTR):
        return True
    return markers is not None and el in markers
