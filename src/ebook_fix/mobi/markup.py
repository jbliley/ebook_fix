"""
ebook_fix.mobi.markup

Turns a MOBI7 book's raw markup into clean, well-formed XHTML pages.

MOBI7 markup is old, sloppy HTML with several private extensions, so a
normal XML parser can't be pointed at it. What's handled here:

- Page breaks (`<mbp:pagebreak/>`) become the boundaries between output
  files: one XHTML file per page. A page that would be empty (a break
  right after another break) is folded into its neighbor.
- Links (`<a filepos="N">`) point at a *byte offset* in the original
  text, not a named anchor. Every offset that anything points at (a
  link, the guide, the table of contents) gets a real `id="fileposN"`
  anchor put at exactly that spot, and each link is then rewritten to
  the output file that ended up holding it. A target sitting on the
  start of a tag lands on that tag's own `id` when possible instead of
  a stray empty anchor.
- Images (`<img recindex="N">`) become `<img src=...>` pointing at the
  extracted image files.
- Paragraph spacing and indent live in private `height` and `width`
  attributes (top margin and first-line indent); alignment is `align`;
  `<font size color>` and `<center>` are obsolete HTML. All of these
  become small shared CSS classes (m1, m2, ... most-used first) rather
  than repeating an inline style on every paragraph.
- Unclosed and mis-nested tags are repaired (an open `<p>` closes when
  the next block starts, a stray `</br>` is ignored, and so on) so
  every output page parses as strict XML.
- Text sitting directly in the page body with no block around it gets
  wrapped in a paragraph.

Confirmed against the real MOBI7 sample (examples/MOBI-Example.mobi).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as _xml_escape

# ---------------------------------------------------------------------
# Tokenizing
# ---------------------------------------------------------------------

# A tag, comment, or declaration. Quoted attribute values may contain
# '>'; a second, looser alternative catches a tag whose quotes are
# unbalanced instead of letting one bad quote swallow the book.
_TOKEN_RE = re.compile(
    rb"<!--.*?-->"
    rb"|<[/!?]?[A-Za-z](?:\"[^\"<]*\"|'[^'<]*'|[^'\">])*>"
    rb"|<[/!?]?[A-Za-z][^>]*>",
    re.S,
)
_ATTR_RE = re.compile(
    r"""([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*)))?"""
)
_FILEPOS_RE = re.compile(rb"filepos\s*=\s*[\"']?(\d+)")

# Control characters XML doesn't allow.
_BAD_XML_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

_VOID = {"br", "hr", "img"}
_BLOCKS = {
    "address", "blockquote", "div", "dl", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "ol", "p", "pre", "table", "ul",
}
_ALLOWED = {
    "a", "abbr", "address", "b", "blockquote", "br", "caption", "cite", "code",
    "col", "colgroup", "dd", "del", "dfn", "div", "dl", "dt", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "ol", "p",
    "pre", "q", "s", "samp", "small", "span", "strike", "strong", "sub", "sup",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul", "var",
}
_TAG_MAP = {
    "center": "div", "big": "span", "strike": "s", "tt": "span", "font": "span",
    "acronym": "abbr", "listing": "pre", "xmp": "pre", "dir": "ul", "menu": "ul",
}
# Structural elements that, like blocks, never sit inside a paragraph's
# inline text and so never trigger the loose-text paragraph wrapper.
_BLOCKISH_EXTRA = {
    "li", "dt", "dd", "tr", "td", "th", "caption", "thead", "tbody", "tfoot",
    "colgroup", "col",
}
# Elements that mark the edge of a paragraph's scope when looking for an
# open <p> to close.
_P_SCOPE_BOUNDARY = {"td", "th", "li", "caption", "table", "ul", "ol", "dl"}
# Elements that hold only other elements; loose whitespace inside them
# is dropped instead of kept.
_STRUCTURAL = {"ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "tr", "colgroup"}

# HTML's own font-size scale (size 3 is the normal size).
_FONT_SIZES = {1: "0.63em", 2: "0.82em", 3: "1em", 4: "1.13em", 5: "1.5em", 6: "2em", 7: "3em"}

_UNIT_VALUE = re.compile(r"^-?(\d+\.?\d*|\.\d+)(em|ex|pt|px|%|in|cm|mm|pc)?$")
_ALIGN_VALUES = {"left", "right", "center", "justify"}

_LINK_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _clean_text(value: str) -> str:
    return _BAD_XML_CHARS.sub("", value)


def _esc_attr(value: str) -> str:
    return _xml_escape(value, {'"': "&quot;"})


def _css_length(raw: str) -> str | None:
    """A MOBI length attribute -> a CSS length, or None if unusable. A
    bare number (no unit) is read as pixels."""
    raw = raw.strip().lower()
    if not _UNIT_VALUE.match(raw):
        return None
    if raw.lstrip("-").replace(".", "").isdigit():
        return "0" if float(raw) == 0 else raw + "px"
    return raw


def _sanitize_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.:-]", "_", value.strip())
    if not value:
        return ""
    if not re.match(r"[A-Za-z_]", value[0]):
        value = "id_" + value
    return value


@dataclass
class _Token:
    kind: str       # "tag" or "text"
    start: int
    end: int


@dataclass
class _Open:
    """One element on the logical open-element stack."""
    src_name: str
    out_name: str | None     # None = a source tag that produced no output tag
    attrs: list              # [(name, value)] without any id
    auto: bool = False       # a paragraph this converter added around loose text


@dataclass
class Page:
    body: str = ""
    ids: set = field(default_factory=set)


@dataclass
class MarkupResult:
    pages: list = field(default_factory=list)          # list[Page]
    css_rules: list = field(default_factory=list)      # [(class name, declarations)]
    guide: list = field(default_factory=list)          # [(type, title, filepos)]
    used_images: set = field(default_factory=set)      # recindex numbers referenced
    dropped_images: int = 0
    id_page: dict = field(default_factory=dict)        # id -> page index


class _Converter:
    def __init__(self, text: bytes, encoding: str, targets: set, images: dict, image_href):
        self.data = text
        self.encoding = "utf-8" if encoding == "utf-8" else "cp1252"
        self.images = images
        self.image_href = image_href    # recindex -> href string (or None)
        self.targets = targets

        self.pages: list[Page] = []
        self.cur: list[str] = []
        self.cur_ids: set = set()
        self.stack: list[_Open] = []
        self.page_open = False
        self.has_content = False
        self.pending: list[str] = []
        self.in_head = False
        self.guide: list = []
        self.used_images: set = set()
        self.dropped_images = 0
        self.seen_ids: set = set()

        self.class_index: dict[str, int] = {}
        self.class_decls: list[str] = []
        self.class_counts: list[int] = []

    # -- decoding -------------------------------------------------------

    def _decode(self, raw: bytes) -> str:
        if self.encoding == "utf-8":
            text = raw.decode("utf-8", errors="replace")
        else:
            text = raw.decode("cp1252", errors="replace")
        return _clean_text(html.unescape(text))

    # -- token pre-pass -------------------------------------------------

    def _tokenize(self) -> tuple[list, dict]:
        """Splits the markup into tags and text runs, and works out where
        each link target's anchor has to go: a target on a tag's start
        goes before that tag, one inside a tag snaps to that tag's start,
        and one inside a text run splits the run there."""
        data = self.data
        raw_tokens: list[_Token] = []
        pos = 0
        for m in _TOKEN_RE.finditer(data):
            if m.start() > pos:
                raw_tokens.append(_Token("text", pos, m.start()))
            raw_tokens.append(_Token("tag", m.start(), m.end()))
            pos = m.end()
        if pos < len(data):
            raw_tokens.append(_Token("text", pos, len(data)))

        anchor_at: dict[int, list[int]] = {}
        end_targets: list[int] = []
        # A target past the end of the text can't be a real place in the
        # book (a corrupt link); it is left out so the link degrades to
        # plain text instead of pointing at the end of the book.
        remaining = sorted(t for t in self.targets if t <= len(data))
        ti = 0
        tokens: list[_Token] = []
        for tok in raw_tokens:
            while ti < len(remaining) and remaining[ti] <= tok.start:
                anchor_at.setdefault(tok.start, []).append(remaining[ti])
                ti += 1
            if tok.kind == "tag":
                while ti < len(remaining) and remaining[ti] < tok.end:
                    anchor_at.setdefault(tok.start, []).append(remaining[ti])
                    ti += 1
                tokens.append(tok)
                continue
            # text run: split at every target strictly inside it
            cursor = tok.start
            while ti < len(remaining) and remaining[ti] < tok.end:
                cut = remaining[ti]
                if self.encoding == "utf-8":
                    while cut > tok.start and (data[cut] & 0xC0) == 0x80:
                        cut -= 1
                if cut > cursor:
                    tokens.append(_Token("text", cursor, cut))
                    cursor = cut
                anchor_at.setdefault(cursor, []).append(remaining[ti])
                ti += 1
            tokens.append(_Token("text", cursor, tok.end))
        while ti < len(remaining):
            end_targets.append(remaining[ti])
            ti += 1
        if end_targets:
            anchor_at.setdefault(len(data), []).extend(end_targets)
        return tokens, anchor_at

    # -- output helpers -------------------------------------------------

    def _emit(self, s: str) -> None:
        self.cur.append(s)

    def _class_for(self, decls: str) -> str:
        idx = self.class_index.get(decls)
        if idx is None:
            idx = len(self.class_decls)
            self.class_index[decls] = idx
            self.class_decls.append(decls)
            self.class_counts.append(0)
        self.class_counts[idx] += 1
        return f"\x01C{idx}\x02"

    def _start_html(self, name: str, attrs: list, first_id: str = "") -> str:
        parts = [name]
        if first_id:
            parts.append(f'id="{_esc_attr(first_id)}"')
            self.cur_ids.add(first_id)
        for k, v in attrs:
            if k == "\x01href":
                parts.append(v)   # already a complete placeholder attribute
            else:
                parts.append(f'{k}="{_esc_attr(v)}"')
        return "<" + " ".join(parts) + ">"

    def _begin_content(self) -> None:
        """Called before anything visible is written: if this page just
        started and elements were still open from the previous one,
        reopen them here."""
        if not self.page_open:
            self.page_open = True
            for entry in self.stack:
                if entry.out_name:
                    self._emit(self._start_html(entry.out_name, entry.attrs))

    def _close_tag(self, name: str) -> None:
        """Emits a closing tag. A block-level element also gets a single
        following space, so plain-text extraction elsewhere in the
        project (frontmatter detection, word counts, and so on) doesn't
        glue this element's text onto the next one -- e.g. a heading
        "Dedication" immediately followed by a paragraph "I feel an
        army..." must read as two words, not "DedicationI". Ordinary
        EPUB source markup has this same separating whitespace built
        in (it's how a human-edited or Calibre-produced file is
        formatted); MOBI7's own markup mostly doesn't, so this converter
        adds it back. A single space, not a newline: the Whitespace
        Normalizer repair module treats an already-single-space
        whitespace-only text node as nothing to fix, so this never
        shows up as a spurious finding on a freshly converted book (a
        newline would, since it isn't already normalized)."""
        self._emit(f"</{name}>")
        if name in _BLOCKS or name in _BLOCKISH_EXTRA:
            self._emit(" ")

    def _pop_to(self, index: int) -> None:
        """Closes stack entries down to and including `index`."""
        while len(self.stack) > index:
            entry = self.stack.pop()
            if entry.out_name and self.page_open:
                self._close_tag(entry.out_name)

    def _flush_pending(self) -> None:
        for ident in self.pending:
            if ident in self.seen_ids:
                continue
            self.seen_ids.add(ident)
            self.cur_ids.add(ident)
            self._emit(f'<a id="{ident}"></a>')
        self.pending = []

    def _take_pending_for_element(self) -> str:
        """The first still-unused pending anchor id, to be attached to the
        element about to open; any others are written as empty anchors."""
        first = ""
        rest = []
        for ident in self.pending:
            if ident in self.seen_ids:
                continue
            if not first:
                first = ident
                self.seen_ids.add(ident)
            else:
                rest.append(ident)
        self.pending = rest
        if rest:
            self._flush_pending()
        return first

    def _prepare_inline(self) -> None:
        self._begin_content()
        if not self.stack:
            self.stack.append(_Open("p", "p", [], auto=True))
            self._emit("<p>")
            self.has_content = True
        self._flush_pending()

    def _close_open_paragraph(self) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            name = self.stack[i].out_name
            if name == "p":
                self._pop_to(i)
                return
            if name in _P_SCOPE_BOUNDARY:
                return

    # -- attributes -----------------------------------------------------

    def _attrs_to_output(self, src: str, out: str, attrs: dict) -> tuple[list, str]:
        """Returns ([(name, value)] for the output tag excluding id, own
        id) with private and obsolete attributes folded into a class."""
        keep: list = []
        style: dict[str, str] = {}
        own_id = ""
        extra_class = ""

        if src == "center":
            style["text-align"] = "center"
        elif src == "big":
            style["font-size"] = "1.2em"
        elif src == "tt":
            style["font-family"] = "monospace"

        for k, v in attrs.items():
            v = v.strip()
            if k == "id" or (k == "name" and out == "a"):
                if not own_id:
                    own_id = _sanitize_id(v)
            elif k == "class":
                extra_class = v
            elif k == "title":
                keep.append(("title", v))
            elif k == "style":
                for decl in v.split(";"):
                    if ":" in decl:
                        prop, val = decl.split(":", 1)
                        style.setdefault(prop.strip().lower(), val.strip())
            elif k == "align" and out not in ("img", "table", "hr"):
                if v.lower() in _ALIGN_VALUES:
                    style["text-align"] = v.lower()
            elif k == "height" and out not in ("img", "table", "td", "th", "tr", "col", "hr"):
                length = _css_length(v)
                if length is not None:
                    style["margin-top"] = length
            elif k == "width" and out not in ("img", "table", "td", "th", "tr", "col", "hr"):
                length = _css_length(v)
                if length is not None:
                    style["text-indent"] = length
            elif k == "width" and out in ("table", "td", "th", "col"):
                length = _css_length(v)
                if length is not None:
                    style["width"] = length
            elif k in ("colspan", "rowspan") and out in ("td", "th") and v.isdigit():
                keep.append((k, v))
            elif k == "start" and out == "ol" and v.lstrip("-").isdigit():
                keep.append((k, v))
            elif k == "value" and out == "li" and v.lstrip("-").isdigit():
                keep.append((k, v))
            elif src == "font" and k == "size":
                m = re.match(r"^([+-]?)(\d)$", v)
                if m:
                    n = int(m.group(2))
                    size = 3 + n if m.group(1) == "+" else 3 - n if m.group(1) == "-" else n
                    size = max(1, min(7, size))
                    if size != 3:
                        style["font-size"] = _FONT_SIZES[size]
            elif src == "font" and k == "color":
                if re.match(r"^#?[0-9A-Za-z]+$", v):
                    style["color"] = v if not re.match(r"^[0-9A-Fa-f]{6}$", v) else "#" + v

        classes = []
        if extra_class:
            classes.append(extra_class)
        if style:
            order = ["margin-top", "text-indent", "text-align", "width", "font-size", "font-family", "color"]
            names = [n for n in order if n in style] + sorted(n for n in style if n not in order)
            decls = "; ".join(f"{n}: {style[n]}" for n in names)
            classes.append(self._class_for(decls))
        if classes:
            keep.append(("class", " ".join(classes)))
        return keep, own_id

    # -- events ---------------------------------------------------------

    def _handle_text(self, raw: bytes) -> None:
        if self.in_head:
            return
        text = self._decode(raw)
        if not text:
            return
        if text.strip():
            self._prepare_inline()
            self._emit(_xml_escape(text))
            self.has_content = True
            return
        # Whitespace only: keep it between inline pieces of a paragraph,
        # drop it between blocks and inside table/list scaffolding.
        if not self.stack or not self.page_open:
            return
        if self.stack[-1].out_name is None or self.stack[-1].out_name in _STRUCTURAL:
            return
        self._emit(_xml_escape(text))

    def _pagebreak(self) -> None:
        if not self.has_content:
            return
        if self.page_open:
            for entry in reversed(self.stack):
                if entry.out_name:
                    self._close_tag(entry.out_name)
        self._finish_page()
        self.page_open = False
        self.has_content = False

    def _finish_page(self) -> None:
        self.pages.append(Page(body="".join(self.cur), ids=self.cur_ids))
        self.cur = []
        self.cur_ids = set()

    def _handle_reference(self, attrs: dict) -> None:
        pos = attrs.get("filepos", "").strip()
        if pos.isdigit():
            self.guide.append((attrs.get("type", "").strip().lower(), attrs.get("title", "").strip(), int(pos)))

    def _link_attr(self, attrs: dict) -> str:
        """The complete href attribute (as a placeholder resolved after
        pagination), or "" for a link that can't be kept."""
        filepos = attrs.get("filepos", "").strip()
        if filepos.isdigit():
            return f'href="\x01L{int(filepos)}\x02"'
        href = attrs.get("href", "").strip()
        if not href:
            return ""
        if href.startswith("#"):
            return f'href="\x01I{_sanitize_id(href[1:])}\x02"'
        if _LINK_SCHEME.match(href):
            return f'href="{_esc_attr(html.unescape(href))}"'
        return ""

    def _handle_start(self, name: str, attrs: dict, self_closing: bool) -> None:
        if name.startswith("mbp:"):
            if name == "mbp:pagebreak":
                self._pagebreak()
            return
        if name == "head":
            self.in_head = True
            return
        if name == "reference":
            self._handle_reference(attrs)
            return
        if name in ("html", "body", "guide", "meta", "title", "link", "style", "script"):
            return
        if self.in_head:
            return

        out = _TAG_MAP.get(name, name)
        if out not in _ALLOWED:
            return  # unknown tag: drop the tag, keep whatever it wrapped

        if out == "img":
            self._handle_image(attrs)
            return

        if out == "br":
            self._prepare_inline()
            self._emit("<br/>")
            return

        if out == "hr":
            self._close_open_paragraph()
            self._begin_content()
            ident = self._take_pending_for_element()
            self._emit(self._start_html("hr", [], ident)[:-1] + "/>")
            self.has_content = True
            return

        # Structural repairs before opening.
        if out in _BLOCKS:
            self._close_open_paragraph()
        elif out == "li":
            self._close_up_to("li", {"ul", "ol"})
        elif out in ("dt", "dd"):
            self._close_up_to(("dt", "dd"), {"dl"})
        elif out == "tr":
            self._close_up_to("tr", {"table", "thead", "tbody", "tfoot"})
        elif out in ("td", "th"):
            self._close_up_to(("td", "th"), {"tr", "table"})
        elif out == "a":
            self._close_up_to("a", set())

        attr_list, own_id = self._attrs_to_output(name, out, attrs)
        drop_tag = False
        if name == "font" and not attr_list:
            drop_tag = True
        if out == "a":
            href = self._link_attr(attrs)
            if href:
                attr_list.insert(0, ("\x01href", href))
            elif not own_id:
                drop_tag = True

        if drop_tag:
            self.stack.append(_Open(name, None, []))
            if self_closing:
                self.stack.pop()
            return

        has_href = any(k == "\x01href" for k, _ in attr_list)
        structural_or_block = out in _BLOCKS or out in _BLOCKISH_EXTRA
        neutral_anchor = out == "a" and not has_href and own_id and not self.stack

        if structural_or_block or neutral_anchor:
            self._begin_content()
            if own_id and own_id not in self.seen_ids:
                ident = own_id
                self._flush_pending()
            else:
                ident = self._take_pending_for_element()
        else:
            self._prepare_inline()
            ident = own_id if own_id and own_id not in self.seen_ids else ""
        if ident:
            self.seen_ids.add(ident)

        self._emit(self._start_html(out, attr_list, ident))
        if not neutral_anchor:
            self.has_content = True
        self.stack.append(_Open(name, out, attr_list))
        if self_closing:
            self._handle_end(name)

    def _close_up_to(self, names, boundaries: set) -> None:
        if isinstance(names, str):
            names = (names,)
        for i in range(len(self.stack) - 1, -1, -1):
            out = self.stack[i].out_name
            if out in names:
                self._pop_to(i)
                return
            if out in boundaries:
                return

    def _handle_end(self, name: str) -> None:
        if name == "head":
            self.in_head = False
            return
        if self.in_head or name.startswith("mbp:") or name in ("html", "body", "guide", "reference"):
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].src_name == name:
                self._pop_to(i)
                return
        # Nothing open by that name (a stray </br> or </p>): ignored.

    def _handle_image(self, attrs: dict) -> None:
        recindex = None
        for key in ("recindex", "hirecindex", "lorecindex"):
            value = attrs.get(key, "").strip()
            if value.isdigit():
                recindex = int(value)
                break
        href = self.image_href(recindex) if recindex is not None else None
        if not href:
            self.dropped_images += 1
            return
        self.used_images.add(recindex)
        self._prepare_inline()
        parts = [("src", href), ("alt", html.unescape(attrs.get("alt", "")).strip())]
        for dim in ("width", "height"):
            v = attrs.get(dim, "").strip()
            if v.isdigit() and int(v) > 0:
                parts.append((dim, v))
        self._emit(self._start_html("img", parts)[:-1] + "/>")
        self.has_content = True

    # -- driver ---------------------------------------------------------

    def run(self) -> MarkupResult:
        tokens, anchor_at = self._tokenize()
        for tok in tokens:
            for target in anchor_at.get(tok.start, ()):
                ident = f"filepos{target}"
                if ident not in self.pending:
                    self.pending.append(ident)
            raw = self.data[tok.start:tok.end]
            if tok.kind == "text":
                self._handle_text(raw)
                continue
            if raw.startswith(b"<!") or raw.startswith(b"<?"):
                continue
            body = raw[1:-1].decode("latin-1")
            closing = body.startswith("/")
            if closing:
                self._handle_end(body[1:].strip().split()[0].lower() if body[1:].strip() else "")
                continue
            self_closing = body.endswith("/")
            if self_closing:
                body = body[:-1]
            parts = body.strip().split(None, 1)
            if not parts:
                continue
            name = parts[0].lower()
            attrs: dict[str, str] = {}
            if len(parts) > 1:
                for m in _ATTR_RE.finditer(parts[1]):
                    key = m.group(1).lower()
                    val = next((g for g in m.groups()[1:] if g is not None), "")
                    if key not in attrs:
                        attrs[key] = self._decode(val.encode("latin-1")) if val else ""
            self._handle_start(name, attrs, self_closing)

        for target in anchor_at.get(len(self.data), ()):
            ident = f"filepos{target}"
            if ident not in self.pending:
                self.pending.append(ident)

        # Anchors still waiting after the last piece of content go at the
        # end of the last page.
        if self.page_open:
            self._flush_pending()
            for entry in reversed(self.stack):
                if entry.out_name:
                    self._close_tag(entry.out_name)
        if self.has_content or not self.pages:
            self._finish_page()
        else:
            last = self.pages[-1]
            last.body += "".join(self.cur)
            last.ids |= self.cur_ids
            self.cur = []
            self.cur_ids = set()
        if self.pending and self.pages:
            self._flush_pending_into_last_page()

        result = MarkupResult(
            pages=self.pages,
            guide=self.guide,
            used_images=self.used_images,
            dropped_images=self.dropped_images,
        )
        self._finalize_classes(result)
        return result

    def _flush_pending_into_last_page(self) -> None:
        last = self.pages[-1]
        extra = []
        for ident in self.pending:
            if ident not in self.seen_ids:
                self.seen_ids.add(ident)
                last.ids.add(ident)
                extra.append(f'<a id="{ident}"></a>')
        last.body += "".join(extra)
        self.pending = []

    def _finalize_classes(self, result: MarkupResult) -> None:
        """Replaces the temporary class placeholders with final names,
        most-used first (m1 is the commonest)."""
        order = sorted(range(len(self.class_decls)), key=lambda i: (-self.class_counts[i], i))
        names = {idx: f"m{rank + 1}" for rank, idx in enumerate(order)}
        for idx in order:
            result.css_rules.append((names[idx], self.class_decls[idx]))
        pattern = re.compile("\x01C(\\d+)\x02")
        for page in result.pages:
            page.body = pattern.sub(lambda m: names[int(m.group(1))], page.body)


def find_link_targets(text: bytes) -> set:
    """Every byte offset any `filepos=` attribute in the markup points at."""
    return {int(m.group(1)) for m in _FILEPOS_RE.finditer(text)}


def convert_markup(text: bytes, encoding: str, extra_targets, images: dict, image_href) -> MarkupResult:
    """Converts the book's raw markup into pages.

    extra_targets: byte offsets besides the ones the markup's own links
    use that still need an anchor (table-of-contents entries, the start
    of the book).
    images: {recindex: MobiImage}, used only to check a reference is real.
    image_href: recindex -> the href the finished page should use, or None
    if that image can't be included.

    Links are left as placeholders; call resolve_links() once every
    page's file name is known."""
    targets = find_link_targets(text) | {int(t) for t in extra_targets if t is not None}
    converter = _Converter(text, encoding, targets, images, image_href)
    result = converter.run()
    for index, page in enumerate(result.pages):
        for ident in page.ids:
            result.id_page.setdefault(ident, index)
    return result


_LINK_PLACEHOLDER = re.compile("\x01([LI])([^\x02]*)\x02")


def resolve_links(result: MarkupResult, page_filename) -> int:
    """Rewrites every link placeholder to `<file>#<id>`. Returns how many
    links couldn't be resolved (their href is removed, leaving plain
    text)."""
    unresolved = 0

    def replace(match: re.Match) -> str:
        nonlocal unresolved
        kind, value = match.group(1), match.group(2)
        ident = f"filepos{value}" if kind == "L" else value
        page = result.id_page.get(ident)
        if page is None:
            unresolved += 1
            return "\x03"
        return f"{page_filename(page)}#{ident}"

    for page in result.pages:
        body = _LINK_PLACEHOLDER.sub(replace, page.body)
        # An unresolved link's whole href attribute goes away.
        body = re.sub(r'\s?href="\x03"', "", body)
        page.body = body
    return unresolved
