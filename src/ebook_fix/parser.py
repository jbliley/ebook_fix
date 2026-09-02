"""
ebook_fix.parser

Reads an EPUB into memory. This module DOES NOT modify anything; It simply loads the EPUB into the Book model.
"""

from __future__ import annotations
import posixpath
import zipfile
from pathlib import PurePosixPath
from lxml import etree
from ebook_fix.models import (
    Book,
    Metadata,
    ManifestItem,
    Chapter,
    Resource,
    TocEntry,
)

CONTAINER_PATH = "META-INF/container.xml"
NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_OPS_NS = "http://www.idpf.org/2007/ops"
NCX_NS = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}
NAV_NS = {"x": XHTML_NS, "epub": EPUB_OPS_NS}

class EPUBParser:

    def load(self, epub_path):
        book = Book(source=epub_path)
        with zipfile.ZipFile(epub_path, "r") as archive:
            rootfile = self._find_rootfile(archive)
            book.package_path = rootfile
            package = self._load_package_document(
                archive,
                rootfile
            )
            book.opf_document = package
            book.version = package.get("version", "").strip()
            self._read_metadata(
                package,
                book
            )
            manifest = self._read_manifest(
                package
            )
            spine = self._read_spine(
                package
            )
            book.manifest = manifest
            book.spine = spine
            self._read_toc(
                archive,
                manifest,
                book
            )
            self._load_resources(
                archive,
                rootfile,
                manifest,
                book
            )
        return book

# ---------------------------------------------------------

    def _find_rootfile(self, archive):
        xml = archive.read(CONTAINER_PATH)
        tree = etree.fromstring(xml)
        namespace = {
            "c":
            "urn:oasis:names:tc:opendocument:xmlns:container"
        }
        rootfile = tree.find(
            ".//c:rootfile",
            namespaces=namespace
        )
        if rootfile is None:
            raise RuntimeError("No OPF package found.")
        return rootfile.attrib["full-path"]

# ---------------------------------------------------------

    def _load_package_document(
        self,
        archive,
        path
    ):
        xml = archive.read(path)
        return etree.fromstring(xml)

# ---------------------------------------------------------

    def _read_metadata(
        self,
        package,
        book
    ):
        ns = {
            "opf": "http://www.idpf.org/2007/opf",
            "dc": "http://purl.org/dc/elements/1.1/"
        }
        metadata = Metadata()
        metadata.title = self._text(
            package.find(
                ".//dc:title",
                ns
            )
        )
        metadata.creator = self._text(
            package.find(
                ".//dc:creator",
                ns
            )
        )
        metadata.language = self._text(
            package.find(
                ".//dc:language",
                ns
            )
        )
        metadata.publisher = self._text(
            package.find(
                ".//dc:publisher",
                ns
            )
        )
        metadata.identifier = self._text(
            package.find(
                ".//dc:identifier",
                ns
            )
        )
        metadata.description = self._text(
            package.find(
                ".//dc:description",
                ns
            )
        )
        metadata.date = self._text(
            package.find(
                ".//dc:date",
                ns
            )
        )
        metadata.rights = self._text(
            package.find(
                ".//dc:rights",
                ns
            )
        )
        metadata.subject = [
            self._text(el)
            for el in package.findall(
                ".//dc:subject",
                ns
            )
            if self._text(el)
        ]
        book.metadata = metadata

# ---------------------------------------------------------

    def _read_manifest(
        self,
        package
    ):
        ns = {
            "opf": "http://www.idpf.org/2007/opf"
        }
        items = []
        for item in package.findall(
            ".//opf:manifest/opf:item",
            ns
        ):
            items.append(
                ManifestItem(
                    id=item.attrib["id"],
                    href=item.attrib["href"],
                    media_type=item.attrib["media-type"],
                    properties=item.attrib.get(
                        "properties",
                        ""
                    ),
                )
            )
        return items

# ---------------------------------------------------------

    def _read_spine(
        self,
        package
    ):
        ns = {
            "opf": "http://www.idpf.org/2007/opf"
        }
        spine = []
        for ref in package.findall(
            ".//opf:spine/opf:itemref",
            ns
        ):
            spine.append(
                ref.attrib["idref"]
            )
        return spine

# ---------------------------------------------------------

    def _load_resources(
        self,
        archive,
        rootfile,
        manifest,
        book
    ):
        base = PurePosixPath(rootfile).parent
        by_id = {item.id: item for item in manifest}

        # Chapters load in spine (reading) order, not manifest order --
        # the manifest's own listing order is arbitrary and carries no
        # meaning. Anything that cares about the book's actual reading
        # sequence (chapter-boundary detection, front/back-matter
        # zoning, TOC matching) needs this to be right.
        loaded_ids = set()
        for idref in book.spine:
            item = by_id.get(idref)
            if item is None or item.media_type != "application/xhtml+xml":
                continue
            book.chapters.append(
                self._load_chapter(archive, base, item)
            )
            loaded_ids.add(item.id)

        for item in manifest:
            if item.media_type != "application/xhtml+xml":
                continue
            if item.id in loaded_ids:
                continue
            # An xhtml file the manifest declares but the spine never
            # reads (a nav document not in the reading order, an
            # orphaned page, etc.) -- still load it as a Chapter so
            # nothing downstream silently loses it, just after every
            # real spine chapter so ordering stays meaningful.
            book.chapters.append(
                self._load_chapter(archive, base, item)
            )

        for item in manifest:
            if item.media_type == "application/xhtml+xml":
                continue
            elif item.media_type == "text/css":
                book.css.append(
                    Resource(
                        item.id,
                        item.href,
                        item.media_type
                    )
                )
            elif item.media_type.startswith(
                "image/"
            ):
                book.images.append(
                    Resource(
                        item.id,
                        item.href,
                        item.media_type
                    )
                )
            elif "font" in item.media_type:
                book.fonts.append(
                    Resource(
                        item.id,
                        item.href,
                        item.media_type
                    )
                )
            else:
                book.other.append(
                    Resource(
                        item.id,
                        item.href,
                        item.media_type
                    )
                )

# ---------------------------------------------------------

    def _load_chapter(self, archive, base, item):
        href = str(base / item.href)
        xml = archive.read(href)
        xml_parser = etree.XMLParser(
            recover=True,
            encoding="utf-8"
        )
        return Chapter(
            id=item.id,
            href=item.href,
            media_type=item.media_type,
            document=etree.fromstring(
                xml,
                xml_parser
            ),
        )

# ---------------------------------------------------------
# Table of contents (NCX and/or EPUB3 nav document)
# ---------------------------------------------------------

    def _read_toc(
        self,
        archive,
        manifest,
        book
    ):
        """Populates book.toc from whichever of the NCX / nav document
        the book actually has. Prefers the NCX when both are present
        and both parse to something -- see epub3_upgrade.py's own
        label-priority fix for why (a converter-stamped per-page
        <title> is common and unreliable, but an NCX navLabel is
        usually hand-authored or carried over from a real source)."""

        base = PurePosixPath(book.package_path).parent

        ncx_item = next(
            (i for i in manifest if i.media_type == NCX_MEDIA_TYPE),
            None
        )
        nav_item = next(
            (i for i in manifest if "nav" in i.properties.split()),
            None
        )

        entries = []
        source = ""
        ncx_tree = None

        if ncx_item is not None:
            xml = self._read_optional(archive, str(base / ncx_item.href))
            if xml is not None:
                ncx_tree = self._parse_xml(xml)
            if ncx_tree is not None:
                ncx_dir = str(PurePosixPath(ncx_item.href).parent)
                entries = self._parse_ncx(ncx_tree, ncx_dir)
                if entries:
                    source = "ncx"

        if not entries and nav_item is not None:
            entries = self._parse_nav_toc(archive, base, nav_item)
            if entries:
                source = "nav"

        book.toc = self._merge_split_labels(entries)
        book.toc_source = source

        # Phase 3a of the XHTML Recoder plan (see
        # docs/xhtml_recoder_plan.md): keep the NCX around as a live,
        # editable tree on the Book model -- the same idiom as
        # chapter.document / book.opf_document -- rather than only
        # ever reading it once into the read-only book.toc list above.
        # ncx_tree is None (and ncx_href empty) for a book with no NCX
        # at all, or one whose NCX is missing/malformed -- callers
        # that want to edit it need to check for that first. The
        # EPUB3 nav document doesn't need the same treatment here: its
        # media_type already puts it through the normal chapter-
        # loading path below, so it's already a live tree sitting in
        # book.chapters (see Book.nav_chapter).
        book.ncx_document = ncx_tree
        book.ncx_href = ncx_item.href if ncx_item is not None else ""

    def _merge_split_labels(self, entries):
        """Calibre's PDF-to-EPUB conversion (and others like it) often
        splits one chapter's TOC entry into two consecutive navPoints
        that point at the exact same target -- a bare "1." followed by
        the actual title "Tennessee" -- instead of one entry reading
        "1. Tennessee". Left alone, that doubles the entry count and
        breaks anything expecting one TOC entry per chapter (see
        epub3_upgrade.py's _toc_label_by_href, which used to keep only
        whichever half came first and silently drop the other -- almost
        always the real title, since the bare number is listed first).
        This collapses adjacent siblings that share an identical href
        into one entry with the labels joined, recursively, before
        anything else reads book.toc."""
        merged = []
        for entry in entries:
            entry.children = self._merge_split_labels(entry.children)
            if (
                merged
                and entry.href
                and merged[-1].href == entry.href
                and not merged[-1].children
                and not entry.children
            ):
                prev = merged[-1]
                prev.label = " ".join(
                    part for part in (prev.label, entry.label) if part
                ).strip()
                continue
            merged.append(entry)
        return merged

    def _parse_ncx(self, tree, ncx_dir):
        # ncx_dir is the NCX file's own directory (relative to the
        # OPF's, same convention as every other href in this module);
        # a navPoint's own content src is relative to the NCX file
        # itself, so this is the directory those content srcs need
        # resolving against. tree is the already-parsed NCX document --
        # see _read_toc, which loads it once and keeps it around on
        # book.ncx_document (Phase 3a of the XHTML Recoder plan)
        # instead of this method re-reading and re-parsing it itself.
        nav_map = tree.find("ncx:navMap", NCX_NS)
        if nav_map is None:
            return []
        return [
            self._ncx_nav_point(point, ncx_dir)
            for point in nav_map.findall("ncx:navPoint", NCX_NS)
        ]

    def _ncx_nav_point(self, point, ncx_dir):
        label = self._text(
            point.find("ncx:navLabel/ncx:text", NCX_NS)
        )
        content = point.find("ncx:content", NCX_NS)
        src = content.get("src", "") if content is not None else ""
        return TocEntry(
            label=label,
            href=self._resolve_toc_href(ncx_dir, src),
            children=[
                self._ncx_nav_point(child, ncx_dir)
                for child in point.findall("ncx:navPoint", NCX_NS)
            ],
        )

    def _parse_nav_toc(self, archive, base, item):
        nav_dir = str(PurePosixPath(item.href).parent)
        xml = self._read_optional(archive, str(base / item.href))
        if xml is None:
            return []
        tree = self._parse_xml(xml)
        if tree is None:
            return []

        toc_nav = None
        for nav in tree.findall(".//x:nav", NAV_NS):
            if nav.get(f"{{{EPUB_OPS_NS}}}type") == "toc":
                toc_nav = nav
                break
        if toc_nav is None:
            return []

        ol = toc_nav.find("x:ol", NAV_NS)
        if ol is None:
            return []
        return self._nav_ol(ol, nav_dir)

    def _nav_ol(self, ol, nav_dir):
        entries = []
        for li in ol.findall("x:li", NAV_NS):
            a = li.find("x:a", NAV_NS)
            label = ""
            href = ""
            if a is not None:
                label = "".join(a.itertext()).strip()
                href = self._resolve_toc_href(nav_dir, a.get("href", ""))
            else:
                span = li.find("x:span", NAV_NS)
                if span is not None:
                    label = "".join(span.itertext()).strip()
            child_ol = li.find("x:ol", NAV_NS)
            children = self._nav_ol(child_ol, nav_dir) if child_ol is not None else []
            entries.append(TocEntry(label=label, href=href, children=children))
        return entries

    @staticmethod
    def _resolve_toc_href(source_dir, href):
        """Resolves an href found inside the NCX/nav document (which is
        relative to wherever that document lives) into the same
        OPF-relative form every other href in this module uses, so it
        can be compared directly against a Chapter's own href."""
        if not href:
            return ""
        path, _, fragment = href.partition("#")
        if not path:
            # A bare "#fragment" with no file part -- unusual for an
            # NCX/nav (each entry normally names its own document), and
            # not resolvable without more context. Left as-is.
            return href
        resolved = posixpath.normpath(posixpath.join(source_dir, path))
        return f"{resolved}#{fragment}" if fragment else resolved

    @staticmethod
    def _read_optional(archive, full_path):
        """Reads a full in-zip path, tolerating a missing or malformed
        reference (a manifest entry pointing at a file that doesn't
        actually exist in the zip) rather than crashing the whole
        parse over it."""
        try:
            return archive.read(full_path)
        except KeyError:
            return None

    @staticmethod
    def _parse_xml(xml_bytes):
        try:
            return etree.fromstring(
                xml_bytes,
                etree.XMLParser(recover=True, encoding="utf-8"),
            )
        except etree.XMLSyntaxError:
            return None

# ---------------------------------------------------------

    @staticmethod
    def _text(node):
        if node is None:
            return ""
        return (node.text or "").strip()