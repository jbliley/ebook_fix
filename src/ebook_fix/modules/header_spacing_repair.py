"""
ebook_fix.modules.header_spacing_repair

Makes sure there is a visible gap under every chapter header: at least
MIN_LINES blank lines between the header and the first paragraph of the
chapter, and no more than MAX_LINES when a book has more than that.

Found in "The Tomb" (F. Paul Wilson): each chapter number sat directly on
top of the first paragraph. The original file had a blank paragraph in
between, but it held only a newline, which a reader draws as nothing, so
the gap was never really there.

What counts as a chapter header here is deliberately narrow: an element
Chapter Markup has marked, or one the chapter analysis confirmed as a
chapter start. A random heading elsewhere in the book is left alone.

What counts as a line of gap: a blank paragraph holding a non-breaking
space or a line break (each line break is one line), or a bare <br/> right
under the header. A blank paragraph with nothing in it draws nothing, so
it counts for nothing.

A header that is only a plain paragraph (a "Prologue" or "Epilogue" that
the conversion left looking like body text) is restyled to match the
book's numbered chapter headers: same tag and class, with any bold and
line-break wrappers flattened to plain text. Needs at least one real
heading-tag chapter header in the book to copy from.

When it does nothing, on purpose:
- The header is an image, or has no text of its own (it may need the
  space as part of its design), or an image comes right after it.
- The stylesheet already gives real space there: the header's bottom
  margin plus the next paragraph's top margin come to about a line or
  more (see MARGIN_GAP_EM). Adding lines on top would double it.
- The header already has between MIN_LINES and MAX_LINES lines of gap.

It also keeps lines_before (1 by default) blank lines directly above each
chapter header, so the header never sits hard against the top of the page
or the text above it. Margins are not used for this, since many readers
ignore them; a real blank paragraph is added instead.

Existing spacing is never reduced below MIN_LINES, and only ever cut down
when it is above MAX_LINES. Re-running finds the gap already there and
does nothing.
"""
from __future__ import annotations

import re

from ebook_fix.config import HeaderSpacingConfig
from ebook_fix.css import COMMENT_RE, RULE_RE
from ebook_fix.headings import MARKER_ATTR, SPLIT_MARKER_ATTR, chapter_marker_elements, split_header_block
from ebook_fix.modules.paragraph_indent_repair import _book_rules
from ebook_fix.modules.paragraph_spacing_repair import _to_em
from ebook_fix.report import Report

DEFAULT_MIN_LINES = 2
DEFAULT_MAX_LINES = 5
DEFAULT_LINES_BEFORE = 1
# Margin (in em) between a header and the next paragraph that already
# counts as a visible gap, so no more is added.
MARGIN_GAP_EM = 1.5

_NBSP = " "
_STYLE_PROP = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;]+)")
_CLASS_ONLY = re.compile(r"^(?:[a-zA-Z][\w-]*)?\.([\w-]+)$")
XHTML_NS = "http://www.w3.org/1999/xhtml"


def _local(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _text(el) -> str:
    return "".join(el.itertext()).replace(_NBSP, "").strip()


def _has_image(el) -> bool:
    return bool(el.findall(".//{*}img") or el.findall(".//{*}image")
                or _local(el) in ("img", "svg", "image")
                or el.findall(".//{*}svg"))


# ---------------------------------------------------------------------
# Margins from the stylesheet
# ---------------------------------------------------------------------

def _class_margins(rules) -> dict:
    """{class_name: (margin_top_em, margin_bottom_em)} for plain
    `.name` / `tag.name` selectors. Later rules win."""
    found: dict = {}
    for selector, props in rules:
        m = _CLASS_ONLY.match(selector.strip())
        if not m:
            continue
        top, bottom = found.get(m.group(1), (0.0, 0.0))
        shorthand = props.get("margin")
        if shorthand:
            parts = shorthand[0].split()
            if len(parts) == 1:
                t = b = _to_em(parts[0])
            elif len(parts) in (2, 3):
                t = _to_em(parts[0])
                b = _to_em(parts[2] if len(parts) == 3 else parts[0])
            elif len(parts) == 4:
                t, b = _to_em(parts[0]), _to_em(parts[2])
            else:
                t = b = None
            top = t if t is not None else top
            bottom = b if b is not None else bottom
        if "margin-top" in props:
            v = _to_em(props["margin-top"][0])
            top = v if v is not None else top
        if "margin-bottom" in props:
            v = _to_em(props["margin-bottom"][0])
            bottom = v if v is not None else bottom
        found[m.group(1)] = (top, bottom)
    return found


def _element_margins(el, class_margins) -> tuple:
    top = bottom = 0.0
    for cls in (el.get("class") or "").split():
        t, b = class_margins.get(cls, (0.0, 0.0))
        top, bottom = max(top, t), max(bottom, b)
    inline = {k.lower(): v.strip() for k, v in _STYLE_PROP.findall(el.get("style") or "")}
    if "margin-top" in inline:
        v = _to_em(inline["margin-top"])
        top = v if v is not None else top
    if "margin-bottom" in inline:
        v = _to_em(inline["margin-bottom"])
        bottom = v if v is not None else bottom
    return top, bottom


# ---------------------------------------------------------------------
# Reading the space under a header
# ---------------------------------------------------------------------

def _gap_lines(el) -> int | None:
    """Lines of gap a blank paragraph/div shows, 0 for one that draws
    nothing, None if it is not blank at all."""
    if _local(el) not in ("p", "div"):
        return None
    if _text(el) or _has_image(el):
        return None
    brs = len(el.findall(".//{*}br"))
    if brs:
        return brs
    if _NBSP in "".join(el.itertext()):
        return 1
    return 0


def _next_element(node):
    node = node.getnext()
    while node is not None and not isinstance(node.tag, str):
        node = node.getnext()
    return node


def _block_after_header(header):
    """The element whose following siblings hold the space under the
    header: the header itself, or an ancestor when the header is the last
    thing in a wrapper."""
    node = header
    while _next_element(node) is None:
        parent = node.getparent()
        if parent is None or _local(parent) in ("body", "html"):
            break
        if parent.get(MARKER_ATTR) is None and "chapter-wrapper" in (parent.get("class") or ""):
            break
        node = parent
    return node


def read_header_spacing(header):
    """(gap_elements, lines, first_content) for the space directly under
    `header`. gap_elements are the visible gap items in order, each as
    (element, lines); first_content is the first element after them
    (None if the chapter ends)."""
    block = _block_after_header(header)
    gaps = []
    lines = 0
    node = _next_element(block)
    while node is not None:
        if _local(node) == "br":
            gaps.append((node, 1))
            lines += 1
        else:
            n = _gap_lines(node)
            if n is None:
                break
            if n:
                gaps.append((node, n))
                lines += n
        node = _next_element(node)
    return block, gaps, lines, node


def lines_before_header(header) -> int:
    """Blank lines directly above the header, among its own siblings."""
    lines = 0
    node = header.getprevious()
    while node is not None:
        if not isinstance(node.tag, str):
            node = node.getprevious()
            continue
        if _local(node) == "br":
            lines += 1
        else:
            n = _gap_lines(node)
            if n is None:
                break
            lines += n
        node = node.getprevious()
    return lines


def _gap_paragraph(like):
    from lxml import etree

    ns = like.tag.split("}")[0] + "}" if isinstance(like.tag, str) and like.tag.startswith("{") else ""
    p = etree.Element(f"{ns}p")
    p.set("class", "ebookfix-header-gap")
    p.set("style", "margin: 0; text-indent: 0;")
    p.text = _NBSP
    return p


# ---------------------------------------------------------------------
# Finding headers and deciding
# ---------------------------------------------------------------------

def _headers(book, analysis):
    markers = set()
    summary = getattr(analysis, "chapters", None) if analysis is not None else None
    if summary is not None:
        markers = chapter_marker_elements(summary)
    found = []
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None:
            continue
        body = doc.find(".//{*}body")
        if body is None:
            continue
        for el in body.iter():
            if not isinstance(el.tag, str):
                continue
            if "chapter-wrapper" in (el.get("class") or ""):
                continue
            if el.get(MARKER_ATTR) or el.get(SPLIT_MARKER_ATTR) or el in markers:
                found.append((chapter, el))
    # A header that holds another header (a wrapper) is not itself one.
    # A marker is often an inline <span> or <strong> inside the real header
    # paragraph; the gap goes around the paragraph, not inside it.
    found = [(chapter, split_header_block(el)) for chapter, el in found]
    found_ids = {id(x) for _c, x in found}
    seen = set()
    for chapter, el in found:
        if id(el) in seen:
            continue
        if not any(id(d) in found_ids for d in el.iterdescendants()):
            seen.add(id(el))
            yield chapter, el


_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
MAX_RESTYLE_LENGTH = 40


def restyle_plain_headers(book, analysis):
    """Give paragraph-style chapter headers the tag and class of the book's
    heading-style ones. Returns [(chapter, text)] for each one changed."""
    from collections import Counter

    headers = list(_headers(book, analysis))
    styles = Counter()
    for _chapter, el in headers:
        if _local(el) in _HEADING_TAGS and _text(el):
            styles[(el.tag, el.get("class") or "")] += 1
    if not styles:
        return []
    (tag, cls), _n = styles.most_common(1)[0]
    changed = []
    for chapter, el in headers:
        if _local(el) not in ("p", "div"):
            continue
        text = " ".join(_text(el).split())
        if not text or len(text) > MAX_RESTYLE_LENGTH or _has_image(el):
            continue
        if any(_local(d) in ("p", "div") or _local(d) in _HEADING_TAGS for d in el.iterdescendants()
               if isinstance(d.tag, str)):
            continue
        for child in list(el):
            el.remove(child)
        el.text = text
        el.tag = tag
        if cls:
            el.set("class", cls)
        elif "class" in el.attrib:
            del el.attrib["class"]
        changed.append((chapter, text))
    return changed


def _plain_header_names(book, analysis):
    """What restyle_plain_headers would change, without changing anything."""
    headers = list(_headers(book, analysis))
    has_ref = any(_local(el) in _HEADING_TAGS and _text(el) for _c, el in headers)
    if not has_ref:
        return []
    out = []
    for chapter, el in headers:
        if _local(el) in ("p", "div") and not _has_image(el):
            text = " ".join(_text(el).split())
            if text and len(text) <= MAX_RESTYLE_LENGTH and not any(
                    isinstance(d.tag, str) and (_local(d) in ("p", "div") or _local(d) in _HEADING_TAGS)
                    for d in el.iterdescendants()):
                out.append((chapter, text))
    return out


def plan_header_spacing(book, analysis, min_lines, max_lines, lines_before=0):
    """[(chapter, header, action, detail)] where action is "add" or
    "trim"; headers that need nothing are left out."""
    rules, _sources = _book_rules(book)
    class_margins = _class_margins(rules)
    plan = []
    seen = set()
    for chapter, header in _headers(book, analysis):
        if id(header) in seen:
            continue
        seen.add(id(header))
        if _has_image(header) or not _text(header):
            continue
        if lines_before > 0 and lines_before_header(header) < lines_before:
            plan.append((chapter, header, "before", lines_before - lines_before_header(header)))
        block, gaps, lines, first = read_header_spacing(header)
        if first is None:
            continue
        if _has_image(first) or _local(first) in ("img", "svg"):
            continue
        if lines > max_lines:
            plan.append((chapter, header, "trim", (block, gaps, lines)))
            continue
        if lines >= min_lines:
            continue
        _, header_bottom = _element_margins(block, class_margins)
        first_top, _ = _element_margins(first, class_margins)
        if lines == 0 and header_bottom + first_top >= MARGIN_GAP_EM:
            continue
        plan.append((chapter, header, "add", (block, gaps, lines)))
    return plan


class HeaderSpacingRepair:
    name = "Chapter Header Spacing"

    def __init__(self, config: HeaderSpacingConfig | None = None):
        self.config = config or HeaderSpacingConfig()

    def _limits(self):
        lo = int(getattr(self.config, "min_lines", DEFAULT_MIN_LINES) or 0)
        hi = int(getattr(self.config, "max_lines", DEFAULT_MAX_LINES) or 0)
        lo = max(lo, 0)
        hi = max(hi, lo, 1)
        return lo, hi

    def _before(self) -> int:
        return max(int(getattr(self.config, "lines_before", DEFAULT_LINES_BEFORE) or 0), 0)

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        lo, hi = self._limits()
        plan = plan_header_spacing(book, analysis, lo, hi, self._before())
        plain = _plain_header_names(book, analysis) if getattr(self.config, "match_header_style", True) else []
        if plain:
            report.add(
                plain[0][0].href,
                "Chapter header styled differently",
                "These chapter headers look like body text and will be restyled to match the other "
                "chapter headers: " + ", ".join(f'"{x}"' for _c, x in plain) + ".",
            )
        before = [p for p in plan if p[2] == "before"]
        if before:
            report.add(
                before[0][0].href,
                "Chapter header has no blank line above",
                f"{len(before)} chapter header(s) have fewer than {self._before()} blank line(s) above "
                "them. A blank line will be added.",
            )
        add = [p for p in plan if p[2] == "add"]
        trim = [p for p in plan if p[2] == "trim"]
        if add:
            report.add(
                add[0][0].href,
                "Chapter header has no visible gap",
                f"{len(add)} chapter header(s) have fewer than {lo} blank lines before the first "
                f"paragraph. Blank lines will be added so there are {lo}.",
            )
        if trim:
            report.add(
                trim[0][0].href,
                "Chapter header has too much space",
                f"{len(trim)} chapter header(s) have more than {hi} blank lines before the first "
                f"paragraph. They will be cut down to {hi}.",
            )
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        lo, hi = self._limits()
        added = trimmed = before = 0
        restyled = restyle_plain_headers(book, analysis) if getattr(self.config, "match_header_style", True) else []
        for chapter, _text_ in restyled:
            chapter.modified = True
        if restyled:
            report.add("chapters", "Chapter header restyled",
                       "Restyled to match the other chapter headers: "
                       + ", ".join(f'"{x}"' for _c, x in restyled) + ".")
        for chapter, header, action, info in plan_header_spacing(book, analysis, lo, hi, self._before()):
            if action == "before":
                for _ in range(info):
                    header.addprevious(_gap_paragraph(header))
                before += 1
                chapter.modified = True
                continue
            block, gaps, lines = info
            if action == "add":
                anchor = gaps[-1][0] if gaps else block
                for _ in range(lo - lines):
                    new = _gap_paragraph(block)
                    anchor.addnext(new)
                    anchor = new
                added += 1
            else:
                excess = lines - hi
                for el, n in reversed(gaps):
                    if excess <= 0:
                        break
                    if n <= excess:
                        el.getparent().remove(el)
                        excess -= n
                    else:
                        for br in reversed(el.findall(".//{*}br")):
                            if excess <= 0:
                                break
                            br.getparent().remove(br)
                            excess -= 1
                trimmed += 1
            chapter.modified = True
        if before:
            report.add("chapters", "Chapter header gap added above",
                       f"Added a blank line above {before} chapter header(s).")
        if added:
            report.add("chapters", "Chapter header gap added",
                       f"Added blank lines under {added} chapter header(s) so each has {lo}.")
        if trimmed:
            report.add("chapters", "Chapter header gap reduced",
                       f"Cut the space under {trimmed} chapter header(s) down to {hi} lines.")
        if (added or trimmed or before or restyled) and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report
