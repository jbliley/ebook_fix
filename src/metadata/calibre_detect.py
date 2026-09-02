"""
metadata.calibre_detect

Detects whether a book lives inside a Calibre-managed library, so the
rest of the metadata package knows whether to read/write through a
Calibre backend (metadata.opf + metadata.db) or treat the file as a
lone EPUB. See docs/metadata_plan.md, "Where books are found / dual-
mode operation".

Detection walks up from the book's own folder looking for the two
things a real Calibre library always has: a metadata.opf sitting
right next to the book, and a metadata.db somewhere above it (Calibre
keeps exactly one metadata.db, at the library's root). Both need to be
present -- either alone could just be a coincidence (a metadata.opf
someone left lying around, or an unrelated file happening to be named
metadata.db).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

METADATA_DB_NAME = "metadata.db"
METADATA_OPF_NAME = "metadata.opf"

# Calibre's own on-disk convention: each book's folder name ends with
# " (<id>)", e.g. "Sidewinders (12)". Not documented anywhere official,
# but it's the actual behavior every Calibre version has used.
BOOK_ID_PATTERN = re.compile(r"\((\d+)\)\s*$")

# How many parent directories to check for metadata.db before giving
# up. A real Calibre library is Library Root/Author/Title (id)/file --
# two levels above the book folder -- so this gives comfortable
# headroom without walking the whole filesystem on a lone EPUB.
MAX_WALK_UP = 6


@dataclass(slots=True)
class CalibreContext:
    is_calibre_managed: bool = False
    book_folder: Path | None = None
    metadata_opf_path: Path | None = None
    library_root: Path | None = None
    metadata_db_path: Path | None = None
    book_id: int | None = None


def detect(epub_path) -> CalibreContext:
    """Given the path to an EPUB file, determines whether it's sitting
    inside a Calibre library. Always returns a CalibreContext -- check
    .is_calibre_managed before trusting the rest of its fields."""
    epub_path = Path(epub_path)
    book_folder = epub_path.parent

    metadata_opf_path = book_folder / METADATA_OPF_NAME
    if not metadata_opf_path.is_file():
        return CalibreContext(book_folder=book_folder)

    metadata_db_path = _find_metadata_db(book_folder)
    if metadata_db_path is None:
        return CalibreContext(
            book_folder=book_folder,
            metadata_opf_path=metadata_opf_path,
        )

    return CalibreContext(
        is_calibre_managed=True,
        book_folder=book_folder,
        metadata_opf_path=metadata_opf_path,
        library_root=metadata_db_path.parent,
        metadata_db_path=metadata_db_path,
        book_id=_extract_book_id(book_folder),
    )


def _find_metadata_db(start: Path) -> Path | None:
    current = start
    for _ in range(MAX_WALK_UP):
        candidate = current / METADATA_DB_NAME
        if candidate.is_file():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def _extract_book_id(book_folder: Path) -> int | None:
    match = BOOK_ID_PATTERN.search(book_folder.name)
    return int(match.group(1)) if match else None
