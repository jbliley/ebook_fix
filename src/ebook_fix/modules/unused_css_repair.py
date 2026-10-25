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

Three follow-ups (2026-10-25), found by measuring what auto-fix left behind:
- The tool's own chapter-spacing rules (`[data-ebookfix-chapter] + .calibre2`)
  carry an attribute test, so they were skipped even after the class they name
  was gone from every page. A `[data-ebookfix-...]` test is the tool's own and
  is now looked through; any other attribute test still protects its selector.
- The same cleanup now applies to <style> blocks inside pages, and a block
  with nothing left in it is removed (MM21 kept 42 empty ones, The First
  Mountain Man 77). "Used" is still decided across the whole book.
- A stylesheet file with no rules at all (empty, or only comments) is removed
  together with the <link> to it in every page and its manifest entry, unless
  another stylesheet imports it or a page outside the reading order mentions it.

Skipped on fixed-layout books. A second run finds nothing to remove.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote

from ebook_fix.config import UnusedCssConfig
from ebook_fix.modules.junk_file_repair import JunkFileRepair
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


_AT_STATEMENTS = re.compile(r"(?:\s*@(?:charset|namespace|import)\b[^;{}]*;)+", re.IGNORECASE)
_OWN_ATTR_TEST = re.compile(r"\[data-ebookfix-[\w-]+\]")
_EMPTY_NOISE = re.compile(r"<!--|-->|<!\[CDATA\[|\]\]>")


def _empty_css(text: str) -> bool:
    """True when a stylesheet has no rules, no @ rules, nothing but comments."""
    return not _EMPTY_NOISE.sub("", COMMENT_RE.sub("", text or "")).strip()


def _dead_selector(selector: str, used: set) -> bool:
    selector = _OWN_ATTR_TEST.sub("", selector)
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
        # `@charset "utf-8"; body.x { ... }` reads as one "selector" because
        # the statement has no braces; set the statements aside and judge the
        # real selectors that follow.
        prefix = ""
        pm = _AT_STATEMENTS.match(sel_text)
        if pm:
            prefix = pm.group(0).strip() + "\n"
            sel_text = sel_text[pm.end():].strip()
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
            return comments + prefix
        lead = raw_sel[: len(raw_sel) - len(raw_sel.lstrip())]
        return f"{lead}{prefix}{', '.join(keep)} {{{body}}}"

    new_text = RULE_RE.sub(sub, css_text)
    if counts["selectors"]:
        new_text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", new_text)
    return new_text, counts["rules"], counts["selectors"]


class UnusedCssRepair:
    name = "Unused CSS"

    def __init__(self, config: UnusedCssConfig | None = None):
        self.config = config or UnusedCssConfig()

    # -----------------------------------------------------
    # Stylesheet files
    # -----------------------------------------------------

    def _scan(self, book, used, write: bool):
        """Returns (results, final_text) where results is a list of
        (href, rules_removed, selectors_removed) and final_text maps every
        stylesheet href to its text as it stands after this step."""
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        contents = read_book_css(book)
        new_files = getattr(book, "new_files", None)
        results = []
        final_text = {}
        for res in getattr(book, "css", []) or []:
            zpath = str(base / res.href)
            text = (new_files[zpath].decode("utf-8", "replace")
                    if new_files and zpath in new_files else contents.get(res.href))
            if text is None:
                continue
            new_text, rules, selectors = _rewrite(text, used)
            final_text[res.href] = new_text
            if not selectors:
                continue
            results.append((res.href, rules, selectors))
            if write:
                book.new_files[zpath] = new_text.encode("utf-8")
        return results, final_text

    # -----------------------------------------------------
    # <style> blocks inside pages
    # -----------------------------------------------------

    def _scan_embedded(self, book, used, write: bool):
        """Returns a list of (page href, rules_removed, selectors_removed,
        block_removed). A block is removed when nothing is left in it."""
        results = []
        for chapter in book.chapters:
            doc = getattr(chapter, "document", None)
            if doc is None:
                continue
            for el in list(_root(doc).iter()):
                if not isinstance(el.tag, str) or el.tag.rsplit("}", 1)[-1].lower() != "style":
                    continue
                if len(el) or "<" in (el.text or "").replace("<!--", "").replace("<![CDATA[", ""):
                    continue
                text = el.text or ""
                new_text, rules, selectors = _rewrite(text, used)
                gone = _empty_css(new_text)
                if not selectors and not gone:
                    continue
                results.append((chapter.href, rules, selectors, gone))
                if not write:
                    continue
                if gone:
                    _remove_keep_tail(el)
                else:
                    el.text = new_text
                chapter.modified = True
        return results

    # -----------------------------------------------------
    # Empty stylesheet files
    # -----------------------------------------------------

    def _empty_sheets(self, book, final_text, write: bool):
        """Returns the hrefs of stylesheets that are empty and safe to remove;
        with write=True also removes them and every <link> to them."""
        empties = [h for h, t in final_text.items() if _empty_css(t)]
        if not empties:
            return []
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        chapter_paths = {str(base / c.href) for c in book.chapters}
        outside = []  # text of every HTML file outside the reading order
        new_files = getattr(book, "new_files", None) or {}
        removed = set(getattr(book, "removed_files", set()) or set())
        for path, data in new_files.items():
            if path.lower().endswith(_HTML_EXT) and path not in chapter_paths:
                outside.append(data.decode("utf-8", "replace"))
        source = getattr(book, "source", None)
        if source is not None:
            try:
                with zipfile.ZipFile(source, "r") as archive:
                    for name in archive.namelist():
                        if (name.lower().endswith(_HTML_EXT) and name not in chapter_paths
                                and name not in new_files and name not in removed):
                            outside.append(archive.read(name).decode("utf-8", "replace"))
            except (OSError, zipfile.BadZipFile):
                pass

        safe = []
        for href in empties:
            name = posixpath.basename(unquote(href))
            if any(name in t for t in outside):
                continue
            if any(name in t for h, t in final_text.items() if h != href):
                continue
            safe.append(href)
        if not write:
            return safe

        for href in safe:
            target = posixpath.normpath(unquote(href))
            for chapter in book.chapters:
                doc = getattr(chapter, "document", None)
                if doc is None:
                    continue
                folder = posixpath.dirname(chapter.href)
                for el in list(_root(doc).iter()):
                    if not isinstance(el.tag, str) or el.tag.rsplit("}", 1)[-1].lower() != "link":
                        continue
                    link = el.get("href")
                    if not link or "://" in link:
                        continue
                    if posixpath.normpath(posixpath.join(folder, unquote(link.split("#")[0]))) == target:
                        _remove_keep_tail(el)
                        chapter.modified = True
            item = next((m for m in book.manifest if m.href == href), None)
            if item is not None:
                JunkFileRepair._drop_manifest_item(book, item)
            book.css = [r for r in book.css if r.href != href]
            full = unquote(str(base / href))
            if getattr(book, "new_files", None) is not None:
                book.new_files.pop(str(base / href), None)
                book.new_files.pop(full, None)
            book.removed_files.add(full)
        return safe

    # -----------------------------------------------------

    def _run(self, book, write: bool):
        used, has_script = _used_classes(book)
        if has_script:
            return [], [], []
        files, final_text = self._scan(book, used, write)
        embedded = self._scan_embedded(book, used, write)
        empties = self._empty_sheets(book, final_text, write)
        return files, embedded, empties

    def _report(self, report, files, embedded, empties, done: bool):
        for href, rules, selectors in files:
            report.add(href,
                       "Style rules for unused classes removed" if done else "Style rules for classes no page uses",
                       f"{rules} rule(s) {'removed' if done else 'will be removed'} ({selectors} unused selector(s))")
        for href, rules, selectors, gone in embedded:
            if selectors:
                report.add(href, "Unused style rules in a page removed" if done else "Unused style rules in a page",
                           f"{rules} rule(s) {'removed' if done else 'will be removed'} ({selectors} unused selector(s))")
            if gone:
                report.add(href, "Empty style block removed" if done else "Empty style block",
                           "A <style> block with nothing in it " + ("was removed" if done else "will be removed"))
        for href in empties:
            report.add(href, "Empty stylesheet removed" if done else "Empty stylesheet",
                       f"{href} has no rules; " + ("it and the links to it were removed" if done
                                                   else "it and the links to it will be removed"))

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        files, embedded, empties = self._run(book, write=False)
        self._report(report, files, embedded, empties, done=False)
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        files, embedded, empties = self._run(book, write=True)
        self._report(report, files, embedded, empties, done=True)
        if (files or embedded or empties) and hasattr(book, "mark_modified"):
            book.mark_modified()
        return report


def _remove_keep_tail(el) -> None:
    tail = el.tail
    parent = el.getparent()
    if parent is None:
        return
    prev = el.getprevious()
    parent.remove(el)
    if tail and tail.strip():
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
