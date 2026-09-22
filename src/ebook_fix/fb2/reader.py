"""
ebook_fix.fb2.reader

Opens an FB2 (FictionBook 2.0) file and pulls out everything a
converter needs: metadata, the binary attachments (images), and the
book's body content as lxml elements ready for fb2/markup.py to walk.

Unlike MOBI, FB2 is an open, fully-documented XML format -- there's no
reverse-engineered container or compression to deal with, and the
input is already well-formed XML (or convert_fb2_to_epub refuses it
with a clear message if it isn't). That makes this module much
smaller than mobi/reader.py: parsing is just "read the XML", not
"reconstruct the file format".

A file can have more than one <body>. By FB2 convention (and in both
samples this was verified against), the first is the book's main
content; any body after that holds footnotes/endnotes/comments, not
part of the main reading flow. This module keeps that distinction
(`main_body` vs `auxiliary_bodies`) rather than flattening every body
into one list, since fb2/convert.py treats the two very differently
(one becomes the book's chapters, the others become notes pages).
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ebook_fix.cover import sniff_image_media_type

FB2_NAMESPACES = (
    "http://www.gribuser.ru/xml/fictionbook/2.0",
    "http://www.gribuser.ru/xml/fictionbook/2.1",
)
XLINK_NS = "http://www.w3.org/1999/xlink"


class Fb2Error(Exception):
    """An FB2 file that can't be opened or converted. The message is
    written to be shown to a person as-is."""


@dataclass
class Fb2Author:
    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    nickname: str = ""

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.middle_name, self.last_name) if p]
        if parts:
            return " ".join(parts)
        return self.nickname


@dataclass
class Fb2Binary:
    id: str = ""
    media_type: str = ""
    data: bytes = b""


@dataclass
class Fb2Book:
    ns: str = FB2_NAMESPACES[0]
    title: str = ""
    authors: list = field(default_factory=list)      # list[Fb2Author]
    translators: list = field(default_factory=list)  # list[Fb2Author]
    genres: list = field(default_factory=list)        # raw genre codes
    annotation_text: str = ""
    language: str = ""
    date: str = ""
    publisher: str = ""
    isbn: str = ""
    series_name: str = ""
    series_index: float | None = None
    document_id: str = ""
    cover_binary_id: str = ""
    binaries: dict = field(default_factory=dict)       # id -> Fb2Binary
    main_body: object = None                           # lxml element
    auxiliary_bodies: list = field(default_factory=list)  # list[lxml element]


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _parse_author(el, ns: str) -> Fb2Author:
    def part(tag):
        return _text(el.find(_q(ns, tag)))
    return Fb2Author(
        first_name=part("first-name"),
        middle_name=part("middle-name"),
        last_name=part("last-name"),
        nickname=part("nickname"),
    )


def _href(el, ns: str) -> str:
    """The l:href (or bare href) of an <image>/<a>, with a leading '#'
    stripped -- FB2 always references its own <binary> elements or
    internal ids this way."""
    raw = el.get(f"{{{XLINK_NS}}}href") or el.get("href") or ""
    return raw[1:] if raw.startswith("#") else raw


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

def read_fb2(path: Path) -> Fb2Book:
    """Opens an FB2 file. Raises Fb2Error (with a message meant for a
    person) for anything unreadable."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Fb2Error(f"Couldn't read '{path}': {exc}")

    if not raw.lstrip().startswith(b"<"):
        raise Fb2Error("This doesn't look like an FB2 book (not XML).")

    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    try:
        root = etree.fromstring(raw, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise Fb2Error(f"This FB2 file isn't well-formed XML, so it can't be read: {exc}")

    ns = etree.QName(root).namespace
    if root.tag != _q(ns or "", "FictionBook") or ns not in FB2_NAMESPACES:
        raise Fb2Error(
            "This doesn't look like an FB2 book (expected a FictionBook 2.x root element)."
        )

    book = Fb2Book(ns=ns)

    for binary in root.findall(_q(ns, "binary")):
        bid = binary.get("id") or ""
        text = (binary.text or "").strip()
        if not bid or not text:
            continue
        try:
            data = base64.b64decode(text, validate=False)
        except (ValueError, base64.binascii.Error):
            continue
        # Sniffed from the actual bytes, not trusted from the binary's
        # own content-type attribute -- same posture as the rest of
        # the project (see ebook_fix.cover.sniff_image_media_type):
        # a declared type can be wrong, and a wrong media-type
        # manifest entry is worse than skipping the image.
        media_type = sniff_image_media_type(data)
        if media_type is None:
            continue
        book.binaries[bid] = Fb2Binary(id=bid, media_type=media_type, data=data)

    description = root.find(_q(ns, "description"))
    if description is None:
        raise Fb2Error("This FB2 file has no <description> block, so there's no book metadata to read.")

    title_info = description.find(_q(ns, "title-info"))
    if title_info is None:
        raise Fb2Error("This FB2 file has no <title-info>, so there's no book metadata to read.")

    book.title = _text(title_info.find(_q(ns, "book-title")))

    for author_el in title_info.findall(_q(ns, "author")):
        author = _parse_author(author_el, ns)
        if author.display_name:
            book.authors.append(author)
    for translator_el in title_info.findall(_q(ns, "translator")):
        translator = _parse_author(translator_el, ns)
        if translator.display_name:
            book.translators.append(translator)

    book.genres = [_text(g) for g in title_info.findall(_q(ns, "genre")) if _text(g)]
    book.language = _text(title_info.find(_q(ns, "lang")))

    date_el = title_info.find(_q(ns, "date"))
    if date_el is not None:
        book.date = (date_el.get("value") or _text(date_el)).strip()

    annotation_el = title_info.find(_q(ns, "annotation"))
    if annotation_el is not None:
        # Plain text for dc:description, not markup: paragraph text
        # joined with blank lines, the way a person would read it out
        # loud rather than as HTML.
        paragraphs = [_text(p) for p in annotation_el if _text(p)]
        book.annotation_text = "\n\n".join(paragraphs) if paragraphs else _text(annotation_el)

    sequence_el = title_info.find(_q(ns, "sequence"))
    if sequence_el is not None:
        book.series_name = (sequence_el.get("name") or "").strip()
        number = (sequence_el.get("number") or "").strip()
        if number:
            try:
                book.series_index = float(number)
            except ValueError:
                pass

    coverpage = title_info.find(_q(ns, "coverpage"))
    if coverpage is not None:
        image_el = coverpage.find(_q(ns, "image"))
        if image_el is not None:
            book.cover_binary_id = _href(image_el, ns)

    document_info = description.find(_q(ns, "document-info"))
    if document_info is not None:
        raw_id = _text(document_info.find(_q(ns, "id")))
        match = _UUID_RE.search(raw_id)
        if match:
            book.document_id = match.group(0)

    publish_info = description.find(_q(ns, "publish-info"))
    if publish_info is not None:
        book.publisher = _text(publish_info.find(_q(ns, "publisher")))
        book.isbn = _text(publish_info.find(_q(ns, "isbn")))
        if not book.date:
            book.date = _text(publish_info.find(_q(ns, "year")))

    bodies = root.findall(_q(ns, "body"))
    if not bodies:
        raise Fb2Error("This FB2 file has no <body>, so there's no book text to convert.")
    book.main_body = bodies[0]
    book.auxiliary_bodies = bodies[1:]

    return book
