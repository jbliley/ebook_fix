"""
ebook_fix.modules.junk_file_repair

Removes files that do not belong in a finished book. Found in "Sandman
Slim" (Richard Kadrey), which carries a calibre_bookmarks.txt, a second
copy of the cover picture that nothing uses, and a second copy of a logo.

Two kinds of leftover are handled:

1. Known junk files that tools leave behind inside the book's zip:
   calibre_bookmarks.txt (Calibre's reading bookmarks), .DS_Store, Thumbs.db,
   ehthumbs.db, desktop.ini and the __MACOSX folder. A file like this is only
   removed when it is not part of the reading order. Nothing else in the
   META-INF folder is ever touched.

2. Pictures that are byte-for-byte copies of another picture in the book.
   One copy is kept (the declared cover if one of them is the cover,
   otherwise the one the book actually uses, otherwise the first listed):
   - a copy that nothing refers to is removed;
   - a copy that is only shown through an <img> or SVG <image> in a page is
     repointed to the kept copy first, then removed;
   - a copy referred to from anywhere else (a stylesheet, a link, the
     cover or guide entries, encryption.xml) is left alone and reported.
   A picture that has no identical twin is never touched, used or not.

A second run finds nothing left to remove and does nothing.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote

from ebook_fix.config import JunkFileConfig
from ebook_fix.cover import analyze_book_cover, find_manifest_item_element
from ebook_fix.report import Report

OPF_NS = "http://www.idpf.org/2007/opf"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
ENCRYPTION_PATH = "META-INF/encryption.xml"

JUNK_BASENAMES = frozenset({
    "calibre_bookmarks.txt",
    ".ds_store",
    "thumbs.db",
    "ehthumbs.db",
    "desktop.ini",
})
JUNK_FOLDER_PREFIX = "__macosx/"

_CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)", re.IGNORECASE)
_REFERENCE_ATTRS = ("src", "href", XLINK_HREF, "data", "poster")


class JunkFileRepair:
    name = "Junk File Removal"

    def __init__(self, config: JunkFileConfig | None = None):
        self.config = config or JunkFileConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for path in self._junk_paths(book):
            report.add(path, "Junk file", f"{path}: a leftover file that is not part of the book")
        for plan in self._duplicate_plans(book):
            report.add(plan.remove_href, "Duplicate picture", plan.describe(will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        changed_chapters = set()

        for path in self._junk_paths(book):
            self._remove_path(book, path)
            report.add(path, "Junk file removed", f"{path}: a leftover file that is not part of the book")

        for plan in self._duplicate_plans(book):
            for chapter, element, attr in plan.repoints:
                element.set(attr, self._relative(chapter, plan.keep_href))
                chapter.modified = True
                changed_chapters.add(chapter.href)
            self._remove_manifest_file(book, plan.remove_href)
            report.add(plan.remove_href, "Duplicate picture removed", plan.describe(will=False))

        if report.count and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report

    # -----------------------------------------------------
    # Known junk files
    # -----------------------------------------------------

    def _junk_paths(self, book) -> list[str]:
        if not self.config.remove_known_junk:
            return []
        base = PurePosixPath(book.package_path).parent
        in_spine = self._spine_paths(book, base)

        with zipfile.ZipFile(book.source, "r") as archive:
            names = archive.namelist()

        found = []
        for name in names:
            if name in book.removed_files or name.endswith("/"):
                continue
            lowered = name.lower()
            basename = posixpath.basename(lowered)
            is_junk = (
                basename in JUNK_BASENAMES
                or lowered.startswith(JUNK_FOLDER_PREFIX)
            )
            if not is_junk:
                continue
            if name in in_spine:
                continue
            found.append(name)
        return found

    def _spine_paths(self, book, base) -> set:
        by_id = {m.id: m for m in book.manifest}
        paths = set()
        for idref in book.spine:
            item = by_id.get(idref)
            if item is not None:
                paths.add(unquote(str(base / item.href)))
        return paths

    def _remove_path(self, book, path) -> None:
        base = PurePosixPath(book.package_path).parent
        for item in list(book.manifest):
            if unquote(str(base / item.href)) == path:
                self._drop_manifest_item(book, item)
        book.removed_files.add(path)

    # -----------------------------------------------------
    # Duplicate pictures
    # -----------------------------------------------------

    def _duplicate_plans(self, book) -> list:
        if not self.config.remove_duplicate_images:
            return []
        base = PurePosixPath(book.package_path).parent

        images = [m for m in book.manifest if m.media_type.startswith("image/")]
        by_hash: dict[str, list] = {}
        for item in images:
            data = self._read_bytes(book, unquote(str(base / item.href)))
            if data is None:
                continue
            by_hash.setdefault(hashlib.sha256(data).hexdigest(), []).append(item)

        groups = [items for items in by_hash.values() if len(items) > 1]
        if not groups:
            return []

        refs = _References(book, base)
        protected = self._protected_paths(book, base)
        cover_ids = self._cover_item_ids(book)

        plans = []
        for items in groups:
            declared = [i for i in items if i.id in cover_ids or "cover-image" in i.properties.split()]
            if len(declared) > 1:
                continue
            if declared:
                keeper = declared[0]
            else:
                used = [i for i in items if refs.is_used(unquote(str(base / i.href)))]
                keeper = used[0] if used else items[0]

            keep_path = unquote(str(base / keeper.href))
            for item in items:
                if item is keeper:
                    continue
                path = unquote(str(base / item.href))
                if path in protected or item.id in cover_ids or "cover-image" in item.properties.split():
                    continue
                if refs.blocked(path):
                    continue
                plans.append(_Plan(
                    remove_href=item.href,
                    keep_href=keeper.href,
                    repoints=list(refs.markup_refs(path)),
                    keep_path=keep_path,
                ))
        return plans

    def _protected_paths(self, book, base) -> set:
        """Paths the book's own packaging points at: the guide entries
        and anything named in encryption.xml."""
        protected = set()
        opf = getattr(book, "opf_document", None)
        if opf is not None:
            guide = opf.find(f"{{{OPF_NS}}}guide")
            if guide is not None:
                for ref in guide.findall(f"{{{OPF_NS}}}reference"):
                    href = (ref.get("href") or "").split("#")[0]
                    if href:
                        protected.add(unquote(posixpath.normpath(str(base / href))))
        try:
            with zipfile.ZipFile(book.source, "r") as archive:
                if ENCRYPTION_PATH in archive.namelist():
                    text = archive.read(ENCRYPTION_PATH).decode("utf-8", "replace")
                    for item in book.manifest:
                        full = unquote(str(base / item.href))
                        if full in text:
                            protected.add(full)
        except (OSError, zipfile.BadZipFile):
            pass
        return protected

    def _cover_item_ids(self, book) -> set:
        ids = set()
        cover = analyze_book_cover(book)
        item = getattr(cover, "cover_item", None)
        if item is not None:
            ids.add(item.id)
        opf = getattr(book, "opf_document", None)
        if opf is not None:
            for meta in opf.iter(f"{{{OPF_NS}}}meta"):
                if (meta.get("name") or "").lower() == "cover" and meta.get("content"):
                    ids.add(meta.get("content"))
        return ids

    @staticmethod
    def _read_bytes(book, full_path):
        new_files = getattr(book, "new_files", {}) or {}
        if full_path in new_files:
            return new_files[full_path]
        if full_path in (getattr(book, "removed_files", set()) or set()):
            return None
        try:
            with zipfile.ZipFile(book.source, "r") as archive:
                return archive.read(full_path)
        except (KeyError, OSError, zipfile.BadZipFile):
            return None

    # -----------------------------------------------------
    # Removing a manifest file
    # -----------------------------------------------------

    def _remove_manifest_file(self, book, href) -> None:
        base = PurePosixPath(book.package_path).parent
        item = next((m for m in book.manifest if m.href == href), None)
        if item is not None:
            self._drop_manifest_item(book, item)
        book.removed_files.add(unquote(str(base / href)))

    @staticmethod
    def _drop_manifest_item(book, item) -> None:
        opf = getattr(book, "opf_document", None)
        if opf is not None:
            element = find_manifest_item_element(opf, item.id)
            if element is not None:
                element.getparent().remove(element)
                book.opf_modified = True
        book.manifest = [m for m in book.manifest if m is not item]
        book.images = [r for r in book.images if r.href != item.href]
        book.other = [r for r in book.other if r.href != item.href]

    @staticmethod
    def _relative(chapter, target_href) -> str:
        start = posixpath.dirname(chapter.href) or "."
        return posixpath.relpath(target_href, start=start)


class _Plan:
    """One duplicate picture to remove, and the page references to move
    over to the kept copy first."""

    __slots__ = ("remove_href", "keep_href", "repoints", "keep_path")

    def __init__(self, remove_href, keep_href, repoints, keep_path):
        self.remove_href = remove_href
        self.keep_href = keep_href
        self.repoints = repoints
        self.keep_path = keep_path

    def describe(self, will: bool) -> str:
        verb = "will be removed" if will else "removed"
        if self.repoints:
            return (
                f"{self.remove_href}: an exact copy of {self.keep_href}; "
                f"its {len(self.repoints)} use(s) are pointed at {self.keep_href} and the copy {verb}"
            )
        return f"{self.remove_href}: an exact copy of {self.keep_href} that nothing uses; {verb}"


class _References:
    """Everything in the book that points at a file, by resolved path."""

    def __init__(self, book, base):
        self._markup: dict[str, list] = {}
        self._other: set = set()
        self._scan_chapters(book, base)
        self._scan_stylesheets(book, base)

    def is_used(self, path) -> bool:
        return path in self._markup or path in self._other

    def blocked(self, path) -> bool:
        """True if something other than an <img> or SVG <image> in a page
        points at the file."""
        return path in self._other

    def markup_refs(self, path):
        return self._markup.get(path, [])

    # -----------------------------------------------------

    def _scan_chapters(self, book, base) -> None:
        for chapter in book.chapters:
            document = getattr(chapter, "document", None)
            if document is None:
                continue
            chapter_dir = PurePosixPath(base) / posixpath.dirname(chapter.href)
            for element in document.iter():
                if not isinstance(element.tag, str):
                    continue
                local = element.tag.rsplit("}", 1)[-1].lower()
                for attr in _REFERENCE_ATTRS:
                    value = element.get(attr)
                    if not value or "://" in value or value.startswith(("data:", "#", "mailto:")):
                        continue
                    resolved = self._resolve(chapter_dir, value)
                    is_picture_ref = (local == "img" and attr == "src") or (
                        local == "image" and attr in (XLINK_HREF, "href")
                    )
                    if is_picture_ref:
                        self._markup.setdefault(resolved, []).append((chapter, element, attr))
                    else:
                        self._other.add(resolved)
                style_attr = element.get("style")
                if style_attr:
                    self._add_css_urls(style_attr, chapter_dir)
                if local == "style" and element.text:
                    self._add_css_urls(element.text, chapter_dir)

    def _scan_stylesheets(self, book, base) -> None:
        new_files = getattr(book, "new_files", {}) or {}
        try:
            archive = zipfile.ZipFile(book.source, "r")
        except (OSError, zipfile.BadZipFile):
            archive = None
        try:
            for resource in getattr(book, "css", []) or []:
                full = unquote(str(PurePosixPath(base) / resource.href))
                if full in new_files:
                    text = new_files[full].decode("utf-8", "replace")
                elif archive is not None:
                    try:
                        text = archive.read(full).decode("utf-8", "replace")
                    except KeyError:
                        continue
                else:
                    continue
                self._add_css_urls(text, PurePosixPath(full).parent)
        finally:
            if archive is not None:
                archive.close()

    def _add_css_urls(self, text, folder) -> None:
        for match in _CSS_URL.finditer(text):
            value = match.group(1).strip()
            if not value or "://" in value or value.startswith("data:"):
                continue
            self._other.add(self._resolve(folder, value))

    @staticmethod
    def _resolve(folder, value) -> str:
        value = unquote(value.split("#")[0].split("?")[0])
        return posixpath.normpath(str(PurePosixPath(folder) / value))
