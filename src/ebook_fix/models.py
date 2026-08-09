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
    metadata: Metadata = field(default_factory=Metadata)
    manifest: list[ManifestItem] = field(default_factory=list)
    spine: list[str] = field(default_factory=list)
    toc: list[TocEntry] = field(default_factory=list)
    toc_source: str = ""  # "ncx", "nav", or "" if the book has neither
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
# new_files: files that don't exist in the source archive at all yet
#     (e.g. a generated EPUB3 nav.xhtml), keyed by their full in-zip
#     path, value is the raw bytes to write.

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

# -------------------------------------------------------------
# Helpers
# -------------------------------------------------------------

    def mark_modified(self) -> None:
        self.modified = True