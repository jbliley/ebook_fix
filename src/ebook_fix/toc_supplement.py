"""
ebook_fix.toc_supplement

Helpers for a table of contents that exists but covers only a small part
of the book: a TOC listing just the Parts (and maybe a Prologue) of a
novel that has forty chapters.

Found in Altered Carbon (Richard Morgan, converted from MOBI by Calibre):
the book's own contents had 7 entries (Acknowledgements, Prologue and the
five Parts) and none of the 42 chapters. Worse, each Part entry pointed at
an anchor Calibre left at the very end of the file before the Part's title
page, so tapping "PART 2" landed on the last paragraph of Chapter Eight.

Until now a TOC with more than two entries was treated as the book's own
real table of contents and left alone. TOC Generation (see
modules/toc_generation.py) now also rebuilds a sparse one from the
detected chapter structure, using the functions here to:

- decide when a TOC is sparse (fewer than half as many entries as the
  chapter analysis confirmed chapters);
- keep the original entries the detected structure has no place for
  (Acknowledgements, a Dedication, an Afterword) and slot them in at their
  position in the book instead of throwing them away;
- point the book's own in-text contents page (a list of links, one per
  line) at the corrected places, since it carries the same stale Part
  links as the NCX.
"""
from __future__ import annotations

import posixpath
import re

from ebook_fix.models import TocEntry

# A TOC with fewer than this share of the confirmed chapter count is sparse.
SPARSE_RATIO = 0.5
# Below this many confirmed chapters a short TOC is not suspicious.
MIN_CONFIRMED = 5
# A contents page needs at least this many matching links to be retargeted.
MIN_CONTENTS_LINKS = 3

_NON_WORD = re.compile(r"\W+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([:;,.!?])")


def is_sparse_toc(original_count: int, confirmed_count: int) -> bool:
    return (
        original_count > 0
        and confirmed_count >= MIN_CONFIRMED
        and original_count < confirmed_count * SPARSE_RATIO
    )


def _norm(label: str) -> str:
    return _NON_WORD.sub(" ", label or "").strip().casefold()


def _walk(entries):
    for entry in entries:
        yield entry
        yield from _walk(entry.children)


class _Positions:
    """Where an href sits in reading order: (spine index, element index)."""

    def __init__(self, book):
        self._spine = {}
        by_id = {c.id: c for c in book.chapters}
        for i, idref in enumerate(book.spine):
            chapter = by_id.get(idref)
            if chapter is not None:
                self._spine[chapter.href] = i
        self._docs = {c.href: c.document for c in book.chapters}
        self._ids: dict = {}

    def _id_index(self, file):
        if file not in self._ids:
            index = {}
            doc = self._docs.get(file)
            if doc is not None:
                for n, el in enumerate(doc.iter()):
                    if not isinstance(el.tag, str):
                        continue
                    for key in ("id", "name"):
                        value = el.get(key)
                        if value and value not in index:
                            index[value] = n
            self._ids[file] = index
        return self._ids[file]

    def of(self, href):
        file, _, fragment = (href or "").partition("#")
        spine_index = self._spine.get(file, 10 ** 6)
        offset = self._id_index(file).get(fragment, 0) if fragment else 0
        return spine_index, offset


def _insert(entries, entry, position, pos_of):
    """Put `entry` in reading order among `entries`. When it falls between
    a Part and the next top-level entry, it goes inside that Part."""
    at = len(entries)
    for i, other in enumerate(entries):
        if pos_of(other.href) > position:
            at = i
            break
    if 0 < at < len(entries) and entries[at - 1].children:
        _insert(entries[at - 1].children, entry, position, pos_of)
        return
    entries.insert(at, entry)


def merge_original_entries(book, generated, originals):
    """`generated` (the detected structure's entries) plus every original
    entry it has no counterpart for, in reading order. An original is a
    counterpart when its label matches a generated one or it points at the
    same place. Returns a new list; `generated` is not changed."""
    positions = _Positions(book)
    labels = {_norm(e.label) for e in _walk(generated)}
    places = {positions.of(e.href) for e in _walk(generated)}

    merged = [_copy(e) for e in generated]
    for original in _walk(originals):
        if not original.href or _norm(original.label) in labels:
            continue
        if positions.of(original.href) in places:
            continue
        _insert(merged, TocEntry(label=_tidy(original.label), href=original.href, children=[]),
                positions.of(original.href), positions.of)
    return merged


def _copy(entry):
    return TocEntry(label=_tidy(entry.label), href=entry.href, children=[_copy(c) for c in entry.children])


def _tidy(label):
    """"PART 1 : ARRIVAL" -> "PART 1: ARRIVAL". The Whitespace Normalizer
    fixes the same thing in the book's text and nav document, so a label
    built from the text before that ran must already match it."""
    return _SPACE_BEFORE_PUNCT.sub(r"\1", label or "")


def _resolve(from_href, target):
    file, sep, fragment = target.partition("#")
    if not file:
        return from_href, fragment
    return posixpath.normpath(posixpath.join(posixpath.dirname(from_href), file)), fragment


def retarget_contents_links(book, entries):
    """Fix the links of the book's own contents page (a run of paragraphs
    that are each nothing but one link) so each goes where the matching
    TOC entry goes. Returns the number of links changed."""
    wanted: dict = {}
    for entry in _walk(entries):
        if entry.href:
            wanted.setdefault(_norm(entry.label), []).append(entry.href)
    wanted = {k: v[0] for k, v in wanted.items() if k and len(set(v)) == 1}

    nav_chapter = getattr(book, "nav_chapter", None)
    total = 0
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None or chapter is nav_chapter:
            continue
        found = []
        for a in doc.iter("{*}a"):
            href = a.get("href")
            parent = a.getparent()
            if not href or parent is None:
                continue
            text = _norm("".join(a.itertext()))
            if text in wanted and _norm("".join(parent.itertext())) == text:
                found.append((a, text, href))
        if len({t for _a, t, _h in found}) < MIN_CONTENTS_LINKS:
            continue
        changed = 0
        for a, text, href in found:
            want_file, want_fragment = _resolve("", wanted[text])
            have_file, have_fragment = _resolve(chapter.href, href)
            if (have_file, have_fragment) == (want_file, want_fragment):
                continue
            relative = posixpath.relpath(want_file, posixpath.dirname(chapter.href) or ".")
            a.set("href", relative + (f"#{want_fragment}" if want_fragment else ""))
            changed += 1
        if changed:
            chapter.modified = True
            total += changed
    return total
