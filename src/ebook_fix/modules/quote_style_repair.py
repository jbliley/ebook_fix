"""
ebook_fix.modules.quote_style_repair

Makes the quotation marks in a book consistent. Found in "The Numbers
Killer": thousands of curly quotes (“ ” ’) with 18 straight double quotes
(") and 10 straight apostrophes (') scattered through the pages, which
look wrong next to their neighbours and break the pairing in some fonts.

What this does, only in a book that already uses curly quotes (straight
marks are at most a quarter of the double quotes, or of the single quotes,
and there are at least 10 curly ones to follow):
- A straight double quote becomes “ (opening) or ” (closing) from what
  stands around it, read across the whole paragraph, including text on
  either side of an <i> or <b> tag.
- A straight apostrophe inside a word (don't, Mary's) or at the end of one
  (the dogs' bones, closing quotes) becomes ’. An opening single quote
  becomes ‘ only when the book already uses ‘ and a closing mark follows in
  the same paragraph. Leading elisions ('em, 'tis, '90s) become ’.

What it will not do:
- Touch a book that mostly uses straight quotes, or a mixed one (no
  guessing which side is right).
- Convert a mark it cannot place: inch and foot marks (6", 5'), a quote with
  space or nothing on both sides, or an opening single quote that has no
  partner. Those stay straight.
- Change any word, only the quote characters, and never text inside
  <pre>, <code>, <script> or <style>.

A second run finds nothing left to convert.
"""
from __future__ import annotations

from ebook_fix.config import QuoteStyleConfig
from ebook_fix.report import Report

MIN_CURLY = 10
MAX_STRAIGHT_SHARE = 0.25

OPEN_D, CLOSE_D = "“", "”"
OPEN_S, CLOSE_S = "‘", "’"

_BLOCKS = frozenset({
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "dd", "dt",
    "blockquote", "div", "figcaption", "caption", "pre",
})
_SKIP = frozenset({"pre", "code", "script", "style", "svg", "math"})
_OPENERS = set("([{—–‘“/")
_DASHES = set("—–-")
_CLOSE_PUNCT = set(".,!?;:…)]}’”")
_ELISIONS = frozenset({
    "em", "tis", "twas", "twere", "twill", "twould", "til", "cause", "cos",
    "round", "bout", "nuff", "neath", "fore", "ere", "kay", "sup", "member",
})


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _slots(el):
    """Yield (element, 'text'|'tail') in document order for the text inside
    el, skipping do-not-touch subtrees (their tail still counts)."""
    if _local(el) in _SKIP:
        return
    yield el, "text"
    for child in el:
        yield from _slots(child)
        yield child, "tail"


def _leaf_blocks(root):
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el) not in _BLOCKS or _local(el) in _SKIP - {"pre"}:
            continue
        if _local(el) == "pre":
            continue
        if any(isinstance(d.tag, str) and _local(d) in _BLOCKS for d in el.iterdescendants()):
            continue
        yield el


def _get(el, which):
    return (el.text if which == "text" else el.tail) or ""


def _is_alnum(ch: str) -> bool:
    return bool(ch) and (ch.isalnum())


def _decide_double(prev: str, nxt: str):
    if prev and prev.isdigit():
        return None  # inches
    if prev in _DASHES:
        if _is_alnum(nxt):
            return OPEN_D
        return CLOSE_D
    if not prev or prev.isspace() or prev in _OPENERS:
        if not nxt or nxt.isspace():
            return None
        return OPEN_D
    if _is_alnum(prev) or prev in _CLOSE_PUNCT:
        return CLOSE_D
    return None


def _decide_single(text: str, i: int, uses_open: bool):
    prev = text[i - 1] if i > 0 else ""
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if prev and prev.isdigit() and not _is_alnum(nxt):
        return None  # feet
    if (prev and (prev.isalpha() or prev.isdigit())):
        return CLOSE_S  # don't, Mary's, dogs', closing quote
    if prev in _CLOSE_PUNCT and prev:
        return CLOSE_S
    if not prev or prev.isspace() or prev in _OPENERS or prev in _DASHES:
        if not _is_alnum(nxt):
            return None
        word = ""
        j = i + 1
        while j < len(text) and (text[j].isalpha() or text[j].isdigit()):
            word += text[j]
            j += 1
        if word.lower() in _ELISIONS or (word.isdigit() and len(word) == 2):
            return CLOSE_S
        if uses_open:
            # needs a closing mark later in the same paragraph
            for k in range(j, len(text)):
                if text[k] in ("'", CLOSE_S):
                    before = text[k - 1] if k > 0 else ""
                    after = text[k + 1] if k + 1 < len(text) else ""
                    if before and not before.isspace() and not _is_alnum(after):
                        return OPEN_S
        return None
    return None


class _Counts:
    def __init__(self):
        self.curly_d = self.straight_d = self.curly_s = self.straight_s = 0


def _tally(book) -> _Counts:
    c = _Counts()
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        root = doc.getroot() if hasattr(doc, "getroot") else doc
        for el, which in _slots(root):
            s = _get(el, which)
            if not s:
                continue
            c.curly_d += s.count(OPEN_D) + s.count(CLOSE_D)
            c.straight_d += s.count('"')
            c.curly_s += s.count(OPEN_S) + s.count(CLOSE_S)
            c.straight_s += s.count("'")
    return c


def _uses(curly: int, straight: int) -> bool:
    return (curly >= MIN_CURLY and straight > 0
            and straight <= MAX_STRAIGHT_SHARE * (curly + straight))


def _convert_block(block, do_double: bool, do_single: bool, uses_open: bool, write: bool) -> int:
    slots = [(el, w) for el, w in _slots(block)]
    pieces = [_get(el, w) for el, w in slots]
    text = "".join(pieces)
    if '"' not in text and "'" not in text:
        return 0
    out = list(text)
    changed = 0
    for i, ch in enumerate(text):
        new = None
        if ch == '"' and do_double:
            new = _decide_double(text[i - 1] if i > 0 else "", text[i + 1] if i + 1 < len(text) else "")
        elif ch == "'" and do_single:
            new = _decide_single(text, i, uses_open)
        if new:
            out[i] = new
            changed += 1
    if not changed or not write:
        return changed
    pos = 0
    for (el, which), piece in zip(slots, pieces):
        if not piece:
            continue
        new_piece = "".join(out[pos:pos + len(piece)])
        pos += len(piece)
        if new_piece != piece:
            if which == "text":
                el.text = new_piece
            else:
                el.tail = new_piece
    return changed


class QuoteStyleRepair:
    name = "Quote Style"

    def __init__(self, config: QuoteStyleConfig | None = None):
        self.config = config or QuoteStyleConfig()

    def _run(self, book, write: bool):
        counts = _tally(book)
        do_double = _uses(counts.curly_d, counts.straight_d)
        do_single = _uses(counts.curly_s, counts.straight_s)
        results = []
        if not (do_double or do_single):
            return results
        uses_open = any(
            OPEN_S in (_get(el, w))
            for chapter in book.chapters if chapter.document is not None
            for el, w in _slots(
                chapter.document.getroot() if hasattr(chapter.document, "getroot") else chapter.document)
        )
        for chapter in book.chapters:
            doc = chapter.document
            if doc is None:
                continue
            root = doc.getroot() if hasattr(doc, "getroot") else doc
            n = 0
            for block in list(_leaf_blocks(root)):
                n += _convert_block(block, do_double, do_single, uses_open, write)
            if n:
                results.append((chapter.href, n))
                if write:
                    chapter.modified = True
        return results

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for href, n in self._run(book, write=False):
            report.add(href, "Straight quotes in a book that uses curly quotes",
                       f"{n} straight quote mark(s) will be made curly")
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        results = self._run(book, write=True)
        for href, n in results:
            report.add(href, "Straight quotes made curly",
                       f"{n} straight quote mark(s) were made curly")
        if results and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report
