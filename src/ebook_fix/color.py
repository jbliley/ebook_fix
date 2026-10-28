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

Size rule (2026-10-28)
-----------------------------------------------------
Raised by Jacob: on an e-reader the reader's own light/dark theme
should decide text colors, and he was already ticking "remove" on
every color finding in the Review tab. So the confident bucket now
also takes any color that is not a very small section:

- A color whose value is `inherit`/`initial`/`unset`/`currentColor`
  (it sets no real color).
- A color that only reaches link text (the reader colors links
  itself), empty elements such as <hr>, or classes nothing uses.
- A color reaching more than a very small section of the book: more
  than SMALL_SECTION_MAX_SHARE of the book's words, or more than
  SMALL_SECTION_MAX_WORDS words. Inline colors are totalled per color
  value across the book, so a "this character always talks in blue"
  convention is measured as a whole, not one span at a time.

What stays in `review`: a colored section that is very small (a few
words or lines, e.g. one colored word, a short letter or a "THE END"
line), a color declared in the same rule/style as a background (the
two are a contrast pair, removing only one could leave unreadable
text), and a selector too complex to measure (descendant selectors,
pseudo-elements such as a drop cap's ::first-letter).
ebook_fix.modules.color_strip's `keep_small_sections` setting can turn
the small-section exemption off.
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

# "Very small section" -- see the module docstring's size rule. A color
# reaching no more than this share of the book's words AND no more than
# this many words is kept for review instead of removed.
SMALL_SECTION_MAX_SHARE = 0.01
SMALL_SECTION_MAX_WORDS = 250

# Values that set no actual color -- always safe to drop.
NEUTRAL_COLOR_VALUES = frozenset({"inherit", "initial", "unset", "revert", "currentcolor"})

BACKGROUND_RE = re.compile(r'(?<![a-zA-Z-])background(?:-color|-image)?\s*:\s*([^;}]+)', re.IGNORECASE)
NO_BACKGROUND_VALUES = frozenset({"none", "transparent", "inherit", "initial", "unset", "revert"})

# One simple selector the reach measurement can resolve without a full
# CSS engine: optional tag, any number of .class/#id parts, and the
# link states/[href] a link rule carries. Anything else (descendant or
# child combinators, ::first-letter, :first-child, other attribute
# selectors) is "too complex to measure" and stays in review.
MEASURABLE_SELECTOR_RE = re.compile(
    r'^(\*|[a-zA-Z][a-zA-Z0-9]*)?((?:[.#][a-zA-Z_-][\w-]*)*)'
    r'(?:\[href\]|:(?:link|visited|hover|active|focus))*$'
)
CLASS_OR_ID_PART_RE = re.compile(r'([.#])([a-zA-Z_-][\w-]*)')


def is_neutral_color_value(value: str) -> bool:
    return (value or "").strip().lower().rstrip(";").strip() in NEUTRAL_COLOR_VALUES


def has_background(declarations: str) -> bool:
    """True if a rule body / style attribute declares an actual
    background (anything other than none/transparent/inherit...)."""
    for m in BACKGROUND_RE.finditer(declarations or ""):
        value = m.group(1).strip().lower()
        if value and value.split()[0] not in NO_BACKGROUND_VALUES:
            return True
    return False


def _local(el) -> str:
    return etree.QName(el).localname.lower()


def _words(el) -> int:
    return len(el.xpath("string(.)").split())


class ColorReach:
    """How much of the book's text a color reaches -- built once per
    analysis/repair call from the book's live chapter documents, so it
    always reflects current content (never a stale snapshot).

    Link text is never counted: the reading system colors links
    itself, so a color that only ever reaches link text is always
    removable. A nested match is counted once (its outermost matched
    ancestor already covers the words)."""

    def __init__(self, book):
        self.total_words = 0
        self._by_tag: dict = {}
        self._by_class: dict = {}
        self._by_id: dict = {}
        self._all: list = []
        self._inline_by_value: dict = {}
        self._cache: dict = {}
        for ch in getattr(book, "chapters", []) or []:
            tree = getattr(ch, "document", None)
            root = tree if tree is not None and hasattr(tree, "iter") else (tree.getroot() if tree is not None else None)
            if root is None:
                continue
            body_words = None
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                local = _local(el)
                if local == "body" and body_words is None:
                    body_words = _words(el)
                self._all.append(el)
                self._by_tag.setdefault(local, []).append(el)
                for cl in (el.get("class") or "").split():
                    self._by_class.setdefault(cl, []).append(el)
                if el.get("id"):
                    self._by_id.setdefault(el.get("id"), []).append(el)
                style_val = el.get("style")
                if style_val:
                    cm = COLOR_VALUE_RE.search(style_val)
                    if cm:
                        self._inline_by_value.setdefault(cm.group(1).strip().lower(), []).append(el)
                if local == "font" and el.get("color"):
                    self._inline_by_value.setdefault(el.get("color").strip().lower(), []).append(el)
            self.total_words += body_words or 0

    # -- measuring ------------------------------------------------

    def measure_elements(self, elements) -> int:
        matched = set(elements)
        total = 0
        for el in matched:
            skip = False
            for anc in el.iterancestors():
                if anc in matched:
                    skip = True
                    break
            if skip:
                continue
            if _local(el) == "a" or any(isinstance(a.tag, str) and _local(a) == "a" for a in el.iterancestors()):
                continue
            total += _words(el)
        return total

    def measure_group(self, selector_group: str):
        """Words reached by a comma-separated selector group, or None
        if any part is too complex to measure."""
        key = selector_group.strip()
        if key in self._cache:
            return self._cache[key]
        matched: list = []
        result = 0
        for part in [p.strip() for p in key.split(",") if p.strip()]:
            m = MEASURABLE_SELECTOR_RE.match(part)
            if not m:
                self._cache[key] = None
                return None
            tag = (m.group(1) or "").lower()
            classes, ids = [], []
            for kind, name in CLASS_OR_ID_PART_RE.findall(m.group(2) or ""):
                (classes if kind == "." else ids).append(name)
            if ids:
                candidates = self._by_id.get(ids[0], [])
            elif classes:
                candidates = self._by_class.get(classes[0], [])
            elif tag and tag != "*":
                candidates = self._by_tag.get(tag, [])
            else:
                candidates = self._all
            for el in candidates:
                if tag and tag != "*" and _local(el) != tag:
                    continue
                el_classes = (el.get("class") or "").split()
                if any(c not in el_classes for c in classes):
                    continue
                if ids and el.get("id") != ids[0]:
                    continue
                if any(i != el.get("id") for i in ids):
                    continue
                matched.append(el)
        result = self.measure_elements(matched)
        self._cache[key] = result
        return result

    def inline_words(self, value: str) -> int:
        """Words reached by inline color of this value, totalled over
        the whole book (all inline styles and <font> tags using it)."""
        key = "inline:" + (value or "").strip().lower()
        if key not in self._cache:
            self._cache[key] = self.measure_elements(self._inline_by_value.get((value or "").strip().lower(), []))
        return self._cache[key]

    def is_small(self, words: int) -> bool:
        if words > SMALL_SECTION_MAX_WORDS:
            return False
        return words <= SMALL_SECTION_MAX_SHARE * max(self.total_words, 1)


def _word_count_text(words: int) -> str:
    return f"{words} word" if words == 1 else f"{words} words"


def decide_css(selector_group, declarations, reach, body_text_classes, keep_small=True):
    """(confident, reason, small) for one CSS rule that sets a color.
    See the module docstring's size rule."""
    if is_confident_selector_group(selector_group, body_text_classes):
        return True, "color on the confirmed body-text class or <body> itself", False
    cm = COLOR_VALUE_RE.search(declarations)
    value = cm.group(1).strip() if cm else ""
    if is_neutral_color_value(value):
        return True, "color value sets no real color (inherit/initial/unset)", False
    if has_background(declarations):
        return False, "color sits in the same rule as a background (contrast pair)", False
    words = reach.measure_group(selector_group)
    if words is None:
        return False, "selector too complex to measure how much text it colors", False
    if words == 0:
        return True, "color only reaches link text, empty elements, or nothing the book uses", False
    if keep_small and reach.is_small(words):
        return False, f"color on a very small section ({_word_count_text(words)})", True
    return True, f"color reaching {words} words, more than a very small section", False


def decide_inline(el, value, style_val, href, reach, main_hrefs, keep_small=True):
    """(confident, reason, small) for one inline style="color:..." or
    <font color> element."""
    if (main_hrefs is None or href in main_hrefs) and is_confident_paragraph_context(el):
        return True, "inline color set directly on a main-narrative <p>", False
    if is_neutral_color_value(value):
        return True, "color value sets no real color (inherit/initial/unset)", False
    if style_val and has_background(style_val):
        return False, "inline color sits in the same style as a background (contrast pair)", False
    words = reach.inline_words(value)
    if words == 0:
        return True, "inline color only reaches link text or empty elements", False
    if keep_small and reach.is_small(words):
        return False, f"inline color {value} on a very small section ({_word_count_text(words)} in the whole book)", True
    return True, f"inline color {value} reaching {words} words, more than a very small section", False


@dataclass
class ColorFinding:
    id: str = ""              # stable within one analysis pass -- see analyze_book_color
    href: str = ""            # CSS file href, or chapter href for embedded/inline/font findings
    location_kind: str = ""   # "external_css" / "embedded_style" / "inline_style" / "font_tag"
    context: str = ""         # selector text, or a short description of the element involved
    value: str = ""           # the declared color value, e.g. "#000000" or "red"
    reason: str = ""          # human-readable explanation of the bucket this landed in
    element: object = None    # live element; inline_style/font_tag findings only, not saved to the JSON cache
    small: bool = False       # review finding kept only because it is a very small section


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


def _scan_css_text(text: str, href: str, location_kind: str, body_text_classes: frozenset, summary: BookColorSummary, counters: dict, reach: ColorReach, keep_small: bool = True) -> None:
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
        confident, reason, small = decide_css(selector_group, body, reach, body_text_classes, keep_small)
        finding.reason = reason
        finding.small = small
        (summary.confident if confident else summary.review).append(finding)


def analyze_book_color(book, chapter_summary=None, frontmatter_summary=None, class_profiles=None, keep_small=True) -> BookColorSummary:
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
    reach = ColorReach(book)
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
            reach=reach, keep_small=keep_small,
        )

    # 2. Embedded <style> blocks, inline style="" attributes, and
    #    legacy <font color> tags, per chapter.
    for ch in getattr(book, "chapters", []) or []:
        tree = getattr(ch, "document", None)
        root = tree if tree is not None and hasattr(tree, "iter") else (tree.getroot() if tree is not None else None)
        if root is None:
            continue
        href = getattr(ch, "href", "")
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            local = etree.QName(el).localname.lower()

            if local == "style":
                _scan_css_text(
                    el.text, href=href, location_kind="embedded_style",
                    body_text_classes=body_text_classes, summary=summary, counters=counters,
                    reach=reach, keep_small=keep_small,
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
                    confident, reason, small = decide_inline(
                        el, finding.value, style_val, href, reach, main_hrefs, keep_small,
                    )
                    finding.reason = reason
                    finding.small = small
                    (summary.confident if confident else summary.review).append(finding)

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
                confident, reason, small = decide_inline(
                    el, value, None, href, reach, main_hrefs, keep_small,
                )
                finding.reason = reason
                finding.small = small
                (summary.confident if confident else summary.review).append(finding)

    return summary
