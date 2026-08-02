"""
ebook_fix.modules.images

Detects broken image references:

1. Manifest entries that point at an image file which doesn't
   actually exist inside the EPUB zip. (Report-only for now -- the
   writer doesn't currently rewrite the package document, so a fix
   here wouldn't actually persist to the saved file.)
2. <img> tags inside chapter content whose src points at a file
   that doesn't exist in the zip. These ARE fixed: the broken tag
   is removed from the chapter, since a reference to a file that
   will never exist just renders as a broken-image icon.
"""

from __future__ import annotations
import posixpath
import zipfile
from pathlib import PurePosixPath
from ebook_fix.config import ImageRepairConfig
from ebook_fix.report import Report


class ImageRepair:
    name = "Image Repair"

    def __init__(self, config: ImageRepairConfig | None = None):
        self.config = config or ImageRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book):
        report = Report(self.name)
        archive_names = self._archive_names(book)
        base = PurePosixPath(book.package_path).parent

        # 1. Manifest entries pointing at files that don't exist.
        if self.config.report_missing_manifest_images:
            for item in book.manifest:
                if not item.media_type.startswith("image/"):
                    continue
                resolved = self._resolve(base, item.href)
                if resolved not in archive_names:
                    report.add(
                        "content.opf",
                        "Manifest references missing image",
                        f"Manifest references missing image: {item.href}",
                    )

        # 2. <img> tags in chapters pointing at files that don't exist.
        if self.config.fix_broken_images:
            for chapter in book.chapters:
                chapter_dir = self._resolve(base, chapter.href)
                chapter_dir = PurePosixPath(chapter_dir).parent
                for img in self._img_tags(chapter):
                    src = img.get("src")
                    if not src:
                        continue
                    resolved = self._resolve(chapter_dir, src)
                    if resolved not in archive_names:
                        report.add(
                            chapter.href,
                            "Broken image reference",
                            f"Broken image reference: {src}",
                        )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book):
        if not self.config.fix_broken_images:
            return

        archive_names = self._archive_names(book)
        base = PurePosixPath(book.package_path).parent

        for chapter in book.chapters:
            chapter_dir = self._resolve(base, chapter.href)
            chapter_dir = PurePosixPath(chapter_dir).parent
            changed = False

            for img in list(self._img_tags(chapter)):
                src = img.get("src")
                if not src:
                    continue
                resolved = self._resolve(chapter_dir, src)
                if resolved not in archive_names:
                    parent = img.getparent()
                    if parent is not None:
                        parent.remove(img)
                        changed = True

            if changed:
                chapter.modified = True
                book.mark_modified()

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _archive_names(self, book):
        with zipfile.ZipFile(book.source, "r") as archive:
            return set(archive.namelist())

    def _resolve(self, base, href):
        """Resolve an href relative to `base`, collapsing any ../ segments."""
        return posixpath.normpath(str(PurePosixPath(base) / href))

    def _img_tags(self, chapter):
        if chapter.document is None:
            return []
        return chapter.document.findall(".//{*}img")
