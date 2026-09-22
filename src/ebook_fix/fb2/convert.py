"""
ebook_fix.fb2.convert

FB2 -> EPUB conversion, start to finish: read the book (reader.py),
convert its body content into XHTML pages (markup.py), and assemble
the EPUB (ebook_fix.epub_builder -- shared with the MOBI converter).

Like the MOBI converter, this is a faithful conversion, not a cleanup:
the book's own text, images, cover, metadata, links and structure come
across as they were. Running `repair` on the result is still worth
doing (typography, EPUB3 upgrade, and so on), but unlike MOBI, FB2
already carries a real table of contents and real section structure,
so nothing here is left for TOC Generation to build from scratch the
way an unstructured MOBI is.

See docs/fb2_conversion_plan.md for scope and what's not covered yet.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ebook_fix.cover import standard_cover_filename
from ebook_fix.epub_builder import (
    EpubImage,
    EpubSpec,
    TocItem,
    normalize_isbn,
    page_filename,
    write_epub,
)
from ebook_fix.fb2.genres import genre_label
from ebook_fix.fb2.markup import (
    Fb2Renderer,
    assign_missing_section_ids,
    collect_ids,
    collect_toc,
)
from ebook_fix.fb2.reader import Fb2Book, Fb2Error, read_fb2

_IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
    "image/webp": "webp", "image/svg+xml": "svg",
}

_BASE_CSS = """\
p {
  margin: 0;
}

img {
  max-width: 100%;
}

.epigraph, .cite {
  margin: 1em 2em;
}

.stanza {
  margin: 1em 0;
}

.text-author {
  text-align: right;
  font-style: italic;
}

.subtitle {
  font-weight: bold;
}
"""


@dataclass
class ConvertResult:
    source: Path = None
    output: Path = None
    title: str = ""
    authors: list = field(default_factory=list)
    page_count: int = 0
    image_count: int = 0
    toc_entry_count: int = 0
    footnote_page_count: int = 0
    has_cover: bool = False
    unresolved_links: int = 0
    dropped_images: int = 0
    warnings: list = field(default_factory=list)


def default_output_path(source: Path) -> Path:
    return Path(source).with_suffix(".epub")


def _local(el) -> str:
    return el.tag.split("}", 1)[-1] if isinstance(el.tag, str) else ""


def _top_sections(body) -> list:
    return [c for c in body if _local(c) == "section"]


def _unique_id(book: Fb2Book) -> str:
    if book.document_id:
        return book.document_id.lower()
    seed = "|".join([book.title, "/".join(a.display_name for a in book.authors), book.isbn])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ebook_fix:fb2:" + seed))


def _image_filenames(book: Fb2Book) -> dict:
    """binary id -> output filename, assigned in a stable (sorted-id)
    order so converting the same book twice gives the same names. The
    cover gets the standardized name Cover Repair already uses, so
    that module has nothing left to rename."""
    names: dict[str, str] = {}
    others = sorted(bid for bid in book.binaries if bid != book.cover_binary_id)
    for i, bid in enumerate(others, start=1):
        ext = _IMAGE_EXTENSIONS[book.binaries[bid].media_type]
        names[bid] = f"image{i:05d}.{ext}"
    if book.cover_binary_id in book.binaries:
        names[book.cover_binary_id] = standard_cover_filename(book.binaries[book.cover_binary_id].media_type)
    return names


def convert_fb2_to_epub(source: Path, output: Path | None = None, overwrite: bool = False) -> ConvertResult:
    """Converts one FB2 file to an EPUB. Raises Fb2Error (with a message
    meant for a person) if the book can't be converted."""
    source = Path(source)
    output = Path(output) if output else default_output_path(source)

    if output.resolve() == source.resolve():
        raise Fb2Error("The output file can't be the same as the input file.")
    if output.exists() and not overwrite:
        raise Fb2Error(f"'{output}' already exists. Use --overwrite to replace it, or choose a different -o/--output.")

    book = read_fb2(source)
    result = ConvertResult(source=source, output=output, title=book.title, authors=[a.display_name for a in book.authors])

    if not _top_sections(book.main_body):
        raise Fb2Error("This book's main body has no sections, so there's no text to convert.")

    image_names = _image_filenames(book)
    has_cover = book.cover_binary_id in book.binaries

    def image_href(bid: str):
        if bid not in image_names:
            return None
        return "../images/" + image_names[bid]

    # -- give every titled section an id, main body and auxiliary alike,
    # so every table-of-contents entry has somewhere to link to.
    id_counter = [0]
    for section in _top_sections(book.main_body):
        assign_missing_section_ids(section, id_counter)
    for aux_body in book.auxiliary_bodies:
        for section in aux_body.findall(f"{{{book.ns}}}section"):
            assign_missing_section_ids(section, id_counter)

    # -- page layout: an optional body-frontmatter page, one page per
    # top-level main section, then one page per auxiliary body.
    probe = Fb2Renderer(book.ns, image_href, {}, {}, page_filename)
    frontmatter = probe.render_body_frontmatter(book.main_body)
    main_sections = _top_sections(book.main_body)

    page_index = 0
    cover_page = None
    if has_cover:
        # FB2's cover is pure metadata (<coverpage>) with no equivalent
        # inline page the way a MOBI book's own body often already has
        # one -- so a dedicated cover page is created here, matching
        # ordinary EPUB convention (a real page, not just the manifest
        # declaration), placed first in the spine.
        cover_page = page_index
        page_index += 1
    frontmatter_page = None
    if frontmatter is not None:
        frontmatter_page = page_index
        page_index += 1
    section_pages = {}
    for section in main_sections:
        section_pages[id(section)] = page_index
        page_index += 1
    aux_pages = []
    for aux_body in book.auxiliary_bodies:
        aux_pages.append(page_index)
        page_index += 1

    # -- note links resolve to auxiliary-body pages; every other
    # internal link resolves within the main body's own id map. Both
    # are fully known now, before any page is actually rendered.
    note_hrefs: dict[str, str] = {}
    for aux_body, page in zip(book.auxiliary_bodies, aux_pages):
        indexed = [(page, s) for s in aux_body.findall(f"{{{book.ns}}}section")]
        note_hrefs.update({ident: f"{page_filename(page)}#{ident}" for ident, p in collect_ids(indexed).items()})
    own_ids = collect_ids([(section_pages[id(s)], s) for s in main_sections])

    renderer = Fb2Renderer(book.ns, image_href, note_hrefs, own_ids, page_filename)

    pages: list[tuple[str, str]] = [None] * page_index
    toc: list[TocItem] = []

    if cover_page is not None:
        pages[cover_page] = ("Cover", f'<p class="image"><img src="{image_href(book.cover_binary_id)}" alt="Cover"/></p>')

    # nav.xhtml and toc.ncx live at the OEBPS root, one level up from
    # text/ where every page actually is, so every TOC href needs that
    # prefix -- unlike an <a href> a page's own body content points at
    # another page with (both already inside text/, see
    # Fb2Renderer._resolve_href), which does not.
    if frontmatter is not None:
        title_text, body_html = renderer.render_body_frontmatter(book.main_body)
        pages[frontmatter_page] = (title_text or book.title or source.stem, body_html)
        if title_text:
            toc.append(TocItem(label=title_text, href=f"text/{page_filename(frontmatter_page)}", level=0))

    for section in main_sections:
        page = section_pages[id(section)]
        title_text, body_html = renderer.render_section_page(section)
        pages[page] = (title_text or book.title or source.stem, body_html)
        entries = collect_toc(section, page, 0, page_filename)
        toc.extend(TocItem(label=e.label, href=f"text/{e.href}", level=e.level) for e in entries)

    footnote_labels: list[str] = []
    for aux_body, page in zip(book.auxiliary_bodies, aux_pages):
        label = aux_body.get("name") or "Notes"
        title_text, body_html = renderer.render_notes_body(aux_body, fallback_label=label.title())
        pages[page] = (title_text, body_html)
        footnote_labels.append(title_text)
        toc.append(TocItem(label=title_text, href=f"text/{page_filename(page)}", level=0))

    if any(p is None for p in pages):
        raise Fb2Error("Internal error: not every page was rendered.")  # pragma: no cover -- defensive only

    result.unresolved_links = renderer.unresolved_links
    result.dropped_images = renderer.dropped_images
    result.footnote_page_count = len(aux_pages)
    if result.unresolved_links:
        result.warnings.append(
            f"{result.unresolved_links} link(s) pointed at a place that doesn't exist in the book; "
            "they were kept as plain text."
        )
    if result.dropped_images:
        result.warnings.append(
            f"{result.dropped_images} image reference(s) pointed at a missing or unsupported image and were left out."
        )

    # -- images: only the cover and images actually used are written.
    used = set(renderer.used_images)
    if book.cover_binary_id:
        used.add(book.cover_binary_id)
    images = [
        EpubImage(image_names[bid], book.binaries[bid].media_type, book.binaries[bid].data)
        for bid in sorted(used)
        if bid in book.binaries and bid in image_names
    ]
    cover_filename = image_names.get(book.cover_binary_id, "") if book.cover_binary_id in book.binaries else ""

    # OPF2's <guide> and EPUB3's nav landmarks use different vocabularies
    # for the same idea ("text"/"bodymatter" both mean "start reading
    # here") -- kept as separate (guide_type, landmark_type) pairs
    # rather than deriving one list from the other, same as the MOBI
    # converter's _GUIDE_TYPES table does.
    landmarks = []
    guide = []
    if cover_page is not None:
        href = f"text/{page_filename(cover_page)}"
        landmarks.append(("cover", href, "Cover"))
        guide.append(("cover", "Cover", href))
    first_content_page = frontmatter_page if frontmatter_page is not None else (section_pages[id(main_sections[0])] if main_sections else 0)
    href = f"text/{page_filename(first_content_page)}"
    landmarks.append(("bodymatter", href, "Start of Book"))
    guide.append(("text", "Start of Book", href))

    isbn = normalize_isbn(book.isbn)
    subjects = [genre_label(g) for g in book.genres]

    css = _BASE_CSS

    spec = EpubSpec(
        title=book.title.strip() or source.stem,
        language=book.language.strip() or "und",
        unique_id=_unique_id(book),
        authors=[a.display_name for a in book.authors],
        publisher=book.publisher,
        description=book.annotation_text,
        date=book.date,
        subjects=subjects,
        contributors=[t.display_name for t in book.translators],
        isbn=isbn,
        series_name=book.series_name,
        series_index=book.series_index,
        pages=pages,
        css=css,
        images=images,
        cover_filename=cover_filename,
        toc=toc,
        landmarks=landmarks,
        guide=guide,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    write_epub(output, spec)

    result.page_count = len(pages)
    result.image_count = len(images)
    result.toc_entry_count = len(toc)
    result.has_cover = bool(cover_filename)
    return result


def print_convert_report(result: ConvertResult) -> None:
    """Plain-text summary, same style `analyze`/the MOBI converter use."""
    from ebook_fix.report import console, print_header

    print_header("[Conversion]")
    console.print(f"Source: {result.source}")
    console.print(f"Output: {result.output}")
    console.print("")
    print_header("[Result]")
    console.print(f"Title: {result.title}")
    if result.authors:
        console.print(f"Author: {', '.join(result.authors)}")
    console.print(f"Pages: {result.page_count}" + (f" ({result.footnote_page_count} footnote page(s))" if result.footnote_page_count else ""))
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
