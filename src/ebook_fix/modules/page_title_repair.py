"""
ebook_fix.modules.page_title_repair

Replaces junk page titles. Found in "The Numbers Killer": all 42 chapter
files carry `<title>Unknown</title>` (a Calibre leftover). Some reading apps
and file browsers show that tag as the page's name, and EPUB validators
expect a real one.

What this does, for each text page of the book:
- If the `<title>` is missing, empty, or one of the usual placeholders
  (Unknown, Untitled, Title, None, No title, a bare file name like
  part0000_split_003.html), it is set to the page's first heading
  (h1 to h3, or a heading paragraph that Chapter Markup marked) when there is one, otherwise to the book's title.
- Any other title, however plain, is left exactly as it is.

Only the `<title>` text changes; nothing in the page body is touched, and
the table of contents is not read or changed. A second run finds nothing to
replace.
"""
from __future__ import annotations

import re

from lxml import etree

from ebook_fix.config import PageTitleConfig
from ebook_fix.report import Report

_JUNK = frozenset({
    "", "unknown", "untitled", "title", "none", "no title", "null", "document",
    "unknown title", "(unknown)", "[unknown]",
})
_FILE_LIKE = re.compile(r"^[\w\-. ]+\.(?:x?html?|xml)$", re.IGNORECASE)
_SPLIT_LIKE = re.compile(r"^(?:part|split|chapter|section|index)[\d_ \-]*(?:split[\d_ \-]*)?$", re.IGNORECASE)
MAX_LEN = 120


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def is_junk_title(text: str | None) -> bool:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    low = t.lower()
    return low in _JUNK or bool(_FILE_LIKE.match(t)) or bool(_SPLIT_LIKE.match(t))


def _root(doc):
    return doc.getroot() if hasattr(doc, "getroot") else doc


def _first_heading(root) -> str:
    for el in root.iter():
        if isinstance(el.tag, str) and (
            _local(el) in ("h1", "h2", "h3") or el.get("data-ebookfix-chapter")
        ):
            text = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
            if text and not is_junk_title(text):
                return text if len(text) <= MAX_LEN else text[:MAX_LEN].rstrip()
    return ""


def _book_title(book) -> str:
    meta = getattr(book, "metadata", None)
    title = getattr(meta, "title", None) or ""
    return re.sub(r"\s+", " ", title).strip()


def _title_el(root):
    for el in root.iter():
        if isinstance(el.tag, str) and _local(el) == "title":
            parent = el.getparent()
            if parent is not None and _local(parent) == "head":
                return el
    return None


def _head(root):
    for el in root:
        if isinstance(el.tag, str) and _local(el) == "head":
            return el
    return None


class PageTitleRepair:
    name = "Page Titles"

    def __init__(self, config: PageTitleConfig | None = None):
        self.config = config or PageTitleConfig()

    def _scan(self, book, write: bool):
        results = []
        fallback = _book_title(book)
        for chapter in book.chapters:
            doc = chapter.document
            if doc is None:
                continue
            root = _root(doc)
            title_el = _title_el(root)
            current = (title_el.text or "") if title_el is not None else None
            if current is not None and not is_junk_title(current):
                continue
            head = _head(root)
            if head is None:
                continue
            new = _first_heading(root) or fallback
            if not new or is_junk_title(new):
                continue
            results.append((chapter.href, current, new))
            if not write:
                continue
            if title_el is None:
                title_el = etree.SubElement(head, f"{{{etree.QName(head).namespace}}}title"
                                            if etree.QName(head).namespace else "title")
                head.remove(title_el)
                head.insert(0, title_el)
            title_el.text = new
            chapter.modified = True
        return results

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for href, old, new in self._scan(book, write=False):
            report.add(href, "Placeholder page title",
                       f"title {old!r} will become {new!r}" if old else f"missing title will be set to {new!r}")
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        results = self._scan(book, write=True)
        for href, old, new in results:
            report.add(href, "Placeholder page title replaced",
                       f"title {old!r} became {new!r}" if old else f"missing title set to {new!r}")
        if results and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report
