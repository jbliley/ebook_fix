"""
ebook_fix.link_index

One record per page of the page's links, ids and the other facts the link and
contents repairs read, kept by the analysis store so those repairs open only
the pages that can have something to do.

Dead Link Cleanup, TOC Cleanup, Page Titles and the link part of Cover Repair
each used to walk every element of every page to find the same kind of thing:
which links a page has and where they go, which ids and anchors it holds, which
pictures it shows, what its title says and whether it is an empty page. Those
are plain facts about one page's markup, so they are worked out once per page
and kept while the page is unchanged (see ebook_fix.finding_cache for exactly
when a kept record is trusted).

The record holds no elements. A repair that finds from its record that a page
may need work opens that page and does exactly what it always did there, so
the result is the same as when every page was read.

A page that cannot be loaded (document None) has an empty record with
`loaded=False`.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from ebook_fix.cover import _looks_like_cover_link

_OPS = "http://www.idpf.org/2007/ops"
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
# TOC Cleanup: links that start like this go outside the book.
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:")

# Dead Link Cleanup: a page linking to this many pages is a contents page, not
# a chapter, and the first few text blocks of a page count as its heading.
CONTENTS_LIKE_LINKS = 8
HEADING_BLOCKS = 3
BLOCK_NAMES = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "blockquote", "section"}
PAGE_BREAK_ID = re.compile(r"^calibre_pb_\d+$")


@dataclass(frozen=True)
class PageLinks:
    href: str = ""
    loaded: bool = True
    # Every id on the page, and the name of every <a name=...> anchor.
    ids: frozenset = frozenset()
    # The page is the EPUB 3 navigation document (a <nav epub:type="toc">).
    is_toc_nav: bool = False
    # Dead Link Cleanup: the (stripped) address of every <a> that could be a dead
    # link: not empty, not a bare "#" and without a scheme such as http:.
    dead_link_candidates: frozenset = frozenset()
    # Dead Link Cleanup: the files the page's links go to (fragment dropped),
    # whose number says whether the page is a contents page.
    link_target_files: frozenset = frozenset()
    # Dead Link Cleanup: for each of the first few text blocks of the page, its
    # words reduced to a key and the id that reaches it, as (key, anchor), for
    # keys of three characters or more.
    heading_blocks: tuple = ()
    # TOC Cleanup: the book paths of the files the page's links go to (links
    # that start with http:, mailto: and so on are left out).
    toc_link_paths: frozenset = frozenset()
    # TOC Cleanup: the page has a body with no text and no elements at all.
    empty_page: bool = False
    # Page Titles: the text of the <title> in the head (None when there is
    # none; an empty title is "").
    title_text: object = None
    # Cover Repair: the address of every <img> and SVG <image> as written
    # (pictures first, then SVG images), the address of every <link> and <a>
    # with no "#" and no scheme, and the same for the <link> tags that look
    # like a cover declaration.
    image_srcs: tuple = ()
    link_refs: tuple = ()
    cover_link_hrefs: tuple = ()


@dataclass
class BookLinks:
    chapters: list = field(default_factory=list)


def _root(document):
    return document.getroot() if hasattr(document, "getroot") else document


def _local_name(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def clean_text(text) -> str:
    """Whitespace-collapsed text, with non-breaking and zero-width spaces
    treated as nothing."""
    text = (text or "").replace(" ", " ").replace("​", "").replace("﻿", "")
    return " ".join(text.split())


def body_text(body) -> str:
    texts = []
    for el in body.iter():
        if not isinstance(el.tag, str):
            if el is not body and el.tail:
                texts.append(el.tail)
            continue
        texts.append(el.text or "")
        if el is not body:
            texts.append(el.tail or "")
    return clean_text("".join(texts))


def is_empty_document(document) -> bool:
    """True for a page with a body that holds no text and no elements, not even
    an empty wrapper (TOC Cleanup's idea of an empty page)."""
    body = document.find(".//{*}body")
    if body is None:
        return False
    if body_text(body):
        return False
    return not any(isinstance(el.tag, str) for el in body.iterdescendants())


def resolve_path(source_dir: str, href: str) -> str:
    """Book path of an address found inside a file in source_dir (fragment
    dropped); empty when the address has no file part."""
    path = (href or "").partition("#")[0]
    if not path:
        return ""
    return posixpath.normpath(posixpath.join(source_dir, path))


def heading_key(text) -> str:
    """A heading or link text reduced to its words: lowercase, no
    punctuation, no leading "Chapter". 'Chapter 12. "Tomorrow Thou
    Diest!"' and '12. "TOMORROW THOU DIEST!"' both become
    '12 tomorrow thou diest'."""
    words = re.findall(r"\w+", (text or "").casefold())
    if words and words[0] == "chapter":
        words = words[1:]
    return " ".join(words)


def anchor_for(block) -> str:
    """The id a link can use to reach this block: its own id, an anchor
    inside it, or an empty anchor just before it. Empty when there is none
    (the link then goes to the top of the page)."""
    candidates = []
    if block.get("id"):
        candidates.append(block.get("id"))
    for el in block.iter():
        if not isinstance(el.tag, str):
            continue
        value = el.get("id") or (el.get("name") if _local_name(el) == "a" else None)
        if value:
            candidates.append(value)
    previous = block.getprevious()
    for _ in range(2):
        if previous is None:
            break
        if isinstance(previous.tag, str):
            if previous.get("id") and not "".join(previous.itertext()).strip():
                candidates.append(previous.get("id"))
            if "".join(previous.itertext()).strip():
                break
        previous = previous.getprevious()
    # A page-break marker (calibre_pb_12) is the sort of thing other
    # repairs tidy away, so the heading's own anchor is preferred.
    for value in candidates:
        if not PAGE_BREAK_ID.match(value):
            return value
    return candidates[0] if candidates else ""


def top_blocks(document) -> list:
    """The first few text-bearing blocks of a page, as (element, text)."""
    root = _root(document)
    body = next((el for el in root.iter() if isinstance(el.tag, str) and _local_name(el) == "body"), root)
    found = []
    for el in body.iter():
        if not isinstance(el.tag, str) or _local_name(el) not in BLOCK_NAMES:
            continue
        if any(isinstance(child.tag, str) and _local_name(child) in BLOCK_NAMES for child in el.iterdescendants()):
            continue                      # only the innermost block holds the text
        text = " ".join("".join(el.itertext()).split())
        if text:
            found.append((el, text))
            if len(found) >= HEADING_BLOCKS:
                break
    return found


def analyze_link_page(href, document) -> PageLinks:
    if document is None:
        return PageLinks(href=href, loaded=False)
    root = _root(document)
    page_dir = posixpath.dirname(href)
    ids, candidates, target_files, toc_paths = set(), set(), set(), set()
    pictures, svg_images, link_refs, cover_links = [], [], [], []
    is_nav = False
    title_text = None
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = el.tag.rsplit("}", 1)[-1]
        lowered = local.lower()
        value = el.get("id")
        if value:
            ids.add(value)
        if local == "a":
            name = el.get("name")
            if name:
                ids.add(name)
            raw = el.get("href")
            if raw is not None:
                stripped = raw.strip()
                if stripped and stripped != "#" and not SCHEME.match(stripped):
                    candidates.add(stripped)
        if lowered == "a":
            stripped = (el.get("href") or "").strip()
            if stripped and not stripped.startswith("#") and not SCHEME.match(stripped):
                target_files.add(stripped.partition("#")[0])
            link = el.get("href") or ""
            if not link.startswith(EXTERNAL_PREFIXES):
                resolved = resolve_path(page_dir, link)
                if resolved:
                    toc_paths.add(resolved)
        if local == "nav" and el.get(f"{{{_OPS}}}type") == "toc":
            is_nav = True
        if local == "img":
            src = el.get("src")
            if src:
                pictures.append(src)
        if local == "image":
            ref = el.get(_XLINK_HREF) or el.get("href")
            if ref:
                svg_images.append(ref)
        if local in ("link", "a"):
            ref = el.get("href")
            if ref and "#" not in ref and not SCHEME.match(ref):
                link_refs.append(ref)
                if local == "link" and _looks_like_cover_link(el):
                    cover_links.append(ref)
        if title_text is None and lowered == "title":
            parent = el.getparent()
            if parent is not None and _local_name(parent) == "head":
                title_text = el.text or ""
    blocks = []
    for block, text in top_blocks(document):
        key = heading_key(text)
        if len(key) >= 3:
            blocks.append((key, anchor_for(block)))
    return PageLinks(
        href=href,
        loaded=True,
        ids=frozenset(ids),
        is_toc_nav=is_nav,
        dead_link_candidates=frozenset(candidates),
        link_target_files=frozenset(target_files),
        heading_blocks=tuple(blocks),
        toc_link_paths=frozenset(toc_paths),
        empty_page=is_empty_document(document),
        title_text=title_text,
        image_srcs=tuple(pictures + svg_images),
        link_refs=tuple(link_refs),
        cover_link_hrefs=tuple(cover_links),
    )


def analyze_book_links(book, cache=None) -> BookLinks:
    from ebook_fix.finding_cache import analyze_pages

    return analyze_pages(book, "links", analyze_link_page, BookLinks(), cache=cache)


def links_for(book, analysis=None) -> list:
    """The page records for `book` as it is now, in the order of
    `book.chapters`. They come from the analysis store the report carries when
    there is one, so a page that has not changed is not read again; without a
    store every page is read."""
    store = getattr(analysis, "store", None)
    if store is not None:
        return store.findings("links", book).chapters
    return analyze_book_links(book).chapters


def links_from_store(book, analysis=None):
    """The same records, but only when the report carries a store (None
    otherwise). For the repairs whose old code was cheap enough that reading
    every page just to make records would be slower."""
    store = getattr(analysis, "store", None)
    if store is None:
        return None
    return store.findings("links", book).chapters
