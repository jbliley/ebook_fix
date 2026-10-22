"""
ebook_fix.modules.line_height_repair

Removes a tight, hard-coded line height from body text so the reader's own
line spacing setting applies.

Found in "The Numbers Killer" (a Calibre conversion from Amazon): every body
paragraph and every span inside it carries `line-height: 1.2`, which packs
the lines close together and cannot be changed in the reading app. A book
like "Hunter's Moon" declares no line height at all, so the app's setting
(usually roomier) is used, and that is the spacing Jacob prefers. The
project already treats a fixed line height as a theme-fighting property (see
ebook_fix.css), the same way it leaves font family, color and size to the
reader.

What this does: a `line-height` declaration below `min_line_height` (1.4 by
default; written as a plain number, in em, or as a percentage) is removed
from the rule or the inline style that holds it. Nothing is added.

Only body-size text is touched:
- A rule is left alone when any of its selectors names a heading tag
  (h1 to h6), a code or table element, or a class that is used on a heading.
- A rule whose font-size is above 1.1em (a chapter title or other display
  text written as a paragraph) is left alone.
- A line height of `normal`, `inherit`, or one in px or pt (no way to tell
  how tight it is) is left alone, as is any line height at or above the
  minimum.
- Inline styles on headings, and on anything inside a heading, are skipped.

Works on external stylesheets, embedded <style> blocks and inline style
attributes. Re-running finds nothing tight and does nothing.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from ebook_fix.config import LineHeightConfig
from ebook_fix.css import read_book_css
from ebook_fix.report import Report

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL_LH = re.compile(r"^\s*line-height\s*:\s*(.+?)\s*$", re.IGNORECASE | re.DOTALL)
_LENGTH = re.compile(r"^\s*(-?\d*\.?\d+)\s*(em|rem|%)?\s*$", re.IGNORECASE)
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_SKIP_TAGS = frozenset(_HEADINGS) | {"pre", "code", "table", "td", "th", "tr", "caption"}
_SELECTOR_TAG = re.compile(r"(?<![\w.#-])([a-zA-Z][a-zA-Z0-9]*)")
_CLASS = re.compile(r"\.([A-Za-z_][\w-]*)")
MAX_DISPLAY_FONT_EM = 1.1


def _ratio(value: str):
    """A line height as a multiple of the font size, or None if not a plain number."""
    m = _LENGTH.match(value or "")
    if not m:
        return None
    number, unit = float(m.group(1)), (m.group(2) or "").lower()
    return number / 100 if unit == "%" else number


def _font_em(body: str):
    m = re.search(r"font-size\s*:\s*([^;}]+)", body, re.IGNORECASE)
    if not m:
        return None
    v = m.group(1).strip().lower()
    mm = re.match(r"^(\d*\.?\d+)\s*(em|rem|%)$", v)
    if not mm:
        return None
    n = float(mm.group(1))
    return n / 100 if mm.group(2) == "%" else n


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def heading_classes(book) -> set:
    """Classes used on a heading element, or on anything inside one."""
    found = set()
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        for h in doc.iter(*[f"{{*}}{t}" for t in _HEADINGS]):
            for el in h.iter():
                if isinstance(el.tag, str):
                    found.update((el.get("class") or "").split())
    return found


def _strip_tight(body: str, min_ratio: float):
    """(new_body, removed_count) with each tight line-height declaration gone.
    The text around the other declarations, including the whitespace before
    a closing brace, is kept as it was."""
    chunks = body.split(";")
    out = []
    removed = 0
    last = len(chunks) - 1
    for idx, chunk in enumerate(chunks):
        m = _DECL_LH.match(chunk)
        if m:
            r = _ratio(m.group(1))
            if r is not None and r < min_ratio:
                removed += 1
                if idx == last:
                    # the final declaration (no ";" after it): keep only the
                    # whitespace that leads up to the closing brace
                    out.append(chunk[len(chunk.rstrip()):])
                continue
        out.append(chunk)
        if idx != last:
            out.append(";")
    # a dropped declaration in the middle took its own ";" with it
    return "".join(out), removed


def _rewrite_css(css_text: str, skip_classes: set, min_ratio: float):
    """(new_text, declarations_removed)."""
    removed_total = 0

    def sub(match):
        nonlocal removed_total
        selector_text = match.group(1)
        if selector_text.strip().startswith("@"):
            return match.group(0)
        tags = {t.lower() for t in _SELECTOR_TAG.findall(re.sub(r"\[[^\]]*\]|:[\w-]+(\([^)]*\))?", "", selector_text))}
        if tags & _SKIP_TAGS or set(_CLASS.findall(selector_text)) & skip_classes:
            return match.group(0)
        body = match.group(2)
        font = _font_em(body)
        if font is not None and font > MAX_DISPLAY_FONT_EM:
            return match.group(0)
        new_body, n = _strip_tight(body, min_ratio)
        if not n:
            return match.group(0)
        removed_total += n
        return f"{selector_text}{{{new_body}}}"

    return _RULE.sub(sub, css_text), removed_total


class LineHeightRepair:
    name = "Line Height"

    def __init__(self, config: LineHeightConfig | None = None):
        self.config = config or LineHeightConfig()

    # -----------------------------------------------------

    def _scan(self, book, write: bool) -> int:
        min_ratio = float(self.config.min_line_height)
        skip = heading_classes(book)
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        total = 0

        contents = read_book_css(book)
        new_files = getattr(book, "new_files", None)
        for res in getattr(book, "css", []) or []:
            zpath = str(base / res.href)
            text = new_files[zpath].decode("utf-8", "replace") if new_files and zpath in new_files else contents.get(res.href)
            if not text:
                continue
            new_text, n = _rewrite_css(text, skip, min_ratio)
            if n:
                total += n
                if write:
                    book.new_files[zpath] = new_text.encode("utf-8")

        for chapter in book.chapters:
            doc = chapter.document
            if doc is None:
                continue
            touched = False
            for style in doc.iter("{*}style"):
                if style.text:
                    new_text, n = _rewrite_css(style.text, skip, min_ratio)
                    if n:
                        total += n
                        touched = True
                        if write:
                            style.text = new_text
            for el in doc.iter():
                if not isinstance(el.tag, str) or "line-height" not in (el.get("style") or "").lower():
                    continue
                if any(_local(a) in _HEADINGS for a in el.iterancestors()) or _local(el) in _HEADINGS:
                    continue
                new_style, n = _strip_tight(el.get("style"), min_ratio)
                if n:
                    total += n
                    touched = True
                    if write:
                        new_style = new_style.strip()
                        if new_style:
                            el.set("style", new_style)
                        else:
                            del el.attrib["style"]
            if touched and write:
                chapter.modified = True
        return total

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        n = self._scan(book, write=False)
        if n:
            report.add("stylesheet", "Tight fixed line height", self._describe(n, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        n = self._scan(book, write=True)
        if n:
            report.add("stylesheet", "Tight fixed line height removed", self._describe(n, will=False))
            if hasattr(book, "mark_modified"):
                book.mark_modified()
        return report

    def _describe(self, n, will):
        return (
            f"{n} fixed line height(s) below {self.config.min_line_height} on body text "
            f"{'will be' if will else 'were'} removed, so the reading app's own line spacing is used"
        )
