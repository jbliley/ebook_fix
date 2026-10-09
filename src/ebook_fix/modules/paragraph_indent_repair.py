"""
ebook_fix.modules.paragraph_indent_repair

Adds a first-line indent to body paragraphs when the book has none. Found
in a converted "To Kill a Mockingbird" (FB2): every body paragraph is a
bare `<p>`, and the stylesheet says `p { margin: 0 }`, so the book is one
unbroken wall of text with nothing to show where a paragraph starts. An
indent and a gap are the two ways a book marks a new paragraph; this book
has neither.

This is the opposite of modules/paragraph_spacing_repair.py (which removes
a gap when there is also an indent), and like it, it only edits the
stylesheet. It acts only when ALL of these are true:

- The book has a clear main body-text style: one kind of paragraph (bare
  `<p>`, or `<p class="x">`) that is at least half of all ordinary
  paragraphs, with at least MIN_PARAGRAPHS of them, and prose-length text.
- The stylesheet gives that paragraph no indent (or an explicit zero).
- The stylesheet explicitly gives it no gap (margin-top and margin-bottom
  both under 0.5em). A paragraph with NO margin declared at all is left
  alone, since readers draw their own default gap there and that is
  already a way of marking paragraphs.
- Few of the paragraphs fake an indent with leading spaces or inline
  styles.
- Nothing in the stylesheet could be indenting that paragraph some other
  way (a `p + p` rule, an indented wrapper div, an `!important`). When in
  doubt it does nothing.

What it adds is one block at the end of the stylesheet that already styles
those paragraphs: `p { text-indent: 1.5em }` (the amount is a config
option), followed by exceptions so these stay flush: the first paragraph
after a heading, a scene break or a chapter marker (a typographic
convention, and a config option), centered or right-aligned paragraphs,
block quotes, epigraphs, poems and table or list cells.

Works on external stylesheets and on embedded <style> blocks. Re-running
finds the indent already there and does nothing.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from pathlib import PurePosixPath

from ebook_fix.config import ParagraphIndentConfig
from ebook_fix.css import COMMENT_RE, RULE_RE, read_book_css
from ebook_fix.modules.paragraph_spacing_repair import MIN_GAP_EM, _to_em
from ebook_fix.report import Report

# Fewer body paragraphs than this and there is not enough book to judge.
MIN_PARAGRAPHS = 30
# The main body style must be at least this share of all counted paragraphs.
MIN_DOMINANT_SHARE = 0.5
# Median visible text length of the body paragraphs; below this the text is
# lines (verse, a script, a list), not prose paragraphs.
MIN_MEDIAN_CHARS = 40
# If this share of the body paragraphs already fake an indent (leading
# spaces, an inline text-indent), the book is already indented.
FAKE_INDENT_SHARE = 0.10

DEFAULT_INDENT = "1.5em"

# Paragraphs inside these are not ordinary body paragraphs.
_SKIP_ANCESTOR_TAGS = {"blockquote", "table", "ul", "ol", "li", "figure", "aside", "pre", "dl"}
_SKIP_ANCESTOR_CLASSES = {"epigraph", "cite", "stanza", "poem", "annotation", "quote", "verse"}

_LEADING_SPACE = re.compile(r"^(?:[    　]|\s{2,})")
_INLINE_INDENT = re.compile(r"text-indent\s*:\s*([^;]+)", re.IGNORECASE)
_PROPERTY = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;]+)")
_SIMPLE = re.compile(r"^([a-zA-Z][\w-]*|\*)?((?:\.[\w-]+)*)$")
_COMBINATORS = re.compile(r"\s*[>+~]\s*|\s+")
_PSEUDO_OR_ATTR = re.compile(r"(:[\w-]+(\([^)]*\))?|\[[^\]]*\])")

_FLUSH_AFTER = (
    [f"h{n} + {{sel}}" for n in range(1, 7)]
    + ["hr + {sel}", "[data-ebookfix-chapter] + {sel}", '{sel_p}[style*="text-align: center"] + {sel}',
       '{sel_p}[style*="text-align:center"] + {sel}']
)
_ALWAYS_FLUSH = (
    ".epigraph {sel}", ".cite {sel}", ".stanza {sel}", ".poem {sel}", ".annotation {sel}", ".quote {sel}",
    "blockquote {sel}", "li {sel}", "td {sel}", "th {sel}", "p.subtitle",
)
_ALIGNED_FLUSH = (
    '{sel}[style*="text-align: center"]', '{sel}[style*="text-align:center"]',
    '{sel}[style*="text-align: right"]', '{sel}[style*="text-align:right"]',
    "{sel}[data-ebookfix-chapter]",
)


# ---------------------------------------------------------------------
# Reading the stylesheet
# ---------------------------------------------------------------------

def _parse_rules(css_text: str) -> list:
    """[(selector, {property: (value, important)})] in document order.
    Rules inside an at-rule (@media) are read as ordinary rules."""
    rules = []
    for m in RULE_RE.finditer(COMMENT_RE.sub("", css_text or "")):
        selector_group = m.group(1).strip()
        if not selector_group or selector_group.startswith("@"):
            continue
        props = {}
        for pm in _PROPERTY.finditer(m.group(2)):
            value = pm.group(2).strip()
            important = "!important" in value.lower()
            value = re.sub(r"\s*!important\s*", "", value, flags=re.IGNORECASE).strip()
            props[pm.group(1).lower()] = (value, important)
        for selector in selector_group.split(","):
            selector = selector.strip()
            if selector:
                rules.append((selector, props))
    return rules


def _margin_sides(value: str):
    """(top, bottom) in em from a `margin` shorthand, or None if unreadable."""
    parts = value.split()
    if not 1 <= len(parts) <= 4:
        return None
    ems = [_to_em(p) for p in parts]
    if any(e is None for e in ems):
        return None
    if len(ems) == 1:
        return ems[0], ems[0]
    if len(ems) == 2:
        return ems[0], ems[0]
    if len(ems) == 3:
        return ems[0], ems[2]
    return ems[0], ems[2]


def _last_compound(selector: str):
    """(tag or None, set of classes) of the last compound selector, with
    pseudo-classes and attribute tests removed."""
    pieces = [p for p in _COMBINATORS.split(selector.strip()) if p]
    if not pieces:
        return None, set()
    last = _PSEUDO_OR_ATTR.sub("", pieces[-1])
    m = _SIMPLE.match(last)
    if not m:
        return None, set()
    tag = m.group(1)
    classes = {c for c in m.group(2).split(".") if c}
    return (None if tag in (None, "*") else tag.lower()), classes


def _nonzero_indent(value: str) -> bool:
    em = _to_em(value)
    return em is None or em != 0   # an unreadable value counts as an indent


class _Style:
    """What the stylesheet says about one paragraph kind (a class set)."""

    def __init__(self):
        self.indent = None          # (specificity, order, value) of the winning rule
        self.margin_top = None
        self.margin_bottom = None
        self.unknown = False        # something might indent it that we can't resolve
        self.source_rules = 0       # direct rules found (where a block can be appended)

    def _offer(self, attr, specificity, order, value):
        current = getattr(self, attr)
        if current is None or (specificity, order) >= (current[0], current[1]):
            setattr(self, attr, (specificity, order, value))


def resolve_style(rules: list, classes: frozenset, ancestor_tags: set, ancestor_classes: set) -> _Style:
    """Resolves text-indent and the vertical margins for a <p> with exactly
    `classes`, from the simple selectors that clearly apply to it. Anything
    that might indent it in a way this does not model sets `.unknown`."""
    style = _Style()
    for order, (selector, props) in enumerate(rules):
        indent = props.get("text-indent")
        margin = props.get("margin")
        top = props.get("margin-top")
        bottom = props.get("margin-bottom")
        if not any((indent, margin, top, bottom)):
            continue

        m = _SIMPLE.match(selector)
        if m:
            tag = (m.group(1) or "").lower()
            sel_classes = {c for c in m.group(2).split(".") if c}
            applies = tag in ("", "*", "p") and sel_classes <= classes
            if applies:
                specificity = (1 if tag == "p" else 0) + 10 * len(sel_classes)
                style.source_rules += 1
                if indent:
                    if indent[1]:                       # !important: cannot be overridden by an added rule
                        style.unknown = True
                    style._offer("indent", specificity, order, indent[0])
                if margin:
                    sides = _margin_sides(margin[0])
                    if sides is None:
                        style.unknown = True
                    else:
                        style._offer("margin_top", specificity, order, sides[0])
                        style._offer("margin_bottom", specificity, order, sides[1])
                if top:
                    style._offer("margin_top", specificity + 0.5, order, _to_em(top[0]))
                if bottom:
                    style._offer("margin_bottom", specificity + 0.5, order, _to_em(bottom[0]))
                continue
            if tag in ("body", "html") and not sel_classes:
                if indent:   # text-indent inherits down to the paragraph, below any direct rule
                    style._offer("indent", -1, order, indent[0])
                continue
            # A rule for an ancestor (a wrapper div, say) can indent the
            # paragraph by inheritance.
            if indent and _nonzero_indent(indent[0]):
                if (tag and tag in ancestor_tags) or (sel_classes and sel_classes <= ancestor_classes):
                    style.unknown = True
            continue

        # A complex selector (descendant, sibling, pseudo-class...). A zero
        # indent there cannot create an indent, so it is ignored; a non-zero
        # one might be indenting the body paragraphs (`p + p`, `p:first-line`).
        if indent and _nonzero_indent(indent[0]):
            tag, sel_classes = _last_compound(selector)
            could_match_p = (tag in (None, "p") and sel_classes <= classes) or (tag in ancestor_tags) or (
                sel_classes and sel_classes <= ancestor_classes)
            if could_match_p:
                style.unknown = True
    return style


# ---------------------------------------------------------------------
# Reading the book
# ---------------------------------------------------------------------

def _book_rules(book):
    """(rules, sources) where sources lists every place the stylesheet text
    lives: ("css", href) for an external file, ("style", chapter) for a
    <style> block in a chapter."""
    rules = []
    sources = []
    contents = read_book_css(book)
    new_files = getattr(book, "new_files", None) or {}
    base = PurePosixPath(getattr(book, "package_path", "") or "").parent
    for res in getattr(book, "css", []) or []:
        zpath = str(base / res.href)
        text = new_files[zpath].decode("utf-8", "replace") if zpath in new_files else contents.get(res.href)
        if text:
            sources.append(("css", res.href, text))
            rules.extend(_parse_rules(text))
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None:
            continue
        for style_el in doc.iter("{*}style"):
            if style_el.text:
                sources.append(("style", chapter, style_el.text))
                rules.extend(_parse_rules(style_el.text))
    return rules, sources


def _ancestors(p):
    node = p.getparent()
    while node is not None:
        yield node
        node = node.getparent()


def _tag(el) -> str:
    return el.tag.split("}")[-1].lower() if isinstance(el.tag, str) else ""


def _is_aligned_inline(p) -> bool:
    style = (p.get("style") or "").lower().replace(" ", "")
    return "text-align:center" in style or "text-align:right" in style


def find_unindented_body_style(book) -> dict | None:
    """The finding for this book, or None when the book should be left
    alone: {"classes", "label", "count", "gap_top", "gap_bottom"} for the
    book's main body paragraph style."""
    return assess_body_style(book)[0]


def assess_body_style(book):
    """(finding or None, reason). The reason says in a few words why a book
    was left alone (or "ok"), so a test or a person can see why."""
    keys = Counter()
    lengths = {}
    fake = Counter()
    anc_tags, anc_classes = {}, {}
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None:
            continue
        for p in doc.iter("{*}p"):
            text = "".join(p.itertext()).strip()
            if not text:
                continue
            ancestors = list(_ancestors(p))
            if any(_tag(a) in _SKIP_ANCESTOR_TAGS for a in ancestors):
                continue
            if any(set((a.get("class") or "").split()) & _SKIP_ANCESTOR_CLASSES for a in ancestors):
                continue
            if _is_aligned_inline(p) or p.get("data-ebookfix-chapter") is not None:
                continue
            key = frozenset((p.get("class") or "").split())
            keys[key] += 1
            lengths.setdefault(key, []).append(len(text))
            raw = "".join(p.itertext())
            inline = _INLINE_INDENT.search(p.get("style") or "")
            if _LEADING_SPACE.match(raw) or (inline and _nonzero_indent(inline.group(1))):
                fake[key] += 1
            tags = anc_tags.setdefault(key, set())
            classes = anc_classes.setdefault(key, set())
            for a in ancestors:
                tags.add(_tag(a))
                classes.update((a.get("class") or "").split())

    total = sum(keys.values())
    if not keys or total < MIN_PARAGRAPHS:
        return None, "too few paragraphs"
    key, count = keys.most_common(1)[0]
    if count < MIN_PARAGRAPHS or count / total < MIN_DOMINANT_SHARE:
        return None, "no single main body style"
    if statistics.median(lengths[key]) < MIN_MEDIAN_CHARS:
        return None, "paragraphs too short to be prose"
    if fake[key] / count >= FAKE_INDENT_SHARE:
        return None, "already indented with spaces or inline styles"

    rules, _sources = _book_rules(book)
    style = resolve_style(rules, key, anc_tags[key], anc_classes[key])
    if style.unknown:
        return None, "stylesheet too unusual to be sure"
    indent_value = style.indent[2] if style.indent else None
    if indent_value is not None and _nonzero_indent(indent_value):
        return None, "already indented"
    # The gap has to be explicitly zero. No margin declared at all is left
    # alone: the reader supplies its own default gap there.
    if style.margin_top is None or style.margin_bottom is None:
        return None, "no gap declared (reader's default gap applies)"
    top, bottom = style.margin_top[2], style.margin_bottom[2]
    if top is None or bottom is None or max(top, bottom) >= MIN_GAP_EM:
        return None, "has a gap between paragraphs"
    if style.source_rules == 0:
        return None, "no stylesheet rule to build on"
    return {
        "classes": key,
        "label": "plain paragraphs" if not key else "paragraphs of class " + " ".join(f".{c}" for c in sorted(key)),
        "count": count,
        "gap_top": top,
        "gap_bottom": bottom,
    }, "ok"


# ---------------------------------------------------------------------
# Writing the stylesheet
# ---------------------------------------------------------------------

def _centered_classes(rules: list) -> set:
    """Classes the stylesheet centers or right-aligns (a paragraph with one
    of those stays flush)."""
    found = set()
    for selector, props in rules:
        align = props.get("text-align")
        if not align or align[0].lower() not in ("center", "right"):
            continue
        tag, classes = _last_compound(selector)
        m = _SIMPLE.match(selector.strip())
        if m and tag in (None, "p") and classes:
            found.update(classes)
    return found


def build_indent_block(key: frozenset, indent: str, flush_first: bool, centered: set) -> str:
    """The CSS appended to the stylesheet."""
    sel_p = "p" + "".join(f".{c}" for c in sorted(key))   # e.g. "p" or "p.calibre1"
    exceptions = []
    exceptions.extend(t.format(sel=sel_p) for t in _ALWAYS_FLUSH)
    exceptions.extend(t.format(sel=sel_p) for t in _ALIGNED_FLUSH)
    exceptions.extend(f"{sel_p}.{c}" if key else f"p.{c}" for c in sorted(centered - set(key)))
    lines = [
        "",
        "/* ebook_fix: this book had no paragraph indent and no gap between paragraphs, so a",
        "   first-line indent was added to show where each paragraph starts. */",
        f"{sel_p} {{ text-indent: {indent}; }}",
        f"{', '.join(exceptions)} {{ text-indent: 0; }}",
    ]
    if flush_first:
        flush = [t.format(sel=sel_p, sel_p="p") for t in _FLUSH_AFTER]
        lines.append(
            "/* the first paragraph after a heading or a scene break is not indented */"
        )
        lines.append(f"{', '.join(flush)} {{ text-indent: 0; }}")
    return "\n".join(lines) + "\n"


class ParagraphIndentRepair:
    name = "Paragraph Indent"

    def __init__(self, config: ParagraphIndentConfig | None = None):
        self.config = config or ParagraphIndentConfig()

    def _indent(self) -> str:
        value = (getattr(self.config, "indent", "") or "").strip()
        em = _to_em(value)
        return value if em is not None and em > 0 else DEFAULT_INDENT

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        finding = find_unindented_body_style(book)
        if finding:
            report.add("stylesheet", "Paragraph indent missing", self._describe(finding, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        finding = find_unindented_body_style(book)
        if not finding:
            return report

        rules, sources = _book_rules(book)
        key = finding["classes"]
        centered = _centered_classes(rules)
        block = build_indent_block(key, self._indent(), bool(self.config.flush_first_paragraph), centered)

        # Append the block to every stylesheet (or <style> block) that holds
        # a rule applying to these paragraphs, so the added rule always comes
        # after the one it overrides.
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        written = 0
        for kind, where, text in sources:
            if not any(self._applies(sel, props, key) for sel, props in _parse_rules(text)):
                continue
            new_text = text.rstrip("\n") + "\n" + block
            if kind == "css":
                book.new_files[str(base / where)] = new_text.encode("utf-8")
            else:
                for style_el in where.document.iter("{*}style"):
                    if style_el.text == text:
                        style_el.text = new_text
                        where.modified = True
            written += 1

        if written:
            if hasattr(book, "mark_modified"):
                book.mark_modified()
            report.add("stylesheet", "Paragraph indent added", self._describe(finding, will=False))
        return report

    @staticmethod
    def _applies(selector, props, key) -> bool:
        m = _SIMPLE.match(selector)
        if not m:
            return False
        tag = (m.group(1) or "").lower()
        classes = {c for c in m.group(2).split(".") if c}
        return tag in ("", "*", "p") and classes <= key and any(
            k in props for k in ("margin", "margin-top", "margin-bottom", "text-indent")
        )

    def _describe(self, finding, will):
        return (
            f"{finding['count']} {finding['label']} have no indent and no gap between them; "
            f"a {self._indent()} indent {'will be' if will else 'was'} added"
        )
