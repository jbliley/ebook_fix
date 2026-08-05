"""
ebook_fix.epub_version

Detects which package (OPF) version a book was authored against, and
classifies whether it needs to be upgraded to EPUB 3.

This module only classifies -- it doesn't touch the book. The actual
rewrite (bumping the version, adding the EPUB3 nav document, etc.)
happens in ebook_fix.modules.epub3_upgrade, which calls detect() to
decide whether there's anything to do.
"""

from __future__ import annotations
from dataclasses import dataclass

# The version every book gets upgraded to.
TARGET_VERSION = "3.0"


@dataclass(slots=True)
class VersionInfo:
    raw_version: str = ""          # exactly what was in <package version="...">, if anything
    detected_version: str = ""     # normalized version string used for display/reporting
    major: int = 0                 # 1, 2, or 3
    is_epub3: bool = False
    needs_upgrade: bool = False
    target_version: str = TARGET_VERSION


def detect(book) -> VersionInfo:
    """
    Classify the EPUB/OPF version of `book`.

    `book.version` is whatever raw string the parser found on the
    <package version="..."> attribute (parser.py sets this; it may be
    empty for very old Open eBook-era files that omit the attribute
    entirely).
    """
    raw = (getattr(book, "version", "") or "").strip()

    # A missing version attribute is itself a strong signal of a very
    # old (pre-EPUB2) Open Packaging Format file. Treat it as "1.2"
    # (the last OEBPS version before the OPF/version attribute became
    # mandatory) purely for display and major-version math -- it still
    # needs the same upgrade path as an EPUB 2.x file.
    normalized = raw or "1.2"
    major = _major_version(normalized)
    is_epub3 = major >= 3

    return VersionInfo(
        raw_version=raw,
        detected_version=normalized,
        major=major,
        is_epub3=is_epub3,
        needs_upgrade=not is_epub3,
    )


def _major_version(version_str: str) -> int:
    try:
        return int(version_str.split(".")[0])
    except (ValueError, IndexError):
        return 0
