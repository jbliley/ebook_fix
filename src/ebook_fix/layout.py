"""
ebook_fix.layout

Descriptive analysis of whether a book (or individual pages within it)
is fixed-layout ("pre-paginated" in EPUB3 terms) rather than ordinary
reflowable prose -- comics, picture books, and "print replica" titles,
where content sits at an exact pixel position over a background image
rather than flowing to fit the reading system's own font/size/theme.

Raised by Jacob (2026-09-19): several repair modules in this project
assume reflowable prose and would very likely damage a fixed-layout
page if run against it unattended -- Color Strip and Font Strip would
treat a comic's deliberate, exact color/font as a conversion-tool
artifact to remove; Chapter Markup, TOC Generation, Running Title
Repair, and Scene Break Repair all reason about "chapters" and
"paragraphs" that a one-page-per-image book simply doesn't have; and
Paragraph Repair's cleanup of stray/orphaned spans is exactly how
you'd break a page where every span is a deliberately, absolutely
positioned text box. This module exists purely to detect that
situation so ebook_fix.engine can skip those modules automatically --
see the risky-module list and _apply_fixed_layout_guard() there.

Like ebook_fix.color and ebook_fix.fonts, this module only records
findings -- it doesn't decide what to skip. It records two tiers,
for the same reason those two modules split into confident/review:

Confirmed (safe to auto-gate)
------------------------------
The book -- or an individual page, via the EPUB3 per-spine-item
override -- declares itself pre-paginated via the standard
`<meta property="rendition:layout">pre-paginated</meta>` in the OPF
metadata, or an `<itemref properties="rendition:layout-pre-paginated">`
on that page's own spine entry (which can flip an otherwise-reflowable
book's single special page fixed-layout, or vice versa flip one page
of an otherwise fixed-layout book back to reflowable -- both read
here). This is the book unambiguously declaring what it is; nothing
here is a guess.

Deliberately lenient about the spec's `prefix` declaration some
producers skip: this matches on the literal `rendition:layout`/
`rendition:layout-pre-paginated` strings regardless of whether the
`<package>` element properly declares that prefix, the same trade-off
ebook_fix.css's regex-based scanning already documents (real-world
files are often sloppier than the spec, and there's no plausible
false-positive from a producer accidentally using this exact string
for something else).

Possible (heuristic, flagged for manual review -- never auto-gated)
----------------------------------------------------------------------
No rendition metadata either way, but the page's own content looks
fixed-layout anyway: a `<meta name="viewport" content="width=...,
height=...">` (the de-facto signal most fixed-layout producers who
skip the proper EPUB3 metadata still include, since reading systems
rely on it for the page's pixel dimensions) together with at least
one absolutely/fixed-positioned element, or three or more positioned
elements even without a viewport tag. Two conditions, not one, are
required in the weaker (no-viewport) case specifically to avoid a
false positive on an image-heavy but genuinely reflowable book -- a
photo-illustrated cookbook with `<img>` tags sitting in normal
flowing paragraphs (verified against a real one: see
docs/analysis_roadmap.md's dated entry) has plenty of images and zero
positioned elements, so it correctly never trips this.

Positioned-element detection covers both an inline
`style="position:absolute"`/`fixed` and a class whose rule (in any of
the book's stylesheets, or a chapter's own embedded <style> block)
sets `position: absolute`/`fixed` -- the same two sources
ebook_fix.fonts/color already scan for their own confident/review
splits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE, CLASS_SELECTOR_RE

OPF_NS = "http://www.idpf.org/2007/opf"

POSITION_RULE_RE = re.compile(r'position\s*:\s*(absolute|fixed)', re.IGNORECASE)
VIEWPORT_WIDTH_RE = re.compile(r'width\s*=\s*(\d+)', re.IGNORECASE)
VIEWPORT_HEIGHT_RE = re.compile(r'height\s*=\s*(\d+)', re.IGNORECASE)

# A page needs at least this many positioned elements to look
# fixed-layout on positioning alone, with no viewport meta backing it
# up -- one stray `position: absolute` (a pull-quote, a floated
# sidebar) is common in ordinary reflowable books and isn't enough by
# itself. With a viewport meta present too, a single positioned
# element is enough -- see module docstring.
MIN_POSITIONED_WITHOUT_VIEWPORT = 3


@dataclass
class ChapterLayoutFinding:
    href: str = ""
    # "pre-paginated" / "reflowable" / "" (no per-page override in
    # the spine -- this page just inherits whatever the book-level
    # metadata says, if anything).
    declared_layout: str = ""
    has_viewport_meta: bool = False
    viewport_width: int | None = None
    viewport_height: int | None = None
    positioned_element_count: int = 0
    word_count: int = 0
    image_count: int = 0
    # Heuristic verdict for THIS page alone, independent of
    # declared_layout/confirmation -- see module docstring.
    looks_fixed_layout: bool = False


@dataclass
class BookLayoutSummary:
    # "pre-paginated" / "reflowable" / "" (book doesn't declare this
    # metadata at all).
    book_declared_layout: str = ""
    confirmed_fixed_layout: bool = False
    confirmed_chapters: list = field(default_factory=list)   # [href, ...]
    # Heuristic-only findings -- pages that look fixed-layout but
    # aren't already confirmed one way or the other. Never includes
    # anything already in confirmed_chapters.
    possible_chapters: list = field(default_factory=list)     # [ChapterLayoutFinding, ...]
    # Every page's finding, confirmed or not -- for anything downstream
    # that wants the full picture rather than just the two buckets above.
    chapter_findings: list = field(default_factory=list)      # [ChapterLayoutFinding, ...]


def _positioned_classes_in_css(css_text: str) -> set:
    """Class names (no leading '.') declared anywhere with an
    absolute/fixed `position`, across every selector in a comma-
    separated group -- same "a rule naming several selectors" handling
    ebook_fix.color/fonts use for their own selector-group scanning."""
    classes = set()
    if not css_text:
        return classes
    text = COMMENT_RE.sub("", css_text)
    for m in RULE_RE.finditer(text):
        selector, body = m.group(1), m.group(2)
        if not POSITION_RULE_RE.search(body):
            continue
        classes.update(CLASS_SELECTOR_RE.findall(selector))
    return classes


def _book_declared_layout(opf) -> str:
    if opf is None:
        return ""
    for el in opf.iter():
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el).localname.lower() != "meta":
            continue
        if (el.get("property") or "").strip().lower() == "rendition:layout":
            return (el.text or "").strip().lower()
    return ""


def _spine_item_overrides(opf) -> dict:
    """{idref: "pre-paginated"/"reflowable"} for every spine <itemref>
    carrying an explicit per-page rendition:layout override."""
    overrides = {}
    if opf is None:
        return overrides
    for el in opf.iter():
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el).localname.lower() != "itemref":
            continue
        idref = el.get("idref")
        if not idref:
            continue
        props = (el.get("properties") or "").lower()
        if "rendition:layout-pre-paginated" in props:
            overrides[idref] = "pre-paginated"
        elif "rendition:layout-reflowable" in props:
            overrides[idref] = "reflowable"
    return overrides


def analyze_book_layout(book) -> BookLayoutSummary:
    r = BookLayoutSummary()
    opf = getattr(book, "opf_document", None)

    r.book_declared_layout = _book_declared_layout(opf)
    book_declared_pre = r.book_declared_layout == "pre-paginated"
    item_overrides = _spine_item_overrides(opf)

    # Positioned classes from every external stylesheet, plus every
    # chapter's own embedded <style> block (a fixed-layout page often
    # ships its positioning inline in the page itself rather than in a
    # shared stylesheet).
    positioned_classes = set()
    for _href, text in read_book_css(book).items():
        positioned_classes |= _positioned_classes_in_css(text)
    for ch in getattr(book, "chapters", []) or []:
        tree = getattr(ch, "document", None)
        if tree is None:
            continue
        for el in tree.iter():
            if not isinstance(el.tag, str):
                continue
            if etree.QName(el).localname.lower() == "style":
                positioned_classes |= _positioned_classes_in_css(el.text or "")

    for ch in getattr(book, "chapters", []) or []:
        href = getattr(ch, "href", "")
        finding = ChapterLayoutFinding(href=href)
        finding.declared_layout = item_overrides.get(getattr(ch, "id", ""), "")

        tree = getattr(ch, "document", None)
        if tree is not None:
            for el in tree.iter():
                if not isinstance(el.tag, str):
                    continue
                tag = etree.QName(el).localname.lower()
                if tag == "meta" and (el.get("name") or "").lower() == "viewport":
                    finding.has_viewport_meta = True
                    content = el.get("content") or ""
                    wm = VIEWPORT_WIDTH_RE.search(content)
                    hm = VIEWPORT_HEIGHT_RE.search(content)
                    if wm:
                        finding.viewport_width = int(wm.group(1))
                    if hm:
                        finding.viewport_height = int(hm.group(1))
                elif tag in ("img", "image"):
                    finding.image_count += 1

                style_attr = el.get("style") or ""
                if POSITION_RULE_RE.search(style_attr):
                    finding.positioned_element_count += 1
                else:
                    cls = el.get("class")
                    if cls and positioned_classes.intersection(cls.split()):
                        finding.positioned_element_count += 1

            finding.word_count = len("".join(tree.itertext()).split())

        finding.looks_fixed_layout = (
            (finding.has_viewport_meta and finding.positioned_element_count >= 1)
            or finding.positioned_element_count >= MIN_POSITIONED_WITHOUT_VIEWPORT
        )

        is_confirmed = (
            finding.declared_layout == "pre-paginated"
            or (book_declared_pre and finding.declared_layout != "reflowable")
        )
        if is_confirmed:
            r.confirmed_chapters.append(href)
        elif finding.looks_fixed_layout:
            r.possible_chapters.append(finding)

        r.chapter_findings.append(finding)

    r.confirmed_fixed_layout = bool(r.confirmed_chapters)
    return r
