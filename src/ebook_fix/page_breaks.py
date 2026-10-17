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


def deepest_first_block(el):
    """Mirror of deepest_last_block, forward instead of backward: the
    first block-level element you'd actually reach reading `el` (and
    everything inside it) top to bottom -- e.g. for a <div> whose
    first child is a <p>, that's the <p>, not the <div>."""
    if not isinstance(el.tag, str):
        return None
    if is_block(el):
        return el
    for child in el:
        if not isinstance(child.tag, str):
            continue
        found = deepest_first_block(child)
        if found is not None:
            return found
    return None


def closest_following_block(el):
    """Mirror of closest_preceding_block, walking forward: the nearest
    actual block-level element after `el` in reading order. Used by
    case3_structural.py to find where a new chapter's content actually
    starts right after a structural divider (a bare <hr>, or an
    element carrying a forced page-break) -- the divider/marker
    element itself isn't part of the new chapter's content, so word
    counts and structural-cleanliness checks need to anchor on
    whatever comes right after it instead. Returns None if there's
    nothing block-level left after `el` anywhere in its document (it's
    the last content there -- already effectively a file boundary, so
    no new split point to find)."""
    node = el
    while node is not None:
        nxt = node.getnext()
        while nxt is not None:
            deepest = deepest_first_block(nxt)
            if deepest is not None:
                return deepest
            nxt = nxt.getnext()
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


# ---------------------------------------------------------------------
# Redundant breaks (a forced break that can only produce a blank page)
# ---------------------------------------------------------------------
# Every spine file already starts on a fresh page in a reading app. A
# forced page break with nothing visible between it and the start (or end)
# of its file adds a second break on top of that one, which shows up as an
# empty page before every chapter. Found in "The Loner: Crossfire": each
# chapter file opened with a blank paragraph carrying page-break-after.

BREAK_PROPERTIES = ("page-break-after", "break-after", "page-break-before", "break-before")
_FORCED_VALUES = {"always", "page", "left", "right", "recto", "verso"}
_VISIBLE_TAGS = {"img", "svg", "image", "video", "audio", "object", "hr", "table", "canvas", "math"}
_SKIP_TEXT = {"style", "script", "title"}


def _style_dict(el) -> list:
    out = []
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out.append((k.strip(), v.strip()))
    return out


def forced_break_props(el) -> dict:
    """{property: 'after'|'before'} for each forced-break declaration in
    el's inline style."""
    found = {}
    for k, v in _style_dict(el):
        kl = k.lower()
        if kl in BREAK_PROPERTIES and v.lower().replace("!important", "").strip() in _FORCED_VALUES:
            found[kl] = "after" if kl.endswith("after") else "before"
    return found


def _strip_break_props(el, props) -> None:
    kept = [(k, v) for k, v in _style_dict(el) if k.lower() not in props]
    if kept:
        el.set("style", "; ".join(f"{k}: {v}" for k, v in kept) + ";")
    elif "style" in el.attrib:
        del el.attrib["style"]


def _own_visible(el, tail: bool) -> bool:
    text = el.tail if tail else el.text
    return bool(text and text.replace(" ", " ").strip())


def redundant_forced_breaks(root) -> list:
    """[(element, [properties])] for forced breaks in one document that
    have no visible content between them and the start/end of the file
    (or the previous kept break). Document order walk."""
    body = None
    for el in root.iter():
        if isinstance(el.tag, str) and etree.QName(el).localname.lower() == "body":
            body = el
            break
    if body is None:
        return []

    redundant = []
    kept_after = []        # kept break-after elements, for the end-of-file check
    visible = False        # visible content since file start / last kept break
    for event, el in etree.iterwalk(body, events=("start", "end")):
        if not isinstance(el.tag, str):
            continue
        name = etree.QName(el).localname.lower()
        if event == "start":
            props = forced_break_props(el)
            before = [p for p, side in props.items() if side == "before"]
            if before:
                if visible:
                    visible = False
                else:
                    redundant.append((el, before))
            if name in _VISIBLE_TAGS:
                visible = True
            elif name not in _SKIP_TEXT and _own_visible(el, tail=False):
                visible = True
        else:
            props = forced_break_props(el)
            after = [p for p, side in props.items() if side == "after"]
            if after:
                if visible:
                    visible = False
                    kept_after.append((el, after))
                else:
                    redundant.append((el, after))
            if _own_visible(el, tail=True):
                visible = True

    # A kept break-after with nothing visible after it, up to the end of
    # the file, is redundant too (the next file starts a new page anyway).
    if kept_after and not visible:
        redundant.append(kept_after[-1])
    return redundant


def chapter_break_target(marker):
    """closest_preceding_block(marker), or None when a forced break there
    would be redundant: nothing visible comes before the marker in its own
    file, so the file boundary is already the page break."""
    target = closest_preceding_block(marker)
    if target is None:
        return None
    # Visible content before the marker (other than blank blocks)?
    prev_text = marker.xpath(
        "preceding::text()[normalize-space(translate(., ' ', ' '))]"
        "[not(ancestor::*[local-name()='head'])]"
    )
    prev_media = marker.xpath(
        "preceding::*[local-name()='img' or local-name()='svg' or local-name()='image' or local-name()='hr']"
    )
    # Text inside the marker's own ancestors that precedes it is counted by
    # the XPath above already; ancestors' own text nodes before the marker
    # are in "preceding" as well.
    if not prev_text and not prev_media:
        return None
    # A forced break already sits between the last visible content and this
    # marker (on a blank spacer paragraph, say): marking the nearest block
    # again would only stack a second break and show an empty page.
    for el in reversed(marker.xpath("preceding::*")):
        if forced_break_props(el):
            return None   # a break-after OR break-before (an empty page-break <div>, say)
        name = etree.QName(el).localname.lower() if isinstance(el.tag, str) else ""
        if name in _VISIBLE_TAGS or (name not in _SKIP_TEXT and name != "head" and _own_visible(el, tail=False)) \
                or _own_visible(el, tail=True):
            # Last visible content: a wrapper around it (not around the
            # marker) may carry the break.
            marker_ancestors = {id(a) for a in marker.iterancestors()}
            for anc in el.iterancestors():
                if id(anc) in marker_ancestors:
                    break
                if forced_break_props(anc).keys() & {"page-break-after", "break-after"}:
                    return None
            break
    return target
