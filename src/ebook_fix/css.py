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

COMMENT_RE = re.compile(r'/\*.*?\*/', re.S)
FONT_FACE_RE = re.compile(r'@font-face\s*\{([^}]*)\}', re.S)
RULE_RE = re.compile(r'([^{}]+)\{([^{}]*)\}', re.S)
CLASS_SELECTOR_RE = re.compile(r'\.([a-zA-Z_-][\w-]*)')
ID_SELECTOR_RE = re.compile(r'#([a-zA-Z_-][\w-]*)')
FONT_FAMILY_RE = re.compile(r'font-family\s*:\s*([^;]+)')
URL_RE = re.compile(r'url\(\s*[\'"]?([^\'")]+)[\'"]?\s*\)')

SAMPLE_LIMIT = 30


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
            s.page_break_rule_count += 1
        if "height:" in body or "max-height:" in body:
            s.forced_height_count += 1

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

    inline_style_element_count: int = 0


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

    calibre_found = [c for c in all_declared_classes if c.startswith("calibre")]
    s.calibre_class_count = len(calibre_found)
    s.calibre_classes = sorted(calibre_found)

    return s
