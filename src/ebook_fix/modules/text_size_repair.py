"""
ebook_fix.modules.text_size_repair

Fixes text that shows up far too large (or small) in some reading apps
because the book sizes its text in two layers that only work together.

Found in "The Loner: Crossfire" (a Calibre conversion from MOBI): the
page body is set to `font-size: 0.41667em`, and every piece of text sits
in a span set to `font-size: 1.8em`. Multiplied, that is 0.75em, which
looks fine in a reader that follows the CSS exactly. Many reading apps
ignore the size set on the page body (they use their own setting for it),
so those apps show only the 1.8em: text almost twice the normal size.

What this does: it adds a few lines to the end of the stylesheet that
set the body size to 1em and divide every other class's size by the inner
factor, so the text comes out at 1em in every reader, and the relative
sizes (a bigger title, a smaller note) stay in proportion.

It only acts when ALL of these are true:
- The body text's size comes from a body-level factor outside 0.9em to
  1.1em AND an inner factor outside 0.77em to 1.3em, which roughly cancel
  (the combined size is between 0.5em and 2em).
- Every size involved is in em, rem or %, set by a plain class rule in a
  stylesheet file (no <style> blocks, no inline font sizes on the text,
  no font-size on tag selectors other than headings).
- The class that sets the body size is used on the body only.
When in doubt it does nothing. Re-running finds the sizes already fixed
and does nothing.

Second job, size runs inside a paragraph: some conversions mimic small
caps (or a bigger display word) by putting a run of text in a class with
a very different size, for example 0.556em on "CARL MONROE, ATTORNEY AT
LAW" in the middle of a sentence, which is hard to read. This pass finds
classes whose text is shown at less than `inline_min` or more than
`inline_max` times the book's normal text size, and only when every use
of the class is out of range by the same amount, at least one use is a
run inside a paragraph of otherwise normal-sized text, and the class is
never used on text that is already normal-sized or on a one or two
letter initial (a drop cap). The class size is moved
to the nearest edge of that range (so 0.556em becomes 0.9em with the
defaults). Classes that only ever size whole paragraphs (fine print) or
sit beside another size class are left alone. Turn it off with
`fix_inline_size = false`.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from ebook_fix.config import TextSizeConfig
from ebook_fix.modules.paragraph_indent_repair import _book_rules
from ebook_fix.report import Report

BODY_LOW, BODY_HIGH = 0.9, 1.1
INNER_LOW, INNER_HIGH = 0.77, 1.3
COMBINED_LOW, COMBINED_HIGH = 0.5, 2.0

_SIZE = re.compile(r"^\s*([\d.]+)\s*(em|rem|%)\s*$", re.IGNORECASE)
_CLASS_ONLY = re.compile(r"^(?:[a-zA-Z][\w-]*)?\.([\w-]+)$")
_HEADING = re.compile(r"^h[1-6]$", re.IGNORECASE)


def _factor(value):
    m = _SIZE.match(value or "")
    if not m:
        return None
    number, unit = float(m.group(1)), m.group(2).lower()
    return number / 100 if unit == "%" else number


def _local(el) -> str:
    tag = el.tag
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _class_sizes(rules):
    """({class: factor}, ok). ok is False when something sizes text in a
    way this does not handle (tag selectors, complex selectors, odd
    units)."""
    sizes = {}
    for selector, props in rules:
        if "font-size" not in props:
            continue
        value = props["font-size"][0]
        m = _CLASS_ONLY.match(selector.strip())
        if m:
            f = _factor(value)
            if f is None:
                return {}, False
            sizes[m.group(1)] = f
            continue
        sel = selector.strip().lower()
        if _HEADING.match(sel):
            continue
        return {}, False
    return sizes, True


def find_text_size_problem(book):
    """None, or {body_classes, body_factor, inner_factor, combined,
    overrides: {class: new_em}} when the text size depends on a
    body-level factor cancelling an inner one."""
    rules, sources = _book_rules(book)
    if any(kind == "style" for kind, _w, _t in sources):
        for kind, _w, text in sources:
            if kind == "style" and "font-size" in text.lower():
                return None
    sizes, ok = _class_sizes(rules)
    if not ok or not sizes:
        return None

    chains = Counter()
    body_classes = set()
    other_use = Counter()
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None:
            continue
        for el in doc.iter():
            if not isinstance(el.tag, str):
                continue
            if "font-size" in (el.get("style") or "").lower() and (el.text or "").strip():
                return None
            if _local(el) == "body":
                body_classes.update(c for c in (el.get("class") or "").split() if c in sizes)
            else:
                for c in (el.get("class") or "").split():
                    if c in sizes:
                        other_use[c] += 1
            text = (el.text or "").strip()
            if not text:
                continue
            body_f = inner_f = 1.0
            node = el
            while node is not None:
                local = _local(node)
                for c in (node.get("class") or "").split():
                    if c in sizes:
                        if local in ("body", "html"):
                            body_f *= sizes[c]
                        else:
                            inner_f *= sizes[c]
                node = node.getparent()
            chains[(round(body_f, 4), round(inner_f, 4))] += len(text)
    if not chains:
        return None
    (b, i), _count = chains.most_common(1)[0]
    combined = b * i
    if BODY_LOW <= b <= BODY_HIGH or INNER_LOW <= i <= INNER_HIGH:
        return None
    if not COMBINED_LOW <= combined <= COMBINED_HIGH:
        return None
    if any(other_use[c] for c in body_classes):
        return None

    overrides = {c: 1.0 for c in body_classes if abs(sizes[c] - 1.0) > 0.001}
    for c, f in sizes.items():
        if c in body_classes or not other_use[c]:
            continue
        new = round(f / i, 3)
        if abs(new - f) > 0.001:
            overrides[c] = new
    if not overrides:
        return None
    return {
        "body_classes": sorted(body_classes),
        "body_factor": b,
        "inner_factor": i,
        "combined": combined,
        "overrides": overrides,
    }


_BLOCKS = {"p", "div", "li", "blockquote", "td", "th", "dd", "dt"}
_SKIP_TAGS = {
    "sup", "sub", "small", "big", "h1", "h2", "h3", "h4", "h5", "h6",
    "head", "title", "script", "style", "pre", "code", "svg", "math",
}
INLINE_NEIGHBOR_CHARS = 20


def _sized_classes(node, sizes):
    return [c for c in (node.get("class") or "").split() if c in sizes]


def _units(doc, sizes):
    """Yield (owner, text) for every piece of visible text, with `owner` the
    element whose font size applies to it. Text inside tags with their own
    browser sizes (headings, sup, small...) or with an inline font-size is
    skipped, so nothing here depends on guessing those."""
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        for owner, text in ((el, el.text), (el.getparent(), el.tail)):
            if owner is None or not (text or "").strip():
                continue
            node = owner
            skip = False
            while node is not None:
                if _local(node) in _SKIP_TAGS or "font-size" in (node.get("style") or "").lower():
                    skip = True
                    break
                node = node.getparent()
            if not skip:
                yield owner, text


def _chain(owner, sizes):
    """(block, below, above): the nearest block element, the sized classes
    strictly below it (list of (class, node)), and those on it or above.
    None when one element carries two sized classes (cascade order
    unknown)."""
    block = None
    node = owner
    while node is not None:
        if _local(node) in _BLOCKS:
            block = node
            break
        node = node.getparent()
    below, above = [], []
    node = owner
    reached = False
    while node is not None:
        if node is block:
            reached = True
        found = _sized_classes(node, sizes)
        if len(found) > 1:
            return None
        for c in found:
            (above if reached else below).append((c, node))
        node = node.getparent()
    return block, below, above


def find_inline_size_problem(book, sizes, lo, hi):
    """None, or {overrides: {class: new_em}, runs, chars, ratio, target}
    for classes whose text is a run inside normal-sized paragraphs shown
    at less than `lo` or more than `hi` times the normal text size.
    `sizes` is {class: factor} as the stylesheet will read after any body
    size fix."""
    if not sizes:
        return None
    rules, sources = _book_rules(book)
    for kind, _w, text in sources:
        if kind == "style" and "font-size" in text.lower():
            return None

    units = []
    weight = Counter()
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None:
            continue
        for owner, text in _units(doc, sizes):
            info = _chain(owner, sizes)
            if info is None:
                continue
            block, below, above = info
            eff = 1.0
            for c, _n in below + above:
                eff *= sizes[c]
            chars = len(text.strip())
            units.append((block, below, above, round(eff, 3), chars))
            weight[round(eff, 3)] += chars
    if not weight:
        return None
    baseline = weight.most_common(1)[0][0]
    if baseline <= 0:
        return None

    def in_band(eff):
        r = eff / baseline
        return lo - 0.001 <= r <= hi + 0.001

    normal_chars = Counter()
    for block, _b, _a, eff, chars in units:
        if block is not None and in_band(eff):
            normal_chars[block] += chars

    def real(pairs):
        # Classes that really change the size (a class at 1em is inert).
        return [c for c, _n in pairs if abs(sizes[c] - 1.0) > 0.001]

    usage = {}   # class -> {"ratios": [], "inline": chars, "block": chars, "ok": bool}

    def entry(c):
        return usage.setdefault(c, {"ratios": [], "inline": 0, "block": 0, "ok": True})

    for block, below, above, eff, chars in units:
        if in_band(eff):
            # A class that also shows normal-sized text somewhere is
            # not safe to resize.
            for c in real(below) + real(above):
                entry(c)["ok"] = False
            continue
        for c in real(above):
            entry(c)["ok"] = False
        culprits = real(below)
        for c in culprits:
            u = entry(c)
            # One or two characters at a different size is a drop cap or
            # an initial, which is deliberate: never touch its class.
            if len(culprits) != 1 or block is None or chars <= 2:
                u["ok"] = False
                continue
            u["ratios"].append(eff / baseline)
            if normal_chars[block] >= INLINE_NEIGHBOR_CHARS:
                u["inline"] += chars
            else:
                u["block"] += chars

    overrides = {}
    runs = chars_total = 0
    shown = []
    for c, u in usage.items():
        # Needs at least one run inside normal-sized text; a class that
        # only ever sizes whole paragraphs may be deliberate fine print.
        if not u["ok"] or not u["inline"]:
            continue
        if max(u["ratios"]) - min(u["ratios"]) > 0.02:
            continue
        ratio = sorted(u["ratios"])[len(u["ratios"]) // 2]
        target = lo if ratio < lo else hi
        new = round(sizes[c] * target / ratio, 3)
        if abs(new - sizes[c]) < 0.001:
            continue
        overrides[c] = new
        runs += len(u["ratios"])
        chars_total += u["inline"] + u["block"]
        shown.append((c, ratio, target))
    if not overrides:
        return None
    return {"overrides": overrides, "runs": runs, "chars": chars_total, "classes": shown}


def pct(ratio: float) -> str:
    return f"{round(ratio * 100)}%"


def _fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") + "em"


class TextSizeRepair:
    name = "Text Size"

    def __init__(self, config: TextSizeConfig | None = None):
        self.config = config or TextSizeConfig()

    def _plan(self, book):
        """(problem, inline, overrides): the body-size problem (or None),
        the in-paragraph size runs found once that fix is applied (or
        None), and every class size to write."""
        problem = find_text_size_problem(book)
        overrides = dict(problem["overrides"]) if problem else {}
        inline = None
        if getattr(self.config, "fix_inline_size", True):
            rules, _sources = _book_rules(book)
            sizes, ok = _class_sizes(rules)
            if ok and sizes:
                sizes.update(overrides)
                inline = find_inline_size_problem(
                    book, sizes,
                    float(getattr(self.config, "inline_min", 0.9)),
                    float(getattr(self.config, "inline_max", 1.1)),
                )
                if inline:
                    overrides.update(inline["overrides"])
        return problem, inline, overrides

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        problem, inline, _overrides = self._plan(book)
        if problem:
            report.add("stylesheet", "Text size depends on the page body size", self._describe(problem, will=True))
        if inline:
            report.add("stylesheet", "Text runs much smaller or larger than the rest", self._describe_inline(inline, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        problem, inline, overrides = self._plan(book)
        if not overrides:
            return report

        rules, sources = _book_rules(book)
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        written = 0
        for kind, where, text in sources:
            if kind != "css":
                continue
            declared = [c for c in overrides if re.search(rf"\.{re.escape(c)}\s*[{{,]", text)]
            if not declared:
                continue
            note = "text size no longer depends on the page body size" if problem else "text runs kept close to the normal size"
            block = f"\n/* ebook_fix: {note} */\n" + "".join(
                f".{c} {{ font-size: {_fmt(overrides[c])}; }}\n" for c in declared
            )
            book.new_files[str(base / where)] = (text.rstrip("\n") + "\n" + block).encode("utf-8")
            written += 1
        if written:
            if hasattr(book, "mark_modified"):
                book.mark_modified()
            if problem:
                report.add("stylesheet", "Text size fixed", self._describe(problem, will=False))
            if inline:
                report.add("stylesheet", "Text run sizes fixed", self._describe_inline(inline, will=False))
        return report

    @staticmethod
    def _describe_inline(inline, will):
        parts = [
            f"{pct(ratio)} of the normal size {'will be' if will else 'was'} set to {pct(target)}"
            for _c, ratio, target in inline["classes"]
        ]
        return (
            f"{inline['runs']} pieces of text, at least one inside a paragraph ({inline['chars']} characters, "
            f"such as small caps) were far from the normal text size and hard to read: "
            + "; ".join(parts) + "."
        )

    @staticmethod
    def _describe(problem, will):
        return (
            f"The body text is sized by {_fmt(problem['body_factor'])} on the page body times "
            f"{_fmt(problem['inner_factor'])} on the text. Reading apps that ignore the body size "
            f"show it at {_fmt(problem['inner_factor'])}. The sizes "
            f"{'will be' if will else 'were'} reset so the text shows at 1em everywhere."
        )
