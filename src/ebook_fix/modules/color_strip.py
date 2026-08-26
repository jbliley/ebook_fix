"""
ebook_fix.modules.color_strip

Strips every hardcoded text `color` declaration from a book -- external
CSS files, embedded <style> blocks, and inline style="" attributes --
regardless of which class (if any) carries it, and regardless of
map-css's role/confidence guesses. Built for `ebook-fixer auto-fix`
(see engine.py), which is meant to run unattended: unlike
ClassStandardizeRepair's "theme-neutral" treatment, this isn't limited
to classes a human (or the class-mapping confidence bar) has signed
off on -- every hardcoded text color in the book is treated as
something the reader's own color scheme/night-mode should control
instead. No review gate, by design.

Deliberately narrower than ebook_fix.css.THEME_FIGHTING_PROPERTIES:
only the `color` property itself. `background`/`background-color`,
`font-family`, and `font-size` are left alone here -- those are a
different kind of decision (a background image, a deliberate serif
choice) than a hardcoded text color actively fighting a reader's night
mode, and this module is meant to do exactly the one thing it says on
the tin. Any of those can still be cleaned up on a per-class basis
through the normal reviewed class-mapping path (the "theme-neutral"
role in ebook_fix.modules.class_standardize) if that's ever wanted.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from lxml import etree

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE
from ebook_fix.report import Report

# Matches a `color: ...;` declaration but not `background-color`,
# `border-color`, etc -- the negative lookbehind rejects any match
# where "color" is preceded by a letter or hyphen, i.e. anything
# that's actually a longer property name ending in "-color".
COLOR_DECLARATION_RE = re.compile(r'(?<![a-zA-Z-])color\s*:\s*[^;]+;?', re.IGNORECASE)


class ColorStripRepair:
    name = "Color Strip"

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------
    # No separate analyze() -- like ClassStandardizeRepair, this is a
    # documented exception to the analyze-first/repair-reads-analysis
    # split (see docs). It doesn't need a prior descriptive pass:
    # "color: ..." is unambiguous wherever it appears, so there's
    # nothing for a shared analysis step to usefully record first.

    def repair(self, book, analysis=None):
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
            new_text, count = self._strip_css_text(text)
            if count:
                book.new_files[zpath] = new_text.encode("utf-8")
                changed_anything = True
                report.add(
                    res.href,
                    "Hardcoded text color removed",
                    f"{count} declaration(s) removed from {res.href}.",
                )

        # 2. Embedded <style> blocks and inline style="" attributes,
        #    per chapter.
        for chapter in book.chapters:
            root = self._root(chapter.document)
            if root is None:
                continue
            changed = False
            chapter_count = 0

            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue

                if etree.QName(el).localname.lower() == "style":
                    new_text, count = self._strip_css_text(el.text or "")
                    if count:
                        el.text = new_text
                        changed = True
                        chapter_count += count
                    continue

                style_val = el.get("style")
                if not style_val:
                    continue
                stripped, count = self._strip_inline_style(style_val)
                if count:
                    if stripped:
                        el.set("style", stripped)
                    else:
                        del el.attrib["style"]
                    changed = True
                    chapter_count += count

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

    def _strip_css_text(self, text: str):
        """Remove any `color: ...;` declaration from a CSS blob (an
        external stylesheet or an embedded <style> block's contents),
        leaving every other declaration and the rule structure
        untouched. Comments are stripped in the process, same as
        css.py's own scan does -- a side effect on any block this
        actually changes. Returns (new_text, count_removed)."""
        if not text:
            return text, 0
        cleaned = COMMENT_RE.sub("", text)
        count = 0

        def strip_rule(m):
            nonlocal count
            selector = m.group(1)
            body = m.group(2)
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
