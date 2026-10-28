"""
ebook_fix.fonts

Descriptive analysis of embedded-font usage across a book, and a
confidence call on each usage: is this an embedded font used for the
book's own body text (confident -- safe for
ebook_fix.modules.font_strip to remove, letting the reader's own font
choice apply instead), or does it look like a deliberate, narrower
design choice (a chapter-opener display face, an in-story "different
language" font, a monospace font for code) that only a person should
decide about (review)?

Raised by Jacob (2026-09-12), mirroring ebook_fix.color's own
confident/review split -- see that module's docstring for the
reasoning this one follows almost exactly. The one thing genuinely new
here, beyond what color-strip needed: a font is a real embedded
resource (an actual file declared in the manifest via an @font-face
rule), not just a CSS property value. Removing one safely means:

1. Only ever acting on a font-family declaration that actually
   resolves to one of this book's own embedded font resources via an
   @font-face rule's `src: url(...)`. A plain `font-family: Georgia,
   serif;` naming a system font isn't an embedded-font question at
   all -- that's ebook_fix.modules.class_standardize's "theme-neutral"
   territory (see ebook_fix.css's THEME_FIGHTING_PROPERTIES docstring
   for why color-strip itself already draws that same line), not this
   module's.
2. Never removing the underlying font FILE (and its manifest entry,
   and any encryption.xml reference) unless EVERY place in the whole
   book that declares that exact font-family is confident -- a font
   shared between the confirmed body-text class and, say, a heading
   class is still a real, intentional part of the book's design, even
   though the body-text usage alone would otherwise qualify. The
   confident CSS declaration still gets stripped either way; the
   resource itself is only ever deleted when nothing else needs it.

Confident (safe for font_strip to remove)
-----------------------------------------------------
- A `font-family` declared on the class ebook_fix.class_map has
  already identified as the book's `body-text` role, at medium or
  high confidence -- same bar ebook_fix.color uses.
- A `font-family` declared on the `body` element selector itself.
- An inline `style="font-family:..."` (or legacy `<font face="...">`)
  sitting directly on a <p> in a chapter frontmatter.py has classified
  as the book's main narrative zone.

...and only when the declared family's first name (fallback fonts
after the first comma don't count -- they're not what's actually being
embedded) matches a family this book embeds via its own @font-face
rule(s).

Reader's-font rule (2026-10-28)
-----------------------------------------------------
Raised by Jacob, same reasoning as ebook_fix.color's size rule: the
reader's own font setting should win. He chose to remove embedded
fonts on titles, drop caps and other small sections too (the 9
leftover findings in the example books were all small display uses),
which also lets the font file go once nothing needs it. So every
embedded font usage is confident EXCEPT:
- a family stack ending in monospace, cursive or fantasy (code, a
  handwritten letter -- the generic fallback names the intent);
- text containing private-use characters (U+E000-U+F8FF), which means
  the font draws symbols or icons and the reader's font would show
  empty boxes;
- a selector too complex to measure what it reaches (descendant
  selectors, pseudo-elements), same caution as color.

The older review-only description below is what applied before that.

Review only (flagged, never auto-repaired)
-----------------------------------------------------
Everything else -- an embedded font's family declared on <em>/<i>/<b>/
<strong>/<span>/etc, on a narrowly-used or unmapped class, on a
compound/descendant/id selector, or anywhere on a front/back-matter
page. Same mixed-selector-group caution as color.py: a rule naming
both a body-text class and something else is review, not confident.

Mirrors ebook_fix.color's `id` scheme (a plain per-(href, location_kind)
enumeration) and its "recompute fresh, don't trust an analysis-time
snapshot" approach for repair -- see that module's docstring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from lxml import etree

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE, FONT_FACE_RE, FONT_FAMILY_RE, URL_RE
from ebook_fix.class_map import build_class_profiles
from ebook_fix.chapters import analyze_book_chapters
from ebook_fix.frontmatter import analyze_book_frontmatter, MAIN_ZONE
from ebook_fix.color import is_confident_selector_group, is_confident_paragraph_context, ColorReach

# Matches a `font-family: ...;` declaration. Deliberately not reusing
# ebook_fix.css.FONT_FAMILY_RE as-is for scanning arbitrary rule
# bodies/inline styles -- that one is anchored for use inside an
# already-isolated @font-face block, not a negative-lookbehind guard
# against similarly-named properties the way color.py's own
# COLOR_DECLARATION_RE guards against "background-color" etc. There's
# no "background-font-family" equivalent to guard against here, so a
# plain match is enough.
FONT_FAMILY_DECLARATION_RE = re.compile(r'font-family\s*:\s*[^;]+;?', re.IGNORECASE)
FONT_FAMILY_VALUE_RE = re.compile(r'font-family\s*:\s*([^;]+);?', re.IGNORECASE)


def first_family(value: str) -> str:
    """The first font name in a comma-separated font-family value,
    quotes stripped, lowercased -- fallback names after the first
    comma aren't what's actually being embedded, so they're not part
    of the match against this book's own @font-face families. Public
    (not underscore-prefixed) since modules/font_strip.py needs the
    exact same resolution logic to keep its own re-scan's ids and
    matches in sync with what this module's analysis found."""
    first = value.split(",")[0].strip().strip('"\'').strip()
    return first.lower()


def _resolve_font_resource(src: str, font_hrefs: set, font_basenames: dict) -> str:
    """Matches an @font-face `src: url(...)` against this book's own
    embedded font resources, same basename-fallback idiom
    ebook_fix.css.analyze_book_css already uses for its
    missing/unused-embedded-font cross-reference. Returns the
    resource's own href, or "" if this src doesn't resolve to
    anything embedded in the book (a remote/system font reference)."""
    src_clean = src.split("#")[0].split("?")[0].strip()
    if not src_clean or src_clean.lower().startswith(("http://", "https://", "data:")):
        return ""
    if src_clean in font_hrefs:
        return src_clean
    basename = PurePosixPath(src_clean).name
    return font_basenames.get(basename, "")


def _parse_font_face_blocks(css_text: str, font_hrefs: set, font_basenames: dict) -> list[tuple[str, list[str]]]:
    """Every @font-face block in one stylesheet's text, as (family,
    [resource_hrefs]) pairs -- paired within the same block, unlike
    ebook_fix.css's own independent font_families_declared/
    font_face_srcs lists, since more than one @font-face block in the
    same file (or a block declaring several format() variants of the
    same family) needs its family kept together with exactly the
    resources it embeds."""
    pairs = []
    if not css_text:
        return pairs
    cleaned = COMMENT_RE.sub("", css_text)
    for m in FONT_FACE_RE.finditer(cleaned):
        body = m.group(1)
        fm = FONT_FAMILY_RE.search(body)
        if not fm:
            continue
        family = first_family(fm.group(1))
        if not family:
            continue
        hrefs = []
        for um in URL_RE.finditer(body):
            resolved = _resolve_font_resource(um.group(1), font_hrefs, font_basenames)
            if resolved and resolved not in hrefs:
                hrefs.append(resolved)
        if hrefs:
            pairs.append((family, hrefs))
    return pairs


@dataclass
class FontFinding:
    id: str = ""              # stable within one analysis pass -- see analyze_book_font_usage
    href: str = ""            # CSS file href, or chapter href for embedded/inline/font findings
    location_kind: str = ""   # "external_css" / "embedded_style" / "inline_style" / "font_tag"
    context: str = ""         # selector text, or a short description of the element involved
    family: str = ""          # the declared font-family value, e.g. "MyBookFont" or "MyBookFont, serif"
    resolved_family: str = "" # the matched embedded family (lowercased, first name only)
    reason: str = ""          # human-readable explanation of the bucket this landed in
    element: object = None    # live element; inline_style/font_tag findings only, not saved to the JSON cache


@dataclass
class BookFontSummary:
    confident: list = field(default_factory=list)   # [FontFinding] -- safe for font_strip to remove
    review: list = field(default_factory=list)       # [FontFinding] -- flag only, never auto-repaired
    body_text_classes: frozenset = field(default_factory=frozenset)
    main_hrefs: object = None   # set of hrefs, or None if zones couldn't be confirmed
    # Every embedded family this book declares via @font-face, mapped
    # to the resource href(s) it embeds (more than one when a family
    # ships several format() variants of the same face) -- used by
    # font_strip.py to know exactly which files to delete once a
    # family's every usage turns out confident.
    family_to_hrefs: dict = field(default_factory=dict)
    # The reverse direction, for reporting which family(ies) a
    # specific resource belongs to.
    href_to_families: dict = field(default_factory=dict)

    @property
    def confident_count(self) -> int:
        return len(self.confident)

    @property
    def review_count(self) -> int:
        return len(self.review)

    def fully_confident_families(self) -> set:
        """Every embedded family where EVERY usage found anywhere in
        the book (confident or review) is confident -- see module
        docstring. A family with zero usages at all (embedded but
        never actually applied by any rule) isn't included here: no
        finding exists to make it "confident" in the first place, and
        cleaning up a genuinely orphaned, never-referenced font is a
        different, already-documented gap (ebook_fix.css's
        unused_embedded_fonts), not this feature's job."""
        confident_families = {f.resolved_family for f in self.confident}
        review_families = {f.resolved_family for f in self.review}
        return confident_families - review_families


SPECIAL_GENERICS = frozenset({"monospace", "cursive", "fantasy"})
PRIVATE_USE_RE = re.compile("[\ue000-\uf8ff]")


def _has_special_generic(declared: str) -> bool:
    names = [n.strip().strip('"\'').strip().lower() for n in declared.split(",")]
    return any(n in SPECIAL_GENERICS for n in names)


def decide_font_css(selector_group, declared, reach, body_text_classes):
    """(confident, reason) for one CSS rule declaring an embedded font."""
    if is_confident_selector_group(selector_group, body_text_classes):
        return True, "embedded font-family on the confirmed body-text class or <body> itself"
    if _has_special_generic(declared):
        return False, "embedded font with a monospace/cursive/fantasy stack (code or handwriting)"
    matched = reach.matched_group(selector_group)
    if matched is None:
        return False, "selector too complex to measure what text it reaches"
    for el in matched:
        if PRIVATE_USE_RE.search(el.xpath("string(.)")):
            return False, "embedded font draws symbol glyphs (private-use characters)"
    return True, "embedded font on text the reader's own font can display"


def decide_font_inline(el, declared, href, reach, main_hrefs):
    """(confident, reason) for one inline style / <font face> usage."""
    if (main_hrefs is None or href in main_hrefs) and is_confident_paragraph_context(el):
        return True, "inline embedded font-family set directly on a main-narrative <p>"
    if _has_special_generic(declared):
        return False, "embedded font with a monospace/cursive/fantasy stack (code or handwriting)"
    if PRIVATE_USE_RE.search(el.xpath("string(.)")):
        return False, "embedded font draws symbol glyphs (private-use characters)"
    return True, "embedded font on text the reader's own font can display"


def _scan_css_text(text: str, href: str, location_kind: str, body_text_classes: frozenset,
                    family_to_hrefs: dict, summary: BookFontSummary, counters: dict, reach=None) -> None:
    if not text:
        return
    cleaned = COMMENT_RE.sub("", text)
    for m in RULE_RE.finditer(cleaned):
        selector_group = m.group(1).strip()
        body = m.group(2)
        if not selector_group or selector_group.startswith("@"):
            continue
        fm = FONT_FAMILY_VALUE_RE.search(body)
        if not fm:
            continue
        declared = fm.group(1).strip()
        resolved = first_family(declared)
        if resolved not in family_to_hrefs:
            continue  # not an embedded font this book actually ships -- out of scope, see module docstring
        key = (href, location_kind)
        i = counters.get(key, 0)
        counters[key] = i + 1
        finding = FontFinding(
            id=f"{location_kind}:{href}:{i}",
            href=href, location_kind=location_kind,
            context=selector_group, family=declared, resolved_family=resolved,
        )
        confident, reason = decide_font_css(selector_group, declared, reach, body_text_classes)
        finding.reason = reason
        (summary.confident if confident else summary.review).append(finding)


def analyze_book_font_usage(book, chapter_summary=None, frontmatter_summary=None, class_profiles=None) -> BookFontSummary:
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

    # Build the family -> embedded resource(s) map from every
    # @font-face rule in the book FIRST -- every scan below only cares
    # about a font-family that actually resolves to something this
    # book embeds (see module docstring), so this has to exist before
    # any usage can be classified at all.
    font_resources = getattr(book, "fonts", []) or []
    font_hrefs = {getattr(f, "href", "") for f in font_resources}
    font_basenames = {PurePosixPath(h).name: h for h in font_hrefs}

    family_to_hrefs: dict = {}
    href_to_families: dict = {}

    contents = read_book_css(book)
    for res in getattr(book, "css", []) or []:
        for family, hrefs in _parse_font_face_blocks(contents.get(res.href), font_hrefs, font_basenames):
            family_to_hrefs.setdefault(family, set()).update(hrefs)
            for h in hrefs:
                href_to_families.setdefault(h, set()).add(family)

    # @font-face rules can also be written directly into a chapter's
    # own inline <style> block, not just a linked stylesheet -- same
    # scope ebook_fix.css.analyze_inline_chapter_css itself doesn't
    # yet cover for its own font cross-reference (see that module's
    # docstring), but there's no reason this scan can't check chapter
    # <style> blocks directly since it's already walking them below
    # for font-family usage anyway.
    for ch in getattr(book, "chapters", []) or []:
        tree = getattr(ch, "document", None)
        root = tree if tree is not None and hasattr(tree, "iter") else (tree.getroot() if tree is not None else None)
        if root is None:
            continue
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            if etree.QName(el).localname.lower() != "style":
                continue
            for family, hrefs in _parse_font_face_blocks(el.text, font_hrefs, font_basenames):
                family_to_hrefs.setdefault(family, set()).update(hrefs)
                for h in hrefs:
                    href_to_families.setdefault(h, set()).add(family)

    summary = BookFontSummary(
        body_text_classes=body_text_classes,
        main_hrefs=main_hrefs,
        family_to_hrefs={k: sorted(v) for k, v in family_to_hrefs.items()},
        href_to_families={k: sorted(v) for k, v in href_to_families.items()},
    )
    if not family_to_hrefs:
        return summary  # book embeds no fonts at all (or none any @font-face rule actually references) -- nothing to scan for

    counters: dict = {}
    reach = ColorReach(book)

    # 1. External CSS files
    for res in getattr(book, "css", []) or []:
        _scan_css_text(
            contents.get(res.href), href=res.href, location_kind="external_css",
            body_text_classes=body_text_classes, family_to_hrefs=family_to_hrefs,
            summary=summary, counters=counters, reach=reach,
        )

    # 2. Embedded <style> blocks, inline style="" attributes, and
    #    legacy <font face> tags, per chapter.
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
                    body_text_classes=body_text_classes, family_to_hrefs=family_to_hrefs,
                    summary=summary, counters=counters, reach=reach,
                )
                continue

            style_val = el.get("style")
            if style_val:
                fm = FONT_FAMILY_VALUE_RE.search(style_val)
                if fm:
                    resolved = first_family(fm.group(1))
                    if resolved in family_to_hrefs:
                        key = (href, "inline_style")
                        i = counters.get(key, 0)
                        counters[key] = i + 1
                        finding = FontFinding(
                            id=f"inline_style:{href}:{i}",
                            href=href, location_kind="inline_style",
                            context=f'<{local} style="{style_val}">',
                            family=fm.group(1).strip(), resolved_family=resolved, element=el,
                        )
                        confident, reason = decide_font_inline(el, finding.family, href, reach, main_hrefs)
                        finding.reason = reason
                        (summary.confident if confident else summary.review).append(finding)

            if local == "font" and el.get("face"):
                value = el.get("face")
                resolved = first_family(value)
                if resolved in family_to_hrefs:
                    key = (href, "font_tag")
                    i = counters.get(key, 0)
                    counters[key] = i + 1
                    finding = FontFinding(
                        id=f"font_tag:{href}:{i}",
                        href=href, location_kind="font_tag",
                        context=f'<font face="{value}">',
                        family=value, resolved_family=resolved, element=el,
                    )
                    confident, reason = decide_font_inline(el, value, href, reach, main_hrefs)
                    finding.reason = reason
                    (summary.confident if confident else summary.review).append(finding)

    return summary
