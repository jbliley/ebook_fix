"""
ebook_fix.text_range

Shared "text between two points in a document" helpers. Originally
lived in structure.py (built for chapter word-span checks), pulled out
here once scene_breaks.py needed the exact same "is there real content
between this element and that one" logic -- same reasoning as
page_breaks.py's own docstring: two modules independently reinventing
this is exactly the kind of duplication worth avoiding.

All four functions work on plain lxml elements/documents and don't
know anything about chapters, candidates, or any other ebook_fix
concept -- deliberately generic.
"""

from __future__ import annotations


def text_from(element) -> list:
    """Every text chunk from `element` (inclusive of its own content)
    through the end of its document, as lxml "smart string" objects,
    in true document reading order.

    This is `.//text()` (descendant text) unioned with `following::
    text()` (everything after this element closes) -- lxml/libxml2
    keep `|` unions in document order, so this correctly includes
    things a naive iter()-based walk misses: an *ancestor's* tail text
    that falls after `element` in reading order but is only ever
    visited, in a plain pre-order walk, when that ancestor itself is
    reached (which happens before `element`, not after).
    """
    return element.xpath(".//text() | following::text()")


def text_range(start_element, end_element) -> str:
    """Text strictly from `start_element` (inclusive) up to
    `end_element` (exclusive), for two elements in the same document.

    Since `text_from(end_element)` is always a document-order suffix
    of `text_from(start_element)` (end comes later), the range is just
    the length difference between the two -- no need to compare
    individual nodes for identity/equality, which smart strings don't
    make reliable anyway.
    """
    at_or_after_start = text_from(start_element)
    at_or_after_end = text_from(end_element) if end_element is not None else []
    n = len(at_or_after_start) - len(at_or_after_end)
    return "".join(str(t) for t in at_or_after_start[:n])


def text_to_end_of_doc(element) -> str:
    return "".join(str(t) for t in text_from(element))


def text_strictly_after(element) -> list:
    """Every text chunk that comes after `element` *closes* -- unlike
    `text_from`, does NOT include `element`'s own descendant text.
    Useful when `element` is itself a heading/marker whose own label
    text ("Chapter One") shouldn't count as "content following this
    point" for whatever's being checked."""
    return element.xpath("following::text()")


def text_range_strictly_after(start_element, end_element) -> str:
    """Text after `start_element` closes (exclusive of its own
    descendant text -- see `text_strictly_after`) up to `end_element`
    (exclusive), for two elements in the same document. Same suffix-
    length trick as `text_range`, just anchored one step later."""
    at_or_after_start = text_strictly_after(start_element)
    at_or_after_end = text_from(end_element) if end_element is not None else []
    n = len(at_or_after_start) - len(at_or_after_end)
    return "".join(str(t) for t in at_or_after_start[:n])


def text_before(doc_root, end_element) -> str:
    """Everything in `doc_root`'s document before `end_element`
    (exclusive) -- the same suffix-length trick as `text_range`, just
    anchored at the start of the document instead of at another
    element."""
    full = doc_root.xpath(".//text()")
    at_or_after_end = text_from(end_element)
    n = len(full) - len(at_or_after_end)
    return "".join(str(t) for t in full[:n])
