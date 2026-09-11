"""
ebook_fix.modules.color_strip

Strips only the `confident` hardcoded text `color` declarations
ebook_fix.color's analysis pass identified -- body-text-role classes,
the `body` element selector itself, and inline color set directly on
a main-narrative <p> (or a <font color> tag directly wrapping one).
Built for `ebook-fixer auto-fix` (see engine.py), which is meant to
run unattended, but color is no longer treated as unambiguous
wherever it appears: a pull-quote, a "this character always talks in
blue" convention, or a stylized initial are real, intentional choices
this module now leaves alone. See ebook_fix.color's module docstring
for the full confident/review split and the reasoning behind it.

Everything ebook_fix.color put in its `review` bucket is reported,
never touched here -- see engine.py's "[Possible Decorative Color --
Manual Review]" section and gui/analysis_view.py's manual-review
list, the same pattern already used for dangling paragraph endings
and possessive candidates.

Deliberately narrower than ebook_fix.css.THEME_FIGHTING_PROPERTIES:
only the `color` property itself. `background`/`background-color`,
`font-family`, and `font-size` are left alone here -- those are a
different kind of decision (a background image, a deliberate serif
choice) than a hardcoded text color actively fighting a reader's
night mode. Any of those can still be cleaned up on a per-class basis
through the normal reviewed class-mapping path (the "theme-neutral"
role in ebook_fix.modules.class_standardize) if that's ever wanted.

Re-scans the book's raw CSS/element text itself with ebook_fix.color's
own classification helpers rather than trusting the live `element`
references on analysis's `confident` findings to still be the right
things to touch -- consistent with how ellipsis_repair.py and
apostrophe_repair.py already recompute fresh against current state
rather than trusting an analysis-time snapshot. In practice this also
makes repair idempotent almost for free: a second pass finds nothing
left to reclassify as confident, since the first pass already removed
those declarations.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from lxml import etree

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE
from ebook_fix.report import Report
from ebook_fix.color import (
    analyze_book_color,
    is_confident_selector_group,
    is_confident_paragraph_context,
    COLOR_DECLARATION_RE,
)


class ColorStripRepair:
    name = "Color Strip"

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        color = analysis.color if analysis is not None else analyze_book_color(book)
        for finding in color.confident:
            report.add(finding.href, "Hardcoded text color found", finding.context)
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        color = analysis.color if analysis is not None else analyze_book_color(book)
        body_text_classes = color.body_text_classes
        main_hrefs = color.main_hrefs

        report = Report(self.name)
        changed_anything = False
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent

        # 1. External CSS files
        contents = read_book_css(book)
        new_files = getattr(book, "new_files", None) or {}
        for res in getattr(book, "css", []) or []:
            zpath = str(base / res.href)
            # read_book_css always re-reads the original archive, so a
            # CSS file another pass (or this same module, last pass)
            # already rewrote into book.new_files would otherwise look
            # untouched again here, and the same declarations would be
            # "removed" every pass forever -- prefer the live override
            # if one exists, same idea as working from a chapter's live
            # document instead of re-parsing the source archive.
            if zpath in new_files:
                text = new_files[zpath].decode("utf-8", errors="replace")
            else:
                text = contents.get(res.href)
            if not text:
                continue
            new_text, count = self._strip_css_text(text, body_text_classes)
            if count:
                book.new_files[zpath] = new_text.encode("utf-8")
                changed_anything = True
                report.add(
                    res.href,
                    "Hardcoded text color removed",
                    f"{count} declaration(s) removed from {res.href}.",
                )

        # 2. Embedded <style> blocks, inline style="" attributes, and
        #    legacy <font color> tags, per chapter.
        for chapter in book.chapters:
            root = self._root(chapter.document)
            if root is None:
                continue
            href = getattr(chapter, "href", "")
            in_main_zone = main_hrefs is None or href in main_hrefs
            changed = False
            chapter_count = 0

            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                local = etree.QName(el).localname.lower()

                if local == "style":
                    new_text, count = self._strip_css_text(el.text or "", body_text_classes)
                    if count:
                        el.text = new_text
                        changed = True
                        chapter_count += count
                    continue

                if in_main_zone and is_confident_paragraph_context(el):
                    style_val = el.get("style")
                    if style_val:
                        stripped, count = self._strip_inline_style(style_val)
                        if count:
                            if stripped:
                                el.set("style", stripped)
                            else:
                                del el.attrib["style"]
                            changed = True
                            chapter_count += count

                    if local == "font" and el.get("color"):
                        del el.attrib["color"]
                        changed = True
                        chapter_count += 1

            if changed:
                chapter.modified = True
                changed_anything = True
                report.add(
                    chapter.href,
                    "Hardcoded text color removed",
                    f"{chapter_count} declaration(s) removed from {chapter.href}.",
                )

        if changed_anything:
            book.mark_modified()

        return report

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _root(self, tree):
        if tree is None:
            return None
        return tree if hasattr(tree, "iter") else tree.getroot()

    def _strip_css_text(self, text: str, body_text_classes: frozenset):
        """Remove any `color: ...;` declaration, but only from a rule
        whose selector group ebook_fix.color considers confident (see
        is_confident_selector_group) -- leaving every other rule, and
        every other declaration within a stripped rule, untouched.
        Comments are stripped in the process, same as css.py's own
        scan does -- a side effect on any block this actually changes.
        Returns (new_text, count_removed)."""
        if not text:
            return text, 0
        cleaned = COMMENT_RE.sub("", text)
        count = 0

        def strip_rule(m):
            nonlocal count
            selector = m.group(1)
            body = m.group(2)
            if not is_confident_selector_group(selector.strip(), body_text_classes):
                return m.group(0)
            new_body, n = COLOR_DECLARATION_RE.subn("", body)
            if not n:
                return m.group(0)
            count += n
            return f"{selector}{{{new_body}}}"

        new_text = RULE_RE.sub(strip_rule, cleaned)
        return new_text, count

    def _strip_inline_style(self, style_value: str):
        """Remove any `color: ...;` from an inline style="" value,
        keeping everything else exactly as declared. Returns
        (new_value, count_removed)."""
        new_value, n = COLOR_DECLARATION_RE.subn("", style_value)
        if not n:
            return style_value, 0
        cleaned = re.sub(r'\s*;\s*;+', ';', new_value).strip().strip(";").strip()
        return cleaned, n
