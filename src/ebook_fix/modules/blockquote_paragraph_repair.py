"""
ebook_fix.modules.blockquote_paragraph_repair

Turns story paragraphs that a conversion wrote as <blockquote> back into
real <p> paragraphs. Found in "Altered Carbon" (a Calibre conversion from
MOBI): the last paragraph of every chapter is a
`<blockquote class="calibre5">` while every other paragraph is a
`<p class="calibre6">`. The blockquote carries a 2em left margin and a
1em first-line indent, so the end of each chapter sits visibly further in
than the text above it.

Why it happens: in the MOBI the last paragraph before a break carried an
indent tag, and the conversion turned that into a blockquote.

What this does, only in a book that is clearly built this way:
- A <blockquote> is converted when it holds only text and inline tags (no
  paragraphs or other blocks, no line breaks, no style attribute), sits
  directly on the page or in a plain wrapper div, and is the LAST paragraph
  of a run: the block before it is a body paragraph and the block after it
  is not (a line break, a page break, the end of the chapter, a heading).
  A real quotation inside the story is followed by more paragraphs, so it
  does not match.
- The book counts as "built this way" when at least 5 blockquotes of one
  class match and they are at least 70% of that class's blockquotes in the
  whole book. A book that uses blockquotes for real quotations, or has only
  a few matches, is left alone.
- The converted paragraph takes the book's own body paragraph class, so it
  looks exactly like the paragraphs above it. The id and any other
  attribute stay as they were, and the text is not changed (the text of
  every page is checked to be identical before and after).

Skipped on fixed-layout books. A second run finds nothing left to convert.
The class the blockquotes used becomes unused, so Unused CSS removes its
rule afterwards.
"""
from __future__ import annotations

from collections import Counter

from ebook_fix.config import BlockquoteParagraphConfig
from ebook_fix.report import Report

MIN_MATCHES = 5
MIN_SHARE = 0.70
MIN_BODY_PARAGRAPHS = 50

_INLINE = frozenset({
    "span", "i", "em", "b", "strong", "a", "sup", "sub", "u", "small",
    "big", "cite", "abbr", "q", "s", "strike", "font", "mark",
})
_WRAPPER_PARENTS = frozenset({"div", "body", "section"})
_NOT_BODY_P_ATTRS = ("data-ebookfix-chapter",)
_NBSP = "\xa0"


def _root(doc):
    return doc.getroot() if hasattr(doc, "getroot") else doc


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _text(el) -> str:
    return "".join(el.itertext()).replace(_NBSP, " ").strip()


def _page_text(doc) -> str:
    return "".join(_root(doc).itertext())


def _is_body_p(el, body_class) -> bool:
    return (
        _local(el) == "p"
        and (el.get("class") or "").strip() == body_class
        and not any(el.get(a) for a in _NOT_BODY_P_ATTRS)
    )


def _body_class(book):
    """The class attribute of the book's ordinary paragraphs, or None when
    no class clearly holds the body text."""
    counts = Counter()
    total = 0
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        for el in _root(doc).iter():
            if _local(el) != "p" or any(el.get(a) for a in _NOT_BODY_P_ATTRS):
                continue
            cls = (el.get("class") or "").strip()
            if not cls or "ebookfix-header-gap" in cls:
                continue
            if len(_text(el)) < 20:
                continue
            counts[cls] += 1
            total += 1
    if not counts:
        return None
    cls, n = counts.most_common(1)[0]
    if n < MIN_BODY_PARAGRAPHS or n < total * 0.5:
        return None
    return cls


def _next_element(el):
    nxt = el.getnext()
    while nxt is not None and not isinstance(nxt.tag, str):
        nxt = nxt.getnext()
    return nxt


def _prev_element(el):
    prev = el.getprevious()
    while prev is not None and not isinstance(prev.tag, str):
        prev = prev.getprevious()
    return prev


def _is_candidate(el, body_class) -> bool:
    if _local(el) != "blockquote":
        return False
    if el.get("style"):
        return False
    parent = el.getparent()
    if parent is None or _local(parent) not in _WRAPPER_PARENTS:
        return False
    for child in el.iter():
        if child is el:
            continue
        name = _local(child)
        if name not in _INLINE:
            return False
    if len(_text(el)) < 1:
        return False
    prev = _prev_element(el)
    if prev is None or not _is_body_p(prev, body_class):
        return False
    nxt = _next_element(el)
    if nxt is not None and _is_body_p(nxt, body_class):
        return False
    return True


def find_blockquote_paragraphs(book):
    """(body_class, {blockquote class: [elements]}, [(href, count)]) for the
    classes that qualify, or None when the book is not built this way."""
    body_class = _body_class(book)
    if not body_class:
        return None
    all_counts = Counter()
    matches = {}
    owners = {}
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        for el in _root(doc).iter():
            if _local(el) != "blockquote":
                continue
            cls = (el.get("class") or "").strip()
            all_counts[cls] += 1
            if cls and _is_candidate(el, body_class):
                matches.setdefault(cls, []).append(el)
                owners[el] = chapter
    plan = {}
    for cls, els in matches.items():
        if len(els) >= MIN_MATCHES and len(els) >= all_counts[cls] * MIN_SHARE:
            plan[cls] = els
    if not plan:
        return None
    per_page = Counter()
    for els in plan.values():
        for el in els:
            per_page[owners[el].href] += 1
    return body_class, plan, owners, per_page


def convert(book, body_class, plan, owners) -> int:
    """Turn the planned blockquotes into body paragraphs. Returns how many."""
    done = 0
    touched = {}
    for els in plan.values():
        for el in els:
            chapter = owners[el]
            doc = chapter.document
            if id(chapter) not in touched:
                touched[id(chapter)] = (chapter, _page_text(doc))
            tag = el.tag
            el.tag = tag[: tag.rfind("}") + 1] + "p" if "}" in tag else "p"
            el.set("class", body_class)
            done += 1
    for chapter, before in touched.values():
        if _page_text(chapter.document) != before:
            raise RuntimeError("Blockquote Paragraphs changed the text of a page")
        chapter.modified = True
    return done


class BlockquoteParagraphRepair:
    name = "Blockquote Paragraphs"

    def __init__(self, config: BlockquoteParagraphConfig | None = None):
        self.config = config or BlockquoteParagraphConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        found = find_blockquote_paragraphs(book)
        if not found:
            return report
        _body, _plan, _owners, per_page = found
        for href, n in per_page.items():
            report.add(
                href,
                "Paragraphs written as blockquotes",
                f"{n} story paragraph(s) written as blockquote will become normal paragraphs",
            )
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        found = find_blockquote_paragraphs(book)
        if not found:
            return report
        body_class, plan, owners, per_page = found
        pages = dict(per_page)
        convert(book, body_class, plan, owners)
        for href, n in pages.items():
            report.add(
                href,
                "Blockquote paragraphs recoded",
                f"{n} story paragraph(s) written as blockquote became normal paragraphs "
                f"(same look as the paragraphs above them)",
            )
        if hasattr(book, "mark_modified"):
            book.mark_modified()
        return report
