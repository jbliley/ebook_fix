"""
ebook_fix.css

Descriptive CSS analysis. Like typography.py, this module only records
facts -- it doesn't decide whether something is broken. A stylesheet with
200 unused classes isn't necessarily wrong (themes/templates often ship
extra rules), but it's useful information for a repair module (or a
person) deciding how aggressively to clean things up.

This uses a light regex-based scan rather than a full CSS parser, since
adding a CSS-parsing dependency isn't worth it for descriptive analysis.
It's good enough for: selectors, declared classes/ids, !important usage,
duplicate selectors within a file, @font-face declarations, and basic
brace-balance sanity. It will not perfectly handle every edge case of the
CSS spec (deeply nested at-rules, escaped characters in selectors, etc.)
-- that's a deliberate tradeoff for coverage without new dependencies.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from lxml import etree

COMMENT_RE = re.compile(r'/\*.*?\*/', re.S)
FONT_FACE_RE = re.compile(r'@font-face\s*\{([^}]*)\}', re.S)
RULE_RE = re.compile(r'([^{}]+)\{([^{}]*)\}', re.S)
CLASS_SELECTOR_RE = re.compile(r'\.([a-zA-Z_-][\w-]*)')
ID_SELECTOR_RE = re.compile(r'#([a-zA-Z_-][\w-]*)')
FONT_FAMILY_RE = re.compile(r'font-family\s*:\s*([^;]+)')
URL_RE = re.compile(r'url\(\s*[\'"]?([^\'")]+)[\'"]?\s*\)')

SAMPLE_LIMIT = 30

# Repair modules (e.g. chapter_markup) that inject their own <style id="ebookfix-...">
# blocks tag them with this prefix. Used to separate "CSS the source book shipped
# with" from "CSS ebook_fix itself put there" when scanning inline/embedded styles.
INJECTED_STYLE_ID_PREFIX = "ebookfix-"


# ---------------------------------------------------------------------
# Per-file report
# ---------------------------------------------------------------------

@dataclass
class CSSFileReport:
    href: str = ""
    rule_count: int = 0
    important_count: int = 0
    declared_classes: Counter = field(default_factory=Counter)
    declared_ids: Counter = field(default_factory=Counter)
    duplicate_selectors: list = field(default_factory=list)  # [(selector, count), ...]
    font_families_declared: list = field(default_factory=list)
    font_face_srcs: list = field(default_factory=list)
    braces_balanced: bool = True
    read_error: str = ""
    page_break_rule_count: int = 0
    forced_height_count: int = 0


def analyze_css_text(css_text: str, href: str = "") -> CSSFileReport:
    """
    Scan a single stylesheet's raw text and record what's in it.
    Pure function -- does not mutate `css_text`.
    """
    r = CSSFileReport(href=href)
    if not css_text:
        return r

    open_braces = css_text.count("{")
    close_braces = css_text.count("}")
    r.braces_balanced = open_braces == close_braces

    text = COMMENT_RE.sub("", css_text)

    for m in FONT_FACE_RE.finditer(text):
        body = m.group(1)
        for fm in FONT_FAMILY_RE.finditer(body):
            r.font_families_declared.append(fm.group(1).strip().strip('"\''))
        for um in URL_RE.finditer(body):
            r.font_face_srcs.append(um.group(1).strip())

    selector_counts = Counter()
    for m in RULE_RE.finditer(text):
        selector = m.group(1).strip()
        body = m.group(2)
        if not selector or selector.startswith("@"):
            continue
        r.rule_count += 1
        r.important_count += body.count("!important")
        selector_counts[selector] += 1
        for cm in CLASS_SELECTOR_RE.finditer(selector):
            r.declared_classes[cm.group(1)] += 1
        for im in ID_SELECTOR_RE.finditer(selector):
            r.declared_ids[im.group(1)] += 1
        if "page-break" in body or "break-before" in body or "break-after" in body:
            r.page_break_rule_count += 1
        if "height:" in body or "max-height:" in body:
            r.forced_height_count += 1

    r.duplicate_selectors = [(sel, cnt) for sel, cnt in selector_counts.items() if cnt > 1]
    return r


def read_book_css(book) -> dict:
    """
    Reopen the EPUB archive and read the raw text of every declared CSS
    resource. The parser doesn't keep stylesheet content in memory (only
    id/href/media_type), so this re-reads from `book.source` on demand --
    same pattern the writer/repair modules would need for byte-level work.
    Returns {href: text}. Files that can't be read get an empty string
    and the reason is recorded in the corresponding CSSFileReport later.
    """
    contents = {}
    source = getattr(book, "source", None)
    css_resources = getattr(book, "css", []) or []
    if source is None or not css_resources:
        return contents

    base = PurePosixPath(getattr(book, "package_path", "") or "").parent
    try:
        with zipfile.ZipFile(source, "r") as archive:
            for res in css_resources:
                zpath = str(base / res.href)
                try:
                    raw = archive.read(zpath)
                    contents[res.href] = raw.decode("utf-8", errors="replace")
                except KeyError:
                    contents[res.href] = None
    except (OSError, zipfile.BadZipFile):
        pass
    return contents


# ---------------------------------------------------------------------
# Book-wide aggregation / cross-referencing against HTML usage
# ---------------------------------------------------------------------

@dataclass
class BookCSSSummary:
    css_file_count: int = 0
    total_rules: int = 0
    total_important: int = 0

    declared_class_count: int = 0
    declared_id_count: int = 0
    
    # Detect hazardous properties in rule bodies
    page_break_rule_count: int = 0
    forced_height_count: int = 0

    # Count and list of default named "calibre" css classes from calibre conversion
    calibre_class_count: int = 0
    calibre_classes: list = field(default_factory=list)

    # Classes declared in CSS but never used by any chapter's HTML.
    unused_classes: list = field(default_factory=list)
    unused_class_total: int = 0

    # Classes used in HTML but not declared by any stylesheet.
    undeclared_classes: list = field(default_factory=list)  # [(class, usage_count), ...]
    undeclared_class_total: int = 0

    duplicate_selectors_by_file: dict = field(default_factory=dict)  # href -> [(selector, count), ...]
    unbalanced_brace_files: list = field(default_factory=list)
    unreadable_files: list = field(default_factory=list)

    font_families_declared: list = field(default_factory=list)
    font_face_srcs_referenced: list = field(default_factory=list)
    missing_embedded_fonts: list = field(default_factory=list)  # @font-face url()s with no matching font resource
    unused_embedded_fonts: list = field(default_factory=list)   # font resources nothing's @font-face rule ever references

    inline_style_element_count: int = 0

    # --- Inline/embedded CSS the external-stylesheet scan above can't see:
    # <style> blocks written directly into a chapter's <head>, and style=""
    # attributes on individual elements. Repair modules (e.g. chapter_markup)
    # inject page-break CSS this way rather than via a linked stylesheet.
    embedded_style_block_count: int = 0          # <style> elements found in chapter HTML
    injected_style_block_count: int = 0          # ...of those, ones ebook_fix itself added
    embedded_style_rule_count: int = 0
    embedded_page_break_rule_count: int = 0
    embedded_forced_height_count: int = 0

    inline_style_attr_page_break_count: int = 0  # style="" attrs with page-break/break-before/after
    inline_style_attr_forced_height_count: int = 0  # style="" attrs with height/max-height

    chapters_with_page_break_styling: list = field(default_factory=list)  # hrefs


def analyze_inline_chapter_css(book, s: BookCSSSummary) -> None:
    """
    Walk every chapter's DOM (already-parsed lxml tree, no re-reading from
    disk) looking for CSS that never touches a linked stylesheet: <style>
    blocks in the chapter's own <head>, and style="" attributes on
    individual elements. `read_book_css`/`analyze_book_css` above only see
    `book.css` resources, so a module that injects markup-level CSS
    directly (like chapter_markup's page-break rules) is invisible to that
    scan -- this is the counterpart that catches it. Mutates `s` in place.
    """
    for ch in getattr(book, "chapters", []) or []:
        tree = getattr(ch, "document", None)
        if tree is None:
            continue
        root = tree if hasattr(tree, "iter") else tree.getroot()
        if root is None:
            continue
        href = getattr(ch, "href", "")
        flagged = False

        for el in root.iter():
            if not isinstance(el.tag, str):
                continue  # comments, PIs, etc.
            local = etree.QName(el).localname.lower()

            if local == "style":
                s.embedded_style_block_count += 1
                style_id = el.get("id", "") or ""
                if style_id.startswith(INJECTED_STYLE_ID_PREFIX):
                    s.injected_style_block_count += 1
                file_report = analyze_css_text(el.text or "", href=f"{href}#style[{style_id or 'inline'}]")
                s.embedded_style_rule_count += file_report.rule_count
                s.embedded_page_break_rule_count += file_report.page_break_rule_count
                s.embedded_forced_height_count += file_report.forced_height_count
                if file_report.page_break_rule_count:
                    flagged = True
                continue

            style_val = el.get("style")
            if not style_val:
                continue
            low = style_val.lower()
            if "page-break" in low or "break-before" in low or "break-after" in low:
                s.inline_style_attr_page_break_count += 1
                flagged = True
            if "height:" in low or "max-height:" in low:
                s.inline_style_attr_forced_height_count += 1

        if flagged:
            s.chapters_with_page_break_styling.append(href)


def analyze_book_css(book, chapter_reports: list) -> BookCSSSummary:
    """
    Read and analyze every stylesheet in the book, then cross-reference
    declared classes/ids against what's actually used across all chapters
    (chapter_reports = analyzer.ChapterAnalysis list, already scanned once).
    """
    s = BookCSSSummary()
    css_resources = getattr(book, "css", []) or []
    s.css_file_count = len(css_resources)

    contents = read_book_css(book)

    all_declared_classes = Counter()
    all_declared_ids = Counter()

    for res in css_resources:
        text = contents.get(res.href)
        if text is None:
            s.unreadable_files.append(res.href)
            continue
        file_report = analyze_css_text(text, res.href)

        s.total_rules += file_report.rule_count
        s.total_important += file_report.important_count
        s.page_break_rule_count += file_report.page_break_rule_count
        s.forced_height_count += file_report.forced_height_count
        all_declared_classes.update(file_report.declared_classes)
        all_declared_ids.update(file_report.declared_ids)

        if file_report.duplicate_selectors:
            s.duplicate_selectors_by_file[res.href] = file_report.duplicate_selectors
        if not file_report.braces_balanced:
            s.unbalanced_brace_files.append(res.href)

        s.font_families_declared.extend(file_report.font_families_declared)
        s.font_face_srcs_referenced.extend(file_report.font_face_srcs)

    s.declared_class_count = len(all_declared_classes)
    s.declared_id_count = len(all_declared_ids)

    # --- Cross-reference against actual HTML usage ---
    used_classes = Counter()
    used_ids = set()
    inline_style_total = 0
    for ch in chapter_reports:
        used_classes.update(getattr(ch, "css_classes", Counter()))
        used_ids.update(getattr(ch, "ids", []))
        inline_style_total += getattr(ch, "inline_style_count", 0)
    s.inline_style_element_count = inline_style_total

    unused = sorted(set(all_declared_classes) - set(used_classes))
    s.unused_class_total = len(unused)
    s.unused_classes = unused[:SAMPLE_LIMIT]

    undeclared = sorted(
        ((cls, cnt) for cls, cnt in used_classes.items() if cls not in all_declared_classes),
        key=lambda pair: -pair[1],
    )
    s.undeclared_class_total = len(undeclared)
    s.undeclared_classes = undeclared[:SAMPLE_LIMIT]

    # --- Embedded font cross-reference ---
    font_hrefs = {getattr(f, "href", "") for f in (getattr(book, "fonts", []) or [])}
    font_basenames = {PurePosixPath(h).name for h in font_hrefs}
    for src in s.font_face_srcs_referenced:
        src_clean = src.split("#")[0].split("?")[0]
        basename = PurePosixPath(src_clean).name
        if src_clean not in font_hrefs and basename not in font_basenames:
            # Skip obvious remote/system font references; only flag local-looking paths.
            if not src_clean.lower().startswith(("http://", "https://", "data:")):
                s.missing_embedded_fonts.append(src)

    # --- Reverse direction: a font embedded in the book that no
    # @font-face rule in any linked stylesheet ever references -- dead
    # weight bloating the file for no reason. Same scope limitation as
    # the check above: an @font-face rule written directly into a
    # chapter's inline <style> block isn't seen by either check yet
    # (analyze_inline_chapter_css doesn't collect font_face_srcs).
    referenced_srcs = set()
    referenced_basenames = set()
    for src in s.font_face_srcs_referenced:
        src_clean = src.split("#")[0].split("?")[0]
        referenced_srcs.add(src_clean)
        referenced_basenames.add(PurePosixPath(src_clean).name)

    for font in (getattr(book, "fonts", []) or []):
        href = getattr(font, "href", "")
        basename = PurePosixPath(href).name
        if href not in referenced_srcs and basename not in referenced_basenames:
            s.unused_embedded_fonts.append(href)

    calibre_found = [c for c in all_declared_classes if c.startswith("calibre")]
    s.calibre_class_count = len(calibre_found)
    s.calibre_classes = sorted(calibre_found)

    analyze_inline_chapter_css(book, s)

    return s
