"""
ebook_fix.writer

Saves a Book back out to a valid EPUB file. Chapters flagged as
modified are re-serialized from their DOM; the OPF is re-serialized if
opf_modified is set. Any other file -- including one that already
exists in the source archive -- can be overridden by putting its full
in-zip path in book.new_files (e.g. a repair module that rewrote a CSS
file's bytes directly, without going through a Chapter/DOM). A file
can also be dropped entirely by putting its full in-zip path in
book.removed_files (e.g. a whole Gutenberg back-matter page with no
real content left after repair). Anything not covered by one of those
is copied through from the original file untouched, byte for byte.
"""

from __future__ import annotations
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from lxml import etree


class EPUBWriter:

    def save(self, book, output_path):
        output_path = Path(output_path)
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
        removed_files = set(getattr(book, "removed_files", set()) or set())
        opf_modified = bool(getattr(book, "opf_modified", False))

        # Written to a temporary file first, then moved into place only
        # once everything below has succeeded -- this matters most when
        # output_path is the same file as book.source (e.g. `repair
        # --overwrite` replacing the original in place). Opening
        # output_path directly in "w" mode would truncate it
        # immediately, and since the source archive below is still
        # being read from as each untouched file is copied through,
        # that would corrupt the very file this method is reading from
        # mid-copy -- exactly the "Truncated file header" crash this
        # was written to prevent.
        tmp_dir = str(output_path.parent) if str(output_path.parent) else "."
        tmp_fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, suffix=".epub.tmp")
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)

        try:
            with zipfile.ZipFile(book.source, "r") as src:
                names = src.namelist()

                with zipfile.ZipFile(
                    tmp_path, "w", zipfile.ZIP_DEFLATED
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

                        if name in removed_files:
                            new_files.pop(name, None)
                            continue

                        if name in modified_chapters:
                            chapter = modified_chapters[name]
                            data = self._serialize(chapter)
                        elif name == book.package_path and opf_modified:
                            data = self._serialize_opf(book.opf_document)
                        elif name in new_files:
                            data = new_files[name]
                        else:
                            data = src.read(name)

                        dest.writestr(name, data)
                        new_files.pop(name, None)

                    # Anything genuinely new that wasn't already an
                    # entry in the original archive.
                    for name, data in new_files.items():
                        if name in removed_files:
                            continue
                        dest.writestr(name, data)

            os.replace(tmp_path, output_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


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

