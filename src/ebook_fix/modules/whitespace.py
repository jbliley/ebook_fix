"""
ebook_fix.modules.whitespace

Normalizes whitespace while preserving the DOM structure.

Goals
-----
- Remove leading/trailing whitespace from text nodes.
- Collapse repeated whitespace into a single space.
- Remove spaces before punctuation.
- Preserve required spaces around inline elements.
- Leave <pre>, <code>, SVG, MathML, etc. untouched.
"""

from __future__ import annotations
import re
from ebook_fix.report import Report


_SKIP_TAGS = {
    "pre",
    "code",
    "svg",
    "math",
}

_COLLAPSE_RE = re.compile(r"[ \t\r\n]+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_MISSING_AFTER_PUNCT = re.compile(r"([,.;:!?])([A-Za-z])")


class WhitespaceRepair:
    name = "Whitespace Normalizer"
    def analyze(self, book, analysis=None):
        report = Report(self.name)
        for chapter in book.chapters:
            if chapter.document is None:
                continue
            changes = self._count_issues(chapter.document)
            for issue, count in changes.items():
                for _ in range(count):
                    report.add(chapter.href, issue)
        return report

    def repair(self, book, analysis=None):
        for chapter in book.chapters:
            if chapter.document is None:
                continue
            changed = self._normalize_tree(chapter.document)
            if changed:
                chapter.modified = True
                book.mark_modified()

    # -------------------------------------------------

    def _normalize_tree(self, root):
        changed = False
        for element in root.iter():
            if self._skip(element):
                continue
            if element.text is not None:
                new = self._normalize_text(element.text)
                if new != element.text:
                    element.text = new
                    changed = True
            if element.tail is not None:
                new = self._normalize_tail(element.tail)
                if new != element.tail:
                    element.tail = new
                    changed = True
        return changed

    def _normalize_text(self, text):
        text = _COLLAPSE_RE.sub(" ", text)
        text = text.strip()
        text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
        text = _MISSING_AFTER_PUNCT.sub(r"\1 \2", text)
        return text

    def _normalize_tail(self, text):
        text = _COLLAPSE_RE.sub(" ", text)
        text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
        text = _MISSING_AFTER_PUNCT.sub(r"\1 \2", text)
        return text

    def _skip(self, element):
        tag = getattr(element, "tag", None)

        if not isinstance(tag, str):
            return True

        if tag.startswith("{"):
            tag = tag.split("}", 1)[1]

        return tag.lower() in _SKIP_TAGS

    def _count_issues(self, root):
        counts = {
            "Leading/trailing whitespace": 0,
            "Repeated whitespace": 0,
            "Space before punctuation": 0,
            "Missing space after punctuation": 0,
        }

        for element in root.iter():
            if self._skip(element):
                continue
            for text in (element.text, element.tail):
                if not text:
                    continue
                if text != text.strip():
                    counts["Leading/trailing whitespace"] += 1
                if _COLLAPSE_RE.sub(" ", text) != text:
                    counts["Repeated whitespace"] += 1
                if _SPACE_BEFORE_PUNCT.search(text):
                    counts["Space before punctuation"] += 1
                if _MISSING_AFTER_PUNCT.search(text):
                    counts["Missing space after punctuation"] += 1
        return counts