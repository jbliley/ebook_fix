"""
ebook_fix.modules.paragraph

Detects and repairs two very common issues in poorly-converted EPUBs:

1. Empty paragraphs - leftover <p></p> tags with no real content,
   usually artifacts of a PDF or HTML conversion.
2. Mid-sentence paragraph splits - a paragraph that was cut off
   before it reached the end of a sentence, with the rest of the
   sentence sitting in the *next* paragraph. This is a classic
   symptom of eBooks generated from raw page-by-page PDF text.
"""

from __future__ import annotations
from ebook_fix.report import Report

SENTENCE_ENDINGS = ('.', '!', '?', '"', "'", ')', '\u201d', '\u2019')


class ParagraphRepair:
    name = "Paragraph Repair"

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book):
        report = Report(self.name)
        for chapter in book.chapters:
            paragraphs = self._paragraphs(chapter)
            for p in paragraphs:
                if self._is_empty(p):
                    report.add(chapter.href, "Empty paragraph")

            for first, second in zip(paragraphs, paragraphs[1:]):
                if self._is_empty(first) or self._is_empty(second):
                    continue
                if self._looks_mid_sentence(first, second):
                    report.add(
                        chapter.href,
                        "Paragraph appears to be split mid-sentence",
                    )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book):
        for chapter in book.chapters:
            changed = False
            paragraphs = self._paragraphs(chapter)

            # Merge mid-sentence splits first, working backwards so
            # removing an element doesn't shift indices we still need.
            i = len(paragraphs) - 2
            while i >= 0:
                first, second = paragraphs[i], paragraphs[i + 1]
                if (
                    not self._is_empty(first)
                    and not self._is_empty(second)
                    and self._looks_mid_sentence(first, second)
                ):
                    self._merge(first, second)
                    paragraphs.pop(i + 1)
                    changed = True
                i -= 1

            # Then drop any paragraphs that are still empty.
            for p in list(paragraphs):
                if self._is_empty(p):
                    parent = p.getparent()
                    if parent is not None:
                        parent.remove(p)
                        changed = True

            if changed:
                chapter.modified = True
                book.mark_modified()

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _paragraphs(self, chapter):
        if chapter.document is None:
            return []
        return chapter.document.findall(".//{*}p")

    def _text(self, element):
        return "".join(element.itertext()).strip()

    def _is_empty(self, element):
        return self._text(element) == "" and len(element) == 0

    def _looks_mid_sentence(self, first, second):
        first_text = self._text(first)
        second_text = self._text(second)
        if not first_text or not second_text:
            return False
        if first_text.endswith(SENTENCE_ENDINGS):
            return False
        return second_text[0].islower()

    def _merge(self, first, second):
        """Append second paragraph's content onto first, then drop second."""
        if first.text is None:
            first.text = ""
        if len(first) == 0:
            first.text = (first.text or "") + " " + (second.text or "").lstrip()
        else:
            last_child = first[-1]
            last_child.tail = (last_child.tail or "") + " " + (second.text or "").lstrip()

        for child in list(second):
            first.append(child)

        parent = second.getparent()
        if parent is not None:
            parent.remove(second)
