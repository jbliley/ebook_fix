"""
ebook_fix.modules.unused_css_repair

Removes style rules for classes that no page uses. Calibre conversions ship
a rule for every class in the original file, so "Hunter's Moon" carries 312
declared classes (283 of them unused) and "The Numbers Killer" has leftovers
from every earlier cleanup. The analysis has only been reporting these as
"Unused CSS classes declared".

What this does:
- A selector is dropped from its rule when it names a class that appears
  nowhere in the book's pages (text pages, the contents page, cover page and
  any other HTML file in the book). A class-name combination such as
  `.a .b` or `p.a` can never match when any one of its classes is unused.
  When every selector in a rule is dropped, the rule goes; when only some are,
  the rest stay.
- Rules whose selector has no class in it (`body`, `p`, `h1`, `a`, `img`)
  are never touched, and neither are @font-face, @page or other @ rules.
- Selectors with a pseudo-function or an attribute test (`:not(...)`,
  `:is(...)`, `[...]`) are left alone, and so are class names containing
  "ebookfix".
- Nothing is removed from a book that contains a script, since a script can
  add classes at reading time.

Stylesheet files only; the <style> blocks inside pages are not edited.
Skipped on fixed-layout books. A second run finds nothing to remove.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import PurePosixPath

from ebook_fix.config import UnusedCssConfig
from ebook_fix.css import COMMENT_RE, CLASS_SELECTOR_RE, RULE_RE, read_book_css
from ebook_fix.report import Report

_CLASS_ATTR = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_HTML_EXT = (".xhtml", ".html", ".htm")


def _protected(name: str) -> bool:
    return "ebookfix" in name.lower()


def _root(doc):
    return doc.getroot() if hasattr(doc, "getroot") else doc


def _used_classes(book):
    """(set of class names used anywhere in the book, has_script)."""
    used = set()
    has_script = False
    base = PurePosixPath(getattr(book, "package_path", "") or "").parent
    chapter_paths = set()
    for chapter in book.chapters:
        chapter_paths.add(str(base / chapter.href))
        doc = chapter.document
        if doc is None:
            continue
        for el in _root(doc).iter():
            if not isinstance(el.tag, str):
                continue
            if el.tag.rsplit("}", 1)[-1].lower() == "script":
                has_script = True
            raw = el.get("class")
            if raw:
                used.update(raw.split())

    def scan_bytes(data: bytes):
        nonlocal has_script
        text = data.decode("utf-8", "replace")
        if re.search(r"<script\b", text, re.IGNORECASE):
            has_script = True
        for m in _CLASS_ATTR.finditer(text):
            used.update((m.group(1) or m.group(2) or "").split())

    new_files = getattr(book, "new_files", None) or {}
    for path, data in new_files.items():
        if path.lower().endswith(_HTML_EXT) and path not in chapter_paths:
            scan_bytes(data)
    removed = set(getattr(book, "removed_files", set()) or set())
    source = getattr(book, "source", None)
    if source is not None:
        try:
            with zipfile.ZipFile(source, "r") as archive:
                for name in archive.namelist():
                    if (name.lower().endswith(_HTML_EXT) and name not in chapter_paths
                            and name not in new_files and name not in removed):
                        scan_bytes(archive.read(name))
        except (OSError, zipfile.BadZipFile):
            pass
    return used, has_script


def _dead_selector(selector: str, used: set) -> bool:
    if "(" in selector or "[" in selector:
        return False
    classes = CLASS_SELECTOR_RE.findall(selector)
    if not classes:
        return False
    return any(c not in used and not _protected(c) for c in classes)


def _rewrite(css_text: str, used: set):
    """Returns (new_text, rules_removed, selectors_removed)."""
    counts = {"rules": 0, "selectors": 0}

    def sub(match):
        raw_sel, body = match.group(1), match.group(2)
        sel_text = COMMENT_RE.sub("", raw_sel).strip()
        if not sel_text or sel_text.startswith("@"):
            return match.group(0)
        comments = "".join(re.findall(r"/\*.*?\*/", raw_sel, re.DOTALL))
        selectors = [s.strip() for s in sel_text.split(",") if s.strip()]
        keep = [s for s in selectors if not _dead_selector(s, used)]
        if len(keep) == len(selectors):
            return match.group(0)
        counts["selectors"] += len(selectors) - len(keep)
        if not keep:
            counts["rules"] += 1
            return comments
        lead = raw_sel[: len(raw_sel) - len(raw_sel.lstrip())]
        return f"{lead}{', '.join(keep)} {{{body}}}"

    new_text = RULE_RE.sub(sub, css_text)
    if counts["selectors"]:
        new_text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", new_text)
    return new_text, counts["rules"], counts["selectors"]


class UnusedCssRepair:
    name = "Unused CSS"

    def __init__(self, config: UnusedCssConfig | None = None):
        self.config = config or UnusedCssConfig()

    def _scan(self, book, write: bool):
        used, has_script = _used_classes(book)
        if has_script:
            return []
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        contents = read_book_css(book)
        new_files = getattr(book, "new_files", None)
        results = []
        for res in getattr(book, "css", []) or []:
            zpath = str(base / res.href)
            text = (new_files[zpath].decode("utf-8", "replace")
                    if new_files and zpath in new_files else contents.get(res.href))
            if not text:
                continue
            new_text, rules, selectors = _rewrite(text, used)
            if not selectors:
                continue
            results.append((res.href, rules, selectors))
            if write:
                book.new_files[zpath] = new_text.encode("utf-8")
        return results

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        for href, rules, selectors in self._scan(book, write=False):
            report.add(href, "Style rules for classes no page uses",
                       f"{rules} rule(s) will be removed ({selectors} unused selector(s))")
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        results = self._scan(book, write=True)
        for href, rules, selectors in results:
            report.add(href, "Style rules for unused classes removed",
                       f"{rules} rule(s) removed ({selectors} unused selector(s))")
        if results and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report
