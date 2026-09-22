"""
ebook_fix.fb2.markup

Turns FB2's own well-formed XML body content into XHTML pages. Unlike
MOBI, there's no sloppy markup to repair here -- FB2 is a real XML
format, so this is a straightforward recursive walk of an already
well-formed tree, dispatching by tag name.

Page splitting follows the book's own structure, not anything invented:
one XHTML file per section that is a *direct child* of the main
`<body>`. Nested subsections stay inline in that same file as headings
(`<h2>`, `<h3>`, ... one level per nesting depth, capped at `<h6>`),
the same "convert faithfully, let the repair pipeline split further if
it wants to" approach the MOBI converter takes. If the body itself
carries its own `<title>`/`<epigraph>` (a book-level title/dedication
page before the first named part -- seen in FB2-Example.fb2), that
becomes its own leading page.

A second `<body>` and beyond are treated as footnotes/endnotes, not
main content (FB2 convention, confirmed against FB2-ForeignLanguage.fb2
"notes" body): each becomes one page holding every one of its sections
wrapped in an EPUB3 `<aside epub:type="footnote">`, and every
`<a type="note" href="#id">` in the main content is rewritten to point
there.

Because the whole document is parsed upfront (no streaming/pagination
constraint the way MOBI's byte-offset text has), every link's target
page is known before any HTML is written -- no placeholder-and-resolve
step is needed here the way mobi/markup.py needs one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as _xml_escape

from ebook_fix.fb2.reader import XLINK_NS, _href, _q, _text

_MAX_HEADING = 6


def _e(text: str) -> str:
    return _xml_escape(text)


def _ea(text: str) -> str:
    return _xml_escape(text, {'"': "&quot;"})


def _sanitize_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.:-]", "_", (value or "").strip())
    if not value:
        return ""
    if not re.match(r"[A-Za-z_]", value[0]):
        value = "id_" + value
    return value


@dataclass
class TocEntry:
    label: str = ""
    href: str = ""
    level: int = 0


@dataclass
class Page:
    title: str = ""
    body: str = ""


@dataclass
class MarkupResult:
    pages: list = field(default_factory=list)          # list[Page]
    toc: list = field(default_factory=list)             # list[TocEntry]
    used_images: set = field(default_factory=set)       # binary ids referenced
    unresolved_links: int = 0
    dropped_images: int = 0


def collect_ids(indexed_sections) -> dict:
    """Every `id` attribute anywhere under each given section, mapped to
    that section's output page index. `indexed_sections` is
    [(page_index, section_element), ...] for the *top-level* sections
    of one body. Called once before any rendering, since FB2's
    structure -- unlike MOBI's byte-stream text -- is fully known
    upfront, so there's no need for MOBI markup.py's placeholder-and-
    resolve-afterward approach."""
    ids: dict[str, int] = {}
    for page_index, section in indexed_sections:
        for el in section.iter():
            raw_id = el.get("id")
            if raw_id:
                ids.setdefault(_sanitize_id(raw_id), page_index)
    return ids


def assign_missing_section_ids(section_el, counter: list) -> None:
    """Guarantees every `<section>` that has a `<title>` (and so will
    get a table-of-contents entry) has an `id` for that entry to link
    to, generating one (`secN`) if the source didn't supply one.
    `counter` is a one-element list used as a mutable int so nested
    calls share one running count. Recurses into every nested
    subsection regardless of depth."""
    if section_el.find(f"{{{section_el.tag.split('}')[0][1:]}}}title") is not None and not section_el.get("id"):
        counter[0] += 1
        section_el.set("id", f"sec{counter[0]}")
    for child in section_el:
        if isinstance(child.tag, str) and child.tag.endswith("}section"):
            assign_missing_section_ids(child, counter)


def collect_toc(section_el, page_index: int, depth: int, page_filename) -> list:
    """The table-of-contents entries for one top-level section and all
    of its nested subsections -- they share the same output page/file
    (nested subsections render inline, not as separate pages), so only
    the anchor differs between entries at different depths."""
    entries: list[TocEntry] = []
    title_el = section_el.find(f"{{{section_el.tag.split('}')[0][1:]}}}title")
    label = " ".join(_text(p) for p in title_el if p.tag.endswith("}p")).strip() or _text(title_el) if title_el is not None else ""
    if label:
        anchor = _sanitize_id(section_el.get("id") or "")
        href = page_filename(page_index) + (f"#{anchor}" if anchor else "")
        entries.append(TocEntry(label=label, href=href, level=depth))
    for child in section_el:
        if isinstance(child.tag, str) and child.tag.endswith("}section"):
            entries.extend(collect_toc(child, page_index, depth + 1, page_filename))
    return entries


class Fb2Renderer:
    def __init__(self, ns: str, image_href, note_hrefs: dict, own_ids: dict, page_filename):
        self.ns = ns
        self.image_href = image_href      # binary id -> href string, or None
        self.note_hrefs = note_hrefs      # sanitized id -> "file#id" (auxiliary bodies)
        self.own_ids = own_ids            # sanitized id -> page index (this body)
        self.page_filename = page_filename
        self.used_images: set = set()
        self.unresolved_links = 0
        self.dropped_images = 0

    def _q(self, tag: str) -> str:
        return _q(self.ns, tag)

    def _local(self, el) -> str:
        return el.tag.split("}", 1)[-1] if isinstance(el.tag, str) else ""

    # -- inline content ---------------------------------------------------

    def _inline_children(self, el) -> str:
        parts = [_e(el.text)] if el.text else []
        for child in el:
            parts.append(self._inline_one(child))
            if child.tail:
                parts.append(_e(child.tail))
        return "".join(parts)

    def _resolve_href(self, el, is_note: bool) -> str | None:
        raw = _href(el, self.ns)
        if not raw:
            return None
        if el.get(f"{{{XLINK_NS}}}href", el.get("href", "")).startswith("#"):
            ident = _sanitize_id(raw)
            if ident in self.note_hrefs:
                return self.note_hrefs[ident]
            if ident in self.own_ids:
                return f"{self.page_filename(self.own_ids[ident])}#{ident}"
            self.unresolved_links += 1
            return None
        return _ea(raw)  # an ordinary external URL

    def _inline_one(self, el) -> str:
        tag = self._local(el)
        if tag == "emphasis":
            return f"<em>{self._inline_children(el)}</em>"
        if tag == "strong":
            return f"<strong>{self._inline_children(el)}</strong>"
        if tag == "strikethrough":
            return f"<s>{self._inline_children(el)}</s>"
        if tag == "sub":
            return f"<sub>{self._inline_children(el)}</sub>"
        if tag == "sup":
            return f"<sup>{self._inline_children(el)}</sup>"
        if tag == "code":
            return f"<code>{self._inline_children(el)}</code>"
        if tag == "style":
            name = _sanitize_id(el.get("name", ""))
            cls = f' class="fb2-{name}"' if name else ""
            return f"<span{cls}>{self._inline_children(el)}</span>"
        if tag == "a":
            is_note = (el.get("type") or "").strip().lower() == "note"
            href = self._resolve_href(el, is_note)
            body = self._inline_children(el)
            if href is None:
                return body
            epub_type = ' epub:type="noteref"' if is_note else ""
            return f'<a{epub_type} href="{href}">{body}</a>'
        if tag == "image":
            return self._image(el)
        # Unrecognized inline tag: keep its content, drop the wrapper --
        # same defensive posture as mobi/markup.py for unknown tags.
        return self._inline_children(el)

    def _image(self, el) -> str:
        bid = _href(el, self.ns)
        href = self.image_href(bid) if bid else None
        if not href:
            self.dropped_images += 1
            return ""
        self.used_images.add(bid)
        alt = _ea(el.get("alt", ""))
        return f'<img src="{href}" alt="{alt}"/>'

    # -- block content ------------------------------------------------

    def _own_id(self, el) -> str:
        raw = el.get("id")
        return f' id="{_ea(_sanitize_id(raw))}"' if raw else ""

    def _title_html(self, title_el, tag: str, id_source=None) -> str:
        """id_source lets a heading carry its *section's* id (so a TOC
        entry has something to link to) rather than the <title>
        element's own id, which FB2 books essentially never set."""
        lines = [self._inline_children(p) for p in title_el if self._local(p) == "p"]
        if not lines and _text(title_el):
            lines = [_e(_text(title_el))]
        own_id = self._own_id(id_source if id_source is not None else title_el)
        return f"<{tag}{own_id}>" + "<br/>".join(lines) + f"</{tag}>"

    def _title_text(self, title_el) -> str:
        return " ".join(_text(p) for p in title_el if self._local(p) == "p").strip() or _text(title_el)

    def _epigraph(self, el) -> str:
        parts = [f'<blockquote class="epigraph"{self._own_id(el)}>']
        for child in el:
            parts.append(self._block_one(child, depth=0, in_epigraph=True))
        parts.append("</blockquote>")
        return "".join(parts)

    def _cite(self, el) -> str:
        parts = [f'<blockquote class="cite"{self._own_id(el)}>']
        for child in el:
            parts.append(self._block_one(child, depth=0, in_epigraph=True))
        parts.append("</blockquote>")
        return "".join(parts)

    def _poem(self, el) -> str:
        parts = [f'<div class="poem"{self._own_id(el)}>']
        for child in el:
            tag = self._local(child)
            if tag == "title":
                parts.append(self._title_html(child, "p"))
            elif tag == "epigraph":
                parts.append(self._epigraph(child))
            elif tag == "stanza":
                parts.append(self._stanza(child))
            elif tag in ("date", "text-author"):
                cls = "date" if tag == "date" else "text-author"
                parts.append(f'<p class="{cls}">{self._inline_children(child)}</p>')
        parts.append("</div>")
        return "".join(parts)

    def _stanza(self, el) -> str:
        parts = [f'<div class="stanza"{self._own_id(el)}>']
        for child in el:
            tag = self._local(child)
            if tag == "title":
                parts.append(self._title_html(child, "p"))
            elif tag == "subtitle":
                parts.append(f'<p class="subtitle">{self._inline_children(child)}</p>')
            elif tag == "v":
                parts.append(f'<p class="v">{self._inline_children(child)}</p>')
        parts.append("</div>")
        return "".join(parts)

    def _table(self, el) -> str:
        parts = [f"<table{self._own_id(el)}>"]
        for tr in el:
            if self._local(tr) != "tr":
                continue
            parts.append("<tr>")
            for cell in tr:
                cell_tag = self._local(cell)
                if cell_tag not in ("th", "td"):
                    continue
                align = (cell.get("align") or "").strip().lower()
                style = f' style="text-align: {align}"' if align in ("left", "right", "center", "justify") else ""
                colspan = cell.get("colspan", "")
                span = f' colspan="{_ea(colspan)}"' if colspan.isdigit() else ""
                parts.append(f"<{cell_tag}{style}{span}>{self._inline_children(cell)}</{cell_tag}>")
            parts.append("</tr>")
        parts.append("</table>")
        return "".join(parts)

    def _block_one(self, el, depth: int, in_epigraph: bool = False) -> str:
        tag = self._local(el)
        if tag == "p":
            return f"<p{self._own_id(el)}>{self._inline_children(el)}</p>"
        if tag == "subtitle":
            return f'<p class="subtitle"{self._own_id(el)}>{self._inline_children(el)}</p>'
        if tag == "text-author":
            return f'<p class="text-author"{self._own_id(el)}>{self._inline_children(el)}</p>'
        if tag == "empty-line":
            # FB2's own "blank line" marker, most often used exactly the
            # way ebook_fix.scene_breaks describes a real scene break:
            # a deliberate pause or fragment boundary marked with a
            # divider. Rendered as a real <hr/> rather than an empty
            # paragraph so the project's own scene-break classification
            # and normalization (real break vs. chapter-edge artifact,
            # standardized to "* * *") picks it up automatically,
            # instead of this converter inventing a second, competing
            # way to represent the same idea. An empty paragraph was
            # tried first and rejected: Paragraph Repair's own
            # emptiness check strips whitespace the same way Python's
            # str.strip() does, which treats a non-breaking space as
            # strippable too, so it was silently deleting these.
            return "<hr/>"
        if tag == "poem":
            return self._poem(el)
        if tag == "cite":
            return self._cite(el)
        if tag == "table":
            return self._table(el)
        if tag == "image":
            return f'<p class="image">{self._image(el)}</p>'
        if tag == "epigraph" and not in_epigraph:
            return self._epigraph(el)
        if tag == "annotation":
            parts = [f'<div class="annotation"{self._own_id(el)}>']
            for child in el:
                parts.append(self._block_one(child, depth, in_epigraph))
            parts.append("</div>")
            return "".join(parts)
        if tag == "section":
            return self._section_body(el, depth + 1)
        # Unrecognized block tag: render its content as a paragraph
        # rather than losing it -- same defensive posture as elsewhere.
        inline = self._inline_children(el)
        return f"<p>{inline}</p>" if inline.strip() else ""

    def _section_body(self, section_el, depth: int) -> str:
        """Everything inside one `<section>`: its own title/epigraphs/
        image, then either its direct content or its nested
        subsections (FB2 sections are one or the other, not both --
        but every child is still walked in document order regardless,
        so nothing is lost even if a file bends that rule)."""
        parts = []
        heading_tag = f"h{min(depth + 1, _MAX_HEADING)}"
        for child in section_el:
            tag = self._local(child)
            if tag == "title":
                parts.append(self._title_html(child, heading_tag, id_source=section_el))
            else:
                parts.append(self._block_one(child, depth))
        return "".join(parts)

    # -- pages ----------------------------------------------------------

    def render_section_page(self, section_el) -> tuple[str, str]:
        title_el = section_el.find(self._q("title"))
        title_text = self._title_text(title_el) if title_el is not None else ""
        return title_text, self._section_body(section_el, depth=0)

    def render_body_frontmatter(self, body_el) -> tuple[str, str] | None:
        title_el = body_el.find(self._q("title"))
        epigraphs = body_el.findall(self._q("epigraph"))
        image_el = body_el.find(self._q("image"))
        if title_el is None and not epigraphs and image_el is None:
            return None
        parts = []
        if image_el is not None:
            parts.append(f'<p class="image">{self._image(image_el)}</p>')
        title_text = ""
        if title_el is not None:
            title_text = self._title_text(title_el)
            parts.append(self._title_html(title_el, "h1"))
        for epigraph in epigraphs:
            parts.append(self._epigraph(epigraph))
        return title_text, "".join(parts)

    def render_notes_body(self, body_el, fallback_label: str) -> tuple[str, str]:
        title_el = body_el.find(self._q("title"))
        title_text = self._title_text(title_el) if title_el is not None else (body_el.get("name") or fallback_label)
        parts = [self._title_html(title_el, "h1") if title_el is not None else f"<h1>{_e(title_text)}</h1>"]
        for section in body_el.findall(self._q("section")):
            raw_id = section.get("id")
            ident = f' id="{_ea(_sanitize_id(raw_id))}"' if raw_id else ""
            note_title_el = section.find(self._q("title"))
            lead = ""
            if note_title_el is not None:
                label = self._title_text(note_title_el)
                if label:
                    lead = f"<strong>{_e(label)}.</strong> "
            body_parts = []
            for child in section:
                if child is note_title_el:
                    continue
                body_parts.append(self._block_one(child, depth=0))
            if lead:
                # The footnote's own number/label is folded into the
                # start of its first paragraph rather than given a
                # paragraph of its own -- a lone block whose entire text
                # is just a number ("1", "2", "3", ...) in a strictly
                # rising sequence is exactly what Chapter Markup's
                # detector looks for, and a footnotes page full of them
                # was getting misread as 37 book chapters. Inline text
                # ("1. <the footnote>") doesn't whole-string-match a
                # bare-number marker, so it's never mistaken for one.
                if body_parts and body_parts[0].startswith("<p"):
                    close = body_parts[0].index(">") + 1
                    body_parts[0] = body_parts[0][:close] + lead + body_parts[0][close:]
                else:
                    body_parts.insert(0, f'<p class="footnote-title">{lead.rstrip()}</p>')
            parts.append(f'<aside epub:type="footnote"{ident}>{"".join(body_parts)}</aside>')
        return title_text, "".join(parts)
