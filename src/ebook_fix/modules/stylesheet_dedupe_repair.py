"""
ebook_fix.modules.stylesheet_dedupe_repair

Moves a stylesheet that is pasted, word for word, into the top of several
chapter files out into one shared stylesheet file. Found in "Sandman Slim"
(Richard Kadrey), where the same 42 KB block of style rules sits inside
12 of the book's files, so the file is padded with the same rules 12 times
and any later style change has to be made 12 times.

What counts: a <style> block in a page's <head> whose text is identical
(ignoring spacing) in at least two files of the book and is at least
min_size bytes long (1024 by default, so the tiny @page rule many
converters stamp into every page is left alone). The first copy is
written to a new file (stylesheet.css, or the next free name), added to the
book's file list, and every copy is replaced by a <link> to that file, in
the same spot, so the order of the rules does not change.

What is left alone:
- A <style> block that only one file has, or that differs between files.
- A block with a media or other special attribute.
- A file that has the same block more than once.
- A block that refers to other files (url(...) or @import) when its files
  are not all in the same folder, because the paths in it would stop
  pointing at the right place once the rules move to a new folder.

Runs before the other stylesheet repairs, so they see the shared file. As a
side effect, Text Size can now act on a book it used to skip because of the
embedded rules. A second run finds nothing to move and does nothing.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath

from lxml import etree

from ebook_fix.config import StylesheetDedupeConfig
from ebook_fix.cover import archive_names
from ebook_fix.models import ManifestItem, Resource
from ebook_fix.report import Report

OPF_NS = "http://www.idpf.org/2007/opf"
CSS_MEDIA_TYPE = "text/css"
NEW_FILE_STEM = "stylesheet"

_WHITESPACE = re.compile(r"\s+")
_HTML_COMMENT_WRAP = re.compile(r"^\s*<!--(.*?)-->\s*$", re.DOTALL)
_REFERS_TO_FILES = re.compile(r"url\s*\(|@import", re.IGNORECASE)


class _Copy:
    """One <style> block found in one file."""

    __slots__ = ("chapter", "element", "chapter_dir")

    def __init__(self, chapter, element, chapter_dir):
        self.chapter = chapter
        self.element = element
        self.chapter_dir = chapter_dir


class EmbeddedStylesheetRepair:
    name = "Embedded Stylesheet Consolidation"

    def __init__(self, config: StylesheetDedupeConfig | None = None):
        self.config = config or StylesheetDedupeConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        groups, _skipped = self._find_groups(book)
        for css_text, copies in groups:
            size_kb = len(css_text.encode("utf-8")) / 1024
            for copy in copies:
                report.add(
                    copy.chapter.href,
                    "Repeated embedded stylesheet",
                    f"{copy.chapter.href}: a {size_kb:.1f} KB stylesheet that {len(copies)} files all carry "
                    "will be moved to one shared stylesheet file",
                )
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        opf = getattr(book, "opf_document", None)
        if opf is None:
            return report
        manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
        if manifest_el is None:
            return report

        groups, _skipped = self._find_groups(book)
        for css_text, copies in groups:
            css_href = self._choose_href(book, copies, css_text)
            if css_href is None:
                continue
            self._write_stylesheet(book, manifest_el, css_href, css_text)

            for copy in copies:
                link = self._make_link(copy, css_href)
                parent = copy.element.getparent()
                if parent is None:
                    continue
                link.tail = copy.element.tail
                parent.replace(copy.element, link)
                copy.chapter.modified = True
                report.add(
                    copy.chapter.href,
                    "Embedded stylesheet moved",
                    f"{copy.chapter.href}: the embedded stylesheet was replaced by a link to {css_href}",
                )
            report.add(
                css_href,
                "Shared stylesheet created",
                f"{css_href}: holds the stylesheet that {len(copies)} files used to carry",
            )

        if report.count:
            if hasattr(book, "mark_modified"):
                book.mark_modified()
        return report

    # -----------------------------------------------------
    # Finding the repeated blocks
    # -----------------------------------------------------

    def _find_groups(self, book):
        """([(css_text, [_Copy, ...]), ...], skipped_notes). Only blocks
        that appear exactly once in each of at least two files."""
        by_key: dict[str, list[_Copy]] = {}
        text_for_key: dict[str, str] = {}
        counts: dict[tuple[str, str], int] = {}

        for chapter in book.chapters:
            document = getattr(chapter, "document", None)
            if document is None:
                continue
            chapter_dir = posixpath.dirname(chapter.href)
            for style in document.iterfind(".//{*}head//{*}style"):
                if not self._is_plain_style(style):
                    continue
                css_text = self._clean_text(style.text)
                if not css_text:
                    continue
                key = _WHITESPACE.sub(" ", css_text)
                counts[(key, chapter.href)] = counts.get((key, chapter.href), 0) + 1
                by_key.setdefault(key, []).append(_Copy(chapter, style, chapter_dir))
                text_for_key.setdefault(key, css_text)

        groups = []
        skipped = []
        for key, copies in by_key.items():
            usable = [c for c in copies if counts[(key, c.chapter.href)] == 1]
            if len(usable) < 2:
                continue
            css_text = text_for_key[key]
            if len(css_text.encode("utf-8")) < self.config.min_size:
                continue
            if _REFERS_TO_FILES.search(css_text) and len({c.chapter_dir for c in usable}) > 1:
                skipped.append("refers to other files from several folders")
                continue
            groups.append((css_text, usable))
        return groups, skipped

    @staticmethod
    def _is_plain_style(style) -> bool:
        if len(style):
            return False
        if style.get("media") or style.get("scoped") is not None:
            return False
        style_type = (style.get("type") or "").strip().lower()
        return style_type in ("", CSS_MEDIA_TYPE)

    @staticmethod
    def _clean_text(text) -> str:
        text = (text or "").strip()
        wrapped = _HTML_COMMENT_WRAP.match(text)
        if wrapped:
            text = wrapped.group(1).strip()
        return text

    # -----------------------------------------------------
    # Writing the shared stylesheet
    # -----------------------------------------------------

    def _choose_href(self, book, copies, css_text):
        """Where the shared file goes, relative to the OPF folder, or None
        if no safe name could be found."""
        dirs = {c.chapter_dir for c in copies}
        if len(dirs) == 1:
            folder = next(iter(dirs))
        elif _REFERS_TO_FILES.search(css_text):
            return None
        else:
            existing = [posixpath.dirname(r.href) for r in getattr(book, "css", []) or []]
            folder = existing[0] if existing else ""

        base = PurePosixPath(book.package_path).parent
        taken_paths = (archive_names(book) | set(book.new_files)) - set(book.removed_files)
        taken_hrefs = {m.href for m in book.manifest}

        n = 0
        while True:
            filename = f"{NEW_FILE_STEM}.css" if n == 0 else f"{NEW_FILE_STEM}_{n}.css"
            href = posixpath.join(folder, filename) if folder else filename
            if href not in taken_hrefs and str(base / href) not in taken_paths:
                return href
            n += 1

    def _write_stylesheet(self, book, manifest_el, href, css_text) -> None:
        base = PurePosixPath(book.package_path).parent
        existing_ids = {item.get("id") for item in manifest_el.findall(f"{{{OPF_NS}}}item")}
        item_id = "embedded_styles"
        n = 0
        while item_id in existing_ids:
            n += 1
            item_id = f"embedded_styles_{n}"

        item = etree.SubElement(manifest_el, f"{{{OPF_NS}}}item")
        item.set("id", item_id)
        item.set("href", href)
        item.set("media-type", CSS_MEDIA_TYPE)

        book.manifest.append(ManifestItem(id=item_id, href=href, media_type=CSS_MEDIA_TYPE))
        book.css.append(Resource(item_id, href, CSS_MEDIA_TYPE))
        book.new_files[str(base / href)] = (css_text.rstrip() + "\n").encode("utf-8")
        book.opf_modified = True

    @staticmethod
    def _make_link(copy, css_href):
        namespace = etree.QName(copy.element).namespace
        tag = f"{{{namespace}}}link" if namespace else "link"
        link = etree.Element(tag)
        link.set("rel", "stylesheet")
        link.set("type", CSS_MEDIA_TYPE)
        start = copy.chapter_dir or "."
        link.set("href", posixpath.relpath(css_href, start=start))
        return link

