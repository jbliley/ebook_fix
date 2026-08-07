"""
ebook_fix.modules.epub3_upgrade

Upgrades EPUB 2.x (and older Open Packaging Format) books to EPUB 3.

This is a structural upgrade, not a content rewrite -- it doesn't touch
chapter markup. It:

1. Bumps the OPF <package version="..."> attribute to "3.0".
2. Adds/refreshes a dcterms:modified metadata entry, required by EPUB 3.
3. Generates an EPUB 3 Navigation Document (nav.xhtml) built from the
   spine reading order and each chapter's own <title> (falling back to
   its first heading, then its filename), since EPUB 3 requires exactly
   one manifest item with properties="nav".

The existing NCX, if present, is left exactly as-is and still
referenced by <spine toc="ncx">, so EPUB2-only readers keep working.

Runs first in the repair pipeline (see engine.py) so every other
module operates on the upgraded structure.
"""

from __future__ import annotations
import datetime
from pathlib import PurePosixPath
from lxml import etree

from ebook_fix import epub_version
from ebook_fix.models import ManifestItem
from ebook_fix.report import Report

OPF_NS = "http://www.idpf.org/2007/opf"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_OPS_NS = "http://www.idpf.org/2007/ops"

NAV_MEDIA_TYPE = "application/xhtml+xml"


class EPUB3UpgradeRepair:
    name = "EPUB 3 Upgrade"

    def __init__(self, config=None):
        self.config = config

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        if self.config is not None and not getattr(self.config, "enabled", True):
            return report

        info = epub_version.detect(book)
        if info.needs_upgrade:
            report.add(
                "content.opf",
                "EPUB version below 3.0",
                f"Package version is {info.detected_version!r}; "
                f"will be upgraded to EPUB {info.target_version}.",
            )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        if self.config is not None and not getattr(self.config, "enabled", True):
            return

        info = epub_version.detect(book)
        if not info.needs_upgrade:
            return

        opf = getattr(book, "opf_document", None)
        if opf is None:
            # Nothing parsed to edit against -- can't safely upgrade.
            return

        opf.set("version", info.target_version)
        self._ensure_modified_meta(opf)
        self._add_nav_document(book, opf)

        book.version = info.target_version
        book.opf_modified = True
        book.mark_modified()

    # -----------------------------------------------------
    # Metadata
    # -----------------------------------------------------

    def _ensure_modified_meta(self, opf):
        metadata = opf.find(f"{{{OPF_NS}}}metadata")
        if metadata is None:
            return
        for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
            if meta.get("property") == "dcterms:modified":
                meta.text = self._timestamp()
                return
        meta = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
        meta.set("property", "dcterms:modified")
        meta.text = self._timestamp()

    def _timestamp(self):
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # -----------------------------------------------------
    # Navigation document
    # -----------------------------------------------------

    def _add_nav_document(self, book, opf):
        manifest = opf.find(f"{{{OPF_NS}}}manifest")
        if manifest is None:
            return

        # Already has a nav doc -- don't add a second one.
        for item in manifest.findall(f"{{{OPF_NS}}}item"):
            if "nav" in (item.get("properties") or "").split():
                return

        existing_hrefs = {item.get("href") for item in manifest.findall(f"{{{OPF_NS}}}item")}
        existing_ids = {item.get("id") for item in manifest.findall(f"{{{OPF_NS}}}item")}

        href = self._unique(existing_hrefs, "nav.xhtml", "nav{n}.xhtml")
        item_id = self._unique(existing_ids, "nav", "nav{n}")

        item = etree.SubElement(manifest, f"{{{OPF_NS}}}item")
        item.set("id", item_id)
        item.set("href", href)
        item.set("media-type", NAV_MEDIA_TYPE)
        item.set("properties", "nav")

        book.manifest.append(
            ManifestItem(id=item_id, href=href, media_type=NAV_MEDIA_TYPE, properties="nav")
        )

        base = PurePosixPath(book.package_path).parent
        book.new_files[str(base / href)] = self._build_nav_bytes(book)

    def _unique(self, existing, first, template):
        if first not in existing:
            return first
        n = 1
        while template.format(n=n) in existing:
            n += 1
        return template.format(n=n)

    def _build_nav_bytes(self, book):
        E = f"{{{XHTML_NS}}}"
        html = etree.Element(E + "html", nsmap={None: XHTML_NS, "epub": EPUB_OPS_NS})
        head = etree.SubElement(html, E + "head")
        title_el = etree.SubElement(head, E + "title")
        title_el.text = getattr(book.metadata, "title", "") or "Table of Contents"

        body = etree.SubElement(html, E + "body")
        nav = etree.SubElement(body, E + "nav")
        nav.set(f"{{{EPUB_OPS_NS}}}type", "toc")
        nav.set("id", "toc")
        heading = etree.SubElement(nav, E + "h1")
        heading.text = "Table of Contents"
        ol = etree.SubElement(nav, E + "ol")

        by_id = {item.id: item for item in book.manifest}
        chapters_by_id = {c.id: c for c in book.chapters}
        for idref in book.spine:
            item = by_id.get(idref)
            if item is None or item.media_type != "application/xhtml+xml":
                continue
            label = self._chapter_label(chapters_by_id.get(idref)) or item.href
            li = etree.SubElement(ol, E + "li")
            a = etree.SubElement(li, E + "a")
            a.set("href", item.href)
            a.text = label

        return etree.tostring(
            html,
            xml_declaration=True,
            encoding="utf-8",
            doctype="<!DOCTYPE html>",
        )

    def _chapter_label(self, chapter):
        if chapter is None or chapter.document is None:
            return None
        doc = chapter.document
        head_title = doc.find(".//{*}head/{*}title")
        if head_title is not None:
            text = (head_title.text or "").strip()
            if text:
                return text
        for level in range(1, 7):
            heading = doc.find(f".//{{*}}h{level}")
            if heading is not None:
                text = "".join(heading.itertext()).strip()
                if text:
                    return text
        return None
