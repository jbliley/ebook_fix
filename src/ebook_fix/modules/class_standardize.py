"""
ebook_fix.modules.class_standardize

Applies a confirmed class-role mapping (produced by `map-css
--write-mapping`, then reviewed and edited by a person) to a book:
renames the mapped classes everywhere they appear -- external CSS
files, embedded <style> blocks, class="" attributes -- and replaces
each renamed class's declared properties with a small standardized
rule set for its role, so body text and chapter headings behave the
same way across every book instead of carrying over whatever
font-size/color/margin a conversion tool happened to bake in. The
standardized rules deliberately leave font-family, color, and
font-size undeclared so the reader's own theme controls them.

Deliberately narrow scope:
- Only touches classes present in the mapping file. Anything not
  listed -- including "low confidence" or "unknown" classes the
  analysis flagged -- is left completely alone.
- Fully REPLACES a mapped class's rule body with the standardized set
  below. This is a standardize, not a merge: anything else the
  original rule declared (letter-spacing, a custom font stack, etc.)
  is dropped along with the properties this module exists to remove.
- Chapter breaks are applied as page-break-after on the block right
  before each chapter-heading element, not page-break-before on the
  heading -- see ebook_fix.page_breaks for why, and the same logic
  chapter_markup.py uses for its own default marking.
- Also strips any leftover page-break-before chapter_markup put
  directly on the heading element it detected (chapter_markup itself
  no longer injects that -- see its own docstring -- but this guards
  against a book repaired with an older version of this tool, or any
  other source of a stray page-break-before on a mapped heading).
- Two mapped classes with the same new_name collapse into one class,
  intentionally -- that's how "these two are really the same thing"
  gets expressed in the mapping file.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lxml import etree

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE
from ebook_fix.class_map import SIMPLE_CLASS_SELECTOR_RE
from ebook_fix.report import Report
from ebook_fix.page_breaks import closest_preceding_block, mark_page_break_after

VALID_ROLES = ("chapter-heading", "body-text")

# The properties a standardized class ends up with. Order here is the
# order they're written back out in.
STANDARD_RULES = {
    "body-text": {
        "text-align": "left",
        "text-indent": "1.2em",
        "margin-top": "0",
        "margin-bottom": "0.6em",
    },
    "chapter-heading": {
        "text-align": "center",
        "font-weight": "bold",
        "text-indent": "0",
        "margin-top": "2em",
        "margin-bottom": "1em",
    },
}

# The chapter-break itself is NOT part of the static rule above -- CSS has
# no "the element right before this one" selector, so it can't be expressed
# as a class rule at all. It's applied at repair time as page-break-after
# on the block immediately preceding each chapter-heading element (see
# ebook_fix.page_breaks for the actual walk/marking logic and why
# page-break-after, not page-break-before, is used).

# Stripped from a mapped class's rule (and any inline style="" on an
# element carrying that class) even though they're not part of the
# standardized set above -- these are specifically the properties that
# fight a reader's own theme/font-size/night-mode settings.
STRIP_PROPERTIES = {
    "font-family", "color", "background", "background-color",
    "font-size", "height", "max-height", "min-height", "line-height",
}

# Stripped specifically from a chapter-heading element's own inline
# style (not from body-text elements). This module marks the chapter
# break as page-break-after on the *preceding* block instead -- if the
# heading itself still carries a leftover page-break-before from
# chapter_markup's own injection, both properties end up active on the
# same boundary, which is the "risks a blank page in some reading
# systems" case. Clearing it here is the actual fix for that, not just
# a note about it.
CHAPTER_HEADING_STRIP_PROPERTIES = {"page-break-before", "break-before"}

PROPERTY_RE = re.compile(r'([a-zA-Z-]+)\s*:\s*([^;]+)')


@dataclass
class ClassMappingEntry:
    old_name: str
    role: str
    new_name: str


class MappingError(ValueError):
    """Raised for a malformed or invalid mapping file. The message is
    meant to be shown to a person as-is."""


def load_mapping_file(path) -> list[ClassMappingEntry]:
    """Read and validate a mapping TOML file written by `map-css
    --write-mapping` (and then edited by hand). Raises MappingError on
    anything invalid -- a bad role, a duplicate old_name, an old_name
    that isn't a plausible CSS class token -- rather than silently
    skipping bad entries, since this feeds a rename across the whole
    book and a typo here should stop the run, not do something odd."""
    path = Path(path)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise MappingError(f"Mapping file not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise MappingError(f"Mapping file isn't valid TOML: {exc}")

    raw_entries = data.get("class", [])
    if not isinstance(raw_entries, list):
        raise MappingError("Mapping file's 'class' key must be a list of class entries.")

    entries = []
    seen_old_names = set()
    class_token_re = re.compile(r'^[a-zA-Z_][\w-]*$')

    for i, raw in enumerate(raw_entries, start=1):
        old_name = raw.get("old_name", "")
        role = raw.get("role", "")
        new_name = raw.get("new_name", "")

        if not old_name or not class_token_re.match(old_name):
            raise MappingError(f"class entry #{i}: old_name {old_name!r} isn't a valid CSS class name.")
        if not new_name or not class_token_re.match(new_name):
            raise MappingError(f"class entry #{i}: new_name {new_name!r} isn't a valid CSS class name.")
        if role not in VALID_ROLES:
            raise MappingError(f"class entry #{i}: role {role!r} must be one of {VALID_ROLES}.")
        if old_name in seen_old_names:
            raise MappingError(f"class entry #{i}: old_name {old_name!r} appears more than once in the mapping file.")
        seen_old_names.add(old_name)

        entries.append(ClassMappingEntry(old_name=old_name, role=role, new_name=new_name))

    return entries


def _rule_text(role: str) -> str:
    props = STANDARD_RULES[role]
    return " ".join(f"{k}: {v};" for k, v in props.items())


def _strip_inline_style(style_value: str, extra_strip: frozenset = frozenset()) -> str:
    """Remove any STRIP_PROPERTIES declarations from an inline style=""
    value, keeping everything else. `extra_strip` adds more properties
    to remove on top of that (used for chapter-heading elements, see
    CHAPTER_HEADING_STRIP_PROPERTIES). Declaration order/formatting of
    kept properties is normalized to 'prop: value;', not preserved
    verbatim."""
    to_strip = STRIP_PROPERTIES | extra_strip
    kept = []
    for m in PROPERTY_RE.finditer(style_value):
        prop = m.group(1).strip()
        if prop.lower() in to_strip:
            continue
        val = m.group(2).strip().rstrip(";").strip()
        kept.append(f"{prop}: {val};")
    return " ".join(kept)


class ClassStandardizeRepair:
    name = "Class Standardize"

    def __init__(self, mapping: list[ClassMappingEntry] | None = None):
        self.mapping = mapping or []
        self.by_old_name = {m.old_name: m for m in self.mapping}
        self.chapter_heading_new_names = {
            m.new_name for m in self.mapping if m.role == "chapter-heading"
        }

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        if not self.mapping:
            return report

        for old_name, entry in self.by_old_name.items():
            count = 0
            for chapter in book.chapters:
                root = self._root(chapter.document)
                if root is None:
                    continue
                for el in root.iter():
                    if not isinstance(el.tag, str):
                        continue
                    if old_name in (el.get("class") or "").split():
                        count += 1
            if count:
                report.add(
                    "*",
                    f".{old_name} -> .{entry.new_name} ({entry.role})",
                    f"{count} element(s) will be renamed and restyled to the standard {entry.role} rules.",
                )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        report = Report(self.name)
        if not self.mapping:
            return report

        changed_anything = False
        rename_counts: dict[str, int] = {}
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent

        # 1. External CSS files
        contents = read_book_css(book)
        for res in getattr(book, "css", []) or []:
            text = contents.get(res.href)
            if not text:
                continue
            new_text, changed = self._rewrite_css_text(text)
            if changed:
                zpath = str(base / res.href)
                book.new_files[zpath] = new_text.encode("utf-8")
                changed_anything = True

        # 2. Embedded <style> blocks, class="" attributes, and style=""
        #    attributes, per chapter.
        for chapter in book.chapters:
            root = self._root(chapter.document)
            if root is None:
                continue
            changed = False

            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue

                if etree.QName(el).localname.lower() == "style":
                    new_text, style_changed = self._rewrite_css_text(el.text or "")
                    if style_changed:
                        el.text = new_text
                        changed = True
                    continue

                cls_attr = el.get("class")
                if not cls_attr:
                    continue
                classes = cls_attr.split()
                touched = any(c in self.by_old_name for c in classes)
                if not touched:
                    continue

                for c in classes:
                    if c in self.by_old_name:
                        rename_counts[c] = rename_counts.get(c, 0) + 1

                new_classes = [self.by_old_name[c].new_name if c in self.by_old_name else c for c in classes]
                deduped = list(dict.fromkeys(new_classes))  # two old classes can map to one new name
                if deduped != classes:
                    el.set("class", " ".join(deduped))
                    changed = True

                el_roles = {self.by_old_name[c].role for c in classes if c in self.by_old_name}
                extra_strip = CHAPTER_HEADING_STRIP_PROPERTIES if "chapter-heading" in el_roles else frozenset()

                style_val = el.get("style")
                if style_val:
                    stripped = _strip_inline_style(style_val, extra_strip)
                    if stripped != style_val:
                        if stripped:
                            el.set("style", stripped)
                        else:
                            del el.attrib["style"]
                        changed = True

            if changed:
                chapter.modified = True
                changed_anything = True

            # 3. Mark the chapter break itself: page-break-after on the
            #    block immediately preceding each (now-renamed) chapter
            #    heading, not page-break-before on the heading -- see the
            #    comment above CHAPTER_BREAK_PROPERTIES for why. Runs
            #    after renaming above so class="" already reflects the
            #    new names.
            if self.chapter_heading_new_names:
                for el in root.iter():
                    if not isinstance(el.tag, str):
                        continue
                    classes = (el.get("class") or "").split()
                    if not any(c in self.chapter_heading_new_names for c in classes):
                        continue
                    target = closest_preceding_block(el)
                    if target is None:
                        continue  # first thing in its chapter file -- already a fresh page
                    if mark_page_break_after(target):
                        chapter.modified = True
                        changed_anything = True

        if changed_anything:
            book.mark_modified()

        for old_name, count in rename_counts.items():
            entry = self.by_old_name[old_name]
            report.add(
                "*",
                f".{old_name} -> .{entry.new_name} ({entry.role})",
                f"{count} element(s) renamed and restyled to the standard {entry.role} rules.",
            )

        return report

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _root(self, tree):
        if tree is None:
            return None
        return tree if hasattr(tree, "iter") else tree.getroot()

    def _rewrite_css_text(self, text: str):
        """Rewrite one CSS blob (an external stylesheet or an embedded
        <style> block's contents): any selector that's a simple
        (optionally tag-qualified) reference to a mapped class gets
        renamed and its rule body replaced with the standardized rule
        for its role. A grouped selector mixing mapped and unmapped
        classes (".calibre3, .foo { ... }") is split so the unmapped
        part keeps its original, untouched body. Comments are
        stripped in the process (same as css.py's own scan does) --
        a side effect on any stylesheet or <style> block this method
        actually changes. Returns (new_text, changed)."""
        changed = False
        text = COMMENT_RE.sub("", text)

        def replace_rule(m):
            nonlocal changed
            selector_group = m.group(1)
            body = m.group(2)
            stripped_selector = selector_group.strip()
            if not stripped_selector or stripped_selector.startswith("@"):
                return m.group(0)

            parts = [s.strip() for s in selector_group.split(",")]
            mapped_by_role: dict[str, list[str]] = {}
            unmapped_parts = []

            for part in parts:
                sm = SIMPLE_CLASS_SELECTOR_RE.match(part)
                if sm and sm.group(2) in self.by_old_name:
                    entry = self.by_old_name[sm.group(2)]
                    tag_prefix = sm.group(1) or ""
                    mapped_by_role.setdefault(entry.role, []).append(f"{tag_prefix}.{entry.new_name}")
                else:
                    unmapped_parts.append(part)

            if not mapped_by_role:
                return m.group(0)

            changed = True
            output_rules = []
            for role, selectors in mapped_by_role.items():
                dedup_selectors = list(dict.fromkeys(selectors))
                output_rules.append(f"{', '.join(dedup_selectors)} {{ {_rule_text(role)} }}")
            if unmapped_parts:
                output_rules.append(f"{', '.join(unmapped_parts)} {{{body}}}")

            return "\n" + "\n".join(output_rules)

        new_text = RULE_RE.sub(replace_rule, text)
        return new_text, changed
