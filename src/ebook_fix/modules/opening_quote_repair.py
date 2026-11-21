"""
ebook_fix.modules.opening_quote_repair

Puts back the opening quotation mark a conversion dropped from the first
paragraph of a chapter. Found in "Altered Carbon": Chapter Four opens with
`You seem ill at ease, Mr. Kovacs. Are you?”` and Chapter Twenty-One with
`You know anything about synamorphesterone?”`. Both have a closing curly
quote and no opening one, and the original MOBI already lacked it.

What this does, only in a book that uses curly quotes (at least 10 opening
and 10 closing ones):
- It looks only at the first paragraph after a chapter heading (blank gap
  paragraphs in between are skipped).
- The paragraph is fixed only when it starts with a letter, holds exactly
  one closing quote ” and no opening quote of any kind (“ ‘ or a straight
  ") and the closing quote ends the paragraph, right after . ? ! , or a
  dash or ellipsis. A chapter cannot open in the middle of a quotation, so
  the opening mark is certainly missing.
- “ is inserted at the very start of the paragraph's text. Nothing else
  changes, and the text of the page is checked to differ by exactly that
  one character.

Verse or quoted letters that open a chapter in italics with a closing mark
only at the end of a later paragraph are not touched, because the closing
quote must be in the first paragraph itself. Skipped on fixed-layout books.
A second run finds nothing left to fix.
"""
from __future__ import annotations

from ebook_fix.config import OpeningQuoteConfig
from ebook_fix.report import Report

MIN_CURLY = 10
MARKER_ATTR = "data-ebookfix-chapter"
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BEFORE_CLOSE = set(".?!,…—–-")
_NBSP = "\xa0"


def _root(doc):
    return doc.getroot() if hasattr(doc, "getroot") else doc


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _text(el) -> str:
    return "".join(el.itertext())


def _is_gap(el) -> bool:
    if "ebookfix-header-gap" in (el.get("class") or ""):
        return True
    return _local(el) in ("p", "div") and not _text(el).replace(_NBSP, "").strip() and len(el) == 0


def _first_paragraphs(root):
    """The first real paragraph after each chapter heading."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if not (el.get(MARKER_ATTR) or _local(el) in _HEADINGS):
            continue
        nxt = el.getnext()
        while nxt is not None and (not isinstance(nxt.tag, str) or _is_gap(nxt)):
            nxt = nxt.getnext()
        if nxt is not None and _local(nxt) == "p" and not nxt.get(MARKER_ATTR):
            yield nxt


def _needs_quote(p) -> bool:
    text = _text(p).replace(_NBSP, " ").strip()
    if not text or not text[0].isalpha():
        return False
    if text.count("”") != 1 or any(c in text for c in "“‘\""):
        return False
    end = text.rstrip()
    if not end.endswith("”") or len(end) < 3:
        return False
    return end[-2] in _BEFORE_CLOSE


def _insert_quote(p) -> None:
    for el in p.iter():
        if el.text and el.text.strip():
            lead = len(el.text) - len(el.text.lstrip())
            el.text = el.text[:lead] + "“" + el.text[lead:]
            return


def _curly_counts(book):
    opens = closes = 0
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        text = "".join(_root(doc).itertext())
        opens += text.count("“")
        closes += text.count("”")
    return opens, closes


def find_missing_quotes(book):
    """[(chapter, paragraph)] for each chapter opener missing its quote."""
    opens, closes = _curly_counts(book)
    if opens < MIN_CURLY or closes < MIN_CURLY:
        return []
    found = []
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        for p in _first_paragraphs(_root(doc)):
            if _needs_quote(p):
                found.append((chapter, p))
    return found


class OpeningQuoteRepair:
    name = "Opening Quotes"

    def __init__(self, config: OpeningQuoteConfig | None = None):
        self.config = config or OpeningQuoteConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for chapter, p in find_missing_quotes(book):
            snippet = _text(p).strip()[:40]
            report.add(chapter.href, "Chapter opens without its opening quote",
                       f"“ will be added at the start of: {snippet}")
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        found = find_missing_quotes(book)
        for chapter, p in found:
            before = _text(p)
            _insert_quote(p)
            if "".join(_text(p).split()) != "".join(("“" + before).split()):
                raise RuntimeError("Opening Quotes changed more than the one quote mark")
            chapter.modified = True
            report.add(chapter.href, "Opening quote restored",
                       f"Added the missing “ at the start of: {_text(p).strip()[:40]}")
        if found and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report
