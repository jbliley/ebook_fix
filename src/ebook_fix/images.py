"""
ebook_fix.images

Analyzes image references in the book during the same single pass
the rest of the analyzer runs, so nothing downstream needs to open
the zip or walk the chapters again just to find image problems.

Two things get flagged:

1. Manifest entries that point at an image file which doesn't
   actually exist inside the EPUB zip. Report-only -- there's no
   <img> tag to remove for these, just a stale manifest entry.
2. <img> tags inside chapter content whose src points at a file
   that doesn't exist in the zip. These carry a live reference to
   the actual element, so a repair module can remove it directly
   without having to re-find it.

This module only detects -- it doesn't touch the book. The fix
(removing a broken <img> tag) happens in ebook_fix.modules.images,
which reads this report instead of re-scanning for the same broken
references itself.
"""
from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass
class BrokenImageRef:
    href: str = ""          # chapter file the broken reference was found in
    src: str = ""            # the src attribute exactly as written
    resolved: str = ""       # src, resolved to a path inside the zip
    element: object = None   # live reference to the <img> element itself --
                              # not saved to the JSON cache, see serialize.py


@dataclass
class MissingManifestImage:
    href: str = ""           # the manifest item's href, exactly as written
    resolved: str = ""       # href, resolved to a path inside the zip


@dataclass
class BookImageSummary:
    archive_image_count: int = 0
    broken_image_refs: list = field(default_factory=list)          # [BrokenImageRef]
    missing_manifest_images: list = field(default_factory=list)    # [MissingManifestImage]

    @property
    def broken_image_count(self) -> int:
        return len(self.broken_image_refs)

    @property
    def missing_manifest_image_count(self) -> int:
        return len(self.missing_manifest_images)

    @property
    def chapters_with_broken_images(self) -> list:
        seen = []
        for ref in self.broken_image_refs:
            if ref.href not in seen:
                seen.append(ref.href)
        return seen


def _archive_names(book) -> set:
    with zipfile.ZipFile(book.source, "r") as archive:
        return set(archive.namelist())


def _resolve(base, href) -> str:
    """Resolve an href relative to `base`, collapsing any ../ segments."""
    return posixpath.normpath(str(PurePosixPath(base) / href))


def analyze_book_images(book) -> BookImageSummary:
    summary = BookImageSummary()
    archive_names = _archive_names(book)
    base = PurePosixPath(book.package_path).parent

    for item in getattr(book, "manifest", []) or []:
        if not item.media_type.startswith("image/"):
            continue
        summary.archive_image_count += 1
        resolved = _resolve(base, item.href)
        if resolved not in archive_names:
            summary.missing_manifest_images.append(
                MissingManifestImage(href=item.href, resolved=resolved)
            )

    for chapter in book.chapters:
        if chapter.document is None:
            continue
        chapter_dir = PurePosixPath(_resolve(base, chapter.href)).parent
        for img in chapter.document.findall(".//{*}img"):
            src = img.get("src")
            if not src:
                continue
            resolved = _resolve(chapter_dir, src)
            if resolved not in archive_names:
                summary.broken_image_refs.append(
                    BrokenImageRef(href=chapter.href, src=src, resolved=resolved, element=img)
                )

    return summary
