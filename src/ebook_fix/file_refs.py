"""
ebook_fix.file_refs

One record per page of the files the page points at, kept by the analysis store
so Junk File Removal does not read every page to find out which pictures and
other files are in use.

Junk File Removal looks for duplicate pictures and an unused second copy of the
cover. Both need "what in the book refers to this file": every src, href, data
and poster on every page, and every url(...) in a style attribute or style
block. It used to walk every element of every page to build that, on each
analysis and each repair of every pass, for any book with a cover. That is a
plain fact about one page's markup, so it is worked out once per page and kept
while the page is unchanged (see ebook_fix.finding_cache for exactly when a kept
record is trusted).

The record holds no elements: only the raw address values, split into the ones a
picture (<img src>, SVG <image>) uses and every other one, exactly as the scan
used to classify them. Junk File Removal resolves them against the page's folder
as before. A picture that has to be moved to the kept copy needs the live
element, so only the pages whose record lists that picture are opened for that.

A page that cannot be loaded (document None) has an empty record with
`loaded=False`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)", re.IGNORECASE)
REFERENCE_ATTRS = ("src", "href", XLINK_HREF, "data", "poster")


def usable_value(value) -> bool:
    """A reference worth resolving: not empty, not external, not a data URI,
    in-page anchor or mail link."""
    return bool(value) and "://" not in value and not value.startswith(("data:", "#", "mailto:"))


def css_url_values(text):
    for match in CSS_URL.finditer(text):
        value = match.group(1).strip()
        if not value or "://" in value or value.startswith("data:"):
            continue
        yield value


def element_references(element):
    """(kind, attr, value) for every file reference on one element, where
    kind is "picture" (an <img src> or SVG <image> address), "other" (any other
    attribute address) or "css" (a url(...) in a style attribute or block)."""
    if not isinstance(element.tag, str):
        return
    local = element.tag.rsplit("}", 1)[-1].lower()
    for attr in REFERENCE_ATTRS:
        value = element.get(attr)
        if not usable_value(value):
            continue
        is_picture = (local == "img" and attr == "src") or (
            local == "image" and attr in (XLINK_HREF, "href")
        )
        yield ("picture" if is_picture else "other"), attr, value
    style_attr = element.get("style")
    if style_attr:
        for value in css_url_values(style_attr):
            yield "css", "style", value
    if local == "style" and element.text:
        for value in css_url_values(element.text):
            yield "css", "style", value


@dataclass(frozen=True)
class PageFileRefs:
    href: str = ""
    loaded: bool = True
    picture_values: frozenset = frozenset()
    other_values: frozenset = frozenset()


@dataclass
class BookFileRefs:
    chapters: list = field(default_factory=list)


def analyze_file_refs_page(href, document) -> PageFileRefs:
    if document is None:
        return PageFileRefs(href=href, loaded=False)
    pictures, others = set(), set()
    for element in document.iter():
        for kind, _attr, value in element_references(element):
            (pictures if kind == "picture" else others).add(value)
    return PageFileRefs(href=href, loaded=True,
                        picture_values=frozenset(pictures), other_values=frozenset(others))


def analyze_book_file_refs(book, cache=None) -> BookFileRefs:
    from ebook_fix.finding_cache import analyze_pages

    return analyze_pages(book, "file_refs", analyze_file_refs_page, BookFileRefs(), cache=cache)


def file_refs_for(book, analysis=None) -> list:
    """The page records for `book` as it is now, in the order of
    `book.chapters`; from the analysis store when there is one, otherwise every
    page is read."""
    store = getattr(analysis, "store", None)
    if store is not None:
        return store.findings("file_refs", book).chapters
    return analyze_book_file_refs(book).chapters
