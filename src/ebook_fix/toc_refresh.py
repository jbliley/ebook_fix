"""
ebook_fix.toc_refresh

Keeps `book.toc` (the in-memory copy of the table of contents) in step
with the NCX or nav document after something edits those directly.

Why it exists: splitting a file rewrites the NCX (or nav document), and
adds an entry for any chapter that did not have one, but `book.toc` was
read once when the book was opened. Anything built later from `book.toc`
then left the new entries out. The visible case: EPUB 3 Upgrade builds
`nav.xhtml` from `book.toc`, so a book that was split (a Prologue split
off a copyright page, for example) ended up with its new chapters in the
NCX but missing from `nav.xhtml`. Found in Pilgrimage to Hell and in the
PartiallySplit-Synthetic fixture (5 NCX entries, 3 nav entries).
"""
from __future__ import annotations

import posixpath

from ebook_fix.parser import EPUBParser

EPUB_OPS_NS = "http://www.idpf.org/2007/ops"


def refresh_book_toc(book) -> None:
    parser = EPUBParser()
    entries = None

    if book.toc_source == "ncx" and book.ncx_document is not None:
        ncx_dir = posixpath.dirname(book.ncx_href or "")
        entries = parser._parse_ncx(book.ncx_document, ncx_dir)
    elif book.toc_source == "nav":
        nav = book.nav_chapter
        if nav is not None and nav.document is not None:
            toc_nav = None
            for candidate in nav.document.iter("{*}nav"):
                if candidate.get(f"{{{EPUB_OPS_NS}}}type") == "toc":
                    toc_nav = candidate
                    break
            ol = toc_nav.find("{*}ol") if toc_nav is not None else None
            if ol is not None:
                entries = parser._nav_ol(ol, posixpath.dirname(nav.href))

    if entries is not None:
        book.toc = parser._merge_split_labels(entries)
