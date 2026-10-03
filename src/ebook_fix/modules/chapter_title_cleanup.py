"""
ebook_fix.modules.chapter_title_cleanup

Two small clean-ups for books that already have a table of contents,
both found in "War Against the Mafia" (Don Pendleton):

1. A false chapter. A section heading in the middle of a chapter
   ("DESTRUCTION!") was cut into its own file by the book's original
   conversion, and that file got its own table of contents entry, so
   the contents list shows a chapter that isn't one. When the numbered
   chapters on either side count straight through the entry (2, then
   DESTRUCTION!, then 3), the entry cannot be a chapter. Its text is
   moved back onto the end of the chapter before it, the now-empty file
   is dropped, and the contents entry is removed. The heading itself
   stays where the author put it, as a heading inside the chapter.

2. Mixed hyphen spacing in numbered chapter titles. Some titles read
   "9 - The Lull" and others "9-The Lull". The majority style in the
   book wins (a tie goes to spaced), and the minority is rewritten to
   match. The fix is applied everywhere the title appears: the heading
   in the chapter file, the NCX, the EPUB 3 navigation document, and
   the in-memory contents list. `separator_style` in the config can
   force "spaced" or "tight" instead of "auto".

Only the hyphen and the spaces around it are touched, never the number
or the title. Only a title that begins with a number (optionally after
"Chapter", "Part", "Book" or "Section") and is followed by a hyphen and
a capitalized word of two or more letters is considered, so ordinary prose like "5-year plan"
is never rewritten, and only headings (a heading tag, or something with
title/chapter/heading in its class) near the top of a file are scanned.

Every merge is guarded. It is skipped (and the reason is reported) if
any of these is true:
- the entry has children, or points inside a file rather than at it;
- the file is not directly after the chapter that holds the previous
  numbered entry, or other contents entries also point at it;
- the entry's text doesn't match the first heading in its file;
- the two files wrap their content differently;
- the file contains internal links, or an element id that the chapter
  before it already uses;
- something other than the contents links to the whole file;
- the word count across both files changes after the move (undone).

Runs after Chapter Markup and before EPUB 3 Upgrade, because the EPUB 3
navigation document is built from the contents list this module fixes.
Re-running it on a repaired book finds nothing to do.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from ebook_fix.config import ChapterTitleCleanupConfig
from ebook_fix.report import Report
from ebook_fix.split_fragments import (
    _chain,
    _descend,
    _drop_file,
    _find_body,
    _local,
    _repoint_links,
    _word_count,
)

NCX_NS_URI = "http://www.daisy.org/z3986/2005/ncx/"
EPUB_OPS_NS = "http://www.idpf.org/2007/ops"
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
HEADING_CLASS_HINTS = ("title", "chapter", "head")
LEADING_BLOCKS_SCANNED = 4
MAX_HEADING_CHARS = 100

# An optional opening quote or bracket, a capital, then at least one more
# letter -- so "3-D glasses" (a lone capital) is never read as a title.
_TITLE_START = "[\"'\u201c\u2018(]?[A-Z][A-Za-z]"
_LEAD = r"(?:(?i:chapter|part|book|section)\s+)?"

# Whole heading/label text (already whitespace-normalized).
_NUMBERED_TITLE_RE = re.compile(
    rf"^(?P<lead>{_LEAD})(?P<num>\d{{1,4}})(?P<sep>\s*-\s*)(?P<title>{_TITLE_START}.*)$",
    re.S,
)
# The start of one text node, leading whitespace allowed.
_NODE_START_RE = re.compile(
    rf"^(?P<ws>\s*)(?P<lead>{_LEAD})(?P<num>\d{{1,4}})(?P<sep>\s*-\s*)(?={_TITLE_START})"
)

SEPARATORS = {"spaced": " - ", "tight": "-"}


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join((text or "").split())


def _sep_style(sep: str) -> str:
    if sep == " - ":
        return "spaced"
    if sep == "-":
        return "tight"
    return "other"


def _flatten(entries, parent=None):
    """(entry, containing_list) pairs, depth-first, in document order."""
    for entry in entries:
        yield entry, (parent if parent is not None else entries)
        if entry.children:
            yield from _flatten(entry.children, entry.children)


def _path_of(href: str) -> str:
    return (href or "").partition("#")[0]


def _is_nav_document(doc) -> bool:
    if doc is None:
        return False
    for nav in doc.iter("{*}nav"):
        if nav.get(f"{{{EPUB_OPS_NS}}}type") == "toc":
            return True
    return False


def _class_hint(el) -> bool:
    node = el
    while node is not None and isinstance(node.tag, str):
        if _local(node) == "body":
            break
        classes = (node.get("class") or "").lower()
        if any(hint in classes for hint in HEADING_CLASS_HINTS):
            return True
        node = node.getparent()
    return False


def _leading_headings(chapter):
    """Heading-like blocks within the first few text-bearing blocks of a
    file: (element, normalized text) pairs."""
    body = _find_body(chapter.document)
    if body is None:
        return []
    found = []
    seen_blocks = 0
    for el in body.iter():
        if not isinstance(el.tag, str):
            continue
        name = _local(el)
        if name not in HEADING_TAGS and name != "p":
            continue
        text = _normalize("".join(el.itertext()))
        if not text:
            continue
        seen_blocks += 1
        if name in HEADING_TAGS or _class_hint(el):
            found.append((el, text))
        if seen_blocks >= LEADING_BLOCKS_SCANNED:
            break
    return found


def _text_nodes(el):
    """(owner, 'text' or 'tail') slots inside `el` in true document
    order, not including el's own tail."""
    yield el, "text"
    for child in el:
        if isinstance(child.tag, str):
            yield from _text_nodes(child)
        yield child, "tail"


def _first_text_slot(el):
    for owner, attr in _text_nodes(el):
        value = getattr(owner, attr)
        if value and value.strip():
            return owner, attr, value
    return None


def _desired_text_for_node(value: str, desired_sep: str):
    """New text for one text node if it opens with a numbered title whose
    separator differs from `desired_sep`, else None."""
    m = _NODE_START_RE.match(value)
    if m is None or m.group("sep") == desired_sep:
        return None
    return value[: m.start("sep")] + desired_sep + value[m.end("sep"):]


def _rewrite_element(el, desired_sep: str) -> bool:
    slot = _first_text_slot(el)
    if slot is None:
        return False
    owner, attr, value = slot
    new_value = _desired_text_for_node(value, desired_sep)
    if new_value is None:
        return False
    setattr(owner, attr, new_value)
    return True


def _rewrite_label(label: str, desired_sep: str):
    """New label text for a whole (possibly whitespace-padded) label, or
    None if it needs no change."""
    m = _NODE_START_RE.match(label or "")
    if m is None or m.group("sep") == desired_sep:
        return None
    return label[: m.start("sep")] + desired_sep + label[m.end("sep"):]


# ---------------------------------------------------------------------
# Separator style
# ---------------------------------------------------------------------


def _numbered_style(text: str):
    m = _NUMBERED_TITLE_RE.match(_normalize_keep_sep(text))
    return _sep_style(m.group("sep")) if m else None


def _normalize_keep_sep(text: str) -> str:
    """Collapses runs of whitespace to one space, keeps single spaces
    around a hyphen visible so the separator can be classified."""
    return re.sub(r"\s+", " ", (text or "").strip())


def _toc_label_nodes(book):
    """Every place a contents label lives, as (kind, owner, element):
    kind 'ncx' / 'nav' hold a live element; 'entry' holds a TocEntry."""
    if book.ncx_document is not None:
        for text_el in book.ncx_document.iter(f"{{{NCX_NS_URI}}}text"):
            parent = text_el.getparent()
            if parent is not None and _local(parent) == "navlabel":
                yield "ncx", None, text_el
    for chapter in book.chapters:
        if _is_nav_document(chapter.document):
            for nav in chapter.document.iter("{*}nav"):
                if nav.get(f"{{{EPUB_OPS_NS}}}type") != "toc":
                    continue
                for a in nav.iter("{*}a"):
                    yield "nav", chapter, a
    for entry, _ in _flatten(book.toc):
        yield "entry", None, entry


def _collect_styles(book) -> list:
    styles = []
    for kind, _owner, node in _toc_label_nodes(book):
        if kind == "entry":
            style = _numbered_style(node.label)
        else:
            style = _numbered_style("".join(node.itertext()))
        if style:
            styles.append(style)
    for chapter in book.chapters:
        if chapter.document is None or _is_nav_document(chapter.document):
            continue
        for _el, text in _leading_headings(chapter):
            style = _numbered_style(text)
            if style:
                styles.append(style)
    return styles


def resolve_target_separator(book, config):
    """The separator string to standardize on, or None when there is
    nothing to decide (fewer than 3 numbered titles in auto mode)."""
    mode = getattr(config, "separator_style", "auto")
    if mode in SEPARATORS:
        return SEPARATORS[mode]
    styles = _collect_styles(book)
    if len(styles) < 3:
        return None
    spaced = styles.count("spaced")
    tight = styles.count("tight")
    return SEPARATORS["spaced"] if spaced >= tight else SEPARATORS["tight"]


def find_separator_fixes(book, desired_sep: str) -> list:
    """(location, old_text) for every title that needs its hyphen
    spacing changed. Does not modify anything."""
    fixes = []
    seen_toc = set()
    for kind, _owner, node in _toc_label_nodes(book):
        text = node.label if kind == "entry" else "".join(node.itertext())
        if _rewrite_label(text, desired_sep) is not None:
            # NCX, nav and the in-memory list all describe the same
            # entry, so count each distinct label once.
            key = _normalize(text)
            if key not in seen_toc:
                seen_toc.add(key)
                fixes.append(("table of contents", _normalize(text)))
    for chapter in book.chapters:
        if chapter.document is None or _is_nav_document(chapter.document):
            continue
        for el, text in _leading_headings(chapter):
            slot = _first_text_slot(el)
            if slot is None:
                continue
            if _desired_text_for_node(slot[2], desired_sep) is not None:
                fixes.append((chapter.href, text))
    return fixes


def apply_separator_fixes(book, desired_sep: str) -> int:
    """Rewrites every title found by find_separator_fixes. Returns the
    number of places changed."""
    changed = 0
    ncx_changed = False

    for kind, owner, node in _toc_label_nodes(book):
        if kind == "entry":
            new_label = _rewrite_label(node.label, desired_sep)
            if new_label is not None:
                node.label = new_label
                changed += 1
        elif kind == "ncx":
            if _rewrite_element(node, desired_sep):
                ncx_changed = True
                changed += 1
        elif kind == "nav":
            if _rewrite_element(node, desired_sep):
                owner.modified = True
                book.mark_modified()
                changed += 1

    if ncx_changed:
        book.ncx_modified = True
        book.mark_modified()

    for chapter in book.chapters:
        if chapter.document is None or _is_nav_document(chapter.document):
            continue
        for el, _text in _leading_headings(chapter):
            if _rewrite_element(el, desired_sep):
                chapter.modified = True
                book.mark_modified()
                changed += 1
        new_title = _rewrite_label(chapter.title, desired_sep)
        if new_title is not None:
            chapter.title = new_title

    return changed


# ---------------------------------------------------------------------
# False chapters
# ---------------------------------------------------------------------


@dataclass
class FalseChapter:
    entry: object          # the TocEntry for the false chapter
    entry_list: list       # the list that holds `entry`
    file_chapter: object   # Chapter for the file it points at
    target_chapter: object  # Chapter right before it in the spine
    label: str
    blocker: str | None = None


def _numbered_number(label: str):
    m = _NUMBERED_TITLE_RE.match(_normalize_keep_sep(label))
    return int(m.group("num")) if m else None


def _looks_structural(label: str) -> bool:
    """Prologue / Epilogue / Book One / Part Two and friends."""
    from ebook_fix.chapters import _classify
    try:
        return _classify(_normalize(label)) is not None
    except Exception:
        return False


def find_false_chapters(book) -> list:
    flat = list(_flatten(book.toc))
    by_href = {c.href: (i, c) for i, c in enumerate(book.chapters)}
    results = []

    for i in range(1, len(flat) - 1):
        entry, holder = flat[i]
        prev_entry = flat[i - 1][0]
        next_entry = flat[i + 1][0]
        label = _normalize(entry.label)
        if not label or entry.children:
            continue
        if _numbered_number(entry.label) is not None or _looks_structural(entry.label):
            continue
        prev_num = _numbered_number(prev_entry.label)
        next_num = _numbered_number(next_entry.label)
        if prev_num is None or next_num is None or next_num != prev_num + 1:
            continue

        href = entry.href or ""
        path = _path_of(href)
        if not path or "#" in href:
            continue
        located = by_href.get(path)
        if located is None:
            continue
        index, file_chapter = located
        target = book.chapters[index - 1] if index > 0 else None
        if target is None:
            continue

        candidate = FalseChapter(entry, holder, file_chapter, target, label)
        candidate.blocker = _merge_blocker(book, candidate, prev_entry, flat)
        results.append(candidate)

    return results


def _first_heading_text(chapter) -> str:
    headings = _leading_headings(chapter)
    return headings[0][1] if headings else ""


def _merge_blocker(book, cand: FalseChapter, prev_entry, flat):
    file_chapter = cand.file_chapter
    target = cand.target_chapter

    if _path_of(prev_entry.href) != target.href:
        return "the chapter before it isn't in the file directly ahead of it"

    pointing = [e for e, _ in flat if _path_of(e.href) == file_chapter.href]
    if len(pointing) != 1:
        return "other contents entries also point at this file"

    if _is_nav_document(target.document) or _is_nav_document(file_chapter.document):
        return "one of the files is the navigation document"
    if file_chapter.document is None or target.document is None:
        return "a file has no parsed document"

    if _first_heading_text(file_chapter).lower() != cand.label.lower():
        return "its text doesn't match the first heading in its file"

    body_f = _find_body(file_chapter.document)
    body_t = _find_body(target.document)
    if body_f is None or body_t is None:
        return "a file has no <body>"

    if posixpath.dirname(target.href) != posixpath.dirname(file_chapter.href):
        return "the two files are in different folders"

    tags_t = [_local(el) for el in _chain(body_t)]
    tags_f = [_local(el) for el in _chain(body_f)]
    if tags_t != tags_f:
        return "the two files wrap their content differently"
    container_f = _descend(body_f, tags_f)
    container_t = _descend(body_t, tags_t)
    if container_f is None or container_t is None:
        return "the two files wrap their content differently"

    for el in container_f.iter():
        if _local(el) == "a":
            target_href = el.get("href") or ""
            if target_href and not target_href.startswith(EXTERNAL_PREFIXES) and target_href.startswith("#"):
                return "it contains links to places within its own file"

    ids_t = {el.get("id") for el in body_t.iter() if isinstance(el.tag, str) and el.get("id")}
    ids_f = {el.get("id") for el in body_f.iter() if isinstance(el.tag, str) and el.get("id")}
    if ids_t & ids_f:
        return "it uses an element id the chapter before it already uses"

    if _whole_file_link_exists(book, file_chapter.href):
        return "something other than the contents links to the whole file"

    return None


def _whole_file_link_exists(book, href: str) -> bool:
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None or chapter.href == href or _is_nav_document(doc):
            continue
        for el in doc.iter():
            if _local(el) != "a":
                continue
            link = el.get("href") or ""
            if link.startswith(EXTERNAL_PREFIXES):
                continue
            path, _, fragment = link.partition("#")
            if path == href and not fragment:
                return True
    opf = getattr(book, "opf_document", None)
    if opf is not None:
        for el in opf.iter():
            if not isinstance(el.tag, str) or _local(el) == "item":
                continue
            if _path_of(el.get("href") or "") == href:
                return True
    return False


def _remove_from_ncx(book, file_href: str) -> bool:
    ncx = book.ncx_document
    if ncx is None:
        return False
    ncx_dir = posixpath.dirname(book.ncx_href or "")
    removed = False
    for point in list(ncx.iter(f"{{{NCX_NS_URI}}}navPoint")):
        content = point.find(f"{{{NCX_NS_URI}}}content")
        src = (content.get("src") if content is not None else "") or ""
        path, _, fragment = src.partition("#")
        if not path or fragment:
            continue
        if posixpath.normpath(posixpath.join(ncx_dir, path)) != file_href:
            continue
        if point.find(f"{{{NCX_NS_URI}}}navPoint") is not None:
            continue
        parent = point.getparent()
        previous = point.getprevious()
        if previous is not None:
            previous.tail = point.tail
        elif parent is not None:
            parent.text = point.tail
        parent.remove(point)
        removed = True

    if removed:
        points = list(ncx.iter(f"{{{NCX_NS_URI}}}navPoint"))
        if any(p.get("playOrder") for p in points):
            for n, p in enumerate(points, start=1):
                p.set("playOrder", str(n))
        book.ncx_modified = True
        book.mark_modified()
    return removed


def _remove_from_nav(book, file_href: str) -> bool:
    removed = False
    for chapter in book.chapters:
        if not _is_nav_document(chapter.document):
            continue
        nav_dir = posixpath.dirname(chapter.href)
        for nav in chapter.document.iter("{*}nav"):
            if nav.get(f"{{{EPUB_OPS_NS}}}type") != "toc":
                continue
            for li in list(nav.iter("{*}li")):
                a = li.find("{*}a")
                if a is None or li.find("{*}ol") is not None:
                    continue
                path = (a.get("href") or "").partition("#")[0]
                if "#" in (a.get("href") or "") or not path:
                    continue
                if posixpath.normpath(posixpath.join(nav_dir, path)) != file_href:
                    continue
                parent = li.getparent()
                previous = li.getprevious()
                if previous is not None:
                    previous.tail = li.tail
                elif parent is not None:
                    parent.text = li.tail
                parent.remove(li)
                removed = True
        if removed:
            chapter.modified = True
            book.mark_modified()
    return removed


def _merge_false_chapter(book, cand: FalseChapter) -> bool:
    """Moves the file's content onto the chapter before it, fixes up the
    contents everywhere, and drops the file. Returns False (and undoes
    everything) if the word count check fails."""
    file_chapter = cand.file_chapter
    target = cand.target_chapter
    body_f = _find_body(file_chapter.document)
    body_t = _find_body(target.document)
    tags = [_local(el) for el in _chain(body_t)]
    container_f = _descend(body_f, tags)
    container_t = _descend(body_t, tags)

    words_before = _word_count(body_t) + _word_count(body_f)
    moving = list(container_f)
    if len(container_t):
        last = container_t[-1]
        if not (last.tail or ""):
            last.tail = "\n"
    for child in moving:
        container_t.append(child)

    if _word_count(body_t) != words_before or _word_count(body_f) != 0:
        for child in moving:
            container_f.append(child)
        return False

    target.modified = True
    _repoint_links(book, file_chapter.href, target.href)
    _remove_from_ncx(book, file_chapter.href)
    _remove_from_nav(book, file_chapter.href)
    holder = cand.entry_list
    for i, e in enumerate(holder):
        if e is cand.entry:
            del holder[i]
            break
    _drop_file(book, file_chapter.href)
    return True


# ---------------------------------------------------------------------
# The repair module
# ---------------------------------------------------------------------


class ChapterTitleCleanupRepair:
    name = "Chapter Title Cleanup"

    def __init__(self, config: ChapterTitleCleanupConfig | None = None):
        self.config = config or ChapterTitleCleanupConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        if not book.toc:
            return report

        if self.config.merge_false_chapters:
            for cand in find_false_chapters(book):
                if cand.blocker is None:
                    report.add(
                        cand.file_chapter.href,
                        "False chapter in contents",
                        f"{cand.label!r} is a heading inside {cand.target_chapter.href}, "
                        "not a chapter of its own",
                    )

        if self.config.standardize_title_separators:
            desired = resolve_target_separator(book, self.config)
            if desired is not None:
                for location, text in find_separator_fixes(book, desired):
                    report.add(
                        location,
                        "Chapter title hyphen spacing",
                        f"{text!r} does not match the book's style ({desired!r})",
                    )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        report = Report(self.name)
        if not book.toc:
            return report

        if self.config.merge_false_chapters:
            # Recomputed after every merge: two false chapters in a row
            # would otherwise leave the second pointing at a file the
            # first merge just removed.
            failed = set()
            for _ in range(len(book.chapters) + 1):
                ready = [
                    c for c in find_false_chapters(book)
                    if c.blocker is None and c.file_chapter.href not in failed
                ]
                if not ready:
                    break
                cand = ready[0]
                if _merge_false_chapter(book, cand):
                    report.add(
                        cand.file_chapter.href,
                        "False chapter merged",
                        f"Moved {cand.label!r} back into {cand.target_chapter.href} "
                        "and removed its contents entry",
                    )
                else:
                    failed.add(cand.file_chapter.href)

        if self.config.standardize_title_separators:
            desired = resolve_target_separator(book, self.config)
            if desired is not None:
                fixes = find_separator_fixes(book, desired)
                if fixes and apply_separator_fixes(book, desired):
                    for location, text in fixes:
                        report.add(
                            location,
                            "Chapter title hyphen spacing fixed",
                            f"{text!r} now uses {desired!r}",
                        )
        return report
