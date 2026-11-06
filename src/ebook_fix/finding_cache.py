"""
ebook_fix.finding_cache

Reuses a page's findings for as long as the page itself has not changed.

Whitespace, ellipsis and apostrophe findings are each worked out one page
at a time, from nothing but that page's own markup. During a Fix they are
worked out several times: before Apostrophe Repair, Ellipsis Normalizer
and Whitespace Normalizer run, again when the settling step runs them
once more, and once more in the confirming analysis at the start of the
next pass. Between two of those, a repair usually touches only a few
pages, yet each one used to read every page again.

PageFindingCache keeps the findings of each page together with a
fingerprint of that page's markup. A later request for the same page gets
the kept findings back only when all of the following still hold, and
otherwise the page is read again exactly as before:

- The page's markup serializes to the same bytes it did when the findings
  were made.
- The page's document is the same object (a page rebuilt as a new tree is
  always read again).
- Every element a finding points at is still part of that document.

Because the findings are a plain function of the page's markup, a page that
passes all three checks would give exactly the same findings if it were
read again, so using the kept ones changes nothing about what any repair
does. The cache lives for one repair run only, never longer.
"""

from __future__ import annotations

import hashlib

from lxml import etree


def _root_of(document):
    """The root element of a page document, whether it is held as a tree or
    as an element."""
    getroot = getattr(document, "getroot", None)
    if getroot is not None:
        return getroot()
    return document.getroottree().getroot()


def page_fingerprint(document) -> bytes:
    """A short digest of everything on the page that the per-page checks can
    see."""
    return hashlib.blake2b(etree.tostring(document), digest_size=16).digest()


def _all_elements_attached(summary, root) -> bool:
    """True when every element one of the findings points at still belongs to
    this page's document. Whitespace, ellipsis and apostrophe findings list
    their findings as `issues`; possessive candidates are `candidates`."""
    for issue in (*getattr(summary, "issues", ()), *getattr(summary, "candidates", ())):
        element = getattr(issue, "element", None)
        if element is None:
            return False
        try:
            # Walk up to the top of the element's own branch. A removed
            # element still names the document it came from, so asking it
            # for its document's root would wrongly say it is attached.
            top = element
            parent = top.getparent()
            while parent is not None:
                top = parent
                parent = top.getparent()
            if top is not root:
                return False
        except Exception:
            return False
    return True


class PageFindingCache:
    """Per-page findings kept between the analyses of one repair run."""

    def __init__(self):
        self._entries = {}
        self.reused = 0
        self.recomputed = 0

    def page(self, kind, href, document, compute):
        """The findings for one page. `kind` names the check ("whitespace",
        "ellipsis", "apostrophes"); `compute(href, document)` makes them from
        scratch when the kept ones cannot be trusted."""
        if document is None:
            return compute(href, document)

        root = _root_of(document)
        fingerprint = page_fingerprint(document)
        key = (kind, href)
        entry = self._entries.get(key)
        if (
            entry is not None
            and entry[0] == fingerprint
            and entry[1] is root
            and _all_elements_attached(entry[2], root)
        ):
            self.reused += 1
            return entry[2]

        self.recomputed += 1
        summary = compute(href, document)
        self._entries[key] = (fingerprint, root, summary)
        return summary


def analyze_pages(book, kind, analyze_chapter, summary, cache=None):
    """Fill `summary.chapters` with one finding set per page of `book`, using
    `cache` (a PageFindingCache) when one is given. Without a cache every page
    is read, as always."""
    for chapter in book.chapters:
        href = getattr(chapter, "href", "")
        document = getattr(chapter, "document", None)
        if cache is None:
            page = analyze_chapter(href, document)
        else:
            page = cache.page(kind, href, document, analyze_chapter)
        summary.chapters.append(page)
    return summary
