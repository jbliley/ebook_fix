"""
ebook_fix.modules.contents_page_expand

Adds the missing chapters to a book's own "Table of Contents" page. Found in
"Altered Carbon": the page at the end of the book lists Acknowledgements,
the Prologue and the five Parts, but none of the 42 chapters that the
(rebuilt) table of contents now holds.

What this does, only when all of these are true:
- A page holds a contents list: a run of at least 3 neighbouring paragraphs
  (or list items) that are each nothing but one link.
- Every link on that list matches an entry of the book's table of contents
  by its label, in the same order, and the table's labels are unique (so no
  guessing which "Chapter 1" is meant).
- At least half of the entries already on the page are chapter titles
  ("CHAPTER ONE", "Chapter 12", "Part 2", "Prologue" and the like), so a
  page that lists book titles of a box set is not taken for a chapter list.
- The table of contents has at least 3 entries the list does not show that
  are chapter titles. A table that is missing only a Cover, a Title Page or
  a Dedication is left alone.

The missing entries are added in table of contents order, each as a copy of
a neighbouring entry (same class and markup) pointing at the entry's page
and anchor. Existing entries, their order and their position on the page
are not touched, and nothing else on the page changes. A second run finds
nothing missing. A list that holds anything the table of contents does not
(a link to the copyright page, say) is left alone.
"""
from __future__ import annotations

import copy
import posixpath
import re
from collections import Counter

from ebook_fix.config import ContentsPageConfig
from ebook_fix.report import Report
from ebook_fix.toc_refresh import refresh_book_toc
from ebook_fix.toc_supplement import MIN_CONTENTS_LINKS, _norm, _walk

MIN_MISSING = 3
_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    "fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
    "fifty|sixty|seventy|eighty|ninety|hundred"
)
_CHAPTER_LIKE = re.compile(
    r"^\W*(?:(?:chapter|part|book|section|interlude)\b|prologue\b|epilogue\b|"
    r"\d+\b|[ivxlcdm]+\b\W*$|(?:" + _NUMBER_WORDS + r")\b)",
    re.IGNORECASE,
)
_ITEMS = frozenset({"p", "li", "div"})


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _toc_entries(book):
    try:
        refresh_book_toc(book)
    except Exception:
        pass
    flat = [e for e in _walk(list(book.toc or [])) if e.href and _norm(e.label)]
    labels = [_norm(e.label) for e in flat]
    if len(set(labels)) != len(labels):
        return []
    return flat


def _link_items(doc):
    """Elements that hold nothing but one link, with the link."""
    items = []
    for a in doc.iter("{*}a"):
        parent = a.getparent()
        if parent is None or not a.get("href") or _local(parent) not in _ITEMS:
            continue
        if len([x for x in parent.iter("{*}a") if x.get("href")]) != 1:
            continue
        if _norm("".join(parent.itertext())) == _norm("".join(a.itertext())) and _norm("".join(a.itertext())):
            items.append((parent, a))
    return items


def _runs(items):
    """Groups of link items that are neighbours under one parent."""
    runs, current = [], []
    for parent, a in items:
        if current and (parent.getparent() is not current[-1][0].getparent()
                        or parent.getprevious() is not current[-1][0]):
            runs.append(current)
            current = []
        current.append((parent, a))
    if current:
        runs.append(current)
    return runs


def _make_item(template, label, href):
    item = copy.deepcopy(template)
    for el in item.iter():
        el.attrib.pop("id", None)
    for stray in [x for x in item.iter("{*}a") if not x.get("href")]:
        stray.getparent().remove(stray)
    a = next(item.iter("{*}a"))
    a.set("href", href)
    texts = [el for el in a.iter() if el.text and el.text.strip()]
    if texts:
        texts[0].text = label
        for el in texts[1:]:
            el.text = ""
    else:
        a.text = label
    for el in item.iter():
        if el is not item:
            el.tail = None
    item.tail = template.tail
    return item


def _plan(book):
    """[(chapter, run, missing)] for every contents list that qualifies."""
    flat = _toc_entries(book)
    if len(flat) < MIN_CONTENTS_LINKS + MIN_MISSING:
        return []
    by_label = {_norm(e.label): i for i, e in enumerate(flat)}
    nav = getattr(book, "nav_chapter", None)
    plans = []
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None or chapter is nav:
            continue
        for run in _runs(_link_items(doc)):
            if len(run) < MIN_CONTENTS_LINKS:
                continue
            idx = [by_label.get(_norm("".join(a.itertext()))) for _p, a in run]
            if None in idx or idx != sorted(set(idx)):
                continue
            missing = [e for i, e in enumerate(flat) if i not in set(idx)]
            like = [e for e in missing if _CHAPTER_LIKE.match(e.label or "")]
            shown_like = [1 for i in idx if _CHAPTER_LIKE.match(flat[i].label or "")]
            if len(shown_like) * 2 < len(idx):
                continue
            if len(missing) >= MIN_MISSING and len(like) >= MIN_MISSING:
                plans.append((chapter, run, idx, flat))
    return plans


def _apply(chapter, run, idx, flat) -> int:
    classes = Counter((p.get("class") or "") for p, _a in run)
    common = classes.most_common(1)[0][0]
    template = next(p for p, _a in run if (p.get("class") or "") == common)
    kept = {i: p for i, (p, _a) in zip(idx, run)}
    last = None
    added = 0
    base = posixpath.dirname(chapter.href) or "."
    for i, entry in enumerate(flat):
        if i in kept:
            last = kept[i]
            continue
        file, _sep, frag = entry.href.partition("#")
        href = posixpath.relpath(file, base) + (f"#{frag}" if frag else "")
        item = _make_item(template, entry.label, href)
        if last is None:
            run[0][0].addprevious(item)
        else:
            last.addnext(item)
        last = item
        added += 1
    return added


class ContentsPageExpand:
    name = "Contents Page"

    def __init__(self, config: ContentsPageConfig | None = None):
        self.config = config or ContentsPageConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for chapter, run, idx, flat in _plan(book):
            n = len(flat) - len(idx)
            report.add(chapter.href, "Contents page lists only part of the book",
                       f"The contents page shows {len(idx)} of {len(flat)} entries; {n} will be added")
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for chapter, run, idx, flat in _plan(book):
            before = "".join(chapter.document.itertext())
            n = _apply(chapter, run, idx, flat)
            if n:
                chapter.modified = True
                if hasattr(book, "mark_modified"):
                    book.mark_modified()
                report.add(chapter.href, "Contents page expanded",
                           f"Added {n} missing entr{'y' if n == 1 else 'ies'} to the book's own contents page")
        return report
