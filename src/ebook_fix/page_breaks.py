"""
ebook_fix.page_breaks

Shared logic for marking a chapter break the reliable way. Used by both
chapter_markup.py (the default marker, runs whether or not a class
mapping exists) and class_standardize.py (the CSS-mapping-driven
version). Having this in one place is the point: two modules
independently reinventing "find the block before this element" is
exactly the kind of duplication that led to the original triple-
redundant page-break injection.

Why page-break-after on the *preceding* block, not page-break-before
on the chapter start:
- Apple's own iBooks Asset Guide recommends page-break-after
  specifically for marking chapter breaks -- reading-system support
  for page-break-before is weaker, and using both properties on the
  same boundary at once risks a blank page in some reading systems.
- CSS has no "select the element right before this one" selector, so
  this can only be done at repair time by walking the DOM, not as a
  static stylesheet rule.
"""

from __future__ import annotations

from lxml import etree

CHAPTER_BREAK_PROPERTIES = {"page-break-after": "always", "break-after": "page"}

BLOCK_TAGS = {"p", "div", "blockquote", "ul", "ol", "li", "table", "pre", "section",
              "h1", "h2", "h3", "h4", "h5", "h6"}


def is_block(el) -> bool:
    return isinstance(el.tag, str) and etree.QName(el).localname.lower() in BLOCK_TAGS


def deepest_last_block(el):
    """The last block-level element you'd actually reach reading `el`
    (and everything inside it) top to bottom -- e.g. for a <div> whose
    last child is a <p>, that's the <p>, not the <div>. Marking this
    (rather than the outer wrapper) is what actually sits visually
    right before whatever comes next."""
    if not isinstance(el.tag, str):
        return None
    children = [c for c in el if isinstance(c.tag, str)]
    if children:
        deeper = deepest_last_block(children[-1])
        if deeper is not None:
            return deeper
    return el if is_block(el) else None


def closest_preceding_block(el):
    """Walk backwards from `el` in reading order to find the nearest
    actual block-level element before it: scans preceding siblings
    (descending into each one's own last block-level descendant),
    skipping past ones that aren't block-level themselves (a stray
    <hr>, an anchor, etc.) rather than giving up at the first
    non-block sibling -- and climbs to the parent and repeats if `el`
    has no preceding siblings at all (it's a first child). Returns
    None if there's truly nothing block-level before `el` anywhere in
    its document (it's the very first content there -- already a
    fresh page/screen by virtue of being its own file, so no marker is
    needed)."""
    node = el
    while node is not None:
        prev = node.getprevious()
        while prev is not None:
            deepest = deepest_last_block(prev)
            if deepest is not None:
                return deepest
            prev = prev.getprevious()
        node = node.getparent()
    return None


def mark_page_break_after(el) -> bool:
    """Add page-break-after/break-after to el's inline style, unless
    it's already there (idempotent across repeated repair runs, and
    safe if chapter_markup and class_standardize both try to mark the
    same block). Returns whether anything changed."""
    existing = el.get("style") or ""
    if "page-break-after" in existing.lower():
        return False
    parts = [p.strip() for p in existing.split(";") if p.strip()]
    for prop, val in CHAPTER_BREAK_PROPERTIES.items():
        parts.append(f"{prop}: {val}")
    el.set("style", "; ".join(parts) + ";")
    return True
