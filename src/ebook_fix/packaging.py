"""
ebook_fix.packaging

Detects files sitting inside the EPUB's zip archive that nothing --
not the manifest, not the standard EPUB container entries -- actually
references. This is the reverse of images.py's missing-manifest-image
check: that one flags a manifest entry pointing at a file that doesn't
exist; this flags a file that exists but has no manifest entry (or
container-required role) pointing at it.

Report-only, same "analysis, not repair" pattern as the rest of this
directory. Deciding whether an orphaned file is safe to delete is left
to a person (or a future, more cautious repair module) -- a leftover
desktop-metadata file (.DS_Store, Thumbs.db) is obviously junk, but an
orphaned file could just as easily be a resource referenced only from
somewhere this project doesn't parse yet (a font referenced only from
another font's own metadata, an SVG referenced from inside another
SVG), so this module only lists what it found.
"""
from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# Entries every valid EPUB has, or reasonably can have, that are never
# themselves listed in the OPF manifest -- these aren't orphans even
# though nothing in the manifest sense "references" them.
ALWAYS_EXPECTED = {"mimetype", "META-INF/container.xml"}


@dataclass
class OrphanedFile:
    path: str = ""   # exact path inside the zip archive
    size: int = 0    # bytes, for a quick sense of how much dead weight this is


@dataclass
class BookPackagingSummary:
    orphaned_files: list = field(default_factory=list)  # [OrphanedFile]

    @property
    def orphaned_file_count(self) -> int:
        return len(self.orphaned_files)

    @property
    def orphaned_bytes_total(self) -> int:
        return sum(f.size for f in self.orphaned_files)


def _resolve(base, href) -> str:
    """Resolve an href relative to `base`, collapsing any ../ segments.
    Same helper images.py uses, duplicated here on purpose -- each
    analysis module in this project is self-contained rather than
    sharing small utilities across files."""
    return posixpath.normpath(str(PurePosixPath(base) / href))


def analyze_book_packaging(book) -> BookPackagingSummary:
    """
    Compares every real entry in the EPUB's zip archive against every
    file the manifest, the OPF's own location, and the standard EPUB
    container entries account for. Anything left over is a genuinely
    orphaned file -- present in the archive, referenced by nothing.
    """
    summary = BookPackagingSummary()

    accounted_for = set(ALWAYS_EXPECTED)
    package_path = getattr(book, "package_path", "") or ""
    if package_path:
        accounted_for.add(package_path)

    base = PurePosixPath(package_path).parent if package_path else PurePosixPath(".")
    for item in getattr(book, "manifest", []) or []:
        accounted_for.add(_resolve(base, item.href))

    source = getattr(book, "source", None)
    if source is None:
        return summary

    try:
        with zipfile.ZipFile(source, "r") as archive:
            for info in archive.infolist():
                path = info.filename
                if path.endswith("/"):
                    continue  # directory entry, not a real file
                if path in accounted_for:
                    continue
                summary.orphaned_files.append(OrphanedFile(path=path, size=info.file_size))
    except (OSError, zipfile.BadZipFile):
        pass

    return summary
