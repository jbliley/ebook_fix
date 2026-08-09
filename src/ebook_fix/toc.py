"""
ebook_fix.toc

Reads whatever table-of-contents structure a book's own NCX and/or
EPUB3 nav document already provides -- loaded into book.toc by
parser.py -- and checks it against the book's actual files, in the
same "analysis, not repair" spirit as css.py/paragraphs.py/
whitespace.py.

What this checks
-----------------
- Does each TOC entry's href point at a chapter that actually exists
  in the book (a "missing file" link)?
- If the href has a #fragment, does that id actually exist somewhere
  in that chapter's own document (a "missing anchor" link)?
- Which main-content chapters (per frontmatter.py's zone) aren't
  referenced by the TOC at all, in case a chapter is missing from an
  otherwise-valid TOC.

What this does NOT do
----------------------
This does not judge whether a book's TOC is "good enough," rewrite or
reorder it, or generate one from scratch when a book has none at all
-- that's a separate, bigger effort tracked in
docs/analysis_roadmap.md. This module is purely descriptive: record
what's broken so a future repair module can act on it without
re-parsing the NCX/nav itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ebook_fix.frontmatter import MAIN_ZONE

MISSING_FILE = "missing file"
MISSING_ANCHOR = "missing anchor"


@dataclass
class TocLinkIssue:
    label: str = ""
    href: str = ""
    reason: str = ""  # MISSING_FILE or MISSING_ANCHOR


@dataclass
class BookTocSummary:
    source: str = ""  # "ncx", "nav", or "" if the book has neither
    entry_count: int = 0  # every entry, all nesting levels flattened
    top_level_entry_count: int = 0
    broken_links: list = field(default_factory=list)  # TocLinkIssue
    # hrefs of main-content chapters that no TOC entry (at any level)
    # points at -- only meaningful when a TOC was actually found.
    chapters_missing_from_toc: list = field(default_factory=list)

    @property
    def broken_link_count(self) -> int:
        return len(self.broken_links)


def _flatten(entries):
    for entry in entries:
        yield entry
        if entry.children:
            yield from _flatten(entry.children)


def _main_content_hrefs(chapter_reports):
    if not chapter_reports:
        return []
    return [
        c.href for c in chapter_reports
        if getattr(c, "matter_zone", "unknown") == MAIN_ZONE
    ]


def _ids_in_chapter(chapter):
    doc = getattr(chapter, "document", None)
    if doc is None:
        return set()
    return {
        e.get("id")
        for e in doc.iter()
        if isinstance(e.tag, str) and e.get("id")
    }


def analyze_book_toc(book, chapter_reports=None) -> BookTocSummary:
    summary = BookTocSummary(source=getattr(book, "toc_source", "") or "")
    entries = list(getattr(book, "toc", None) or [])

    if not entries:
        # No TOC to validate, but still worth knowing what a generated
        # one would need to cover later.
        summary.chapters_missing_from_toc = _main_content_hrefs(chapter_reports)
        return summary

    flat = list(_flatten(entries))
    summary.entry_count = len(flat)
    summary.top_level_entry_count = len(entries)

    known_hrefs = {getattr(c, "href", "") for c in book.chapters}
    ids_by_href = {
        getattr(c, "href", ""): _ids_in_chapter(c) for c in book.chapters
    }

    referenced_hrefs = set()
    for entry in flat:
        if not entry.href:
            # A structural-only entry (a part/section heading with no
            # content link of its own) -- nothing to validate.
            continue
        path, _, fragment = entry.href.partition("#")
        if path not in known_hrefs:
            summary.broken_links.append(
                TocLinkIssue(label=entry.label, href=entry.href, reason=MISSING_FILE)
            )
            continue
        referenced_hrefs.add(path)
        if fragment and fragment not in ids_by_href.get(path, set()):
            summary.broken_links.append(
                TocLinkIssue(label=entry.label, href=entry.href, reason=MISSING_ANCHOR)
            )

    summary.chapters_missing_from_toc = [
        href for href in _main_content_hrefs(chapter_reports)
        if href not in referenced_hrefs
    ]
    return summary
