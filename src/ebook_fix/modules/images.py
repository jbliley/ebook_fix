"""
ebook_fix.modules.images

Uses the image findings the analyzer already collected (see
ebook_fix.images) instead of re-scanning the book:

1. Manifest entries that point at an image file which doesn't
   actually exist inside the EPUB zip. (Report-only for now -- the
   writer doesn't currently rewrite the package document, so a fix
   here wouldn't actually persist to the saved file.)
2. <img> tags inside chapter content whose src points at a file
   that doesn't exist in the zip. These ARE fixed: the broken tag
   is removed from the chapter, since a reference to a file that
   will never exist just renders as a broken-image icon.

If this ever runs without an analysis handed to it, it falls back
to scanning the book itself via ebook_fix.images.analyze_book_images.
"""

from __future__ import annotations
from ebook_fix.config import ImageRepairConfig
from ebook_fix.report import Report
from ebook_fix.images import analyze_book_images


class ImageRepair:
    name = "Image Repair"

    def __init__(self, config: ImageRepairConfig | None = None):
        self.config = config or ImageRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        images = analysis.images if analysis is not None else analyze_book_images(book)

        if self.config.report_missing_manifest_images:
            for entry in images.missing_manifest_images:
                report.add(
                    "content.opf",
                    "Manifest references missing image",
                    f"Manifest references missing image: {entry.href}",
                )

        if self.config.fix_broken_images:
            for ref in images.broken_image_refs:
                report.add(
                    ref.href,
                    "Broken image reference",
                    f"Broken image reference: {ref.src}",
                )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        report = Report(self.name)
        if not self.config.fix_broken_images:
            return report

        images = analysis.images if analysis is not None else analyze_book_images(book)

        changed_hrefs = set()
        for ref in images.broken_image_refs:
            img = ref.element
            if img is None:
                continue
            parent = img.getparent()
            if parent is not None:
                parent.remove(img)
                changed_hrefs.add(ref.href)
                report.add(
                    ref.href,
                    "Broken image reference removed",
                    f"Removed <img> pointing at missing file: {ref.src}",
                )

        if not changed_hrefs:
            return report

        for chapter in book.chapters:
            if chapter.href in changed_hrefs:
                chapter.modified = True
        book.mark_modified()

        return report

