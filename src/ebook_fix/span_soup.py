"""
ebook_fix.span_soup

Detects excessive, purposeless nested <span> wrapping -- the kind of
thing conversion tools (calibre in particular) leave behind: a plain
run of text buried under two or three layers of <span> that carry
either no attributes at all, or a class whose own CSS rule turns out
to do nothing (`color: inherit`, `font-size: 1em`, and the like).
Same "analysis, not repair" pattern as toc.py/gutenberg.py/cover.py --
this only records what it finds.

Two independent things get flagged
------------------------------------
1. **Nested wrapper chains** -- a <span> whose only child is another
   <span>, with no text of its own alongside it (a pure "wraps one
   thing" relationship, not a span with several children where one
   happens to be a span). Each span in the document is only ever
   walked once: a chain is followed as deep as it goes starting from
   the first unconsumed span reached in document order, and every
   span it swallows along the way is marked consumed so it doesn't
   also get reported as the start of its own (overlapping) chain.
   This also means a span with several span children -- not a chain
   itself -- still lets each of those children go on to start their
   own chain underneath, if one exists. Each level in a chain is
   checked against two purposeless signals:
     - bare: the span has no attributes at all.
     - no-op class: every class on the span maps to a CSS rule (see
       NO_OP_DECLARATIONS below) that provably has zero visual effect
       -- inheriting the value that would apply anyway.
   A chain where every level is bare/no-op is one a repair module
   could safely collapse to nothing; a chain where only some levels
   are (a real class like "bold" wrapping a no-op "calibre3" wrapping
   the actual text -- confirmed on a real book, see the module's own
   test) still gets reported, just with a lower purposeless count, so
   a person or a future repair module can decide how aggressive to be.

2. **Standalone empty spans** -- a <span> with no text anywhere in it
   and no element children at all (not even a nested span -- that
   case is covered by chain detection above). These show up as inert
   litter sitting next to real content, most often right before a
   chain like the one above (`<span></span><span><span
   class="calibre7">text</span></span>` was found verbatim in a real
   book during this module's development).

What counts as "no-op" CSS
----------------------------
Deliberately conservative: only exact, well-known identity
declarations (a property set to the value it would already have via
inheritance/the CSS default) count. A class with even one declaration
outside that list is left alone -- this module would rather miss a
genuinely pointless class than misclassify one that's actually doing
something. Reuses ebook_fix.css's own regex-based CSS scan rather
than parsing stylesheets a second way.

What this does NOT do
----------------------
Doesn't touch the DOM, doesn't decide which chains are safe to
collapse, and doesn't check inline <style> blocks or style=""
attributes for no-op declarations (only linked stylesheets) -- that's
left for a future pass if this turns out to matter in practice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from ebook_fix.css import COMMENT_RE, RULE_RE, CLASS_SELECTOR_RE, read_book_css

# Exact (property, value) pairs that are always a no-op -- setting a
# property to the value it would already have. Conservative on
# purpose: values like "normal" or "1em" are only a no-op because
# they're the CSS default/inherited value for that specific property,
# so this is a hand-picked list, not a general "looks harmless" guess.
NO_OP_DECLARATIONS = {
    ("color", "inherit"),
    ("font-size", "1em"),
    ("font-size", "100%"),
    ("font-weight", "normal"),
    ("font-weight", "inherit"),
    ("font-weight", "400"),
    ("font-style", "normal"),
    ("font-style", "inherit"),
    ("line-height", "inherit"),
    ("line-height", "normal"),
    ("font-family", "inherit"),
    ("text-align", "inherit"),
    ("text-decoration", "inherit"),
    ("vertical-align", "baseline"),
    ("vertical-align", "inherit"),
}

_DECLARATION_SPLIT_RE = re.compile(r";")
_PROPERTY_VALUE_RE = re.compile(r"^([a-zA-Z-]+)\s*:\s*(.+)$")


@dataclass
class SpanLevel:
    classes: list = field(default_factory=list)   # this span's class attribute, split into tokens
    bare: bool = False                              # no attributes at all
    no_op: bool = False                              # has classes, but every one resolves to a no-op rule


@dataclass
class SpanChainInstance:
    href: str = ""
    depth: int = 0                                   # how many spans deep the chain runs
    levels: list = field(default_factory=list)        # [SpanLevel], outermost first
    text: str = ""                                    # the actual text/content at the bottom of the chain, trimmed
    element: object = None                            # live reference to the outermost span

    @property
    def purposeless_level_count(self) -> int:
        return sum(1 for lvl in self.levels if lvl.bare or lvl.no_op)

    @property
    def fully_purposeless(self) -> bool:
        return self.depth > 0 and self.purposeless_level_count == self.depth


@dataclass
class EmptySpanInstance:
    href: str = ""
    element: object = None


@dataclass
class BookSpanSoupSummary:
    chains: list = field(default_factory=list)              # [SpanChainInstance], depth >= 2 only
    empty_spans: list = field(default_factory=list)          # [EmptySpanInstance]
    no_op_classes: dict = field(default_factory=dict)        # class name -> normalized declaration text, for context

    @property
    def chain_count(self) -> int:
        return len(self.chains)

    @property
    def fully_purposeless_chain_count(self) -> int:
        return sum(1 for c in self.chains if c.fully_purposeless)

    @property
    def empty_span_count(self) -> int:
        return len(self.empty_spans)

    @property
    def max_depth(self) -> int:
        return max((c.depth for c in self.chains), default=0)

    @property
    def chapters_affected(self) -> list:
        seen = []
        for href in [c.href for c in self.chains] + [e.href for e in self.empty_spans]:
            if href not in seen:
                seen.append(href)
        return seen


# ---------------------------------------------------------------------
# No-op class detection from the book's own CSS
# ---------------------------------------------------------------------

def _normalize_declaration(prop: str, value: str) -> tuple:
    return prop.strip().lower(), value.strip().lower().rstrip(";").strip()


def _body_is_no_op(body: str) -> bool:
    """True if every declaration in a rule body is an exact no-op
    pair, or the body has no declarations at all (an empty ruleset is
    trivially a no-op too)."""
    declarations = [d.strip() for d in _DECLARATION_SPLIT_RE.split(body) if d.strip()]
    if not declarations:
        return True
    for decl in declarations:
        m = _PROPERTY_VALUE_RE.match(decl)
        if not m:
            return False
        pair = _normalize_declaration(m.group(1), m.group(2))
        if pair not in NO_OP_DECLARATIONS:
            return False
    return True


def _find_no_op_classes(book) -> dict:
    """Scans every linked stylesheet in the book and returns
    {class_name: declaration_text} for classes whose combined rule
    body is a confirmed no-op. A class declared more than once (across
    files, or with multiple selectors sharing a rule) only counts if
    EVERY occurrence is a no-op -- one real declaration anywhere is
    enough to disqualify it."""
    contents = read_book_css(book)
    no_op = {}
    disqualified = set()

    for text in contents.values():
        if not text:
            continue
        clean = COMMENT_RE.sub("", text)
        for m in RULE_RE.finditer(clean):
            selector = m.group(1).strip()
            body = m.group(2)
            if not selector or selector.startswith("@"):
                continue
            classes = CLASS_SELECTOR_RE.findall(selector)
            if not classes:
                continue
            is_no_op = _body_is_no_op(body)
            for cls in classes:
                if cls in disqualified:
                    continue
                if is_no_op:
                    # Keep the first no-op body text seen, for the
                    # summary's own reference -- doesn't matter which,
                    # they're all effectively identical (no-op).
                    no_op.setdefault(cls, body.strip())
                else:
                    disqualified.add(cls)
                    no_op.pop(cls, None)

    return no_op


# ---------------------------------------------------------------------
# DOM walk
# ---------------------------------------------------------------------

def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return etree.QName(tag).localname.lower()


def _element_children(el) -> list:
    return [c for c in el if isinstance(c.tag, str)]


def _classify_span(el, no_op_classes: dict) -> SpanLevel:
    classes = (el.get("class") or "").split()
    level = SpanLevel(classes=classes)
    if not el.attrib:
        level.bare = True
    elif classes and not (set(el.attrib.keys()) - {"class"}):
        level.no_op = all(cls in no_op_classes for cls in classes)
    return level


def _is_pure_wrapper(el) -> bool:
    """True if `el`'s only meaningful content is a single <span>
    child -- no text of its own before it, no tail-in-parent text
    after it (checked by the caller), nothing else alongside it."""
    children = _element_children(el)
    if len(children) != 1 or _local(children[0].tag) != "span":
        return False
    if (el.text or "").strip():
        return False
    child = children[0]
    if (child.tail or "").strip():
        return False
    return True


def _walk_chain(el, href, no_op_classes, seen) -> SpanChainInstance | None:
    levels = []
    node = el
    while True:
        levels.append(_classify_span(node, no_op_classes))
        seen.add(node)
        if not _is_pure_wrapper(node):
            break
        node = _element_children(node)[0]

    if len(levels) < 2:
        return None

    text = "".join(node.itertext()).strip()
    return SpanChainInstance(href=href, depth=len(levels), levels=levels, text=text, element=el)


def _is_empty_span(el) -> bool:
    if _element_children(el):
        return False
    return not "".join(el.itertext()).strip()


def analyze_book_span_soup(book) -> BookSpanSoupSummary:
    summary = BookSpanSoupSummary()
    summary.no_op_classes = _find_no_op_classes(book)

    for chapter in getattr(book, "chapters", []) or []:
        tree = getattr(chapter, "document", None)
        if tree is None:
            continue
        href = getattr(chapter, "href", "")
        seen = set()

        # Single pass in document order (lxml's .iter() is pre-order,
        # so a span always comes up before its descendants). Every
        # span not already consumed as part of a chain walked from an
        # earlier one gets tried as a fresh chain root -- this
        # correctly separates, say, a span with two children (not a
        # chain itself) from one of those children going on to start
        # its own real nested-wrapper chain underneath.
        for el in tree.iter():
            if _local(el.tag) != "span":
                continue
            if el in seen:
                continue

            chain = _walk_chain(el, href, summary.no_op_classes, seen)
            if chain is not None:
                summary.chains.append(chain)
            elif _is_empty_span(el):
                summary.empty_spans.append(EmptySpanInstance(href=href, element=el))

    return summary
