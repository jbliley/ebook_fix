"""
ebook_fix.toc_corroborated

Table-of-contents entries for a book whose chapters start with a plain
numbered title ("1. OUT TO SEA") and nothing else, when the book's own
contents page confirms those chapters.

Why this exists: a numbered title with no "Chapter" label is a case 3
marker (see docs/xhtml_recoder_plan.md), which is always routed to manual
review because on its own it is weak evidence. It stops being weak when
the book's own contents page links straight to those headings: the author
already told us where each chapter starts. This was found on the books
split out of the Tarzan omnibus, where every chapter heading reads
"1. OUT TO SEA" and the contents page lists "Chapter 1. Out To Sea".

A candidate list is trusted only when all of these hold:
  - the numbers run 1, 2, 3 ... with no gaps and no repeats,
  - there are at least MIN_CHAPTERS of them,
  - at least MIN_CORROBORATED_SHARE of them are the target of a link from
    somewhere else in the book (the contents page).

Nothing is invented: each entry reuses the id the author's own link
already points at, and the label is the author's own link text.
"""
from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote

from ebook_fix.chapters import analyze_case3_book_chapters
from ebook_fix.models import TocEntry
from ebook_fix.toc_generate import _ensure_anchor_id

MIN_CHAPTERS = 5
MIN_CORROBORATED_SHARE = 0.8

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "section"}


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1]


def _link_targets(book) -> dict:
    """{(file href, fragment): link text} for every internal link that
    names a fragment, first one wins (the contents page comes first)."""
    targets = {}
    for chapter in book.chapters:
        if chapter.document is None:
            continue
        for a in chapter.document.iter():
            if not isinstance(a.tag, str) or _local(a) != "a" or not a.get("href"):
                continue
            ref = a.get("href")
            if _SCHEME.match(ref) or "#" not in ref:
                continue
            path, _, frag = ref.partition("#")
            if not frag:
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(chapter.href), unquote(path))) if path else chapter.href
            label = " ".join("".join(a.itertext()).replace("\xa0", " ").split())
            targets.setdefault((target, unquote(frag)), label)
    return targets


def _block_of(element):
    node = element
    for _ in range(4):
        if node is None:
            break
        if _local(node) in _BLOCKS:
            return node
        node = node.getparent()
    return element


def find_corroborated_titles(book):
    """(candidates, hits) for a numbered-title chapter list that the
    book's own contents page confirms, or ([], []) when it does not.
    `hits[i]` is (id, label) for candidate i when the contents page links
    to it, else None."""
    summary = analyze_case3_book_chapters(book)
    best = summary.best_sequence
    if best is None:
        return [], []
    candidates = list(best.candidates)
    numbers = [c.number for c in candidates]
    if len(candidates) < MIN_CHAPTERS or numbers != list(range(1, len(numbers) + 1)):
        return [], []

    targets = _link_targets(book)
    found = []          # (id, label) for the ones the contents page links to
    for c in candidates:
        block = _block_of(getattr(c, "element", None))
        hit = None
        if block is not None:
            for el in block.iter():
                frag = el.get("id") or (el.get("name") if _local(el) == "a" else None)
                if frag and (c.href, frag) in targets:
                    hit = (frag, targets[(c.href, frag)] or c.text)
                    break
        found.append(hit)
    if sum(1 for h in found if h) < max(MIN_CHAPTERS, MIN_CORROBORATED_SHARE * len(candidates)):
        return [], []
    return candidates, found


def build_corroborated_title_entries(book) -> list:
    """TocEntry list for a book whose numbered-title chapters are
    confirmed by its own contents page, or [] when they are not."""
    candidates, found = find_corroborated_titles(book)
    if not candidates:
        return []

    # The few the contents page does not link to still sit in an unbroken
    # 1..N run that the rest of the list confirms, so they get an entry too
    # (an id is added to the heading itself), rather than a silent gap.
    chapters_by_href = {ch.href: ch for ch in book.chapters}
    used = {}
    entries = []
    for number, (c, hit) in enumerate(zip(candidates, found), start=1):
        if hit:
            entries.append(TocEntry(label=hit[1], href=f"{c.href}#{hit[0]}"))
            continue
        chapter = chapters_by_href.get(c.href)
        if chapter is None or chapter.document is None or getattr(c, "element", None) is None:
            return []
        ids = used.setdefault(c.href, {e.get("id") for e in chapter.document.iter() if e.get("id")})
        anchor = _ensure_anchor_id(chapter, c.element, ids, f"toc-chapter-{number}")
        entries.append(TocEntry(label=c.text, href=f"{c.href}#{anchor}" if anchor else c.href))
    return entries
