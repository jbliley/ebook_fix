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
   throw away everything but a handful of top-level entries. A book
   with no TOC at all but a detected chapter structure (see
   ebook_fix.toc_generate) gets a nav built from that structure
   instead -- still one entry per detected chapter/Part, not per
   spine file. Only a book with neither an existing TOC nor anything
   detected falls back to one entry per spine file, labeled from each
   chapter's own <title> (or first heading, or filename) -- see
   ebook_fix.toc_generate.build_spine_fallback_entries.

The existing NCX, if present, is left exactly as-is and still
referenced by <spine toc="ncx">, so EPUB2-only readers keep working.

Runs after Gutenberg Repair, Paragraph Repair, and Chapter Markup
Repair in the pipeline (see engine.py) rather than first, even though
it used to run first: nothing else in the pipeline reads book.version
or otherwise depends on the manifest/OPF already being at EPUB3 before
it runs, and generating a nav document from the book's detected
structure (see point 3 above) needs Chapter Markup to have already
wrapped each confirmed chapter, so its id can be reused instead of
stamping a second, redundant one right next to it -- see
ebook_fix.toc_generate's module docstring for the id-reuse mechanics.
"""

from __future__ import annotations
import datetime
from pathlib import PurePosixPath
from lxml import etree

from ebook_fix import epub_version
from ebook_fix.models import ManifestItem
from ebook_fix.report import Report
from ebook_fix.toc_generate import (
    build_toc_entries,
    build_spine_fallback_entries,
    build_nav_bytes,
)

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
        nav_source = self._add_nav_document(book, opf)

        book.version = target_version
        book.opf_modified = True
        book.mark_modified()

        report.add(
            "content.opf",
            "Package version upgraded",
            f"Package version {detected_version!r} upgraded to EPUB {target_version}.",
        )
        if not had_nav:
            detail = {
                "toc": "Generated an EPUB 3 nav.xhtml from the book's existing TOC.",
                "structure": "Generated an EPUB 3 nav.xhtml from the book's detected chapter structure "
                             "(no existing TOC found).",
                "spine": "Generated an EPUB 3 nav.xhtml from the spine reading order "
                         "(no existing TOC or detected chapter structure found).",
            }[nav_source]
            report.add("content.opf", "Navigation document added", detail)
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
        """Adds the required EPUB3 nav document and returns which kind
        of content it was built from ("toc", "structure", or "spine" --
        see the module docstring), or None if the book already had a
        nav document and nothing was added."""
        manifest = opf.find(f"{{{OPF_NS}}}manifest")
        if manifest is None:
            return None

        # Already has a nav doc -- don't add a second one.
        for item in manifest.findall(f"{{{OPF_NS}}}item"):
            if "nav" in (item.get("properties") or "").split():
                return None

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

        toc_entries = getattr(book, "toc", None) or []
        if toc_entries:
            entries, source = toc_entries, "toc"
        else:
            generated = build_toc_entries(book)
            entries, source = (generated, "structure") if generated else (build_spine_fallback_entries(book), "spine")

        base = PurePosixPath(book.package_path).parent
        book.new_files[str(base / href)] = build_nav_bytes(book, entries)
        return source

    def _unique(self, existing, first, template):
        if first not in existing:
            return first
        n = 1
        while template.format(n=n) in existing:
            n += 1
        return template.format(n=n)