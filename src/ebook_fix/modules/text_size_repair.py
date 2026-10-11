"""
ebook_fix.modules.text_size_repair

Fixes text that shows up far too large (or small) in some reading apps
because the book sizes its text in two layers that only work together.

Found in "The Loner: Crossfire" (a Calibre conversion from MOBI): the
page body is set to `font-size: 0.41667em`, and every piece of text sits
in a span set to `font-size: 1.8em`. Multiplied, that is 0.75em, which
looks fine in a reader that follows the CSS exactly. Many reading apps
ignore the size set on the page body (they use their own setting for it),
so those apps show only the 1.8em: text almost twice the normal size.

What this does: it adds a few lines to the end of the stylesheet that
set the body size to 1em and divide every other class's size by the inner
factor, so the text comes out at 1em in every reader, and the relative
sizes (a bigger title, a smaller note) stay in proportion.

It only acts when ALL of these are true:
- The body text's size comes from a body-level factor outside 0.9em to
  1.1em AND an inner factor outside 0.77em to 1.3em, which roughly cancel
  (the combined size is between 0.5em and 2em).
- Every size involved is in em, rem or %, set by a plain class rule in a
  stylesheet file (no <style> blocks, no inline font sizes on the text,
  no font-size on tag selectors other than headings).
- The class that sets the body size is used on the body only.
When in doubt it does nothing. Re-running finds the sizes already fixed
and does nothing.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from ebook_fix.config import TextSizeConfig
from ebook_fix.modules.paragraph_indent_repair import _book_rules
from ebook_fix.report import Report

BODY_LOW, BODY_HIGH = 0.9, 1.1
INNER_LOW, INNER_HIGH = 0.77, 1.3
COMBINED_LOW, COMBINED_HIGH = 0.5, 2.0

_SIZE = re.compile(r"^\s*([\d.]+)\s*(em|rem|%)\s*$", re.IGNORECASE)
_CLASS_ONLY = re.compile(r"^(?:[a-zA-Z][\w-]*)?\.([\w-]+)$")
_HEADING = re.compile(r"^h[1-6]$", re.IGNORECASE)


def _factor(value):
    m = _SIZE.match(value or "")
    if not m:
        return None
    number, unit = float(m.group(1)), m.group(2).lower()
    return number / 100 if unit == "%" else number


def _local(el) -> str:
    tag = el.tag
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _class_sizes(rules):
    """({class: factor}, ok). ok is False when something sizes text in a
    way this does not handle (tag selectors, complex selectors, odd
    units)."""
    sizes = {}
    for selector, props in rules:
        if "font-size" not in props:
            continue
        value = props["font-size"][0]
        m = _CLASS_ONLY.match(selector.strip())
        if m:
            f = _factor(value)
            if f is None:
                return {}, False
            sizes[m.group(1)] = f
            continue
        sel = selector.strip().lower()
        if _HEADING.match(sel):
            continue
        return {}, False
    return sizes, True


def find_text_size_problem(book):
    """None, or {body_classes, body_factor, inner_factor, combined,
    overrides: {class: new_em}} when the text size depends on a
    body-level factor cancelling an inner one."""
    rules, sources = _book_rules(book)
    if any(kind == "style" for kind, _w, _t in sources):
        for kind, _w, text in sources:
            if kind == "style" and "font-size" in text.lower():
                return None
    sizes, ok = _class_sizes(rules)
    if not ok or not sizes:
        return None

    chains = Counter()
    body_classes = set()
    other_use = Counter()
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None:
            continue
        for el in doc.iter():
            if not isinstance(el.tag, str):
                continue
            if "font-size" in (el.get("style") or "").lower() and (el.text or "").strip():
                return None
            if _local(el) == "body":
                body_classes.update(c for c in (el.get("class") or "").split() if c in sizes)
            else:
                for c in (el.get("class") or "").split():
                    if c in sizes:
                        other_use[c] += 1
            text = (el.text or "").strip()
            if not text:
                continue
            body_f = inner_f = 1.0
            node = el
            while node is not None:
                local = _local(node)
                for c in (node.get("class") or "").split():
                    if c in sizes:
                        if local in ("body", "html"):
                            body_f *= sizes[c]
                        else:
                            inner_f *= sizes[c]
                node = node.getparent()
            chains[(round(body_f, 4), round(inner_f, 4))] += len(text)
    if not chains:
        return None
    (b, i), _count = chains.most_common(1)[0]
    combined = b * i
    if BODY_LOW <= b <= BODY_HIGH or INNER_LOW <= i <= INNER_HIGH:
        return None
    if not COMBINED_LOW <= combined <= COMBINED_HIGH:
        return None
    if any(other_use[c] for c in body_classes):
        return None

    overrides = {c: 1.0 for c in body_classes if abs(sizes[c] - 1.0) > 0.001}
    for c, f in sizes.items():
        if c in body_classes or not other_use[c]:
            continue
        new = round(f / i, 3)
        if abs(new - f) > 0.001:
            overrides[c] = new
    if not overrides:
        return None
    return {
        "body_classes": sorted(body_classes),
        "body_factor": b,
        "inner_factor": i,
        "combined": combined,
        "overrides": overrides,
    }


def _fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") + "em"


class TextSizeRepair:
    name = "Text Size"

    def __init__(self, config: TextSizeConfig | None = None):
        self.config = config or TextSizeConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        problem = find_text_size_problem(book)
        if problem:
            report.add("stylesheet", "Text size depends on the page body size", self._describe(problem, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        problem = find_text_size_problem(book)
        if not problem:
            return report

        rules, sources = _book_rules(book)
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        overrides = problem["overrides"]
        written = 0
        for kind, where, text in sources:
            if kind != "css":
                continue
            declared = [c for c in overrides if re.search(rf"\.{re.escape(c)}\s*[{{,]", text)]
            if not declared:
                continue
            block = "\n/* ebook_fix: text size no longer depends on the page body size */\n" + "".join(
                f".{c} {{ font-size: {_fmt(overrides[c])}; }}\n" for c in declared
            )
            book.new_files[str(base / where)] = (text.rstrip("\n") + "\n" + block).encode("utf-8")
            written += 1
        if written:
            if hasattr(book, "mark_modified"):
                book.mark_modified()
            report.add("stylesheet", "Text size fixed", self._describe(problem, will=False))
        return report

    @staticmethod
    def _describe(problem, will):
        return (
            f"The body text is sized by {_fmt(problem['body_factor'])} on the page body times "
            f"{_fmt(problem['inner_factor'])} on the text. Reading apps that ignore the body size "
            f"show it at {_fmt(problem['inner_factor'])}. The sizes "
            f"{'will be' if will else 'were'} reset so the text shows at 1em everywhere."
        )
