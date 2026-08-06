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

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE

PROPERTY_RE = re.compile(r'([a-zA-Z-]+)\s*:\s*([^;]+)')
# A "simple" selector for our purposes: an optional single tag name
# qualifying exactly one class, e.g. ".calibre3" or "p.calibre3". This
# deliberately excludes descendant/compound selectors (".x .y", "div.x > p")
# since those don't map cleanly onto "these are this class's properties".
SIMPLE_CLASS_SELECTOR_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9]*)?\.([a-zA-Z_-][\w-]*)$')

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

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


def build_class_profiles(book) -> dict:
    """One ClassProfile per class attribute value actually present on some
    element in the book, cross-referenced with declared CSS properties."""
    style_props = _collect_class_properties(book)
    profiles: dict = {}

    for ch in getattr(book, "chapters", []) or []:
        tree = getattr(ch, "document", None)
        if tree is None:
            continue
        root = tree if hasattr(tree, "iter") else tree.getroot()
        if root is None:
            continue
        href = getattr(ch, "href", "")
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            cls_attr = el.get("class")
            if not cls_attr:
                continue
            tag = etree.QName(el).localname.lower()
            for cls in cls_attr.split():
                p = profiles.setdefault(cls, ClassProfile(class_name=cls))
                p.usage_count += 1
                p.tag_counts[tag] += 1
                if href not in p.sample_hrefs and len(p.sample_hrefs) < 3:
                    p.sample_hrefs.append(href)

    for cls, p in profiles.items():
        p.properties = style_props.get(cls, {})
        p.likely_role, p.role_confidence = _guess_role(p)

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


def _guess_role(p: ClassProfile):
    """Heuristic, not authoritative -- see module docstring. Order matters:
    more specific/confident checks run first so a class doesn't fall
    through to a vaguer guess it also happens to match."""
    total = sum(p.tag_counts.values()) or 1
    dominant_tag, dominant_frac = _dominant_tag(p.tag_counts)
    heading_frac = sum(c for t, c in p.tag_counts.items() if t in HEADING_TAGS) / total
    p_frac = p.tag_counts.get("p", 0) / total
    span_frac = p.tag_counts.get("span", 0) / total

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
    if p_frac >= 0.5 and is_centered and (is_large or is_bold):
        return "chapter-heading", "medium"
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
    "text-indent", "margin-top", "margin-bottom",
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
        lines.append(
            f".{p.class_name}  --  used {p.usage_count}x, mostly on {tag_note}\n"
            f"    guess: {p.likely_role} ({p.role_confidence} confidence)\n"
            f"    style: {prop_note}"
        )
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


def write_mapping_file(profiles: dict, path) -> None:
    """
    Write an editable TOML file: one [[class]] block per class that's
    both a standardize-able role (chapter-heading/body-text) and a
    confidence level worth pre-filling (medium/high). Everything else
    is listed as a comment for awareness, not auto-included.

    Read back by ebook_fix.modules.class_standardize.load_mapping_file
    -- either via `map-css --write-mapping` + a separate `repair
    --class-mapping`, or auto-generated by `repair --class-mapping
    <path-that-doesn't-exist-yet>` (which then stops without applying
    anything, so there's still a review step before it's used).
    """
    ordered = sorted(profiles.values(), key=lambda p: -p.usage_count)
    mappable = [p for p in ordered if p.likely_role in MAPPABLE_ROLES and p.role_confidence in MAPPABLE_CONFIDENCE]
    leftover = [p for p in ordered if p not in mappable]

    lines = [
        "# ebook_fix class-standardization mapping",
        "#",
        "# Review every entry below. Delete a [[class]] block entirely",
        "# to leave that class untouched. Edit `new_name` to change what",
        "# it gets renamed to, or `role` (\"chapter-heading\" / \"body-text\")",
        "# if the guess looks wrong.",
        "#",
        "# Applied by: ebook-fixer repair <book> --class-mapping <this file>",
        "",
    ]

    if not mappable:
        lines += [
            "# No classes met the confidence bar for auto-mapping (medium/high).",
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

    if leftover:
        lines.append("# --- left out (low confidence, or not chapter-heading/body-text) ---")
        for p in leftover:
            dominant_tag, frac = _dominant_tag(p.tag_counts)
            tag_note = f"<{dominant_tag}> {frac:.0%}" if dominant_tag else "-"
            lines.append(f"# {p.class_name}: {p.likely_role} ({p.role_confidence} confidence), used {p.usage_count}x, mostly {tag_note}")
        lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")
