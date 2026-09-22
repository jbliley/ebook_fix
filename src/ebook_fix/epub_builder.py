"""
ebook_fix.epub_builder

Assembles a finished EPUB 3 file from what a format converter (MOBI,
FB2, ...) has prepared: XHTML pages, a stylesheet, images, metadata,
series info, and a table of contents. Shared by every "convert some
other ebook format to EPUB, then reuse the whole existing analysis and
repair pipeline" converter under ebook_fix, rather than living inside
any one of them -- it moved here (from ebook_fix.mobi.epub_out) when
the FB2 converter needed the exact same assembly step. Nothing in this
module is MOBI- or FB2-specific.

Written to fit how the rest of ebook_fix already reads and writes EPUBs:
Dublin Core identifiers carry an `opf:scheme` (uuid / ISBN / MOBI-ASIN,
the names identifier_schemes.json already knows), series info is
written in both conventions ebook_fix.series already reads/writes
(calibre: meta tags and the EPUB3 belongs-to-collection block), both a
nav document and an NCX are written when there is a table of contents
(so EPUB 2 readers keep working, same as the EPUB 3 Upgrade repair
leaves things), and an OPF `<guide>` is written alongside the nav
landmarks.

A book with no table of contents at all gets neither a nav document nor
an NCX on purpose: that is exactly the state ebook_fix's own "TOC
generation when missing" repair looks for, and it can build a real
structure-based one, which a placeholder here would only get in the way
of.
"""
from __future__ import annotations

import datetime
import os
import zipfile
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from ebook_fix.series import COLLECTION_ID, format_index

XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"

# Fixed timestamp so converting the same book twice gives identical zips.
_ZIP_TIME = (2000, 1, 1, 0, 0, 0)


@dataclass
class EpubImage:
    filename: str      # e.g. "image00005.jpg"
    media_type: str
    data: bytes


@dataclass
class TocItem:
    label: str
    href: str          # relative to the OPF, e.g. "text/part0003.xhtml#filepos812"
    level: int = 0


@dataclass
class EpubSpec:
    title: str = ""
    language: str = "und"
    unique_id: str = ""            # bare UUID
    authors: list = field(default_factory=list)
    publisher: str = ""
    description: str = ""
    date: str = ""
    rights: str = ""
    subjects: list = field(default_factory=list)
    contributors: list = field(default_factory=list)
    isbn: str = ""
    mobi_asin: str = ""
    series_name: str = ""
    series_index: float | None = None
    pages: list = field(default_factory=list)        # [(title, body_html)] in reading order
    css: str = ""
    images: list = field(default_factory=list)       # list[EpubImage]
    cover_filename: str = ""
    toc: list = field(default_factory=list)          # list[TocItem]
    landmarks: list = field(default_factory=list)    # [(epub_type, href, label)]
    guide: list = field(default_factory=list)        # [(guide_type, title, href)]


def page_filename(index: int) -> str:
    return f"part{index + 1:04d}.xhtml"


def _e(text: str) -> str:
    return escape(text)


def _ea(text: str) -> str:
    return escape(text, {'"': "&quot;"})


def _page_xhtml(title: str, body: str, language: str) -> str:
    # Deliberately no indentation or line breaks between elements: every
    # such gap would become a whitespace-only text node, which the
    # Whitespace Normalizer would then report on every page of a
    # freshly converted book.
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        f'<html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" lang="{_ea(language)}" xml:lang="{_ea(language)}">'
        f"<head><title>{_e(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="../styles/style.css"/></head>'
        f"<body>{body}</body></html>\n"
    )


@dataclass
class _Node:
    item: TocItem
    children: list = field(default_factory=list)


def _build_tree(items: list) -> list:
    """Flat [TocItem with .level] -> nested nodes. A level that jumps by
    more than one is treated as one step deeper, so the nesting is
    always well formed."""
    roots: list = []
    stack: list = []
    for item in items:
        level = min(max(item.level, 0), len(stack))
        del stack[level:]
        node = _Node(item)
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)
    return roots


def _tree_depth(nodes: list) -> int:
    return 1 + max((_tree_depth(n.children) for n in nodes), default=0) if nodes else 0


def _render_ol(nodes: list) -> str:
    items = []
    for node in nodes:
        link = f'<a href="{_ea(node.item.href)}">{_e(node.item.label)}</a>'
        if node.children:
            items.append(f"<li>{link}{_render_ol(node.children)}</li>")
        else:
            items.append(f"<li>{link}</li>")
    return "<ol>" + "".join(items) + "</ol>"


def _nav_xhtml(spec: EpubSpec) -> str:
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n',
        f'<html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" lang="{_ea(spec.language)}" xml:lang="{_ea(spec.language)}">',
        f"<head><title>{_e(spec.title)}</title></head><body>",
        f'<nav epub:type="toc" id="toc"><h1>Contents</h1>{_render_ol(_build_tree(spec.toc))}</nav>',
    ]
    if spec.landmarks:
        items = "".join(
            f'<li><a epub:type="{_ea(t)}" href="{_ea(h)}">{_e(label)}</a></li>'
            for t, h, label in spec.landmarks
        )
        parts.append(f'<nav epub:type="landmarks" id="landmarks" hidden="hidden"><h1>Landmarks</h1><ol>{items}</ol></nav>')
    parts.append("</body></html>\n")
    return "".join(parts)


def _ncx(spec: EpubSpec) -> str:
    counter = 0
    lines: list[str] = []

    def render(nodes: list, depth: int) -> None:
        nonlocal counter
        pad = "  " * depth
        for node in nodes:
            counter += 1
            lines.append(f'{pad}<navPoint id="navPoint-{counter}" playOrder="{counter}">')
            lines.append(f"{pad}  <navLabel><text>{_e(node.item.label)}</text></navLabel>")
            lines.append(f'{pad}  <content src="{_ea(node.item.href)}"/>')
            render(node.children, depth + 1)
            lines.append(f"{pad}</navPoint>")

    tree = _build_tree(spec.toc)
    render(tree, 2)

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "  <head>\n"
        f'    <meta name="dtb:uid" content="{_ea(spec.unique_id)}"/>\n'
        f'    <meta name="dtb:depth" content="{max(1, _tree_depth(tree))}"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        "  </head>\n"
        f"  <docTitle><text>{_e(spec.title)}</text></docTitle>\n"
        "  <navMap>\n" + "\n".join(lines) + "\n  </navMap>\n"
        "</ncx>\n"
    )


def _opf(spec: EpubSpec, has_toc: bool, modified: str) -> str:
    md = [
        f'    <dc:identifier id="bookid" opf:scheme="uuid">{_e(spec.unique_id)}</dc:identifier>',
        f"    <dc:title>{_e(spec.title)}</dc:title>",
    ]
    for author in spec.authors:
        md.append(f'    <dc:creator opf:role="aut">{_e(author)}</dc:creator>')
    if spec.publisher:
        md.append(f"    <dc:publisher>{_e(spec.publisher)}</dc:publisher>")
    if spec.description:
        md.append(f"    <dc:description>{_e(spec.description)}</dc:description>")
    if spec.date:
        md.append(f"    <dc:date>{_e(spec.date)}</dc:date>")
    if spec.rights:
        md.append(f"    <dc:rights>{_e(spec.rights)}</dc:rights>")
    for subject in spec.subjects:
        md.append(f"    <dc:subject>{_e(subject)}</dc:subject>")
    for contributor in spec.contributors:
        md.append(f"    <dc:contributor>{_e(contributor)}</dc:contributor>")
    md.append(f"    <dc:language>{_e(spec.language)}</dc:language>")
    if spec.isbn:
        md.append(f'    <dc:identifier opf:scheme="ISBN">{_e(spec.isbn)}</dc:identifier>')
    if spec.mobi_asin:
        md.append(f'    <dc:identifier opf:scheme="MOBI-ASIN">{_e(spec.mobi_asin)}</dc:identifier>')
    if spec.cover_filename:
        md.append('    <meta name="cover" content="cover-image"/>')
    if spec.series_name:
        # Same two conventions, and the same tag shapes, ebook_fix.series
        # itself reads and writes -- see that module for why both exist.
        md.append(f'    <meta name="calibre:series" content="{_ea(spec.series_name)}"/>')
        if spec.series_index is not None:
            md.append(f'    <meta name="calibre:series_index" content="{_ea(format_index(spec.series_index))}"/>')
        md.append(f'    <meta property="belongs-to-collection" id="{COLLECTION_ID}">{_e(spec.series_name)}</meta>')
        md.append(f'    <meta refines="#{COLLECTION_ID}" property="collection-type">series</meta>')
        if spec.series_index is not None:
            md.append(f'    <meta refines="#{COLLECTION_ID}" property="group-position">{_ea(format_index(spec.series_index))}</meta>')
    md.append(f'    <meta property="dcterms:modified">{modified}</meta>')

    manifest = []
    if has_toc:
        manifest.append('    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        manifest.append('    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    manifest.append('    <item id="style" href="styles/style.css" media-type="text/css"/>')
    for i in range(len(spec.pages)):
        manifest.append(
            f'    <item id="part{i + 1:04d}" href="text/{page_filename(i)}" media-type="application/xhtml+xml"/>'
        )
    for image in spec.images:
        item_id = "cover-image" if image.filename == spec.cover_filename else "img-" + image.filename.rsplit(".", 1)[0]
        props = ' properties="cover-image"' if image.filename == spec.cover_filename else ""
        manifest.append(
            f'    <item id="{item_id}" href="images/{image.filename}" media-type="{image.media_type}"{props}/>'
        )

    spine_attr = ' toc="ncx"' if has_toc else ""
    spine = [f'    <itemref idref="part{i + 1:04d}"/>' for i in range(len(spec.pages))]

    guide = ""
    if spec.guide:
        refs = [
            f'    <reference type="{_ea(kind)}" title="{_ea(title)}" href="{_ea(href)}"/>'
            for kind, title, href in spec.guide
        ]
        guide = "  <guide>\n" + "\n".join(refs) + "\n  </guide>\n"

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">\n'
        + "\n".join(md)
        + "\n  </metadata>\n  <manifest>\n"
        + "\n".join(manifest)
        + f"\n  </manifest>\n  <spine{spine_attr}>\n"
        + "\n".join(spine)
        + "\n  </spine>\n"
        + guide
        + "</package>\n"
    )


_CONTAINER_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    "  <rootfiles>\n"
    '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
    "  </rootfiles>\n"
    "</container>\n"
)


def write_epub(path: Path, spec: EpubSpec) -> None:
    """Writes the EPUB to `path` (via a temporary file in the same
    folder, so a failure never leaves a half-written book behind)."""
    path = Path(path)
    has_toc = bool(spec.toc)
    modified = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    temp = path.with_name(path.name + ".part")
    try:
        with zipfile.ZipFile(temp, "w") as zf:
            def add(name: str, data, compress: bool = True) -> None:
                info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
                info.external_attr = 0o644 << 16
                zf.writestr(info, data if isinstance(data, bytes) else data.encode("utf-8"))

            add("mimetype", b"application/epub+zip", compress=False)
            add("META-INF/container.xml", _CONTAINER_XML)
            add("OEBPS/content.opf", _opf(spec, has_toc, modified))
            if has_toc:
                add("OEBPS/nav.xhtml", _nav_xhtml(spec))
                add("OEBPS/toc.ncx", _ncx(spec))
            add("OEBPS/styles/style.css", spec.css)
            for i, (title, body) in enumerate(spec.pages):
                add(f"OEBPS/text/{page_filename(i)}", _page_xhtml(title, body, spec.language))
            for image in spec.images:
                add(f"OEBPS/images/{image.filename}", image.data, compress=False)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


# ---------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------

_ISBN_RE = re.compile(r"^(97[89])?\d{9}[\dXx]$")


def normalize_isbn(raw: str) -> str:
    """Strips spaces/hyphens and validates the result looks like a real
    ISBN-10 or ISBN-13. Returns "" (never a malformed value) if it
    doesn't -- a book's own metadata sometimes has a garbled or
    placeholder ISBN, and a wrong-looking identifier is worse than a
    missing one."""
    cleaned = re.sub(r"[\s-]", "", raw or "")
    return cleaned if _ISBN_RE.match(cleaned) else ""
