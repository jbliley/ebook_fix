"""
ebook_fix.class_map

css.py deliberately only records facts. This module is the opposite: it
exists to form an opinion. Given a book, it builds one profile per CSS
class actually used in the HTML -- how often, on which tags, and (best
effort) what properties the stylesheet declares for it -- and turns that
into a guessed semantic role like "chapter-heading" or "body-text".

This is aimed squarely at the "calibre1, calibre2, calibre3..." problem:
conversion tools hand out meaningless class names, and figuring out by
hand which one is the chapter title versus the body paragraph versus a
one-off centered note is tedious across a whole library. The guesses
here are a starting point for a person to confirm or correct -- not
something a rename/standardize module should apply unattended. Treat
`likely_role`/`role_confidence` as a first draft, especially anything
marked "low" confidence or "unknown".
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE, THEME_APPEARANCE_PROPERTIES
from ebook_fix.chapters import analyze_book_chapters
from ebook_fix.frontmatter import analyze_book_frontmatter, MAIN_ZONE

PROPERTY_RE = re.compile(r'([a-zA-Z-]+)\s*:\s*([^;]+)')
# A "simple" selector for our purposes: an optional single tag name
# qualifying exactly one class, e.g. ".calibre3" or "p.calibre3". This
# deliberately excludes descendant/compound selectors (".x .y", "div.x > p")
# since those don't map cleanly onto "these are this class's properties".
SIMPLE_CLASS_SELECTOR_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9]*)?\.([a-zA-Z_-][\w-]*)$')

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# A class covering at least this share of every <p> in the whole book
# is treated as "the" body-text class regardless of raw usage count --
# see _guess_role's dominant_p_share check.
DOMINANT_P_SHARE = 0.5

# A class whose usage coincides with at least this share of chapters.py's
# own confirmed chapter-start markers is treated as "the" chapter-heading
# class outright, ahead of any tag/style guessing -- see _guess_role's
# chapter_marker_coverage check.
CHAPTER_MARKER_COVERAGE_THRESHOLD = 0.6

FONT_SIZE_RE = re.compile(r'([\d.]+)\s*(em|rem|%|pt|px)?')


@dataclass
class ClassProfile:
    class_name: str = ""
    usage_count: int = 0
    tag_counts: Counter = field(default_factory=Counter)
    properties: dict = field(default_factory=dict)  # best-effort, from simple selectors only
    sample_hrefs: list = field(default_factory=list)
    likely_role: str = "unknown"
    role_confidence: str = "low"  # low / medium / high
    # How many of this class's <p> uses fall on a page frontmatter.py
    # classified as main content, versus front/back matter -- see
    # build_class_profiles. Equal to tag_counts["p"] whenever zones
    # couldn't be confirmed at all (nothing to exclude).
    main_zone_p_count: int = 0
    # How many of chapters.py's own confirmed chapter-start markers this
    # class's elements directly coincide with -- see build_class_profiles.
    confirmed_chapter_marker_count: int = 0
    # confirmed_chapter_marker_count / (total confirmed chapters in the
    # book), 0.0 if chapters.py found no confirmed sequence at all.
    chapter_marker_coverage: float = 0.0


def _extract_properties(body: str) -> dict:
    props = {}
    for m in PROPERTY_RE.finditer(body):
        props[m.group(1).strip().lower()] = m.group(2).strip()
    return props


def _collect_class_properties(book) -> dict:
    """class_name -> {property: value}, gathered only from selectors that
    unambiguously target a single class (optionally tag-qualified)."""
    contents = read_book_css(book)
    result: dict = {}
    for res in getattr(book, "css", []) or []:
        text = contents.get(res.href)
        if not text:
            continue
        text = COMMENT_RE.sub("", text)
        for m in RULE_RE.finditer(text):
            selector_group = m.group(1).strip()
            body = m.group(2)
            if not selector_group or selector_group.startswith("@"):
                continue
            props = _extract_properties(body)
            if not props:
                continue
            for simple_sel in selector_group.split(","):
                sm = SIMPLE_CLASS_SELECTOR_RE.match(simple_sel.strip())
                if not sm:
                    continue
                cls = sm.group(2)
                # Later rules win, approximating cascade order within the file.
                result.setdefault(cls, {}).update(props)
    return result


def build_class_profiles(book, chapter_summary=None, frontmatter_summary=None) -> dict:
    """One ClassProfile per class attribute value actually present on some
    element in the book, cross-referenced with declared CSS properties
    and, where available, with two other analyses this module used to
    guess at blind:

    - chapters.py's own confirmed chapter-start markers, so a class
      that already coincides with a chapter boundary chapters.py
      independently verified doesn't have to be re-guessed from tag/
      style alone (see _guess_role's chapter_marker_coverage check).
    - frontmatter.py's front/back/main zone classification, so the
      "dominant body-text class" check isn't diluted or skewed by
      title-page, copyright-page, or acknowledgments paragraphs that
      are often styled differently from the book's actual body text
      (see _guess_role's dominant_p_share check).

    `chapter_summary`/`frontmatter_summary` should be the
    chapters.BookChapterSummary / frontmatter.BookFrontMatterSummary
    the caller already computed for this book (see engine.py's
    AnalysisReport) -- passed in so this doesn't have to re-run either
    analysis itself. Falls back to computing both if not given, so
    this still works called standalone (e.g. the `map-css` command).
    """
    if chapter_summary is None:
        chapter_summary = analyze_book_chapters(book)
    if frontmatter_summary is None:
        frontmatter_summary = analyze_book_frontmatter(book, chapter_summary=chapter_summary)

    confirmed_elements = {
        c.element for c in (chapter_summary.confirmed_boundaries or [])
        if c.element is not None
    }
    total_confirmed_chapters = len(chapter_summary.confirmed_boundaries or [])

    # None means "couldn't confirm zones at all" (chapters.py found no
    # sequence to anchor on) -- every href counts as in-scope in that
    # case, same as before this zone check existed, rather than
    # narrowing to an empty, unreliable set.
    main_hrefs = None
    if frontmatter_summary.boundaries_confirmed:
        main_hrefs = {cm.href for cm in frontmatter_summary.chapters if cm.zone == MAIN_ZONE}

    style_props = _collect_class_properties(book)
    profiles: dict = {}
    main_zone_p_total = 0  # denominator for main_zone_p_count above --
    # see _guess_role's "dominant body-text class" check below.

    for ch in getattr(book, "chapters", []) or []:
        tree = getattr(ch, "document", None)
        if tree is None:
            continue
        root = tree if hasattr(tree, "iter") else tree.getroot()
        if root is None:
            continue
        href = getattr(ch, "href", "")
        in_main_zone = main_hrefs is None or href in main_hrefs
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            tag = etree.QName(el).localname.lower()
            if tag == "p" and in_main_zone:
                main_zone_p_total += 1
            cls_attr = el.get("class")
            if not cls_attr:
                continue
            for cls in cls_attr.split():
                p = profiles.setdefault(cls, ClassProfile(class_name=cls))
                p.usage_count += 1
                p.tag_counts[tag] += 1
                if tag == "p" and in_main_zone:
                    p.main_zone_p_count += 1
                if el in confirmed_elements:
                    p.confirmed_chapter_marker_count += 1
                if href not in p.sample_hrefs and len(p.sample_hrefs) < 3:
                    p.sample_hrefs.append(href)

    for cls, p in profiles.items():
        p.properties = style_props.get(cls, {})
        if total_confirmed_chapters:
            p.chapter_marker_coverage = p.confirmed_chapter_marker_count / total_confirmed_chapters
        p.likely_role, p.role_confidence = _guess_role(p, main_zone_p_total)

    return profiles


def _dominant_tag(tag_counts: Counter):
    if not tag_counts:
        return "", 0.0
    tag, count = tag_counts.most_common(1)[0]
    total = sum(tag_counts.values())
    return tag, (count / total if total else 0.0)


def _looks_large(size_value: str) -> bool:
    """Crude check: is this font-size bigger than a plausible body size?"""
    m = FONT_SIZE_RE.match(size_value.strip())
    if not m:
        return False
    num = float(m.group(1))
    unit = m.group(2) or ""
    if unit in ("em", "rem") and num >= 1.15:
        return True
    if unit == "%" and num >= 115:
        return True
    if unit == "pt" and num >= 14:
        return True
    if unit == "px" and num >= 18:
        return True
    return False


def _guess_role(p: ClassProfile, main_zone_p_total: int = 0):
    """Heuristic, not authoritative -- see module docstring. Order matters:
    more specific/confident checks run first so a class doesn't fall
    through to a vaguer guess it also happens to match.

    `main_zone_p_total` is every <p> book-wide that frontmatter.py
    classified as main content (or every <p> in the book, if zones
    couldn't be confirmed at all -- see build_class_profiles), used to
    spot a "dominant" body-text class (see DOMINANT_P_SHARE below), a
    stronger signal than a flat usage-count threshold: a 300-page book
    and a 20-page short story can each have one class that's obviously
    THE body text, but the raw count needed to be confident of that
    differs by an order of magnitude between them.

    `p.chapter_marker_coverage` (see build_class_profiles) is checked
    first, ahead of every tag/style heuristic below -- it's strictly
    stronger evidence than anything else this function looks at, since
    it comes from chapters.py's own confirmed chapter sequence rather
    than a guess about how a heading is likely to be styled.
    """
    total = sum(p.tag_counts.values()) or 1
    dominant_tag, dominant_frac = _dominant_tag(p.tag_counts)
    heading_frac = sum(c for t, c in p.tag_counts.items() if t in HEADING_TAGS) / total
    p_frac = p.tag_counts.get("p", 0) / total
    span_frac = p.tag_counts.get("span", 0) / total
    dominant_p_share = (p.main_zone_p_count / main_zone_p_total) if main_zone_p_total else 0.0

    props = {k: v.lower() for k, v in p.properties.items()}
    font_size = props.get("font-size", "")
    text_align = props.get("text-align", "")
    font_style = props.get("font-style", "")
    font_weight = props.get("font-weight", "")
    text_indent = props.get("text-indent", "")

    is_bold = "bold" in font_weight or (font_weight.isdigit() and int(font_weight) >= 600)
    is_centered = "center" in text_align
    is_large = _looks_large(font_size) if font_size else False
    is_italic = "italic" in font_style or "oblique" in font_style
    is_zero_indent = text_indent in ("0", "0px", "0em", "0pt", "0%")

    # Independent structural corroboration: this class's usage directly
    # coincides with elements chapters.py already confirmed, on its own
    # evidence, as real chapter-start markers. Guarded by dominant_p_share
    # so a class that's already the book's dominant body-text class isn't
    # promoted just because a handful of its (very many) uses happen to
    # land on a chapter's opening paragraph.
    if p.chapter_marker_coverage >= CHAPTER_MARKER_COVERAGE_THRESHOLD and dominant_p_share < DOMINANT_P_SHARE:
        return "chapter-heading", "high"

    # Classes whose dominant tag alone is a strong signal, independent of
    # whatever CSS the (possibly missing) selector declared.
    if dominant_tag == "body" and dominant_frac >= 0.9:
        return "body-wrapper", "low"
    if dominant_tag in ("b", "strong") and dominant_frac >= 0.6:
        return "bold-text", "medium"
    if dominant_tag == "a" and dominant_frac >= 0.6:
        return "link", "medium"

    if heading_frac >= 0.6:
        return "chapter-heading", "high"
    if p_frac >= 0.5 and is_centered and is_large and is_bold:
        # All three signals together (centered, oversized, bold) is a
        # meaningfully stronger case than any one or two alone.
        return "chapter-heading", "high"
    if p_frac >= 0.5 and is_centered and (is_large or is_bold):
        return "chapter-heading", "medium"
    if dominant_p_share >= DOMINANT_P_SHARE and not is_large and not is_bold and not is_italic:
        # Used on most of the book's actual paragraphs -- about as
        # confident as this module gets that a class really is "the"
        # body text, regardless of the book's overall length.
        return "body-text", "high"
    if p_frac >= 0.6 and not is_large and not is_bold and not is_italic:
        return "body-text", ("medium" if p.usage_count >= 20 else "low")
    if is_italic and p_frac >= 0.3:
        return "emphasis-text", "low"
    if is_centered and not heading_frac and p.usage_count < 20:
        return "centered-text", "low"
    if is_zero_indent and p_frac >= 0.3:
        return "no-indent-paragraph", "low"
    if span_frac >= 0.6:
        return "inline-span", "low"
    return "unknown", "low"


# ---------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------

DISPLAY_PROPERTIES = (
    "font-size", "font-weight", "font-style", "text-align",
    "text-indent", "margin-top", "margin-bottom", "color",
)


def format_class_map(profiles: dict) -> str:
    """One block per class, most-used first -- those are the ones worth
    getting right before anything gets renamed."""
    lines = []
    ordered = sorted(profiles.values(), key=lambda p: -p.usage_count)
    for p in ordered:
        dominant_tag, frac = _dominant_tag(p.tag_counts)
        tag_note = f"<{dominant_tag}> {frac:.0%}" if dominant_tag else "-"
        prop_bits = [f"{k}:{p.properties[k]}" for k in DISPLAY_PROPERTIES if k in p.properties]
        prop_note = "; ".join(prop_bits) if prop_bits else "(no declared style found for this class)"
        block = (
            f".{p.class_name}  --  used {p.usage_count}x, mostly on {tag_note}\n"
            f"    guess: {p.likely_role} ({p.role_confidence} confidence)\n"
            f"    style: {prop_note}"
        )
        if p.confirmed_chapter_marker_count:
            block += (
                f"\n    chapter markers: matches {p.confirmed_chapter_marker_count} of "
                f"chapters.py's own confirmed chapter markers "
                f"({p.chapter_marker_coverage:.0%} of them)"
            )
        lines.append(block)
    return "\n".join(lines)


# ---------------------------------------------------------------------
# Mapping file (input to the not-yet-built class-standardize repair
# module -- this only writes the file; nothing reads it back yet)
# ---------------------------------------------------------------------

# Roles worth standardizing first -- see class_map module docstring.
# Anything else (link, bold-text, body-wrapper, unknown, ...) is left
# out of the pre-filled mapping entirely, on purpose.
MAPPABLE_ROLES = ("chapter-heading", "body-text")
MAPPABLE_CONFIDENCE = ("high", "medium")

DEFAULT_ROLE_NAMES = {
    "chapter-heading": "chapter-header",
    "body-text": "body",
}


def _has_theme_fighting_properties(p) -> bool:
    """True if this class declares at least one property from
    ebook_fix.css.THEME_APPEARANCE_PROPERTIES -- a hardcoded color, a
    fixed font-family/font-size, etc. Independent of likely_role: a
    class guessed as "inline-span", "link", "body-wrapper", or even
    "unknown" can still be the actual source of hardcoded black text a
    person notices in a reading app, and often is (a top-level
    body-wrapper class carrying a hardcoded color cascades to every
    descendant that doesn't declare its own override)."""
    return any(k.lower() in THEME_APPEARANCE_PROPERTIES for k in p.properties)


def write_mapping_file(profiles: dict, path) -> None:
    """
    Write an editable TOML file: one [[class]] block per class that's
    both a standardize-able role (chapter-heading/body-text) and a
    confidence level worth pre-filling (medium/high) -- plus one more
    block, role="theme-neutral", for any other class that still
    declares a hardcoded color/font/size regardless of its guessed
    role or confidence (see _has_theme_fighting_properties above).
    Everything else is listed as a comment for awareness, not
    auto-included.

    A theme-neutral entry defaults new_name to the class's own name --
    it's meant to strip theme-fighting properties in place, not rename
    the class the way a real role mapping does. Delete the block (or
    the whole file) if a person doesn't want that class touched.

    Read back by ebook_fix.modules.class_standardize.load_mapping_file
    -- either via `map-css --write-mapping` + a separate `repair
    --class-mapping`, or auto-generated by `repair --class-mapping
    <path-that-doesn't-exist-yet>` (which then stops without applying
    anything, so there's still a review step before it's used).
    """
    ordered = sorted(profiles.values(), key=lambda p: -p.usage_count)
    mappable = [p for p in ordered if p.likely_role in MAPPABLE_ROLES and p.role_confidence in MAPPABLE_CONFIDENCE]
    theme_only = [p for p in ordered if p not in mappable and _has_theme_fighting_properties(p)]
    leftover = [p for p in ordered if p not in mappable and p not in theme_only]

    lines = [
        "# ebook_fix class-standardization mapping",
        "#",
        "# Review every entry below. Delete a [[class]] block entirely",
        "# to leave that class untouched. Edit `new_name` to change what",
        "# it gets renamed to, or `role` (\"chapter-heading\" / \"body-text\"",
        "# / \"theme-neutral\") if the guess looks wrong.",
        "#",
        "# Applied by: ebook-fixer repair <book> --class-mapping <this file>",
        "",
    ]

    if not mappable and not theme_only:
        lines += [
            "# No classes met the confidence bar for auto-mapping (medium/high),",
            "# and none declared a hardcoded color/font/size either.",
            "# Add [[class]] blocks by hand below if you want to map something anyway:",
            "#",
            '# [[class]]',
            '# old_name = "calibreX"',
            '# role = "body-text"',
            '# new_name = "body"',
            "",
        ]

    for p in mappable:
        dominant_tag, frac = _dominant_tag(p.tag_counts)
        tag_note = f"<{dominant_tag}> {frac:.0%}" if dominant_tag else "-"
        lines += [
            "[[class]]",
            f'old_name = "{p.class_name}"',
            f'role = "{p.likely_role}"  # {p.role_confidence} confidence, used {p.usage_count}x, mostly {tag_note}',
            f'new_name = "{DEFAULT_ROLE_NAMES[p.likely_role]}"',
            "",
        ]

    if theme_only:
        lines.append(
            "# --- hardcoded color/font/size found, but no chapter-heading/body-text "
            "role to apply (theme-neutral: strips those properties, keeps everything "
            "else this class declares, no rename) ---"
        )
        for p in theme_only:
            dominant_tag, frac = _dominant_tag(p.tag_counts)
            tag_note = f"<{dominant_tag}> {frac:.0%}" if dominant_tag else "-"
            theme_props = ", ".join(sorted(k for k in p.properties if k.lower() in THEME_APPEARANCE_PROPERTIES))
            lines += [
                "[[class]]",
                f'old_name = "{p.class_name}"',
                f'role = "theme-neutral"  # guessed {p.likely_role} ({p.role_confidence} confidence), '
                f'used {p.usage_count}x, mostly {tag_note}; declares {theme_props}',
                f'new_name = "{p.class_name}"',
                "",
            ]

    if leftover:
        lines.append("# --- left out (low confidence, not chapter-heading/body-text, no hardcoded color/font/size either) ---")
        for p in leftover:
            dominant_tag, frac = _dominant_tag(p.tag_counts)
            tag_note = f"<{dominant_tag}> {frac:.0%}" if dominant_tag else "-"
            lines.append(f"# {p.class_name}: {p.likely_role} ({p.role_confidence} confidence), used {p.usage_count}x, mostly {tag_note}")
        lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")
