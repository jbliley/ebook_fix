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
from ebook_fix.report import Report


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

        if self.config.sync_declarations and self._needs_sync(cover):
            sync_declarations(opf, cover.cover_item)
            report.add(
                "content.opf",
                "Cover declaration synced",
                "EPUB2/EPUB3 cover declarations brought into agreement",
            )
            changed = True

        if self.config.standardize_filename:
            renamed_to = self._standardize_filename(book, opf, cover)
            if renamed_to:
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
