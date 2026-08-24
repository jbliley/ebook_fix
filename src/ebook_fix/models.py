"""
ebook_fix.models

Core data models used throughout the EPUB Fix project.
Nothing in this file will contain repair logic; These classes simply describe the structure of an EPUB in memory.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------

@dataclass(slots=True)
class ManifestItem:
    """One item from the EPUB manifest."""

    id: str
    href: str
    media_type: str
    properties: str = ""

# ---------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------

@dataclass(slots=True)
class Metadata:
    title: str = ""
    creator: str = ""
    language: str = ""
    publisher: str = ""
    identifier: str = ""
    description: str = ""
    date: str = ""
    rights: str = ""
    subject: list[str] = field(default_factory=list)

# ---------------------------------------------------------------------
# Table of contents
# ---------------------------------------------------------------------

@dataclass(slots=True)
class TocEntry:
    """One entry from a book's own NCX and/or EPUB3 nav document, as
    found -- not generated. href is resolved to be relative to the OPF
    (the same convention Chapter.href and ManifestItem.href use), with
    any #fragment kept attached. children holds nested entries in
    document order, for TOCs with sub-sections."""

    label: str = ""
    href: str = ""
    children: list[TocEntry] = field(default_factory=list)

# ---------------------------------------------------------------------
# Chapter
# ---------------------------------------------------------------------

@dataclass(slots=True)
class Chapter:
    """Represents a parsed XHTML document. Document will eventually contain an lxml ElementTree."""

    id: str
    href: str
    media_type: str
    title: str = ""
    document: Any = None
    modified: bool = False

# ---------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------

@dataclass(slots=True)
class Resource:
    """Generic non-XHTML resource."""

    id: str
    href: str
    media_type: str

# ---------------------------------------------------------------------
# Book
# ---------------------------------------------------------------------

@dataclass(slots=True)
class Book:
    """Complete in-memory representation of an EPUB."""

    source: str | Path | None = None
    version: str = ""
    package_path: str = ""
    opf_document: Any = None
    opf_modified: bool = False
    new_files: dict = field(default_factory=dict)
    removed_files: set = field(default_factory=set)
    metadata: Metadata = field(default_factory=Metadata)
    manifest: list[ManifestItem] = field(default_factory=list)
    spine: list[str] = field(default_factory=list)
    toc: list[TocEntry] = field(default_factory=list)
    toc_source: str = ""  # "ncx", "nav", or "" if the book has neither
    ncx_document: Any = None
    ncx_href: str = ""
    ncx_modified: bool = False
    chapters: list[Chapter] = field(default_factory=list)
    css: list[Resource] = field(default_factory=list)
    images: list[Resource] = field(default_factory=list)
    fonts: list[Resource] = field(default_factory=list)
    audio: list[Resource] = field(default_factory=list)
    video: list[Resource] = field(default_factory=list)
    other: list[Resource] = field(default_factory=list)
    modified: bool = False

# opf_document: the raw lxml <package> element from content.opf, kept
#     around so modules that need to edit package-level structure
#     (version, manifest, metadata -- e.g. an EPUB2->EPUB3 upgrade)
#     have something to edit. Set opf_modified=True after changing it
#     so the writer knows to re-serialize it instead of copying the
#     original bytes through untouched.
# ncx_document: the raw lxml <ncx> element from the book's toc.ncx,
#     None if the book has no NCX at all. Same idiom as opf_document --
#     set ncx_modified=True after changing it so the writer knows to
#     re-serialize it. book.toc (above) stays what it's always been: a
#     read-only, already-parsed TocEntry list for anything that just
#     wants to read the table of contents. ncx_document is the live
#     tree underneath it, for anything that needs to actually edit the
#     NCX (see docs/xhtml_recoder_plan.md, Phase 3a). The EPUB3 nav
#     document doesn't get its own field here -- its media_type
#     already puts it through the normal chapter-loading path, so it's
#     already a live tree sitting in book.chapters (see Book.nav_chapter
#     below).
# new_files: files that don't exist in the source archive at all yet
#     (e.g. a generated EPUB3 nav.xhtml), keyed by their full in-zip
#     path, value is the raw bytes to write.
# removed_files: full in-zip paths (same format as new_files' keys) to
#     drop entirely from the saved EPUB -- e.g. a whole Gutenberg
#     back-matter file with no real content left. A module that drops
#     one should also update book.manifest/book.spine/book.chapters
#     to match, or the OPF will reference a file that no longer exists.

# -------------------------------------------------------------
# Convenience Properties
# -------------------------------------------------------------

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    @property
    def image_count(self) -> int:
        return len(self.images)

    @property
    def css_count(self) -> int:
        return len(self.css)

    @property
    def font_count(self) -> int:
        return len(self.fonts)

    @property
    def nav_item(self) -> ManifestItem | None:
        """The manifest entry for the EPUB3 nav document, if the book
        declares one (properties="nav"), else None."""
        for item in self.manifest:
            if "nav" in item.properties.split():
                return item
        return None

    @property
    def nav_chapter(self) -> Chapter | None:
        """The EPUB3 nav document as its own Chapter -- already a
        live, editable lxml tree like any other chapter, since
        nav.xhtml's media_type (application/xhtml+xml) means
        parser.py's normal xhtml-loading path already picked it up.
        This just gives calling code a direct way to find it instead
        of hunting through book.chapters itself. None if the book has
        no nav document."""
        item = self.nav_item
        if item is None:
            return None
        for chapter in self.chapters:
            if chapter.href == item.href:
                return chapter
        return None

# -------------------------------------------------------------
# Helpers
# -------------------------------------------------------------

    def mark_modified(self) -> None:
        self.modified = True