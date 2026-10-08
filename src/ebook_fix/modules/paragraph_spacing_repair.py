"""
ebook_fix.modules.paragraph_spacing_repair

Removes the gap between body paragraphs when the paragraphs are ALSO
indented. Found in "Pilgrimage to Hell": every body paragraph is
`<p class="calibre1">`, and that class has `text-indent: 1.5em` AND
`margin-top: 1em` (plus `margin-bottom: 0.25em`), so a reader draws an
indent and a blank gap between every pair of paragraphs. There are no
<br/> tags involved (Stray Line Break Removal finds nothing to do); the
gap is pure stylesheet spacing, which is why it survived repair.

An indent and a gap both say "new paragraph", and books normally use one
or the other. When the book's main body-text class has both, this module
sets that class's top and bottom margins to 0 and leaves the indent. A
book that is indented with no gap, or gapped with no indent (block style),
is not touched.

Careful about what it will not change:

- Only the book's dominant body-text class (the one the class analysis
  identifies with high confidence as the main text), never headings,
  quotes or any other class.
- Only a class declared with longhand margin-top / margin-bottom. A `margin`
  shorthand is left alone rather than guessed at.
- Only rules whose selectors are the class alone (`.calibre1` or
  `p.calibre1`), so a rule shared with other selectors is never edited.
- Space around chapter headers is kept exactly as it was. Setting the
  margin to 0 would also remove the gap under a chapter title (the first
  paragraph after a heading takes its top margin), which Jacob's rule says
  must not change, so rules are added that give the paragraph right after a
  heading, and a chapter title written as a paragraph, their original top
  margin back.

Works on external stylesheets and on embedded <style> blocks. Re-running it
finds the class already has no gap and does nothing.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from ebook_fix.class_map import build_class_profiles
from ebook_fix.config import ParagraphSpacingConfig
from ebook_fix.css import read_book_css
from ebook_fix.report import Report

# A gap at least this big between paragraphs counts as a real gap. (Where
# two margins meet the larger one wins, so the bigger of top and bottom is
# what a reader sees.)
MIN_GAP_EM = 0.5
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_LENGTH = re.compile(r"^\s*(-?\d*\.?\d+)\s*(em|rem|px|pt)?\s*$", re.IGNORECASE)
_HEADING_SELECTORS = ", ".join(f"h{n} + {{sel}}" for n in range(1, 7))


def _to_em(value):
    """A CSS length as ems, or None if it is not a plain length."""
    m = _LENGTH.match(value or "")
    if not m:
        return None
    number, unit = float(m.group(1)), (m.group(2) or "").lower()
    if unit in ("em", "rem"):
        return number
    if unit == "px":
        return number / 16
    if unit == "pt":
        return number / 12
    return 0.0 if number == 0 else None   # unitless non-zero: not a length


def find_spaced_indented_body_classes(book, analysis=None) -> list:
    """[{class_name, indent, margin_top, margin_bottom, usage}] for each
    dominant body-text class that has both an indent and a gap."""
    chapters = getattr(analysis, "chapters", None) if analysis is not None else None
    frontmatter = getattr(analysis, "frontmatter", None) if analysis is not None else None
    profiles = build_class_profiles(book, chapter_summary=chapters, frontmatter_summary=frontmatter)
    found = []
    for profile in profiles.values():
        if profile.likely_role != "body-text" or profile.role_confidence != "high":
            continue
        props = {k.lower(): v for k, v in profile.properties.items()}
        if "margin" in props:        # shorthand: not guessed at
            continue
        indent = _to_em(props.get("text-indent", ""))
        top = _to_em(props.get("margin-top", ""))
        bottom = _to_em(props.get("margin-bottom", ""))
        if not indent or top is None and bottom is None:
            continue
        gap = max(top or 0.0, bottom or 0.0)
        if gap >= MIN_GAP_EM:
            found.append({
                "class_name": profile.class_name,
                "indent": props.get("text-indent", ""),
                "margin_top": props.get("margin-top", ""),
                "margin_bottom": props.get("margin-bottom", ""),
                "usage": profile.usage_count,
            })
    return found


def _rewrite_css(css_text: str, class_name: str, original_top: str):
    """(new_text, rules_changed). Zeroes margin-top/margin-bottom in rules
    whose selectors are all the class alone, then (if anything changed)
    appends rules restoring the original top margin right after a heading
    and for a chapter title written as a paragraph."""
    selectors_ok = {f".{class_name}", f"p.{class_name}"}
    changed = 0

    def sub(match):
        nonlocal changed
        selectors = [s.strip() for s in match.group(1).split(",")]
        if not selectors or not all(s in selectors_ok for s in selectors):
            return match.group(0)
        body = match.group(2)
        new_body = re.sub(r"(margin-(?:top|bottom)\s*:\s*)[^;}]+", r"\g<1>0", body, flags=re.IGNORECASE)
        if new_body == body:
            return match.group(0)
        changed += 1
        return f"{match.group(1)}{{{new_body}}}"

    new_text = _RULE.sub(sub, css_text)
    if changed:
        sel = f".{class_name}"
        keep = (
            f"\n/* ebook_fix: the gap between indented body paragraphs was removed; the space\n"
            f"   under chapter headers and above chapter titles is kept as it was. */\n"
            f"{_HEADING_SELECTORS.format(sel=sel)}, [data-ebookfix-chapter] + {sel}, {sel}[data-ebookfix-chapter]"
            f" {{ margin-top: {original_top or '1em'}; }}\n"
        )
        new_text = new_text.rstrip("\n") + "\n" + keep
    return new_text, changed


class ParagraphSpacingRepair:
    name = "Paragraph Spacing"

    def __init__(self, config: ParagraphSpacingConfig | None = None):
        self.config = config or ParagraphSpacingConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for item in find_spaced_indented_body_classes(book, analysis):
            report.add("stylesheet", "Gap between indented paragraphs", self._describe(item, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        for item in find_spaced_indented_body_classes(book, analysis):
            cls, top = item["class_name"], item["margin_top"]
            total = 0

            contents = read_book_css(book)
            new_files = getattr(book, "new_files", None) or {}
            for res in getattr(book, "css", []) or []:
                zpath = str(base / res.href)
                text = new_files[zpath].decode("utf-8", "replace") if zpath in new_files else contents.get(res.href)
                if not text:
                    continue
                new_text, n = _rewrite_css(text, cls, top)
                if n:
                    book.new_files[zpath] = new_text.encode("utf-8")
                    total += n

            for chapter in book.chapters:
                doc = chapter.document
                if doc is None:
                    continue
                touched = False
                for style in doc.iter("{*}style"):
                    if style.text:
                        new_text, n = _rewrite_css(style.text, cls, top)
                        if n:
                            style.text = new_text
                            total += n
                            touched = True
                if touched:
                    chapter.modified = True

            if total:
                report.add("stylesheet", "Gap between indented paragraphs removed", self._describe(item, will=False))
        if report.count and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report

    @staticmethod
    def _describe(item, will):
        return (
            f"'.{item['class_name']}' ({item['usage']} paragraphs) has a {item['indent']} indent and a "
            f"{item['margin_top'] or item['margin_bottom']} gap between paragraphs; "
            f"the gap {'will be' if will else 'was'} removed and the indent kept"
        )
