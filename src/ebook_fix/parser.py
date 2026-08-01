"""
ebook_fix.parser

Reads an EPUB into memory. This module DOES NOT modify anything; It simply loads the EPUB into the Book model.
"""

from __future__ import annotations
import zipfile
from pathlib import PurePosixPath
from lxml import etree
from ebook_fix.models import (
    Book,
    Metadata,
    ManifestItem,
    Chapter,
    Resource,
)

CONTAINER_PATH = "META-INF/container.xml"

class EPUBParser:

    def load(self, epub_path):
        book = Book(source=epub_path)
        with zipfile.ZipFile(epub_path, "r") as archive:
            rootfile = self._find_rootfile(archive)
            package = self._load_package_document(
                archive,
                rootfile
            )
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

        for item in manifest:
            href = str(base / item.href)
            if item.media_type == "application/xhtml+xml":
                xml = archive.read(href)
                chapter = Chapter(
                    id=item.id,
                    href=item.href,
                    media_type=item.media_type,
                    document=etree.fromstring(xml),
                )
                book.chapters.append(
                    chapter
                )
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

    @staticmethod
    def _text(node):
        if node is None:
            return ""
        return (node.text or "").strip()