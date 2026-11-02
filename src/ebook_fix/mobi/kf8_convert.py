"""
ebook_fix.mobi.kf8_convert

AZW3/KF8 -> EPUB conversion, start to finish: open the book (kf8.py),
rewrite the KF8 addressing scheme inside its pages to real hrefs, and
assemble the EPUB with the same shared assembler (ebook_fix.epub_builder)
the MOBI7 and FB2 converters use. Nothing here depends on Calibre or any
other converter.

The counterpart of ebook_fix.mobi.convert (which handles classic MOBI7),
and used by it: convert_mobi_to_epub() detects a pure KF8 file and hands
it to convert_kf8_to_epub() below, so the CLI and the GUI need no
KF8-specific code of their own. Like the MOBI7 converter this is a
faithful conversion, not a cleanup: the book's own text, styling, images,
cover, metadata, links and table of contents come across as they were,
and repairing the result is what the normal `repair` command is for.

See docs/azw3_kf8_conversion_plan.md for the phases this builds on and
what's not covered yet (audio, video and other rarely used flows).

Embedded fonts (decoded from the book's FONT records) and SVG flows (a
decorative background in the stylesheet, say) are written out as real files
in the EPUB's fonts/ and images/ folders, and the references to them are
rewritten to match.
"""
from __future__ import annotations

import re
from pathlib import Path

from ebook_fix.epub_builder import (
    EpubImage,
    EpubSpec,
    TocItem,
    normalize_isbn,
    page_filename,
    write_epub,
)
from ebook_fix.mobi.convert import (
    _ASIN_RE,
    _GUIDE_LABELS,
    _GUIDE_TYPES,
    _UUID_RE,
    ConvertResult,
    _image_filename,
    _unique_id,
    _visible_text_length,
    default_output_path,
)
from ebook_fix.mobi.kf8 import (
    body_classes,
    finish_page_anchors,
    fragment_page_map,
    read_kf8,
    referenced_css_flows,
    rewrite_css_flow_refs,
    rewrite_image_refs,
    rewrite_internal_links,
    rewrite_svg_flow_refs,
)
from ebook_fix.mobi.reader import MobiError

_BASE_CSS = """\
img {
  max-width: 100%;
}
"""

_STYLESHEET_HREF = "../styles/style.css"
_KINDLE_LINK_RE = re.compile(r"kindle:pos:fid:")


def _fold_body_classes_into_css(css: str, classes: list[str]) -> str:
    """The book's pages each open with something like <body class="calibre">,
    but a page's <body> tag isn't carried over (the shared EPUB assembler
    writes its own), so a class on it would take its CSS effect with it --
    for AZW3-Example.azw3 and AZW3-Newer.azw3 that's `text-align: justify`
    and a 5pt side margin for the whole book. Rules whose selector is
    exactly that class (`.calibre { ... }`) get `body` added as a second
    selector, so the same declarations apply to the page's real <body>
    instead. A more specific selector that merely mentions the class
    (`.calibre p`) is left alone: it still matches, since the class also
    stays on whatever elements inside the page carry it."""
    for name in classes:
        pattern = re.compile(r"(^|\})(\s*)\." + re.escape(name) + r"(\s*)\{")
        css = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}.{name}, body{m.group(3)}{{", css)
    return css


def _svg_filename(flow_index: int) -> str:
    return f"flow{flow_index:05d}.svg"


def convert_kf8_to_epub(source: Path, output: Path | None = None, overwrite: bool = False) -> ConvertResult:
    """Converts one AZW3/KF8 file to an EPUB. Raises MobiError (with a
    message meant for a person) if the book can't be converted."""
    source = Path(source)
    output = Path(output) if output else default_output_path(source)

    if output.resolve() == source.resolve():
        raise MobiError("The output file can't be the same as the input file.")
    if output.exists() and not overwrite:
        raise MobiError(f"'{output}' already exists. Use --overwrite to replace it, or choose a different -o/--output.")

    book = read_kf8(source)
    result = ConvertResult(
        source=source,
        output=output,
        generation="KF8/AZW3",
        warnings=list(book.warnings),
        text_bytes=len(book.flows[0]),
    )

    # -- images: every reference to one is resolved through here, which
    # is also how the set actually used (plus the cover) gets known ------
    used_images: set[int] = set()
    used_fonts: set[int] = set()
    used_svg: set[int] = set()
    dropped_images = 0

    def image_href(recindex: int):
        nonlocal dropped_images
        image = book.images.get(recindex)
        if image is None:
            # kindle:embed also points at embedded fonts (from @font-face rules)
            font = book.fonts.get(recindex)
            if font is not None:
                used_fonts.add(recindex)
                return "../fonts/" + font.filename
            dropped_images += 1
            return None
        used_images.add(recindex)
        return "../images/" + _image_filename(image, book.cover_recindex)

    def svg_href(flow_index: int):
        used_svg.add(flow_index)
        return f"../images/{_svg_filename(flow_index)}"

    # -- which page anchors need to become real ids: anything the table of
    # contents, the guide, or another page's own links point at ----------
    linked_aids: set[str] = set()
    for entry in [*book.toc, *book.guide_table]:
        if entry.anchor.startswith("aid-"):
            linked_aids.add(entry.anchor[len("aid-"):])

    # -- internal links, then everything else that needs the full set of
    # linked aids to be known first ---------------------------------------
    fragment_page = fragment_page_map(book.skeleton_table, book.fragment_table)
    resolved_links = 0

    def counting_page_filename(index: int) -> str:
        nonlocal resolved_links
        resolved_links += 1
        return page_filename(index)

    total_links = 0
    linked_bodies = []
    for body in book.page_bodies:
        total_links += len(_KINDLE_LINK_RE.findall(body))
        body, aids = rewrite_internal_links(
            body, book.skeleton_table, book.fragment_table, fragment_page, book.pages, counting_page_filename
        )
        linked_aids |= aids
        linked_bodies.append(body)
    result.unresolved_links = total_links - resolved_links

    bodies = []
    unsupported_refs = 0
    for body in linked_bodies:
        body = finish_page_anchors(body, linked_aids)
        body = rewrite_image_refs(body, image_href)
        body = rewrite_css_flow_refs(body, book.flows, lambda _index: _STYLESHEET_HREF)
        body = rewrite_svg_flow_refs(body, book.flows, svg_href)
        unsupported_refs += body.count("kindle:")
        bodies.append(body)

    # -- stylesheet: the book's own CSS flows, in the order its pages link
    # to them (cascade order matters), with the page <body> class folded in
    css = _BASE_CSS
    for flow_index in referenced_css_flows(book.pages):
        if 0 < flow_index < len(book.flows):
            css += "\n" + book.flows[flow_index].decode(book.encoding, errors="replace")
    css = rewrite_image_refs(css, image_href)
    css = rewrite_svg_flow_refs(css, book.flows, svg_href)
    css = _fold_body_classes_into_css(css, body_classes(book.pages))
    unsupported_refs += css.count("kindle:")

    # -- SVG flows actually referenced become real files. An SVG can itself
    # point at pictures (kindle:embed), which sit in the same folder ---------
    svg_files: list = []
    for flow_index in sorted(used_svg):
        def sibling_image(recindex: int):
            image = book.images.get(recindex)
            if image is None:
                return None
            used_images.add(recindex)
            return _image_filename(image, book.cover_recindex)

        text = book.flows[flow_index].decode("utf-8", errors="replace")
        text = rewrite_image_refs(text, sibling_image)
        svg_files.append(EpubImage(_svg_filename(flow_index), "image/svg+xml", text.encode("utf-8")))

    result.dropped_images = dropped_images

    if not any(_visible_text_length(b) for b in bodies) and not used_images:
        raise MobiError("The book doesn't contain any readable text.")

    if dropped_images:
        result.warnings.append(
            f"{dropped_images} image reference(s) pointed at a missing or unsupported image and were left out."
        )
    if result.unresolved_links:
        result.warnings.append(
            f"{result.unresolved_links} link(s) pointed at a place that doesn't exist in the book; "
            "they were kept as plain text."
        )
    if unsupported_refs:
        result.warnings.append(
            f"{unsupported_refs} reference(s) to embedded content this converter doesn't handle yet "
            "(audio, video or another kind of embedded flow) were left as they were and will not display."
        )

    # -- table of contents, landmarks, page titles -----------------------
    title = book.title.strip() or source.stem
    toc: list[TocItem] = []
    page_titles: dict[int, str] = {}
    skipped_toc = 0
    for entry in book.toc:
        if entry.page_index is None:
            skipped_toc += 1
            continue
        href = f"text/{page_filename(entry.page_index)}" + (f"#{entry.anchor}" if entry.anchor else "")
        toc.append(TocItem(label=entry.label, href=href, level=entry.level))
        page_titles.setdefault(entry.page_index, entry.label)
    if skipped_toc:
        result.warnings.append(f"{skipped_toc} table-of-contents entrie(s) pointed at a missing place and were left out.")
    if not book.toc:
        result.warnings.append(
            "This book has no table of contents of its own. Run `repair` on the converted file "
            "to have one generated from the book's chapter structure."
        )

    guide: list = []
    landmarks: list = []
    seen_guide_types: set = set()
    for entry in book.guide_table:
        mapped = _GUIDE_TYPES.get(entry.ref_type)
        if mapped is None or entry.page_index is None or mapped[0] in seen_guide_types:
            continue
        seen_guide_types.add(mapped[0])
        href = f"text/{page_filename(entry.page_index)}" + (f"#{entry.anchor}" if entry.anchor else "")
        label = entry.title or _GUIDE_LABELS[entry.ref_type]
        guide.append((mapped[0], label, href))
        landmarks.append((mapped[1], href, label))

    # -- images ----------------------------------------------------------
    wanted = set(used_images)
    if book.cover_recindex is not None:
        wanted.add(book.cover_recindex)
    images = [
        EpubImage(_image_filename(book.images[n], book.cover_recindex), book.images[n].media_type, book.images[n].data)
        for n in sorted(wanted)
        if n in book.images
    ]
    images += svg_files
    fonts = [
        EpubImage(book.fonts[n].filename, book.fonts[n].media_type, book.fonts[n].data)
        for n in sorted(used_fonts)
    ]
    cover_filename = (
        _image_filename(book.images[book.cover_recindex], book.cover_recindex)
        if book.cover_recindex in book.images else ""
    )

    # -- metadata --------------------------------------------------------
    asin = book.asin.strip()
    asin_is_uuid = bool(_UUID_RE.match(asin))
    mobi_asin = asin.upper() if _ASIN_RE.match(asin.upper()) and not asin_is_uuid else ""
    isbn = normalize_isbn(book.isbn)

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
        pages=[(page_titles.get(i, title), body) for i, body in enumerate(bodies)],
        css=css,
        images=images,
        fonts=fonts,
        cover_filename=cover_filename,
        toc=toc,
        landmarks=landmarks,
        guide=guide,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    write_epub(output, spec)

    result.title = title
    result.authors = list(book.authors)
    result.page_count = len(bodies)
    result.image_count = len(images)
    result.toc_entry_count = len(toc)
    result.has_cover = bool(cover_filename)
    return result
