"""
ebook_fix.modules.body_variant_repair

Gives a stray run of body paragraphs the same look as the rest of the book.

Found in "The Numbers Killer": nearly every body paragraph is
`<p class="calibre17">` (line height 1.2), but eight paragraphs in Chapter
One are `<p class="calibre19">`, a copy of the same class with a line height
of 150%. In a reader those eight paragraphs are visibly more spread out than
the ones around them, with no reason for it.

What this does: when a class is used on paragraphs and is the same as the
book's dominant body-text class in every way except its line height and its
top and bottom margins, those paragraphs are switched to the body class.

It only acts when ALL of these are true:
- The body class is the one the class analysis identifies with high
  confidence as the main text, and has a plain class rule.
- The other class is a plain class rule with no `margin` shorthand, and
  every property other than line-height, margin-top and margin-bottom is
  the same as the body class (size, style, weight; and indent, letter and
  word spacing and justify / left alignment unless Reader Settings is off).
- The other class is used on at most a quarter as many paragraphs as the
  body class, so a book that is deliberately double spaced, or a letter or
  verse set in its own look, is never changed.
- The other class is only ever used on `<p>` elements, and only paragraphs
  with text are switched (a blank spacer paragraph keeps its own spacing).
The old class rule is left in the stylesheet, unused. Runs before Paragraph
Spacing, so the switched paragraphs are treated like the others. A second run
finds nothing to switch.
"""
from __future__ import annotations

import re

from ebook_fix.class_map import build_class_profiles
from ebook_fix.config import BodyVariantConfig
from ebook_fix.report import Report

_IGNORED = frozenset(("line-height", "margin-top", "margin-bottom"))
# Reader Settings strips these from the body class, so once it is on a class
# that differs only in them is the same class (and switching it now is what a
# second repair run would otherwise do).
_READER_IGNORED = frozenset((
    "text-indent", "letter-spacing", "word-spacing", "font-size", "font-family", "color",
    "background", "background-color",
))
# A zero side margin or padding says nothing; Reader Settings removes it.
_ZERO = frozenset(("0", "0%", "0em", "0px", "0pt", "0rem", "auto"))
_SIDE = frozenset(("margin-left", "margin-right", "padding-left", "padding-right"))
_FLEXIBLE_ALIGN = frozenset(("justify", "left", "start"))
MAX_SHARE = 0.25
# A variant whose top or bottom margin differs from the body class by more
# than this is spaced on purpose (the opening paragraph of a bonus story set
# 8em down the page, The Mist), not a stray copy, and is left alone.
MAX_MARGIN_DIFF_EM = 0.75
_LENGTH = re.compile(r"^(-?\d*\.?\d+)\s*(em|rem|ex|px|pt|%)?$")


def _margin_em(value) -> float | None:
    """A margin as ems, or None when it cannot be read (then it counts as
    deliberate: the safe answer is to leave the paragraph as it is)."""
    m = _LENGTH.match((value or "0").strip())
    if not m:
        return None
    number, unit = float(m.group(1)), m.group(2) or ""
    if number == 0:
        return 0.0
    return {"em": number, "rem": number, "ex": number / 2, "px": number / 16,
            "pt": number / 12, "%": number / 100}.get(unit)


def _spacing_is_deliberate(props: dict, body_props: dict) -> bool:
    for key in ("margin-top", "margin-bottom"):
        a, b = _margin_em(props.get(key)), _margin_em(body_props.get(key))
        if a is None or b is None or abs(a - b) > MAX_MARGIN_DIFF_EM:
            return True
    return False


def _props(profile) -> dict:
    return {k.lower().strip(): " ".join(str(v).lower().split()) for k, v in profile.properties.items()}


def _comparable(props: dict, reader_settings: bool) -> dict:
    ignored = _IGNORED | _READER_IGNORED if reader_settings else _IGNORED
    out = {k: v for k, v in props.items() if k not in ignored}
    if reader_settings and out.get("text-align") in _FLEXIBLE_ALIGN:
        del out["text-align"]
    if reader_settings:
        for k in _SIDE:
            if out.get(k) in _ZERO:
                del out[k]
    return out


def find_body_variants(book, analysis=None, reader_settings: bool = True) -> list:
    """[(variant_class, body_class, variant_usage, body_usage)]."""
    chapters = getattr(analysis, "chapters", None) if analysis is not None else None
    frontmatter = getattr(analysis, "frontmatter", None) if analysis is not None else None
    profiles = build_class_profiles(book, chapter_summary=chapters, frontmatter_summary=frontmatter)
    bodies = [p for p in profiles.values() if p.likely_role == "body-text" and p.role_confidence == "high"]
    if len(bodies) != 1:
        return []
    body = bodies[0]
    body_props = _props(body)
    if not body_props or "margin" in body_props:
        return []
    base = _comparable(body_props, reader_settings)
    found = []
    for profile in profiles.values():
        if profile is body or not profile.properties:
            continue
        props = _props(profile)
        if "margin" in props:
            continue
        if _comparable(props, reader_settings) != base:
            continue
        if props == body_props:
            continue
        if _spacing_is_deliberate(props, body_props):
            continue
        if set(profile.tag_counts) - {"p"}:
            continue
        if profile.usage_count > body.usage_count * MAX_SHARE:
            continue
        found.append((profile.class_name, body.class_name, profile.usage_count, body.usage_count))
    return found


def _variant_paragraphs(book, variant):
    """[(chapter, element)] for each paragraph that is only the variant class
    and holds text. A blank spacer paragraph is not body text; its own spacing
    is what keeps the gap it was put there for."""
    found = []
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        for el in doc.iter("{*}p"):
            if (el.get("class") or "").split() != [variant]:
                continue
            if not "".join(el.itertext()).replace("\u00a0", " ").strip():
                continue
            found.append((chapter, el))
    return found


class BodyVariantRepair:
    name = "Body Paragraph Look"

    def __init__(self, config: BodyVariantConfig | None = None, reader_settings: bool = True):
        self.config = config or BodyVariantConfig()
        # True while Reader Settings is on (it removes indent and alignment
        # from the body class, so those no longer tell a variant apart).
        self.reader_settings = reader_settings

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for variant, body, _, _ in find_body_variants(book, analysis, self.reader_settings):
            n = len(_variant_paragraphs(book, variant))
            if n:
                report.add("stylesheet", "Body paragraphs with a different look", self._describe(variant, body, n, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        changed = set()
        for variant, body, _, _ in find_body_variants(book, analysis, self.reader_settings):
            switched = 0
            for chapter, el in _variant_paragraphs(book, variant):
                el.set("class", body)
                chapter.modified = True
                changed.add(chapter.href)
                switched += 1
            if switched:
                report.add("stylesheet", "Body paragraphs with a different look fixed", self._describe(variant, body, switched, will=False))
        if changed and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report

    @staticmethod
    def _describe(variant, body, n, will):
        return (
            f"{n} paragraph(s) use '.{variant}', a copy of the body class '.{body}' that differs only in "
            f"line height or spacing; {'they will be' if will else 'they were'} switched to '.{body}'"
        )
