"""
ebook_fix.color

Descriptive analysis of hardcoded text `color` declarations across a
book -- external CSS files, embedded <style> blocks, inline style=""
attributes, and legacy <font color="..."> tags -- and a confidence
call on each one: is this something the reader's own theme/night-mode
should be allowed to control (confident), or does it look like a
deliberate author/design choice this project shouldn't touch without
a person looking at it first (review)?

Raised by Jacob (2026-09-10) after ebook_fix.modules.color_strip's
blanket, unattended sweep -- strips every hardcoded color, no
exceptions -- turned out to be too broad: a pull-quote, a "this
character always talks in blue" convention, or a stylized initial are
real, intentional choices color_strip previously couldn't tell apart
from a conversion tool's baked-in black body text. This module makes
that distinction, following the same "never silently guess on a
genuinely ambiguous case" principle as ebook_fix.paragraphs' dangling-
ending check and ebook_fix.apostrophes' possessive candidates:
color_strip only acts on the `confident` bucket below; `review` is
reported for a person to look at, never auto-touched.

Confident (safe for color_strip to remove outright)
-----------------------------------------------------
- A `color` declared on the class ebook_fix.class_map has already
  identified as the book's `body-text` role, at medium or high
  confidence.
- A `color` declared on the `body` element selector itself -- the
  worst offender, since it cascades to everything under it that
  doesn't declare its own override.
- An inline `style="color:..."` (or legacy `<font color="...">`)
  sitting directly on a <p> in a chapter frontmatter.py has classified
  as the book's main narrative zone.

Review only (flagged, never auto-repaired)
-----------------------------------------------------
Everything else -- color on <em>/<i>/<b>/<strong>/<span>/etc, on a
narrowly-used or unmapped class, on a compound/descendant/id
selector, or anywhere on a front/back-matter page. This deliberately
includes book-wide "wrapper" classes/divs that cascade color to the
whole document (e.g. a class Calibre puts on a div right under
<body>): recognizing "this wraps everything" reliably is its own
project, so for now it's flagged like any other ambiguous case rather
than silently missed. A class_map "body-wrapper" guess (a class used
almost entirely directly on <body>) is deliberately always "low"
confidence on its own and so never lands in `confident` either --
same reasoning.

A mixed CSS rule (e.g. ".calibre3, .pullquote { color: red; }") is
treated as review, not confident, even though .calibre3 alone might
be the body-text class -- stripping the whole rule would also strip
the ambiguous target sharing it.

Findings carry a live element reference where one applies (inline
style / font tag) so repair can act on it directly within the same
run, same as ebook_fix.paragraphs -- not saved to the JSON cache (see
serialize.py), used within a single run only. CSS-file/embedded-style
findings don't carry one: ebook_fix.modules.color_strip re-scans the
raw text itself using the same classification helpers this module
uses, rather than trying to match text back to a stale finding object
-- the same "recompute fresh, don't trust an analysis-time snapshot"
approach ellipsis_repair.py and apostrophe_repair.py already use.

Each finding also gets a stable `id` (e.g. "external_css:OEBPS/
style.css:2"), a plain enumeration index within its (href,
location_kind) pair -- same simplicity precedent the GUI's own
chapter-split candidate ids already use (see gui/app.py's
_split_candidate_groups). This is what lets a person pick a specific
`review` finding to remove anyway from the GUI Review tab (see
ColorStripRepair.apply_review_removals) without upgrading it to
`confident` for every book -- the id is only meant to stay stable
across two calls to this function against the same, unchanged book
content, the same assumption chapter-split ids already rely on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE
from ebook_fix.class_map import build_class_profiles, SIMPLE_CLASS_SELECTOR_RE
from ebook_fix.chapters import analyze_book_chapters
from ebook_fix.frontmatter import analyze_book_frontmatter, MAIN_ZONE

# Matches a `color: ...;` declaration but not `background-color`,
# `border-color`, etc -- same negative-lookbehind trick
# ebook_fix.modules.color_strip already used, kept in sync here since
# both modules need to agree on what counts as a color declaration.
COLOR_DECLARATION_RE = re.compile(r'(?<![a-zA-Z-])color\s*:\s*[^;]+;?', re.IGNORECASE)
COLOR_VALUE_RE = re.compile(r'(?<![a-zA-Z-])color\s*:\s*([^;]+);?', re.IGNORECASE)

BODY_SELECTOR_RE = re.compile(r'^body\s*$', re.IGNORECASE)

# Elements a color declared directly on is treated as emphasis or
# decoration, not body text, even sitting inside a main-zone <p> --
# see module docstring. <p> itself is deliberately not in this set.
DECORATIVE_TAGS = frozenset({
    "em", "i", "b", "strong", "small", "sup", "sub", "cite", "q", "span",
})


@dataclass
class ColorFinding:
    id: str = ""              # stable within one analysis pass -- see analyze_book_color
    href: str = ""            # CSS file href, or chapter href for embedded/inline/font findings
    location_kind: str = ""   # "external_css" / "embedded_style" / "inline_style" / "font_tag"
    context: str = ""         # selector text, or a short description of the element involved
    value: str = ""           # the declared color value, e.g. "#000000" or "red"
    reason: str = ""          # human-readable explanation of the bucket this landed in
    element: object = None    # live element; inline_style/font_tag findings only, not saved to the JSON cache


@dataclass
class BookColorSummary:
    confident: list = field(default_factory=list)   # [ColorFinding] -- safe for color_strip to remove
    review: list = field(default_factory=list)       # [ColorFinding] -- flag only, never auto-repaired
    # Resolved once here so ebook_fix.modules.color_strip doesn't have
    # to re-run class_map's role guesses itself -- see module docstring.
    body_text_classes: frozenset = field(default_factory=frozenset)
    main_hrefs: object = None   # set of hrefs, or None if zones couldn't be confirmed (every href counts as main)

    @property
    def confident_count(self) -> int:
        return len(self.confident)

    @property
    def review_count(self) -> int:
        return len(self.review)


def is_confident_selector_group(selector_group: str, body_text_classes: frozenset) -> bool:
    """True only if every comma-separated selector in the group is
    either the bare `body` element or a simple (optionally
    tag-qualified) class selector naming a confirmed body-text class.
    See module docstring for why a mixed group doesn't qualify."""
    parts = [p.strip() for p in selector_group.split(",") if p.strip()]
    if not parts:
        return False
    for part in parts:
        if BODY_SELECTOR_RE.match(part):
            continue
        m = SIMPLE_CLASS_SELECTOR_RE.match(part)
        if m and m.group(2) in body_text_classes:
            continue
        return False
    return True


def is_confident_paragraph_context(element) -> bool:
    """True for a plain <p style="color:..."> (color set directly on
    the paragraph itself), or a <font color="..."> tag that's a
    direct child of a <p> with nothing else wrapping it -- the same
    "directly on the paragraph" bar in both cases. A style/font
    nested inside an <em>/<i>/<b>/<strong>/<span>/etc, or attached to
    anything other than a <p>, is emphasis/decoration instead -- see
    module docstring."""
    if not isinstance(element.tag, str):
        return False
    tag = etree.QName(element).localname.lower()
    if tag == "p":
        return True
    if tag == "font":
        parent = element.getparent()
        if parent is not None and isinstance(parent.tag, str):
            return etree.QName(parent).localname.lower() == "p"
    return False


def _scan_css_text(text: str, href: str, location_kind: str, body_text_classes: frozenset, summary: BookColorSummary, counters: dict) -> None:
    if not text:
        return
    cleaned = COMMENT_RE.sub("", text)
    for m in RULE_RE.finditer(cleaned):
        selector_group = m.group(1).strip()
        body = m.group(2)
        if not selector_group or selector_group.startswith("@"):
            continue
        cm = COLOR_VALUE_RE.search(body)
        if not cm:
            continue
        key = (href, location_kind)
        i = counters.get(key, 0)
        counters[key] = i + 1
        finding = ColorFinding(
            id=f"{location_kind}:{href}:{i}",
            href=href, location_kind=location_kind,
            context=selector_group, value=cm.group(1).strip(),
        )
        if is_confident_selector_group(selector_group, body_text_classes):
            finding.reason = "color on the confirmed body-text class or <body> itself"
            summary.confident.append(finding)
        else:
            finding.reason = "color on a class/selector without a confirmed body-text role"
            summary.review.append(finding)


def analyze_book_color(book, chapter_summary=None, frontmatter_summary=None, class_profiles=None) -> BookColorSummary:
    if chapter_summary is None:
        chapter_summary = analyze_book_chapters(book)
    if frontmatter_summary is None:
        frontmatter_summary = analyze_book_frontmatter(book, chapter_summary=chapter_summary)
    if class_profiles is None:
        class_profiles = build_class_profiles(book, chapter_summary=chapter_summary, frontmatter_summary=frontmatter_summary)

    body_text_classes = frozenset(
        p.class_name for p in class_profiles.values()
        if p.likely_role == "body-text" and p.role_confidence in ("medium", "high")
    )
    main_hrefs = None
    if frontmatter_summary.boundaries_confirmed:
        main_hrefs = {cm.href for cm in frontmatter_summary.chapters if cm.zone == MAIN_ZONE}

    summary = BookColorSummary(body_text_classes=body_text_classes, main_hrefs=main_hrefs)
    # id counters, keyed by (href, location_kind) -- see ColorFinding.id.
    # Recomputed fresh every call, same "pure enumeration order, not a
    # persisted key" precedent the GUI's own chapter-split candidate
    # ids (f"{href}::{i}") already rely on -- stable across calls as
    # long as the underlying book content between them hasn't changed.
    counters: dict = {}

    # 1. External CSS files
    contents = read_book_css(book)
    for res in getattr(book, "css", []) or []:
        _scan_css_text(
            contents.get(res.href), href=res.href, location_kind="external_css",
            body_text_classes=body_text_classes, summary=summary, counters=counters,
        )

    # 2. Embedded <style> blocks, inline style="" attributes, and
    #    legacy <font color> tags, per chapter.
    for ch in getattr(book, "chapters", []) or []:
        tree = getattr(ch, "document", None)
        root = tree if tree is not None and hasattr(tree, "iter") else (tree.getroot() if tree is not None else None)
        if root is None:
            continue
        href = getattr(ch, "href", "")
        in_main_zone = main_hrefs is None or href in main_hrefs

        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            local = etree.QName(el).localname.lower()

            if local == "style":
                _scan_css_text(
                    el.text, href=href, location_kind="embedded_style",
                    body_text_classes=body_text_classes, summary=summary, counters=counters,
                )
                continue

            style_val = el.get("style")
            if style_val:
                cm = COLOR_VALUE_RE.search(style_val)
                if cm:
                    key = (href, "inline_style")
                    i = counters.get(key, 0)
                    counters[key] = i + 1
                    finding = ColorFinding(
                        id=f"inline_style:{href}:{i}",
                        href=href, location_kind="inline_style",
                        context=f'<{local} style="{style_val}">',
                        value=cm.group(1).strip(), element=el,
                    )
                    if in_main_zone and is_confident_paragraph_context(el):
                        finding.reason = "inline color set directly on a main-narrative <p>"
                        summary.confident.append(finding)
                    else:
                        finding.reason = (
                            "inline color on a decorative/nested element"
                            if in_main_zone else
                            "inline color outside the main narrative zone"
                        )
                        summary.review.append(finding)

            if local == "font" and el.get("color"):
                value = el.get("color")
                key = (href, "font_tag")
                i = counters.get(key, 0)
                counters[key] = i + 1
                finding = ColorFinding(
                    id=f"font_tag:{href}:{i}",
                    href=href, location_kind="font_tag",
                    context=f'<font color="{value}">',
                    value=value, element=el,
                )
                if in_main_zone and is_confident_paragraph_context(el):
                    finding.reason = "legacy <font color> directly on a main-narrative paragraph"
                    summary.confident.append(finding)
                else:
                    finding.reason = (
                        "legacy <font color> on a decorative/nested element"
                        if in_main_zone else
                        "legacy <font color> outside the main narrative zone"
                    )
                    summary.review.append(finding)

    return summary
