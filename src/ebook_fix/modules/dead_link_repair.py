"""
ebook_fix.modules.dead_link_repair

Fixes links inside the book's own text that go nowhere. Found in
"Sandman Slim" (Richard Kadrey), whose in-book Contents page links to
files that were deleted when the book was converted (`titlepage.html`,
`epigraph.html`, `begin_reading_part1.html#ch01`, and so on) and whose
section headings are each wrapped in a link to a missing `contents.html`.
Every one of its 13 internal links was dead. A link like that does
nothing in most readers, and a dead link to a missing file is an error
in an EPUB validator.

Two kinds of dead link, each with its own switch:

1. A link to a file that is not in the book.
   - If `repoint_from_contents` is on and the link's text is exactly the
     label of one entry in the book's table of contents ("Begin Reading",
     "Title Page"), the link is pointed at that entry instead, so a
     Contents page written for a deleted layout works again. The match
     has to be unique and exact (ignoring case and spacing), and the
     entry's own file has to exist and not be the page the link is on.
   - Otherwise the link is removed and its text and any picture inside
     it stay. A link that was also an anchor target (it has an id or
     name) keeps the anchor and loses only the link.
2. `fix_missing_anchors`: a link to a spot (`#something`) that does not
   exist in a file that does. A link into a different file keeps going
   to that file and loses the spot; a link to a spot in its own file,
   which would only jump to the top of the same page, is removed.

Added 2026-10-16 (found on the books split out of the Tarzan omnibus):
when no contents entry matches, a dead link is also pointed at the
chapter whose opening heading is the link's text. The Tarzan contents
pages list every chapter, but the entries for titles with quotation marks
("Chapter 12. "Tomorrow Thou Diest!"") link to an address made of a long
run of X's, so those chapters could not be reached from the list. The
text is compared ignoring case, punctuation and a leading "Chapter", and
has to match the heading at the top of exactly one other page, so a link
that matches nothing, or two pages, is still removed as before.

Never touched: web and email links (anything with a scheme such as
`http:` or `mailto:`), a bare `#`, links to files that exist and have no
spot named (pictures, stylesheets), and links in the navigation
document (the contents modules own that).

Runs right after TOC Cleanup so the contents list it matches against is
already tidy, and on a book with fixed-layout pages it is skipped with
the other markup repairs, since an empty link can be a clickable region
there. Re-running it on a repaired book finds nothing to do.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote
import zipfile

from ebook_fix.config import DeadLinkConfig
from ebook_fix.report import Report

_OPS = "http://www.idpf.org/2007/ops"
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


@dataclass
class _Finding:
    chapter: object
    element: object
    kind: str        # "missing file" or "missing anchor"
    href: str        # the link as written
    action: str      # "repoint", "unlink" or "drop_fragment"
    new_href: str = ""
    text: str = ""


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _fold(text) -> str:
    text = " ".join((text or "").replace("\u00a0", " ").split()).casefold()
    return text.strip(" .:;,!-\u2013\u2014\"'\u201c\u201d\u2018\u2019")


def _is_nav_document(doc) -> bool:
    if doc is None:
        return False
    return any(nav.get(f"{{{_OPS}}}type") == "toc" for nav in doc.iter("{*}nav"))


def _flatten(entries):
    for entry in entries:
        yield entry
        if entry.children:
            yield from _flatten(entry.children)


def _ids_in(document) -> set:
    found = set()
    for el in document.iter():
        if not isinstance(el.tag, str):
            continue
        value = el.get("id")
        if value:
            found.add(value)
        if el.tag.endswith("}a") or el.tag == "a":
            name = el.get("name")
            if name:
                found.add(name)
    return found


def _unwrap(a) -> None:
    """Removes the <a> tag but keeps everything inside it (text,
    pictures, other tags) and the text that followed it."""
    parent = a.getparent()
    if parent is None:
        return
    index = parent.index(a)
    previous = a.getprevious()
    text = a.text or ""
    tail = a.tail or ""
    children = list(a)

    if text:
        if previous is not None:
            previous.tail = (previous.tail or "") + text
        else:
            parent.text = (parent.text or "") + text
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)
    if tail:
        if children:
            children[-1].tail = (children[-1].tail or "") + tail
        elif previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(a)


_PAGE_BREAK_ID = re.compile(r"^calibre_pb_\d+$")
_CONTENTS_LIKE_LINKS = 8   # a page linking to this many pages is a contents page, not a chapter
_HEADING_BLOCKS = 3        # how many text blocks at the top of a page count as its heading
_BLOCK_NAMES = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "blockquote", "section"}


def _local_name(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _heading_key(text) -> str:
    """A heading or link text reduced to its words: lowercase, no
    punctuation, no leading "Chapter". 'Chapter 12. "Tomorrow Thou
    Diest!"' and '12. "TOMORROW THOU DIEST!"' both become
    '12 tomorrow thou diest'."""
    words = re.findall(r"\w+", (text or "").casefold())
    if words and words[0] == "chapter":
        words = words[1:]
    return " ".join(words)


def _anchor_for(block) -> str:
    """The id a link can use to reach this block: its own id, an anchor
    inside it, or an empty anchor just before it. Empty when there is none
    (the link then goes to the top of the page)."""
    candidates = []
    if block.get("id"):
        candidates.append(block.get("id"))
    for el in block.iter():
        if not isinstance(el.tag, str):
            continue
        value = el.get("id") or (el.get("name") if _local_name(el) == "a" else None)
        if value:
            candidates.append(value)
    previous = block.getprevious()
    for _ in range(2):
        if previous is None:
            break
        if isinstance(previous.tag, str):
            if previous.get("id") and not "".join(previous.itertext()).strip():
                candidates.append(previous.get("id"))
            if "".join(previous.itertext()).strip():
                break
        previous = previous.getprevious()
    # A page-break marker (calibre_pb_12) is the sort of thing other
    # repairs tidy away, so the heading's own anchor is preferred.
    for value in candidates:
        if not _PAGE_BREAK_ID.match(value):
            return value
    return candidates[0] if candidates else ""


def _link_count(document) -> int:
    """How many different pages a page's links go to (a contents page has many)."""
    targets = set()
    for a in document.iter():
        if isinstance(a.tag, str) and _local_name(a) == "a":
            href = (a.get("href") or "").strip()
            if href and not href.startswith("#") and not _SCHEME.match(href):
                targets.add(href.partition("#")[0])
    return len(targets)


def _top_blocks(document) -> list:
    """The first few text-bearing blocks of a page, as (element, text)."""
    root = document.getroot() if hasattr(document, "getroot") else document
    body = next((el for el in root.iter() if isinstance(el.tag, str) and _local_name(el) == "body"), root)
    found = []
    for el in body.iter():
        if not isinstance(el.tag, str) or _local_name(el) not in _BLOCK_NAMES:
            continue
        if any(isinstance(child.tag, str) and _local_name(child) in _BLOCK_NAMES for child in el.iterdescendants()):
            continue                      # only the innermost block holds the text
        text = " ".join("".join(el.itertext()).split())
        if text:
            found.append((el, text))
            if len(found) >= _HEADING_BLOCKS:
                break
    return found


class DeadLinkRepair:
    name = "Dead Link Cleanup"

    def __init__(self, config: DeadLinkConfig | None = None):
        self.config = config or DeadLinkConfig()

    # -----------------------------------------------------
    # Analysis / repair
    # -----------------------------------------------------

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for finding in self._scan(book):
            report.add(finding.chapter.href, *self._describe(finding, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        touched = set()
        for finding in self._scan(book):
            a = finding.element
            if finding.action == "repoint" or finding.action == "drop_fragment":
                a.set("href", finding.new_href)
            else:
                if a.get("id") or a.get("name"):
                    del a.attrib["href"]
                else:
                    _unwrap(a)
            finding.chapter.modified = True
            touched.add(finding.chapter.href)
            report.add(finding.chapter.href, *self._describe(finding, will=False))

        if touched:
            book.mark_modified()
        return report

    # -----------------------------------------------------
    # Finding the dead links
    # -----------------------------------------------------

    def _existing_paths(self, book) -> set:
        """Every file the book will contain once saved, as full in-zip
        paths: the original archive, plus files an earlier pass queued
        to add, minus files it queued to remove."""
        try:
            with zipfile.ZipFile(book.source, "r") as archive:
                names = set(archive.namelist())
        except (OSError, zipfile.BadZipFile, TypeError):
            names = set()
        names |= set(getattr(book, "new_files", {}) or {})
        names -= set(getattr(book, "removed_files", set()) or set())
        return names

    def _scan(self, book) -> list:
        base = PurePosixPath(book.package_path).parent
        existing = self._existing_paths(book)
        chapters = {c.href: c for c in book.chapters if c.document is not None}
        in_memory = {c.href for c in book.chapters}
        id_cache: dict = {}

        def full(path: str) -> str:
            return posixpath.normpath(str(base / path))

        def exists(path: str) -> bool:
            return path in in_memory or full(path) in existing

        toc_labels = self._toc_labels(book, exists)

        # Built the first time a dead link needs it (most books have none).
        headings_cache: list = []

        def headings() -> dict:
            if not headings_cache:
                index: dict = {}
                for page in book.chapters:
                    if page.document is None or _is_nav_document(page.document):
                        continue
                    if _link_count(page.document) >= _CONTENTS_LIKE_LINKS:
                        continue          # a contents page lists chapters; it is not one
                    for block, text in _top_blocks(page.document):
                        key = _heading_key(text)
                        if len(key) >= 3:
                            index.setdefault(key, []).append((page.href, _anchor_for(block)))
                headings_cache.append(index)
            return headings_cache[0]

        findings = []
        for chapter in book.chapters:
            doc = chapter.document
            if doc is None or _is_nav_document(doc):
                continue
            chapter_dir = posixpath.dirname(chapter.href)

            for a in doc.iter("{*}a"):
                href = a.get("href")
                if href is None:
                    continue
                href = href.strip()
                if not href or href == "#" or _SCHEME.match(href):
                    continue

                raw_path, _, raw_fragment = href.partition("#")
                path = unquote(raw_path)
                fragment = unquote(raw_fragment)
                target = (
                    chapter.href
                    if not path
                    else posixpath.normpath(posixpath.join(chapter_dir, path))
                )
                text = "".join(a.itertext()).strip()

                if not exists(target):
                    findings.append(
                        self._missing_file(chapter, a, href, text, toc_labels, chapter_dir, headings)
                    )
                    continue

                if fragment and self.config.fix_missing_anchors and target in chapters:
                    if target not in id_cache:
                        id_cache[target] = _ids_in(chapters[target].document)
                    if fragment not in id_cache[target]:
                        if target == chapter.href:
                            findings.append(_Finding(chapter, a, "missing anchor", href, "unlink", text=text))
                        else:
                            new_href = raw_path
                            findings.append(
                                _Finding(chapter, a, "missing anchor", href, "drop_fragment", new_href, text)
                            )
        return findings

    def _toc_labels(self, book, exists) -> dict:
        """{folded label: [toc entry path-with-fragment]} for entries
        whose file exists. A label that appears more than once maps to
        more than one entry, which is how ambiguity is detected."""
        labels: dict = {}
        for entry in _flatten(book.toc or []):
            label = _fold(entry.label)
            path = (entry.href or "").partition("#")[0]
            if not label or not path or not exists(path):
                continue
            labels.setdefault(label, []).append(entry.href)
        return labels

    def _missing_file(self, chapter, a, href, text, toc_labels, chapter_dir, headings=None) -> _Finding:
        if self.config.repoint_from_contents and text:
            matches = toc_labels.get(_fold(text), [])
            if len(matches) == 1:
                target_path, _, target_fragment = matches[0].partition("#")
                if target_path != chapter.href:
                    new_href = posixpath.relpath(target_path, chapter_dir or ".")
                    if target_fragment:
                        new_href += "#" + target_fragment
                    return _Finding(chapter, a, "missing file", href, "repoint", new_href, text)

            # No contents entry matched: try the chapters' own headings.
            key = _heading_key(text)
            if headings is not None and len(key) >= 3:
                found = [hit for hit in headings().get(key, []) if hit[0] != chapter.href]
                if len(found) == 1:
                    target_path, target_fragment = found[0]
                    new_href = posixpath.relpath(target_path, chapter_dir or ".")
                    if target_fragment:
                        new_href += "#" + target_fragment
                    return _Finding(chapter, a, "missing file", href, "repoint", new_href, text)
        return _Finding(chapter, a, "missing file", href, "unlink", text=text)

    # -----------------------------------------------------
    # Report wording
    # -----------------------------------------------------

    def _describe(self, finding, will: bool):
        shown = finding.text or finding.href
        verb = "will be " if will else ""
        if finding.action == "repoint":
            return (
                f"Dead link {verb}repointed",
                f"{shown!r} linked to the missing {finding.href}; now points at {finding.new_href}",
            )
        if finding.action == "drop_fragment":
            return (
                f"Dead anchor {verb}dropped",
                f"{finding.href} points at a spot that does not exist; now links to {finding.new_href}",
            )
        if finding.kind == "missing anchor":
            return (
                f"Dead link {verb}removed",
                f"{shown!r} linked to a spot on its own page that does not exist ({finding.href})",
            )
        return (
            f"Dead link {verb}removed",
            f"{shown!r} linked to the missing {finding.href}; the text was kept",
        )
