"""
ebook_fix.html.reader

Reads one HTML book file into a plain structure convert.py can turn into
an EPUB: decoded text, a cleaned list of body blocks (paragraphs,
headings, rules, and so on), metadata guessed from the file, and any
local pictures the page points at.

HTML in the wild is messy (unclosed tags, a declared character set that
is wrong, layout wrappers, stray line breaks), so this reads it with
lxml's forgiving HTML parser and then rewrites what it found as clean
XHTML. The book's own text and emphasis come across unchanged; only
layout scaffolding (styles, scripts, wrapper divs, empty line breaks
between blocks) is dropped, because the repair modules re-apply a
standard look afterwards.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree
from lxml import html as lxml_html


class HtmlError(Exception):
    """A problem with an HTML book, worded for a person to read."""


BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol",
    "dl", "table", "pre", "hr", "figure", "center",
}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# Wrappers that are only layout: their children are lifted out.
_WRAPPER_TAGS = {"div", "section", "article", "main", "span", "font", "center", "body", "header", "footer", "nav", "aside"}
_DROP_TAGS = {"script", "style", "noscript", "iframe", "object", "embed", "form", "input", "button", "select", "textarea", "link", "meta", "head", "title", "map", "area", "canvas", "video", "audio"}
_INLINE_KEEP = {"em", "strong", "i", "b", "u", "s", "sup", "sub", "small", "cite", "code", "abbr", "br", "a", "img", "q", "span", "big"}
_RENAME = {"i": "em", "b": "strong", "strike": "s", "del": "s", "big": "strong", "tt": "code", "center": "div"}
_IMAGE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}


@dataclass
class HtmlImage:
    source_name: str      # as written in the HTML
    media_type: str
    data: bytes


@dataclass
class HtmlBook:
    title: str = ""
    authors: list = field(default_factory=list)
    language: str = ""
    publisher: str = ""
    description: str = ""
    date: str = ""
    isbn: str = ""
    series_name: str = ""
    series_index: float | None = None
    blocks: list = field(default_factory=list)       # lxml elements (XHTML-clean), in order
    images: dict = field(default_factory=dict)       # source name -> HtmlImage
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------- decoding

def decode_html(raw: bytes) -> str:
    """Bytes -> text. Order: a byte-order mark, then a UTF-8 attempt
    (which only succeeds on genuine UTF-8), then the declared charset,
    then Windows-1252. A declared "us-ascii" or "iso-8859-1" is treated as
    Windows-1252, which is what such files nearly always really are
    (curly quotes and dashes sit in the range ISO-8859-1 leaves empty)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    head = raw[:4096].decode("ascii", errors="ignore")
    m = re.search(r"charset\s*=\s*[\"']?\s*([\w\-]+)", head, re.I)
    declared = (m.group(1).lower() if m else "")
    if declared and declared not in ("us-ascii", "ascii", "iso-8859-1", "latin1", "latin-1", "windows-1252", "cp1252"):
        try:
            return raw.decode(declared)
        except (LookupError, UnicodeDecodeError):
            pass
    return raw.decode("cp1252", errors="replace")


# --------------------------------------------------------------- metadata

_TRAILING_TAGS = re.compile(r"\s*[\(\[]\s*(?:html?|v?\d+(?:\.\d+)*|retail|rtf|epub|mobi|lit|fixed|proofed|scan(?:ned)?|\w*\s*v\d+(?:\.\d+)*)\s*[\)\]]\s*$", re.I)
_SERIES_BRACKET = re.compile(r"^\[\s*(.+?)\s+(\d+(?:\.\d+)?)\s*\]$")


def parse_name_guess(text: str) -> dict:
    """'Author - [Series 01] - Title (v1.5) (html)' or 'Author - Title (html)'
    -> {author, title, series, index}. Anything it can't make sense of is
    simply left out."""
    text = re.sub(r"\.(html?|xhtml)$", "", text.strip(), flags=re.I)
    for _ in range(4):
        new = _TRAILING_TAGS.sub("", text)
        if new == text:
            break
        text = new
    parts = [p.strip() for p in re.split(r"\s+-\s+", text) if p.strip()]
    out: dict = {}
    series_part = None
    rest = []
    for p in parts:
        m = _SERIES_BRACKET.match(p)
        if m and series_part is None:
            series_part = m
        else:
            rest.append(p)
    if series_part:
        out["series"] = series_part.group(1).strip()
        out["index"] = float(series_part.group(2))
    if len(rest) >= 2:
        out["author"] = rest[0]
        out["title"] = " - ".join(rest[1:])
    elif len(rest) == 1:
        out["title"] = rest[0]
    return out


def _split_authors(text: str) -> list:
    pieces = re.split(r"\s*(?:&|\band\b|;)\s*", text)
    return [p.strip() for p in pieces if p.strip()]


def _meta(root, *names) -> str:
    wanted = {n.lower() for n in names}
    for m in root.iter("meta"):
        n = (m.get("name") or m.get("property") or "").lower()
        if n in wanted and m.get("content", "").strip():
            return m.get("content").strip()
    return ""


# ----------------------------------------------------------------- cleanup

_WS = re.compile(r"[ \t\r\n\f\v]+")


def _style_flags(style: str) -> dict:
    s = (style or "").lower().replace(" ", "")
    return {
        "bold": "font-weight:bold" in s or bool(re.search(r"font-weight:(?:[6-9]00)", s)),
        "italic": "font-style:italic" in s,
        "center": "text-align:center" in s,
        "right": "text-align:right" in s,
    }


def _text(el) -> str:
    return _WS.sub(" ", "".join(el.itertext())).strip()


def _wrap_inline(inner_nodes: list):
    p = etree.Element("p")
    prev = None
    for n in inner_nodes:
        if isinstance(n, str):
            if prev is None:
                p.text = (p.text or "") + n
            else:
                prev.tail = (prev.tail or "") + n
        else:
            p.append(n)
            prev = n
    return p


def _clean_inline(el, base_dir, images, notes, keep_ids=True):
    """Rewrites one inline/content element in place into clean XHTML."""
    # style-driven emphasis -> real tags (before attributes are removed)
    flags = _style_flags(el.get("style"))
    classes = (el.get("class") or "").split()
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag in ("span", "font") and (flags["bold"] or flags["italic"]):
        inner_tag = "strong" if flags["bold"] else "em"
        el.tag = inner_tag
        if flags["bold"] and flags["italic"]:
            wrap = etree.Element("em")
            wrap.text, el.text = el.text, None
            for c in list(el):
                wrap.append(c)
            el.append(wrap)
        tag = inner_tag
    elif tag in _RENAME:
        el.tag = _RENAME[tag]
        tag = el.tag

    keep = {}
    if tag == "a":
        href = el.get("href")
        if href and not href.lower().startswith(("javascript:", "data:")):
            keep["href"] = href
        for name in ("id", "name"):
            if el.get(name):
                keep["id"] = el.get(name)
                break
    elif tag == "img":
        src = el.get("src", "")
        keep["src"] = src
        keep["alt"] = el.get("alt", "") or ""
    elif el.get("id") and keep_ids:
        keep["id"] = el.get("id")
    elif el.get("name") and tag == "a":
        keep["id"] = el.get("name")
    if flags["center"] and tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
        keep["class"] = "center"
    elif flags["right"] and tag in ("p", "div"):
        keep["class"] = "right"
    elif "center" in classes and tag in ("p", "div"):
        keep["class"] = "center"
    el.attrib.clear()
    for k, v in keep.items():
        el.set(k, v)

    # whitespace: HTML collapses runs of whitespace, so this does too
    if tag != "pre":
        if el.text:
            el.text = _WS.sub(" ", el.text)
        for child in el:
            if child.tail:
                child.tail = _WS.sub(" ", child.tail)

    for child in list(el):
        if not isinstance(child.tag, str):
            el.remove(child) if not child.tail else _drop_keep_tail(child)
            continue
        if child.tag in _DROP_TAGS:
            _drop_keep_tail(child)
            continue
        if child.tag == "br":
            child.attrib.clear()
            continue
        _clean_inline(child, base_dir, images, notes)
        if child.tag in ("span", "font", "small") and not child.attrib and child.tag != "small":
            _unwrap(child)
        elif child.tag == "a" and not child.attrib:
            _unwrap(child)


def _drop_keep_tail(child):
    parent = child.getparent()
    tail = child.tail
    if tail:
        prev = child.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(child)


def _unwrap(el):
    parent = el.getparent()
    idx = parent.index(el)
    prev = el.getprevious()
    text = el.text or ""
    if text:
        if prev is not None:
            prev.tail = (prev.tail or "") + text
        else:
            parent.text = (parent.text or "") + text
    kids = list(el)
    tail = el.tail or ""
    parent.remove(el)
    for i, k in enumerate(kids):
        parent.insert(idx + i, k)
    if tail:
        last = kids[-1] if kids else prev
        if last is not None:
            last.tail = (last.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


def _flatten_to_blocks(container, out: list, base_dir, images, notes):
    """Walks a container's children and appends clean top-level blocks
    to `out`: block elements are kept (cleaned), wrapper elements are
    looked through, and runs of loose inline content become paragraphs."""
    run: list = []

    def flush():
        nodes = [n for n in run]
        run.clear()
        if not nodes:
            return
        p = _wrap_inline(nodes)
        has_content = bool(_text(p)) or p.find(".//img") is not None
        if has_content:
            _clean_inline(p, base_dir, images, notes)
            out.append(p)

    if container.text and container.text.strip():
        run.append(container.text)
    for child in list(container):
        tail = child.tail
        tag = child.tag if isinstance(child.tag, str) else ""
        if not tag:
            pass
        elif tag in _DROP_TAGS:
            pass
        elif tag == "br":
            if run:
                run.append(_br())
        elif tag in BLOCK_TAGS:
            flush()
            child.tail = None
            _clean_block(child, out, base_dir, images, notes)
        elif tag in _WRAPPER_TAGS and _has_block_descendant(child):
            flush()
            # A centered wrapper that holds only inline lines stays one paragraph.
            _flatten_to_blocks(child, out, base_dir, images, notes)
        elif tag in _WRAPPER_TAGS and tag in ("div", "section", "article", "header", "footer", "aside", "nav", "main"):
            # a div of inline content (and <br> line breaks) is one paragraph
            flush()
            flags = _style_flags(child.get("style"))
            child.tail = None
            _div_to_paragraphs(child, flags, out, base_dir, images, notes)
        else:
            child.tail = None
            run.append(child)
        if tail and tail.strip():
            run.append(tail)
        elif tail and run:
            run.append(" ")
    flush()


def _br():
    return etree.Element("br")


def _has_block_descendant(el) -> bool:
    for d in el.iterdescendants():
        if isinstance(d.tag, str) and d.tag in BLOCK_TAGS:
            return True
    return False


def _div_to_paragraphs(div, flags, out, base_dir, images, notes):
    """A <div> with only inline content and <br>s: two or more <br> in a
    row split paragraphs, a single <br> stays a line break."""
    groups: list = [[]]
    nodes: list = []
    if div.text:
        nodes.append(div.text)
    for c in div:
        nodes.append(c)
        if c.tail:
            nodes.append(c.tail)
            c.tail = None
    div.text = None
    pending_br = 0
    for n in nodes:
        if not isinstance(n, str) and n.tag == "br":
            pending_br += 1
            continue
        if isinstance(n, str) and not n.strip() and pending_br:
            continue
        if pending_br >= 2:
            groups.append([])
        elif pending_br == 1 and groups[-1]:
            groups[-1].append(_br())
        pending_br = 0
        groups[-1].append(n)
    for g in groups:
        if not g:
            continue
        p = _wrap_inline(g)
        if not (_text(p) or p.find(".//img") is not None):
            continue
        _clean_inline(p, base_dir, images, notes)
        if flags["center"]:
            p.set("class", "center")
        elif flags["right"]:
            p.set("class", "right")
        out.append(p)


def _clean_block(el, out, base_dir, images, notes):
    tag = el.tag
    if tag == "center":
        _flatten_to_blocks(el, out, base_dir, images, notes)
        return
    if tag == "hr":
        e = etree.Element("hr")
        out.append(e)
        return
    if tag in ("ul", "ol", "dl", "table", "blockquote", "figure"):
        # kept as a unit; anything inside is cleaned the same inline way
        if tag == "blockquote" and not any(isinstance(c.tag, str) and c.tag in ("p",) for c in el):
            # loose text inside a blockquote -> wrap in a paragraph
            p = _wrap_inline(([el.text] if el.text and el.text.strip() else []) + [c for c in el])
            for c in list(el):
                el.remove(c)
            el.text = None
            el.append(p)
        _clean_deep(el, base_dir, images, notes)
        out.append(el)
        return
    _clean_inline(el, base_dir, images, notes)
    if tag in HEADING_TAGS or tag == "p" or tag == "pre":
        if not (_text(el) or el.find(".//img") is not None):
            return
    out.append(el)


def _clean_deep(el, base_dir, images, notes):
    for d in list(el.iter()):
        if not isinstance(d.tag, str):
            continue
        if d.tag in ("tbody", "thead", "tfoot", "tr", "td", "th", "table", "ul", "ol", "li", "dl", "dt", "dd", "blockquote", "figure", "figcaption", "caption", "p"):
            keep_colspan = {k: d.get(k) for k in ("colspan", "rowspan") if d.get(k) and d.get(k).isdigit()}
            had_center = _style_flags(d.get("style"))["center"]
            d.attrib.clear()
            for k, v in keep_colspan.items():
                d.set(k, v)
            if had_center and d.tag == "p":
                d.set("class", "center")
        else:
            pass
    _clean_inline_tree(el, base_dir, images, notes)


def _clean_inline_tree(el, base_dir, images, notes):
    for child in list(el):
        if not isinstance(child.tag, str) or child.tag in _DROP_TAGS:
            _drop_keep_tail(child)
            continue
        if child.tag in ("table", "tbody", "thead", "tfoot", "tr", "td", "th", "ul", "ol", "li", "dl", "dt", "dd", "blockquote", "figure", "figcaption", "caption", "p"):
            _clean_inline_tree(child, base_dir, images, notes)
        else:
            _clean_inline(child, base_dir, images, notes)
    if el.text:
        el.text = _WS.sub(" ", el.text)


# ------------------------------------------------------------------ images

def _load_images(blocks: list, base_dir: Path, images: dict, notes: list) -> None:
    dropped = 0
    for img in [i for b in blocks for i in ([b] if b.tag == "img" else []) + list(b.iter("img"))]:
        src = img.get("src", "")
        name = src.split("#")[0].split("?")[0]
        ext = Path(name).suffix.lower()
        path = (base_dir / name) if (name and not re.match(r"^[a-z]+:", name, re.I)) else None
        if name in images:
            continue
        if path is None or ext not in _IMAGE_TYPES or not path.is_file():
            parent = img.getparent()
            if parent is not None:
                _drop_keep_tail(img)
            dropped += 1
            continue
        images[name] = HtmlImage(name, _IMAGE_TYPES[ext], path.read_bytes())
    if dropped:
        notes.append(f"{dropped} picture(s) pointed at a missing or online-only file and were left out.")


# -------------------------------------------------------------------- main

def read_html(path: Path) -> HtmlBook:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HtmlError(f"Couldn't read '{path.name}': {exc}")
    if not raw.strip():
        raise HtmlError("That file is empty.")
    text = decode_html(raw)
    # An XML declaration at the top confuses lxml's HTML parser when the text is a str.
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    try:
        root = lxml_html.document_fromstring(text)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        raise HtmlError("This file doesn't contain readable HTML.")
    body = root.find("body")
    if body is None or not _text(body):
        raise HtmlError("This HTML file has no text in its body.")

    book = HtmlBook()

    # -- metadata: tags in the file, then the file name, then <title>
    title_tag = _WS.sub(" ", (root.findtext("head/title") or "")).strip()
    name_guess = parse_name_guess(path.name)
    title_guess = parse_name_guess(title_tag) if title_tag else {}

    meta_title = _meta(root, "dc.title", "dc:title", "title", "og:title")
    meta_author = _meta(root, "author", "dc.creator", "dc:creator")
    # A file name shaped like "Author - Title" is the most reliable source; a
    # bare name only counts after the page's own <title>.
    structured_name = "author" in name_guess
    book.title = (
        meta_title
        or (name_guess.get("title") if structured_name else "")
        or title_guess.get("title")
        or title_tag
        or name_guess.get("title")
        or path.stem
    )
    author_text = meta_author or name_guess.get("author") or title_guess.get("author") or ""
    book.authors = _split_authors(author_text)
    book.series_name = name_guess.get("series", "")
    book.series_index = name_guess.get("index")
    book.language = (_meta(root, "dc.language", "language") or root.get("lang") or root.get("{http://www.w3.org/XML/1998/namespace}lang") or "").strip()
    book.publisher = _meta(root, "dc.publisher", "publisher")
    book.description = _meta(root, "description", "dc.description")
    book.date = _meta(root, "dc.date", "date")
    isbn = _meta(root, "isbn", "dc.identifier")
    book.isbn = isbn if re.sub(r"[^0-9Xx]", "", isbn).__len__() in (10, 13) else ""

    for bad in body.xpath(".//script|.//style|.//noscript"):
        _drop_keep_tail(bad)

    blocks: list = []
    base_dir = path.parent
    _flatten_to_blocks(body, blocks, base_dir, book.images, book.notes)
    # The scaffolding leaves stray empty blocks; drop them, and a rule at the very start or end.
    blocks = [b for b in blocks if b.tag in ("hr",) or _text(b) or b.find(".//img") is not None or b.tag == "img"]
    while blocks and blocks[0].tag == "hr":
        blocks.pop(0)
    while blocks and blocks[-1].tag == "hr":
        blocks.pop()
    if not any(_text(b) for b in blocks):
        raise HtmlError("This HTML file has no readable text.")
    _load_images(blocks, base_dir, book.images, book.notes)
    book.blocks = blocks
    return book
