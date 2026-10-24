"""
ebook_fix.modules.div_paragraph_repair

Turns paragraphs that were written as <div> tags into real <p> paragraphs.
Found in "Hunter's Moon" and in OmnibusExample: both write every paragraph as
`<div class="p-indent">...</div>`. The analyzer counts only 36 paragraphs in
a 92,000 word book, finds no usable body-text style, and every repair that
looks at paragraphs (spacing, indents, scene openers, reader settings,
paragraph repair) skips the book or works on the wrong thing.

What this does, only in a book that is clearly built this way:
- A <div> is turned into a <p> when it holds only text and inline tags (no
  paragraphs, headings, lists, tables or other blocks inside it) and it is
  used as a paragraph: its class appears on at least 20 such divs and the
  typical one holds at least 40 characters. Short divs such as chapter titles
  and scene markers are not touched.
- The book counts as "built this way" when those divs number at least 100 and
  are at least twice as many as the real <p> paragraphs of any length. A book
  that mixes both, or has only a few, is left alone.
- Other text divs on a page that already has converted paragraphs (the flush
  first paragraph of a scene, say) are converted too when they hold at least
  40 characters.
- The class, id and every other attribute stay exactly as they were, and the
  text is not changed.

So the look does not change:
- A style rule that said `div.p-indent` or a bare `div` keeps working: a
  matching `p.p-indent` or `p` selector is added next to it.
- A converted class gets `margin-top: 0; margin-bottom: 0`, because a <div>
  has no gap above and below and a <p> normally does. (Reader Settings then
  removes it again like any other paragraph gap, so the reading app decides.)

Skipped on fixed-layout books. A second run finds nothing left to convert.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from pathlib import PurePosixPath

from ebook_fix.config import DivParagraphConfig
from ebook_fix.css import COMMENT_RE, RULE_RE, read_book_css
from ebook_fix.report import Report

MIN_CLASS_USES = 20
MIN_MEDIAN_CHARS = 40
MIN_BOOK_DIVS = 100
MIN_RATIO = 2.0
MIN_TIER2_CHARS = 40

_BLOCKS = frozenset({
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "blockquote", "pre", "section",
    "article", "aside", "header", "footer", "nav", "figure", "figcaption", "hr", "form",
    "fieldset", "address", "main", "caption",
})
_DIV_TOKEN = re.compile(
    r"(?<![\w.#\[:-])div((?:\.[\w-]+|#[\w-]+|:{1,2}[\w-]+(?:\([^)]*\))?|\[[^\]]*\])*)"
)


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _root(doc):
    return doc.getroot() if hasattr(doc, "getroot") else doc


def _text_len(el) -> int:
    return len(re.sub(r"\s+", " ", "".join(el.itertext())).strip())


def _is_leaf_text_div(el) -> bool:
    if not isinstance(el.tag, str) or _local(el) != "div":
        return False
    if any(isinstance(d.tag, str) and _local(d) in _BLOCKS for d in el.iterdescendants()):
        return False
    return _text_len(el) > 0


def _protected(el) -> bool:
    if any(k.startswith("data-ebookfix") for k in el.attrib):
        return True
    return "ebookfix" in (el.get("class") or "").lower()


class _Plan:
    def __init__(self):
        self.active = False
        self.classes = set()       # class attribute strings treated as paragraph classes
        self.class_tokens = set()  # the individual class names in them
        self.count = 0


def _build_plan(book) -> _Plan:
    plan = _Plan()
    stats = defaultdict(list)
    p_text = 0
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        for el in _root(doc).iter():
            if not isinstance(el.tag, str):
                continue
            name = _local(el)
            if name == "p":
                if _text_len(el) > 0:
                    p_text += 1
            elif name == "div" and _is_leaf_text_div(el) and not _protected(el):
                stats[(el.get("class") or "").strip()].append(_text_len(el))
    for cls, lengths in stats.items():
        if len(lengths) >= MIN_CLASS_USES and statistics.median(lengths) >= MIN_MEDIAN_CHARS:
            plan.classes.add(cls)
    total = sum(len(stats[c]) for c in plan.classes)
    if total >= MIN_BOOK_DIVS and total >= MIN_RATIO * max(p_text, 1):
        plan.active = True
        plan.count = total
        for cls in plan.classes:
            plan.class_tokens.update(cls.split())
    return plan


def _convert_pages(book, plan: _Plan, write: bool):
    """[(href, converted_count)], and the set of class names actually converted."""
    results = []
    converted_tokens = set()
    for chapter in book.chapters:
        doc = chapter.document
        if doc is None:
            continue
        root = _root(doc)
        todo = []
        page_has_core = False
        tier2 = []
        for el in root.iter():
            if not _is_leaf_text_div(el) or _protected(el):
                continue
            cls = (el.get("class") or "").strip()
            if cls in plan.classes:
                todo.append(el)
                page_has_core = True
            elif _text_len(el) >= MIN_TIER2_CHARS:
                tier2.append(el)
        if page_has_core:
            todo.extend(tier2)
        if not todo:
            continue
        for el in todo:
            converted_tokens.update((el.get("class") or "").split())
            if write:
                el.tag = el.tag[:-3] + "p"
        results.append((chapter.href, len(todo)))
        if write:
            chapter.modified = True
    return results, converted_tokens


def _rewrite_css(css_text: str, tokens: set):
    """Add p-variants of selectors that named div. Returns (text, rules_changed)."""
    changed = {"n": 0}

    def eligible(compound_classes):
        return not compound_classes or all(c in tokens for c in compound_classes)

    def sub(match):
        raw_sel, body = match.group(1), match.group(2)
        sel_text = COMMENT_RE.sub("", raw_sel).strip()
        if not sel_text or sel_text.startswith("@") or "div" not in sel_text.lower():
            return match.group(0)
        selectors = [s.strip() for s in sel_text.split(",") if s.strip()]
        extra = []
        for sel in selectors:
            def repl(m):
                classes = re.findall(r"\.([\w-]+)", m.group(1))
                return "p" + m.group(1) if eligible(classes) else m.group(0)
            variant = _DIV_TOKEN.sub(repl, sel)
            if variant != sel and variant not in selectors and variant not in extra:
                extra.append(variant)
        if not extra:
            return match.group(0)
        changed["n"] += 1
        lead = raw_sel[: len(raw_sel) - len(raw_sel.lstrip())]
        return f"{lead}{', '.join(selectors + extra)} {{{body}}}"

    return RULE_RE.sub(sub, css_text), changed["n"]


class DivParagraphRepair:
    name = "Div Paragraphs"

    def __init__(self, config: DivParagraphConfig | None = None):
        self.config = config or DivParagraphConfig()

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        plan = _build_plan(book)
        if not plan.active:
            return report
        results, _ = _convert_pages(book, plan, write=False)
        for href, n in results:
            report.add(href, "Paragraphs written as div tags",
                       f"{n} paragraph(s) written as div tags will become p tags")
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        plan = _build_plan(book)
        if not plan.active:
            return report
        results, tokens = _convert_pages(book, plan, write=True)
        if not results:
            return report
        self._rewrite_styles(book, tokens)
        for href, n in results:
            report.add(href, "Div paragraphs recoded",
                       f"{n} paragraph(s) written as div tags became p tags")
        if hasattr(book, "mark_modified"):
            book.mark_modified()
        return report

    def _rewrite_styles(self, book, tokens: set) -> None:
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        contents = read_book_css(book)
        new_files = getattr(book, "new_files", None)
        first_zpath = None
        for res in getattr(book, "css", []) or []:
            zpath = str(base / res.href)
            if first_zpath is None:
                first_zpath = zpath
            text = (new_files[zpath].decode("utf-8", "replace")
                    if new_files and zpath in new_files else contents.get(res.href))
            if not text:
                continue
            new_text, n = _rewrite_css(text, tokens)
            if n:
                book.new_files[zpath] = new_text.encode("utf-8")
        for chapter in book.chapters:
            doc = chapter.document
            if doc is None:
                continue
            for style in _root(doc).iter("{*}style"):
                if style.text:
                    new_text, n = _rewrite_css(style.text, tokens)
                    if n:
                        style.text = new_text
                        chapter.modified = True
        if first_zpath is not None and tokens:
            current = (book.new_files[first_zpath].decode("utf-8", "replace")
                       if first_zpath in book.new_files else (contents.get(
                           next((r.href for r in book.css if str(base / r.href) == first_zpath), ""), "") or ""))
            sels = ", ".join(f"p.{t}" for t in sorted(tokens))
            block = (f"\n/* ebook_fix: paragraphs that were div tags had no gap above or below */\n"
                     f"{sels} {{\n    margin-top: 0;\n    margin-bottom: 0;\n    }}\n")
            book.new_files[first_zpath] = (current.rstrip("\n") + "\n" + block).encode("utf-8")
