"""
ebook_fix.html.convert

HTML -> EPUB conversion, start to finish: read the book (reader.py),
split it into chapter pages when it has real chapter headings, and
assemble the EPUB (ebook_fix.epub_builder -- shared with the MOBI and
FB2 converters).

Like those converters, this is a faithful conversion, not a cleanup: the
book's own text, headings, emphasis, pictures and links come across as
they were. Running `repair` on the result is where typography, indents,
the table of contents and the rest get tidied.

Chapter splitting is deliberately conservative. The heading level used is
the shallowest one (h1, then h2, ...) with at least three headings that
each start a real run of text. A heading that is followed by no text at
all before the next heading (a title page's author line, say) stays with
the page before it instead of becoming a one-line "chapter". A book with
no such headings becomes a single page with no table of contents, which is
exactly the state the Table of Contents Generation repair looks for.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ebook_fix.epub_builder import (
    EpubImage,
    EpubSpec,
    TocItem,
    normalize_isbn,
    page_filename,
    write_epub,
)
from ebook_fix.html.reader import HEADING_TAGS, HtmlBook, HtmlError, read_html

_BASE_CSS = """\
p {
  margin: 0;
}

img {
  max-width: 100%;
}

.center {
  text-align: center;
}

.right {
  text-align: right;
}
"""

_MIN_CHAPTERS = 3


@dataclass
class ConvertResult:
    source: Path = None
    output: Path = None
    title: str = ""
    authors: list = field(default_factory=list)
    series: str = ""
    page_count: int = 0
    chapter_count: int = 0
    image_count: int = 0
    toc_entry_count: int = 0
    unresolved_links: int = 0
    warnings: list = field(default_factory=list)


def default_output_path(source: Path) -> Path:
    return Path(source).with_suffix(".epub")


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _has_text_run(blocks: list) -> bool:
    """True if any block that is not a heading carries text."""
    return any(b.tag not in HEADING_TAGS and b.tag != "hr" and _text(b) for b in blocks)


def _segments(blocks: list, tag: str) -> list:
    """[(start_index, end_index)] spans, one per heading of `tag`, each
    from the heading to just before the next such heading."""
    starts = [i for i, b in enumerate(blocks) if b.tag == tag]
    return [(s, starts[k + 1] if k + 1 < len(starts) else len(blocks)) for k, s in enumerate(starts)]


def choose_split(blocks: list):
    """-> (heading tag, [qualifying segment spans]) or (None, [])."""
    for level in range(1, 7):
        tag = f"h{level}"
        qualifying = [(s, e) for s, e in _segments(blocks, tag) if _has_text_run(blocks[s:e])]
        if len(qualifying) >= _MIN_CHAPTERS:
            return tag, qualifying
    return None, []


def _unique_id(book: HtmlBook, blocks: list) -> str:
    body_hash = hashlib.sha1("".join(_text(b) for b in blocks).encode("utf-8")).hexdigest()
    seed = "|".join([book.title, "/".join(book.authors), body_hash])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ebook_fix:html:" + seed))


def _image_names(book: HtmlBook) -> dict:
    names = {}
    for i, source in enumerate(sorted(book.images), start=1):
        ext = Path(source).suffix.lower().lstrip(".")
        ext = "jpg" if ext == "jpeg" else ext
        names[source] = f"image{i:05d}.{ext}"
    return names


def _page_body(blocks: list) -> str:
    return "".join(etree.tostring(b, encoding="unicode", method="xml", with_tail=False) for b in blocks)


def convert_html_to_epub(source: Path, output: Path | None = None, overwrite: bool = False) -> ConvertResult:
    """Converts one HTML file to an EPUB. Raises HtmlError (with a
    message meant for a person) if the book can't be converted."""
    source = Path(source)
    output = Path(output) if output else default_output_path(source)

    if output.resolve() == source.resolve():
        raise HtmlError("The output file can't be the same as the input file.")
    if output.exists() and not overwrite:
        raise HtmlError(f"'{output}' already exists. Use --overwrite to replace it, or choose a different -o/--output.")

    book = read_html(source)
    blocks = book.blocks
    result = ConvertResult(source=source, output=output, title=book.title, authors=list(book.authors), series=book.series_name)
    result.warnings.extend(book.notes)

    # -- page layout
    tag, chapters = choose_split(blocks)
    if tag:
        first_chapter_start = chapters[0][0]
        groups = []
        if first_chapter_start > 0:
            groups.append(("front", blocks[:first_chapter_start]))
        # A heading with no text of its own before the next split heading is
        # not in `chapters`, so its blocks ride along with the page before it.
        bounds = [s for s, _ in chapters] + [len(blocks)]
        for k, (s, _e) in enumerate(chapters):
            groups.append(("chapter", blocks[s:bounds[k + 1]]))
    else:
        groups = [("book", blocks)]

    # -- image names, and the rewrite of <img src> to them
    names = _image_names(book)
    used = set()
    for _kind, grp in groups:
        for b in grp:
            for img in ([b] if b.tag == "img" else []) + list(b.iter("img")):
                src = img.get("src", "")
                key = src.split("#")[0].split("?")[0]
                if key in names:
                    img.set("src", "../images/" + names[key])
                    used.add(key)

    # -- links between pages: an in-book "#id" that lands on another page
    id_page = {}
    for page_index, (_kind, grp) in enumerate(groups):
        for b in grp:
            for el in ([b] + list(b.iter())):
                if isinstance(el.tag, str) and el.get("id"):
                    id_page.setdefault(el.get("id"), page_index)
    unresolved = 0
    for page_index, (_kind, grp) in enumerate(groups):
        for b in grp:
            for a in ([b] if b.tag == "a" else []) + list(b.iter("a")):
                href = a.get("href", "")
                if href.startswith("#"):
                    target = href[1:]
                    if target not in id_page:
                        del a.attrib["href"]
                        unresolved += 1
                    elif id_page[target] != page_index:
                        a.set("href", f"{page_filename(id_page[target])}#{target}")
                elif not re.match(r"^(https?:|mailto:)", href, re.I):
                    if "href" in a.attrib:
                        del a.attrib["href"]
                        unresolved += 1
    result.unresolved_links = unresolved
    if unresolved:
        result.warnings.append(f"{unresolved} link(s) pointed at a place that isn't in the book; they were kept as plain text.")

    # -- pages and table of contents
    pages = []
    toc = []
    for page_index, (kind, grp) in enumerate(groups):
        if kind == "chapter":
            heading = grp[0]
            label = _text(heading) or f"Chapter {len(toc) + 1}"
            title = label
            # Every chapter heading gets an anchor for the contents entry.
            anchor = heading.get("id") or f"chapter{len(toc) + 1}"
            heading.set("id", anchor)
            toc.append(TocItem(label=label, href=f"text/{page_filename(page_index)}#{anchor}", level=0))
        else:
            title = book.title or source.stem
        pages.append((title, _page_body(grp)))

    result.chapter_count = len(toc)

    images = [
        EpubImage(names[k], book.images[k].media_type, book.images[k].data)
        for k in sorted(used)
    ]

    first_href = f"text/{page_filename(0)}"
    landmarks = [("bodymatter", first_href, "Start of Book")]
    guide = [("text", "Start of Book", first_href)]

    spec = EpubSpec(
        title=book.title.strip() or source.stem,
        language=book.language or "und",
        unique_id=_unique_id(book, blocks),
        authors=list(book.authors),
        publisher=book.publisher,
        description=book.description,
        date=book.date,
        isbn=normalize_isbn(book.isbn) if book.isbn else "",
        series_name=book.series_name,
        series_index=book.series_index,
        pages=pages,
        css=_BASE_CSS,
        images=images,
        toc=toc,
        landmarks=landmarks,
        guide=guide,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    write_epub(output, spec)

    result.page_count = len(pages)
    result.image_count = len(images)
    result.toc_entry_count = len(toc)
    return result


def print_convert_report(result: ConvertResult) -> None:
    """Plain-text summary, same style `analyze`/the MOBI and FB2 converters use."""
    from ebook_fix.report import console, print_header

    print_header("[Conversion]")
    console.print(f"Source: {result.source}")
    console.print(f"Output: {result.output}")
    console.print("")
    print_header("[Result]")
    console.print(f"Title: {result.title}")
    if result.authors:
        console.print(f"Author: {', '.join(result.authors)}")
    if result.series:
        console.print(f"Series: {result.series}")
    console.print(f"Pages: {result.page_count} ({result.chapter_count} chapter(s) found)")
    console.print(f"Images: {result.image_count}")
    console.print(f"Table of contents entries: {result.toc_entry_count}")
    if result.warnings:
        console.print("")
        print_header("[Notes]")
        for warning in result.warnings:
            console.print(f"- {warning}")
    console.print("")
    console.print("Next: run `analyze` or `repair` on the new EPUB to check it and clean it up.")
