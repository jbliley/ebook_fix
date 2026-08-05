"""
ebook_fix.writer

Saves a Book back out to a valid EPUB file. Only chapters that were
actually modified are re-serialized; everything else is copied through
from the original file untouched, byte for byte.
"""

from __future__ import annotations
import zipfile
from pathlib import PurePosixPath
from lxml import etree


class EPUBWriter:

    def save(self, book, output_path):
        base = PurePosixPath(book.package_path).parent

        # Map full in-zip path -> chapter, for the chapters we touched.
        modified_chapters = {
            str(base / chapter.href): chapter
            for chapter in book.chapters
            if chapter.modified
        }

        # Files that don't exist in the source archive at all yet (e.g.
        # a generated EPUB3 nav.xhtml). Copied in below; anything that
        # turns out to already be a real archive entry is skipped here
        # since the main loop will handle it.
        new_files = dict(getattr(book, "new_files", {}) or {})
        opf_modified = bool(getattr(book, "opf_modified", False))

        with zipfile.ZipFile(book.source, "r") as src:
            names = src.namelist()

            with zipfile.ZipFile(
                output_path, "w", zipfile.ZIP_DEFLATED
            ) as dest:
                # The "mimetype" entry must be first and stored
                # uncompressed, per the EPUB spec.
                if "mimetype" in names:
                    dest.writestr(
                        zipfile.ZipInfo("mimetype"),
                        src.read("mimetype"),
                        zipfile.ZIP_STORED,
                    )

                for name in names:
                    if name == "mimetype":
                        continue

                    if name in modified_chapters:
                        chapter = modified_chapters[name]
                        data = self._serialize(chapter)
                    elif name == book.package_path and opf_modified:
                        data = self._serialize_opf(book.opf_document)
                    else:
                        data = src.read(name)

                    dest.writestr(name, data)
                    new_files.pop(name, None)

                # Anything genuinely new that wasn't already an entry
                # in the original archive.
                for name, data in new_files.items():
                    dest.writestr(name, data)

    # ---------------------------------------------------------

    def _serialize(self, chapter):
        return etree.tostring(
            chapter.document,
            xml_declaration=True,
            encoding="utf-8",
            doctype="<!DOCTYPE html>",
        )

    def _serialize_opf(self, opf_document):
        return etree.tostring(
            opf_document,
            xml_declaration=True,
            encoding="utf-8",
        )
