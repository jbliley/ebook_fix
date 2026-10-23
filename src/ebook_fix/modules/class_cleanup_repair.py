"""
ebook_fix.modules.class_cleanup_repair

Removes class names that point at nothing. Calibre conversions name every
style `calibre`, `calibre1`, `calibre2` and so on, and after the earlier
repairs have stripped the styling a reading app should decide for itself
(line height, indents, margins, fonts), a lot of those names are left on
paragraphs, divs and spans with no rule behind them. In "The Numbers Killer"
that is `class="calibre"` on the body, `class="calibre1"` on every wrapper
div, and a class on thousands of spans.

What this does:
- A class name is dropped from an element when that name does not appear
  anywhere in the book's CSS (stylesheet files and embedded <style> blocks),
  so removing it cannot change how anything looks. An element left with an
  empty class attribute loses the attribute.
- Names that ebook_fix itself uses (anything containing "ebookfix") are
  never touched.
- If the CSS has an attribute selector on class (`[class~=...]`), nothing
  is changed, because a rule could reach an element without naming its
  class.

Spans that end up with no attributes at all are then removed by Span
Cleanup, which runs right after this module.

Skipped on fixed-layout books. A second run finds nothing to remove.
"""
from __future__ import annotations

import re

from ebook_fix.config import ClassCleanupConfig
from ebook_fix.modules.span_cleanup_repair import _CLASS_TOKEN, _css_text, _root
from ebook_fix.report import Report


def _protected(name: str) -> bool:
    return "ebookfix" in name.lower()


def _scan(book, write: bool):
    css = _css_text(book)
    if re.search(r"\[\s*class\b", css, re.IGNORECASE):
        return []
    mentioned = set(_CLASS_TOKEN.findall(css))
    results = []
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        root = _root(doc)
        removed_names = set()
        n_elements = 0
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            raw = el.get("class")
            if raw is None:
                continue
            names = raw.split()
            keep = [c for c in names if c in mentioned or _protected(c)]
            if len(keep) == len(names) and names:
                continue
            removed_names.update(c for c in names if c not in keep)
            n_elements += 1
            if write:
                if keep:
                    el.set("class", " ".join(keep))
                else:
                    del el.attrib["class"]
        if n_elements:
            results.append((chapter.href, n_elements, removed_names))
            if write:
                chapter.modified = True
    return results


class ClassCleanupRepair:
    name = "Class Cleanup"

    def __init__(self, config: ClassCleanupConfig | None = None):
        self.config = config or ClassCleanupConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for href, n, names in _scan(book, write=False):
            report.add(href, "Class names with no style behind them",
                       f"{n} element(s) carry {len(names)} unused class name(s) that will be removed")
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        results = _scan(book, write=True)
        for href, n, names in results:
            report.add(href, "Unused class names removed",
                       f"{n} element(s) lost {len(names)} unused class name(s)")
        if results and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report
