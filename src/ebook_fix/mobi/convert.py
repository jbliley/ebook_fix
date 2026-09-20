"""
ebook_fix.mobi.convert

MOBI -> EPUB conversion, start to finish: open the book (reader.py),
convert its markup into XHTML pages (markup.py), and assemble the EPUB
(epub_out.py). Nothing here depends on Calibre or any other converter.

The output is an ordinary EPUB 3 file that the rest of ebook_fix
(analyze, repair, the GUI) opens like any other. It is a faithful
conversion, not a cleanup: the book's own text, images, cover,
metadata, links and table of contents come across as they were, and
repairing the result (chapter detection, typography, TOC generation for
a book that never had one, and so on) is what the normal `repair`
command is for.

See docs/mobi_conversion_plan.md for scope and what's not covered yet.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ebook_fix.cover import standard_cover_filename
from ebook_fix.mobi.epub_out import (
    EpubImage,
    EpubSpec,
    TocItem,
    page_filename,
    write_epub,
)
from ebook_fix.mobi.markup import convert_markup, resolve_links
from ebook_fix.mobi.reader import MobiBook, MobiError, read_mobi
from ebook_fix.report import console, print_header

_IMAGE_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif"}

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
_ISBN_RE = re.compile(r"^(97[89])?\d{9}[\dXx]$")

# The MOBI guide's reference types, mapped to the EPUB 2 guide types and
# the EPUB 3 landmark types they correspond to.
_GUIDE_TYPES = {
    "toc": ("toc", "toc"),
    "text": ("text", "bodymatter"),
    "start": ("text", "bodymatter"),
    "cover": ("cover", "cover"),
    "title-page": ("title-page", "titlepage"),
    "copyright-page": ("copyright-page", "copyright-page"),
    "dedication": ("dedication", "dedication"),
    "acknowledgements": ("acknowledgements", "acknowledgments"),
    "foreword": ("foreword", "foreword"),
    "preface": ("preface", "preface"),
    "index": ("index", "index"),
    "glossary": ("glossary", "glossary"),
    "bibliography": ("bibliography", "bibliography"),
    "epigraph": ("epigraph", "epigraph"),
}
_GUIDE_LABELS = {
    "toc": "Table of Contents",
    "text": "Start of Book",
    "start": "Start of Book",
    "cover": "Cover",
    "title-page": "Title Page",
    "copyright-page": "Copyright",
    "dedication": "Dedication",
    "acknowledgements": "Acknowledgements",
    "foreword": "Foreword",
    "preface": "Preface",
    "index": "Index",
    "glossary": "Glossary",
    "bibliography": "Bibliography",
    "epigraph": "Epigraph",
}

_BASE_CSS = """\
p {
  margin: 0;
}

img {
  max-width: 100%;
}
"""


@dataclass
class ConvertResult:
    source: Path = None
    output: Path = None
    title: str = ""
    authors: list = field(default_factory=list)
    generation: str = ""
    page_count: int = 0
    image_count: int = 0
    toc_entry_count: int = 0
    has_cover: bool = False
    text_bytes: int = 0
    dropped_images: int = 0
    unresolved_links: int = 0
    warnings: list = field(default_factory=list)


def _image_filename(image, cover_recindex: int | None = None) -> str:
    """The cover gets the standardized cover filename (cover.jpg, ...) that
    the Cover Repair module would otherwise rename it to later."""
    if image.recindex == cover_recindex:
        return standard_cover_filename(image.media_type)
    return f"image{image.recindex:05d}.{_IMAGE_EXTENSIONS[image.media_type]}"


def _unique_id(book: MobiBook, asin_is_uuid: bool) -> str:
    if asin_is_uuid:
        return book.asin.lower()
    seed = "|".join([book.title, "/".join(book.authors), book.isbn, str(book.header.unique_id)])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ebook_fix:mobi:" + seed))


def _visible_text_length(body: str) -> int:
    return len(re.sub(r"<[^>]*>", "", body).strip())


def default_output_path(source: Path) -> Path:
    return Path(source).with_suffix(".epub")


def convert_mobi_to_epub(source: Path, output: Path | None = None, overwrite: bool = False) -> ConvertResult:
    """Converts one MOBI/AZW/PRC file to an EPUB. Raises MobiError (with
    a message meant for a person) if the book can't be converted."""
    source = Path(source)
    output = Path(output) if output else default_output_path(source)

    if output.resolve() == source.resolve():
        raise MobiError("The output file can't be the same as the input file.")
    if output.exists() and not overwrite:
        raise MobiError(f"'{output}' already exists. Use --overwrite to replace it, or choose a different -o/--output.")

    book = read_mobi(source)
    result = ConvertResult(
        source=source,
        output=output,
        generation=book.generation,
        warnings=list(book.warnings),
        text_bytes=len(book.text),
    )

    def image_href(recindex: int):
        image = book.images.get(recindex)
        if image is None:
            return None
        return "../images/" + _image_filename(image, book.cover_recindex)

    extra_targets = {e.position for e in book.ncx}
    if book.start_offset is not None:
        extra_targets.add(book.start_offset)

    markup = convert_markup(book.text, book.encoding, extra_targets, book.images, image_href)
    result.dropped_images = markup.dropped_images
    result.unresolved_links = resolve_links(markup, page_filename)

    if not any(_visible_text_length(p.body) for p in markup.pages) and not markup.used_images:
        raise MobiError("The book doesn't contain any readable text.")

    if markup.dropped_images:
        result.warnings.append(
            f"{markup.dropped_images} image reference(s) pointed at a missing or unsupported image and were left out."
        )
    if result.unresolved_links:
        result.warnings.append(
            f"{result.unresolved_links} link(s) pointed at a place that doesn't exist in the book; "
            "they were kept as plain text."
        )

    # -- table of contents, landmarks, page titles -----------------------
    title = book.title.strip() or source.stem
    toc: list[TocItem] = []
    page_titles: dict[int, str] = {}
    skipped_toc = 0
    for entry in book.ncx:
        ident = f"filepos{entry.position}"
        page = markup.id_page.get(ident)
        if page is None:
            skipped_toc += 1
            continue
        toc.append(TocItem(label=entry.label, href=f"text/{page_filename(page)}#{ident}", level=entry.level))
        page_titles.setdefault(page, entry.label)
    if skipped_toc:
        result.warnings.append(f"{skipped_toc} table-of-contents entrie(s) pointed at a missing place and were left out.")
    if not book.ncx:
        result.warnings.append(
            "This book has no table of contents of its own. Run `repair` on the converted file "
            "to have one generated from the book's chapter structure."
        )

    guide: list = []
    landmarks: list = []
    seen_guide_types: set = set()
    for kind, guide_title, filepos in markup.guide:
        mapped = _GUIDE_TYPES.get(kind)
        page = markup.id_page.get(f"filepos{filepos}")
        if mapped is None or page is None or mapped[0] in seen_guide_types:
            continue
        seen_guide_types.add(mapped[0])
        href = f"text/{page_filename(page)}#filepos{filepos}"
        label = guide_title or _GUIDE_LABELS[kind]
        guide.append((mapped[0], label, href))
        landmarks.append((mapped[1], href, label))
    if "text" not in seen_guide_types and book.start_offset is not None:
        page = markup.id_page.get(f"filepos{book.start_offset}")
        if page is not None:
            href = f"text/{page_filename(page)}#filepos{book.start_offset}"
            guide.append(("text", "Start of Book", href))
            landmarks.append(("bodymatter", href, "Start of Book"))

    # -- images ----------------------------------------------------------
    wanted = set(markup.used_images)
    if book.cover_recindex is not None:
        wanted.add(book.cover_recindex)
    images = [
        EpubImage(_image_filename(book.images[n], book.cover_recindex), book.images[n].media_type, book.images[n].data)
        for n in sorted(wanted)
        if n in book.images
    ]
    cover_filename = (
        _image_filename(book.images[book.cover_recindex], book.cover_recindex)
        if book.cover_recindex in book.images else ""
    )

    # -- metadata --------------------------------------------------------
    asin = book.asin.strip()
    asin_is_uuid = bool(_UUID_RE.match(asin))
    mobi_asin = asin.upper() if _ASIN_RE.match(asin.upper()) and not asin_is_uuid else ""
    isbn = re.sub(r"[\s-]", "", book.isbn)
    if not _ISBN_RE.match(isbn):
        isbn = ""

    css = _BASE_CSS
    for name, declarations in markup.css_rules:
        body = "".join(f"  {d.strip()};\n" for d in declarations.split(";") if d.strip())
        css += f"\n.{name} {{\n{body}}}\n"

    spec = EpubSpec(
        title=title,
        language=book.language.strip() or "und",
        unique_id=_unique_id(book, asin_is_uuid),
        authors=book.authors,
        publisher=book.publisher,
        description=book.description,
        date=book.date,
        rights=book.rights,
        subjects=book.subjects,
        contributors=book.contributors,
        isbn=isbn,
        mobi_asin=mobi_asin,
        pages=[(page_titles.get(i, title), page.body) for i, page in enumerate(markup.pages)],
        css=css,
        images=images,
        cover_filename=cover_filename,
        toc=toc,
        landmarks=landmarks,
        guide=guide,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    write_epub(output, spec)

    result.title = title
    result.authors = list(book.authors)
    result.page_count = len(markup.pages)
    result.image_count = len(images)
    result.toc_entry_count = len(toc)
    result.has_cover = bool(cover_filename)
    return result


def print_convert_report(result: ConvertResult) -> None:
    """Plain-text summary in the same underlined-header style `analyze`
    uses."""
    print_header("[Conversion]")
    console.print(f"Source: {result.source}")
    console.print(f"Output: {result.output}")
    console.print(f"Format read: {result.generation}")
    console.print("")
    print_header("[Result]")
    console.print(f"Title: {result.title}")
    if result.authors:
        console.print(f"Author: {', '.join(result.authors)}")
    console.print(f"Text: {result.text_bytes:,} bytes, split into {result.page_count} file(s)")
    console.print(f"Images: {result.image_count}")
    console.print(f"Cover: {'yes' if result.has_cover else 'none found'}")
    console.print(f"Table of contents entries: {result.toc_entry_count}")
    if result.warnings:
        console.print("")
        print_header("[Notes]")
        for warning in result.warnings:
            console.print(f"- {warning}")
    console.print("")
    console.print("Next: run `analyze` or `repair` on the new EPUB to check it and clean it up.")
