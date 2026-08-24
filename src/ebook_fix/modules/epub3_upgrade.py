"""
ebook_fix.modules.epub3_upgrade

Upgrades EPUB 2.x (and older Open Packaging Format) books to EPUB 3.

This is a structural upgrade, not a content rewrite -- it doesn't touch
chapter markup. It:

1. Bumps the OPF <package version="..."> attribute to "3.0".
2. Adds/refreshes a dcterms:modified metadata entry, required by EPUB 3.
3. Generates an EPUB 3 Navigation Document (nav.xhtml), since EPUB 3
   requires exactly one manifest item with properties="nav". Built by
   translating the book's own existing TOC (book.toc, from the NCX or
   an existing nav document -- see parser.py) into nested <ol>/<li>
   markup, one entry per TOC entry, not one per spine file -- a book
   can easily have far more chapters than physical XHTML files (a
   calibre-style split puts many chapters in one file, addressed by
   id="..." fragments), and collapsing to one nav entry per file would
   throw away everything but a handful of top-level entries. Only a
   book with no TOC at all falls back to one entry per spine file,
   labeled from each chapter's own <title> (or first heading, or
   filename) -- see _build_spine_ol below.

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

        if analysis is not None:
            needs_upgrade = analysis.summary.epub_needs_upgrade
            detected_version = analysis.summary.epub_version
            target_version = analysis.summary.epub_target_version
        else:
            info = epub_version.detect(book)
            needs_upgrade = info.needs_upgrade
            detected_version = info.detected_version
            target_version = info.target_version

        if needs_upgrade:
            report.add(
                "content.opf",
                "EPUB version below 3.0",
                f"Package version is {detected_version!r}; "
                f"will be upgraded to EPUB {target_version}.",
            )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        report = Report(self.name)
        if self.config is not None and not getattr(self.config, "enabled", True):
            return report

        if analysis is not None:
            needs_upgrade = analysis.summary.epub_needs_upgrade
            detected_version = analysis.summary.epub_version
            target_version = analysis.summary.epub_target_version
        else:
            info = epub_version.detect(book)
            needs_upgrade = info.needs_upgrade
            detected_version = info.detected_version
            target_version = info.target_version

        if not needs_upgrade:
            return report

        opf = getattr(book, "opf_document", None)
        if opf is None:
            # Nothing parsed to edit against -- can't safely upgrade.
            return report

        had_nav = self._has_nav_document(opf)

        opf.set("version", target_version)
        self._ensure_modified_meta(opf)
        self._add_nav_document(book, opf)

        book.version = target_version
        book.opf_modified = True
        book.mark_modified()

        report.add(
            "content.opf",
            "Package version upgraded",
            f"Package version {detected_version!r} upgraded to EPUB {target_version}.",
        )
        if not had_nav:
            report.add(
                "content.opf",
                "Navigation document added",
                "Generated an EPUB 3 nav.xhtml from the book's existing TOC."
                if getattr(book, "toc", None)
                else "Generated an EPUB 3 nav.xhtml from the spine reading order (no existing TOC found).",
            )
        return report

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

    def _has_nav_document(self, opf):
        manifest = opf.find(f"{{{OPF_NS}}}manifest")
        if manifest is None:
            return False
        return any(
            "nav" in (item.get("properties") or "").split()
            for item in manifest.findall(f"{{{OPF_NS}}}item")
        )

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

        toc_entries = getattr(book, "toc", None) or []
        if toc_entries:
            ol = self._build_toc_ol(E, toc_entries)
        else:
            ol = self._build_spine_ol(E, book)
        nav.append(ol)

        return etree.tostring(
            html,
            xml_declaration=True,
            encoding="utf-8",
            doctype="<!DOCTYPE html>",
        )

    def _build_toc_ol(self, E, entries):
        """Mirrors the book's own TOC (book.toc, a TocEntry tree from
        the NCX or an existing nav document -- see parser.py) into
        EPUB 3 nav markup: one <li> per TOC entry, nested <ol>s for
        children, in the same order and depth as the source. This is
        what keeps a book with far more chapters than physical XHTML
        files (a calibre-style split puts many chapters in one file,
        addressed by #fragment) from losing everything but a handful
        of top-level entries -- see the module docstring."""
        ol = etree.Element(E + "ol")
        for entry in entries:
            li = etree.SubElement(ol, E + "li")
            if entry.href:
                a = etree.SubElement(li, E + "a")
                a.set("href", entry.href)
                a.text = entry.label or entry.href
            else:
                # EPUB 3 nav requires a <span>, not a bare <li> text,
                # for an entry with nothing to link to (rare -- a
                # heading-only navPoint with no content src).
                span = etree.SubElement(li, E + "span")
                span.text = entry.label or ""
            if entry.children:
                li.append(self._build_toc_ol(E, entry.children))
        return ol

    def _build_spine_ol(self, E, book):
        """Fallback for a book with no TOC at all (no NCX, no existing
        nav document): one <li> per spine file, labeled from that
        chapter's own <title> (falling back to its first heading, then
        its filename). Coarser than a real TOC, but there's no finer
        structure to draw on."""
        ol = etree.Element(E + "ol")
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
        return ol

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