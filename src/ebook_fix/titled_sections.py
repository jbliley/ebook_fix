"""
ebook_fix.titled_sections

Chapter starts for a book whose sections have a title and nothing else: a
short-story collection or anthology where each story opens with a plain
heading ("THE GRAY CHAMPION.", "SUNDAY AT HOME.") and there is no
"Chapter 1" label and no number to count.

Why this exists: every other detector in chapters.py reads a label word or
a number. A collection of stories has neither, so the book was seen as one
long chapter and no story got its own page, even though the book's own
contents page links straight to every story. Found on Twice-Told Tales
(Hawthorne), converted from MOBI.

The contents page is the evidence. A list of titles is trusted only when
all of these hold:
  - one page holds at least MIN_TITLES links to places in the book,
  - at least MIN_MATCH_SHARE of those links land on a heading-sized
    element whose own text is the link text (ignoring case, punctuation
    and spacing),
  - those headings sit in the book in the same order as the contents
    list,
  - at least MIN_SAME_TAG_SHARE of them use the same tag (all <h3>, say),
  - the headings are not part of the contents list itself (no link inside
    them, not in a table or a list).

Nothing is invented: each boundary is a heading the author's own contents
page already points at. One extension: a heading that looks exactly like the
confirmed ones (same tag, same class and style, short, in capitals when the
confirmed ones are, outside any list) and sits between the first and last
confirmed heading is a story the contents page left out (the four Legends of
the Province-House in Twice-Told Tales, or a title followed by a footnote
mark), and is added too. The detector is a fallback. It only runs when
chapters.py found no numbered chapter run, so a book that detects cleanly
the normal way is never changed.
"""
from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote

from lxml import etree

MIN_TITLES = 5
MIN_MATCH_SHARE = 0.8
MIN_SAME_TAG_SHARE = 0.8
MAX_TITLE_WORDS = 14
MAX_EXTRA_SHARE = 0.25      # added headings may be at most this share of the confirmed ones
UPPER_SHARE = 0.9

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_TITLE_TAGS = _HEADING_TAGS | {"p", "div"}
_LIST_TAGS = {"table", "ul", "ol", "dl"}


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").replace("\xa0", " ").casefold()).strip()


def _text(el) -> str:
    return " ".join("".join(el.itertext()).replace("\xa0", " ").split())


def _ids_by_file(book) -> dict:
    """{href: {id: element}} using id attributes and <a name=...>."""
    found = {}
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None:
            continue
        ids = {}
        for el in doc.iter():
            if not isinstance(el.tag, str):
                continue
            frag = el.get("id")
            if frag:
                ids.setdefault(frag, el)
            if _local(el) == "a" and el.get("name"):
                ids.setdefault(el.get("name"), el)
        found[chapter.href] = ids
    return found


def _title_block(el):
    """The element that holds the title a link lands on: the element
    itself, or for an empty anchor the next element after it."""
    if el is None:
        return None
    if _local(el) == "a" and not _text(el):
        nxt = el.getnext()
        while nxt is not None and not isinstance(nxt.tag, str):
            nxt = nxt.getnext()
        return nxt
    return el


def _inside_list_like(el) -> bool:
    node = el.getparent()
    while node is not None:
        if _local(node) in _LIST_TAGS:
            return True
        node = node.getparent()
    return False


_FOOTNOTE_MARK = re.compile(r"\s*\[\w{1,3}\]\s*$")


def _is_upper(text: str) -> bool:
    letters = [ch for ch in _FOOTNOTE_MARK.sub("", text) if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)


def _look(el) -> tuple:
    return (_local(el), el.get("class") or "", el.get("style") or "")


def _with_look_alikes(chapters, matched, position) -> list:
    """`matched` ([(block, label)] from the contents page) plus the headings
    that look exactly like them and sit between the first and the last one
    but are missing from the contents list. Adds nothing when the look is
    not shared by every confirmed heading, or when there would be more than
    MAX_EXTRA_SHARE of extras (then the look is probably used for something
    else as well)."""
    if not matched:
        return matched
    counts = {}
    for b, _ in matched:
        counts[_look(b)] = counts.get(_look(b), 0) + 1
    look, share = max(counts.items(), key=lambda kv: kv[1])
    if share < UPPER_SHARE * len(matched):
        return matched
    upper = sum(1 for b, _ in matched if _is_upper(_text(b))) >= UPPER_SHARE * len(matched)
    first = position.get(matched[0][0])
    last = position.get(matched[-1][0])
    if first is None or last is None:
        return matched
    have = {b for b, _ in matched}
    extras = []
    for chapter in chapters:
        for el in chapter.document.iter():
            if not isinstance(el.tag, str) or el in have or _look(el) != look:
                continue
            at = position.get(el)
            if at is None or not (first < at < last):
                continue
            text = _text(el)
            if not text or len(text.split()) > MAX_TITLE_WORDS:
                continue
            if upper and not _is_upper(text):
                continue
            if _inside_list_like(el):
                continue
            # A footnote mark after the title ("THE GREAT CARBUNCLE.[4]") is a
            # link too; any other link makes this a contents line, not a title.
            if any(isinstance(d.tag, str) and _local(d) == "a" and d.get("href")
                   and not _FOOTNOTE_MARK.fullmatch(_text(d) or "") for d in el.iter()):
                continue
            extras.append((el, text))
    if not extras or len(extras) > MAX_EXTRA_SHARE * len(matched):
        return matched
    merged = matched + extras
    merged.sort(key=lambda item: position[item[0]])
    return merged


def find_titled_sections(book) -> list:
    """ChapterCandidate list (in book order, `confirmed` set) for a book
    whose titled sections its own contents page confirms, or []."""
    from ebook_fix.chapters import ChapterCandidate

    chapters = [c for c in book.chapters if getattr(c, "document", None) is not None]
    if len(chapters) < 1:
        return []
    spine_index = {c.href: i for i, c in enumerate(chapters)}
    ids = _ids_by_file(book)

    # Position of every element in reading order, for ordering checks.
    position = {}
    for i, chapter in enumerate(chapters):
        for n, el in enumerate(chapter.document.iter()):
            position[el] = (i, n)

    best = None  # (matched count, [(link_index, block, label)])
    for source in chapters:
        links = []
        for a in source.document.iter():
            if not isinstance(a.tag, str) or _local(a) != "a" or not a.get("href"):
                continue
            ref = a.get("href")
            if _SCHEME.match(ref) or "#" not in ref:
                continue
            path, _, frag = ref.partition("#")
            if not frag:
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(source.href), unquote(path))) if path else source.href
            if target not in ids:
                continue
            label = _text(a)
            if not label or len(label.split()) > MAX_TITLE_WORDS:
                continue
            links.append((target, unquote(frag), label))
        if len(links) < MIN_TITLES:
            continue

        matched = []
        seen = set()
        for target, frag, label in links:
            block = _title_block(ids[target].get(frag))
            if block is None or not isinstance(block.tag, str) or block in seen:
                continue
            if _local(block) not in _TITLE_TAGS or len(_text(block).split()) > MAX_TITLE_WORDS:
                continue
            if _norm(_text(block)) != _norm(label):
                continue
            if any(isinstance(d.tag, str) and _local(d) == "a" and d.get("href") for d in block.iter()):
                continue
            if _inside_list_like(block):
                continue
            seen.add(block)
            matched.append((block, label))

        if len(matched) < MIN_TITLES or len(matched) < MIN_MATCH_SHARE * len(links):
            continue
        # Same order as the contents list.
        positions = [position.get(b) for b, _ in matched]
        if any(p is None for p in positions) or positions != sorted(positions):
            continue
        tags = [_local(b) for b, _ in matched]
        top = max(set(tags), key=tags.count)
        if tags.count(top) < MIN_SAME_TAG_SHARE * len(tags):
            continue
        if best is None or len(matched) > best[0]:
            best = (len(matched), matched)

    if best is None:
        return []

    matched_blocks = _with_look_alikes(chapters, best[1], position)
    best = (len(matched_blocks), matched_blocks)

    href_of = {}
    for chapter in chapters:
        for el in chapter.document.iter():
            href_of[el] = chapter.href

    candidates = []
    for block, label in best[1]:
        tag = _local(block)
        href = href_of.get(block, "")
        candidate = ChapterCandidate(
            href=href,
            tag=tag,
            text=_FOOTNOTE_MARK.sub("", _text(block)),
            element_index=position[block][1],
            style=None,
            number=None,
            label_prefix=False,
            label_kind="titled",
            isolated=True,
            is_heading_tag=tag in _HEADING_TAGS,
            heading_level=int(tag[1]) if tag in _HEADING_TAGS else None,
            css_hint=False,
            score=1.0,
            confirmed=True,
            element=block,
        )
        candidates.append(candidate)
    return candidates


def add_titled_sections(book, summary) -> None:
    """Fallback for chapters.analyze_book_chapters: when no numbered run
    was found, adds the contents-confirmed titled sections to
    `summary.confirmed_boundaries` (merged with any Prologue/Epilogue
    already there, in book order). Does nothing when there are none."""
    if summary.best_sequence is not None:
        return
    found = find_titled_sections(book)
    if not found:
        return
    existing = list(summary.confirmed_boundaries or [])
    taken = {c.element for c in existing if c.element is not None}
    found = [c for c in found if c.element not in taken]
    if not found:
        return

    # Re-number book_order over everything, so the new sections sort
    # correctly against Part/Prologue markers numbered by the normal path.
    chapters = [c for c in book.chapters if getattr(c, "document", None) is not None]
    position = {}
    for i, chapter in enumerate(chapters):
        for n, el in enumerate(chapter.document.iter()):
            position[el] = (i, n)
    everyone = list(summary.candidates or []) + [c for c in found] + [
        c for c in existing if c not in (summary.candidates or [])
    ]
    everyone = [c for c in everyone if c.element in position]
    everyone.sort(key=lambda c: position[c.element])
    for order, c in enumerate(everyone, start=1):
        c.book_order = order

    merged = existing + found
    merged.sort(key=lambda c: c.book_order)
    summary.confirmed_boundaries = merged
