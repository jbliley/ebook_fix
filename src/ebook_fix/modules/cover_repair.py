"""
ebook_fix.modules.cover_repair

Uses the cover findings the analyzer already collected (see
ebook_fix.cover) instead of re-scanning the book. Two related, but
separate, actions:

1. Declaration sync -- if exactly one clearly-resolved, existing,
   valid cover image is found, make sure BOTH the EPUB2 <meta
   name="cover"> and EPUB3 properties="cover-image" conventions point
   at it, adding whichever one is missing. Same "write both, leave no
   reader unsupported" posture as ebook_fix.series already takes
   toward calibre's vs EPUB3's series conventions. If the two
   conventions currently disagree, the EPUB3 declaration wins --
   ebook_fix.cover's own analysis already resolves that same way when
   picking `cover_item` (matching the priority modern reading systems
   themselves give it), so syncing the EPUB2 tag to match isn't a new
   guess, just making the file agree with what analysis already
   decided.
2. Filename standardization -- renames the resolved cover file to
   ebook_fix's standard "cover.<ext>" (see
   ebook_fix.cover.standard_cover_filename), keeping it in whatever
   folder it already lives in, and updates every reference: the
   manifest href, both cover declarations, and any <img>/<image>
   element (in any chapter, e.g. a dedicated cover.xhtml page) that
   points at the old filename.

The low-level OPF-editing operations both actions need
(find_manifest_item_element, swap_filename, sync_declarations,
rewrite_chapter_image_references) live in ebook_fix.cover itself,
shared with Engine.replace_cover -- see that module for why.

What this deliberately does NOT do, per docs/cover_repair_replace_plan.md's
core principle (repair should only act with reasonable confidence):

- No cover declared at all -- nothing to resolve from, no filename
  heuristics attempted. Reported by engine.py's own [Cover Image]
  analysis section already; nothing repairable here.
- Declared cover's file missing from the archive, or not an image --
  no real image resource exists to sync/rename.
- Multiple *different* items both carrying properties="cover-image"
  -- genuinely ambiguous, not just a stale-metadata mismatch; left
  alone rather than guessed at.

Explicit cover replacement (the `replace-cover` command) is a
separate operation entirely -- see Engine.replace_cover -- since a
user-supplied image doesn't need any of this guessing in the first
place.
"""

from __future__ import annotations

import posixpath
import struct
import zipfile
from pathlib import PurePosixPath

from ebook_fix.config import CoverRepairConfig
from ebook_fix.cover import (
    analyze_book_cover,
    archive_names,
    find_manifest_item_element,
    rewrite_chapter_image_references,
    standard_cover_filename,
    swap_filename,
    sync_declarations,
)
from ebook_fix.modules.gutenberg_repair import _remove_whole_chapter
from ebook_fix.report import Report

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_OPF_NS = "http://www.idpf.org/2007/opf"

# How many pages after the cover page are checked for a repeat of it,
# and how close (as a fraction) the two images' shapes must be.
_DUPLICATE_SEARCH_PAGES = 3
_ASPECT_TOLERANCE = 0.02


def image_size(data: bytes):
    """(width, height) of a JPEG, PNG or GIF read from its header, or
    None if it can't be read. No image library needed."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except Exception:
        return None
    return None


def _page_images(book, chapter):
    """Full archive paths of every image a page shows (<img> and SVG
    <image>)."""
    base = PurePosixPath(book.package_path).parent
    chapter_dir = PurePosixPath(posixpath.normpath(str(base / chapter.href))).parent
    found = []
    for img in chapter.document.findall(".//{*}img"):
        src = img.get("src")
        if src:
            found.append(posixpath.normpath(str(chapter_dir / src.split("#")[0])))
    for image in chapter.document.findall(".//{*}image"):
        href = image.get(_XLINK_HREF) or image.get("href")
        if href:
            found.append(posixpath.normpath(str(chapter_dir / href.split("#")[0])))
    return found


def _page_has_text(chapter) -> bool:
    body = chapter.document.find(".//{*}body")
    if body is None:
        return True
    for el in body.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag.endswith("}script") or tag.endswith("}style"):
            continue
        if (el.text or "").strip() or ((el.tail or "").strip() and el is not body):
            return True
    return False


def find_duplicate_cover_page(book, cover):
    """Returns (chapter, image_path) for a page that holds nothing but
    a second copy of the cover art near the real cover page, or None.
    Conservative on purpose: the page must have no text and one image,
    sit within the first few pages after the cover page, and the image
    must have the same shape as the cover."""
    chapters = [c for c in book.chapters if c.document is not None]
    cover_path = cover.resolved_href
    cover_idx = None
    for i, c in enumerate(chapters[:6]):
        if cover_path in _page_images(book, c):
            cover_idx = i
            break
    if cover_idx is None:
        return None
    try:
        with zipfile.ZipFile(book.source, "r") as archive:
            cover_size = image_size(archive.read(cover_path))
            if not cover_size or not all(cover_size):
                return None
            for c in chapters[cover_idx + 1:cover_idx + 1 + _DUPLICATE_SEARCH_PAGES]:
                if _page_has_text(c):
                    # A page of text (e.g. a book-details page) can sit
                    # between the cover and its repeat; keep looking.
                    continue
                images = _page_images(book, c)
                if not images:
                    continue
                if len(set(images)) != 1 or images[0] == cover_path:
                    return None
                try:
                    size = image_size(archive.read(images[0]))
                except KeyError:
                    return None
                if not size or not all(size):
                    return None
                ratio_cover = cover_size[0] / cover_size[1]
                ratio_page = size[0] / size[1]
                if abs(ratio_cover - ratio_page) / ratio_cover <= _ASPECT_TOLERANCE:
                    return c, images[0]
                return None
    except (OSError, zipfile.BadZipFile):
        return None
    return None


class CoverRepair:
    name = "Cover Repair"

    def __init__(self, config: CoverRepairConfig | None = None):
        self.config = config or CoverRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        cover = self._get_summary(book, analysis)
        if not self._is_repairable(cover):
            return report

        if self.config.sync_declarations and self._needs_sync(cover):
            report.add(
                "content.opf",
                "Cover declaration synced",
                "EPUB2/EPUB3 cover declarations will be brought into agreement",
            )

        if self.config.standardize_filename:
            target = standard_cover_filename(cover.cover_item.media_type, cover.resolved_href)
            current_name = PurePosixPath(cover.resolved_href).name
            if current_name != target:
                report.add(
                    "content.opf",
                    "Cover file renamed",
                    f"{cover.resolved_href} -> standardized filename \"{target}\"",
                )

        if self.config.remove_duplicate_cover_page:
            dup = find_duplicate_cover_page(book, cover)
            if dup is not None:
                report.add(
                    dup[0].href,
                    "Duplicate cover page",
                    "This page only repeats the cover image near the cover page "
                    "and will be removed.",
                )

        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        cover = self._get_summary(book, analysis)
        if not self._is_repairable(cover):
            return report

        opf = getattr(book, "opf_document", None)
        if opf is None:
            return report

        changed = False

        # Before the rename below: finding the duplicate relies on the
        # cover's current filename.
        if self.config.remove_duplicate_cover_page:
            dup = find_duplicate_cover_page(book, cover)
            if dup is not None:
                page, image_path = dup
                page_href = page.href
                if _remove_whole_chapter(book, page_href):
                    report.add(
                        page_href,
                        "Duplicate cover page removed",
                        "This page only repeated the cover image near the cover page.",
                    )
                    self._remove_image_if_unused(book, image_path, report)
                    changed = True

        if self.config.sync_declarations and self._needs_sync(cover):
            sync_declarations(opf, cover.cover_item)
            # analyze_book_cover() reads properties off book.manifest's
            # ManifestItem snapshot, not the live OPF tree just edited
            # above by sync_declarations() -- without also updating the
            # snapshot here, a fresh re-analysis on the next repair pass
            # would still see the old ("not synced yet") properties
            # string and report this same fix as needed forever, so
            # this module would never converge. cover.cover_item is the
            # very same object stored in book.manifest (analyze_book_cover
            # builds it straight from that list without copying), so
            # mutating it in place here keeps the snapshot honest.
            properties = set((cover.cover_item.properties or "").split())
            properties.add("cover-image")
            cover.cover_item.properties = " ".join(sorted(properties))
            report.add(
                "content.opf",
                "Cover declaration synced",
                "EPUB2/EPUB3 cover declarations brought into agreement",
            )
            changed = True

        if self.config.standardize_filename:
            renamed_to = self._standardize_filename(book, opf, cover)
            if renamed_to:
                # Same reasoning as above: keep the manifest snapshot's
                # href in sync with the live OPF edit _standardize_filename
                # just made, so the next pass doesn't see the stale
                # pre-rename filename and think a rename is still needed.
                target_name = PurePosixPath(renamed_to).name
                cover.cover_item.href = swap_filename(cover.cover_item.href, target_name)
                report.add(
                    "content.opf",
                    "Cover file renamed",
                    f"{cover.resolved_href} -> {renamed_to}",
                )
                changed = True

        if changed:
            book.opf_modified = True
            if hasattr(book, "mark_modified"):
                book.mark_modified()

        return report

    def _remove_image_if_unused(self, book, image_path, report):
        """Drops the removed page's image too, but only if nothing else
        in the book still uses it."""
        for c in book.chapters:
            if c.document is not None and image_path in _page_images(book, c):
                return
        name = PurePosixPath(image_path).name
        for css in book.css:
            try:
                with zipfile.ZipFile(book.source, "r") as archive:
                    base = PurePosixPath(book.package_path).parent
                    text = archive.read(str(base / css.href)).decode("utf-8", "ignore")
            except (OSError, KeyError, zipfile.BadZipFile):
                continue
            if name in text:
                return
        base = PurePosixPath(book.package_path).parent
        item = next(
            (m for m in book.manifest
             if posixpath.normpath(str(base / m.href)) == image_path),
            None,
        )
        opf = getattr(book, "opf_document", None)
        if item is None or opf is None:
            return
        manifest_el = opf.find(f"{{{_OPF_NS}}}manifest")
        if manifest_el is not None:
            for el in manifest_el.findall(f"{{{_OPF_NS}}}item"):
                if el.get("id") == item.id:
                    manifest_el.remove(el)
                    break
        book.manifest = [m for m in book.manifest if m.id != item.id]
        book.images = [r for r in book.images if r.id != item.id]
        book.removed_files.add(image_path)
        report.add("content.opf", "Unused image removed", f"{image_path} was only used by the duplicate page.")

    # -----------------------------------------------------
    # Shared helpers
    # -----------------------------------------------------

    def _get_summary(self, book, analysis=None):
        if analysis is not None and getattr(analysis, "cover", None) is not None:
            return analysis.cover
        return analyze_book_cover(book)

    @staticmethod
    def _is_repairable(cover) -> bool:
        """The one precondition both actions share: exactly one
        clearly-resolved, existing, valid image to work from. Neither
        action ever runs without this, regardless of its own config
        toggle."""
        return (
            cover.cover_item is not None
            and cover.exists_in_archive
            and cover.is_image_media_type
        )

    @staticmethod
    def _needs_sync(cover) -> bool:
        return (
            cover.meta_item is None
            or cover.properties_item is None
            or cover.meta_item.id != cover.properties_item.id
        )

    def _standardize_filename(self, book, opf, cover) -> str | None:
        target_name = standard_cover_filename(cover.cover_item.media_type, cover.resolved_href)
        old_path = cover.resolved_href
        if PurePosixPath(old_path).name == target_name:
            return None

        new_path = swap_filename(old_path, target_name)

        existing = archive_names(book)
        pending_new = set(getattr(book, "new_files", {}) or {})
        pending_removed = set(getattr(book, "removed_files", set()) or set())
        occupied = (existing | pending_new) - pending_removed - {old_path}
        if new_path in occupied:
            # Something else already lives at the target filename --
            # don't silently overwrite an unrelated file.
            return None

        with zipfile.ZipFile(book.source, "r") as archive:
            data = archive.read(old_path)
        book.new_files[new_path] = data
        book.removed_files.add(old_path)

        cover_item_el = find_manifest_item_element(opf, cover.cover_item.id)
        if cover_item_el is not None:
            cover_item_el.set("href", swap_filename(cover_item_el.get("href", ""), target_name))

        rewrite_chapter_image_references(book, old_path, new_path)

        return new_path
