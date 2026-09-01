"""
ebook_fix.case3_structural

Second Case 3 chapter-boundary detection path (see Jacob's three-case
framework in docs/xhtml_recoder_plan.md, and the case3 section of
chapters.py just above analyze_case3_book_chapters). That function
looks for an unlabeled numbered title ("1. The Horror in Clay."); this
module is the fallback for books with neither a label word ("Chapter",
"Part") NOR a bare numbered title anywhere. The only thing left
marking where one chapter ends and the next begins, in that kind of
book, is a structural divider: a bare <hr>, or an element carrying a
forced page-break (via CSS class/id or an inline style="" attribute),
sitting between two stretches of real prose.

Only ever called after BOTH the normal analysis
(chapters.analyze_book_chapters) and the title-based Case 3 path
(chapters.analyze_case3_book_chapters) have already come back empty --
see structure.analyze_case3_structure, which is the actual entry point
map-structure / repair --case3-boundaries calls, and is what decides
when to fall back to this module.

This produces the exact same ChapterCandidate/BookChapterSummary
shapes chapters.py does (reusing chapters.py's own sequence-validation
machinery -- see _find_best_sequence below), so nothing downstream
(structure.py, case3_map.py) has to know or care which of the two
Case 3 paths actually found a book's boundaries.

Numbering a positional signal
------------------------------
Unlike every other marker style, a structural divider carries no
number and no title of its own -- it's just a position. To still ride
chapters.py's existing sequence-validation dynamic-programming search
(which expects each style's candidates to count upward), each
divider's `number` is fabricated as its 1-based rank among same-style
dividers in book order (1, 2, 3, ...). That's not a claim about which
chapter number the divider actually starts -- it's purely a bookkeeping
trick that turns "every one of these is a candidate boundary" into a
sequence _find_best_sequence already knows how to validate (a perfect
run with gap 1 throughout, so nothing gets excluded on that basis
alone; content-length and structural-cleanliness are what actually
filter these downstream, same as any other case3 candidate -- see
structure.apply_content_length_check / apply_structural_cleanliness_check).

Two divider kinds, each its own MarkerStyle
--------------------------------------------
<hr> dividers and forced-page-break markers are kept as two separate
MarkerStyle values (UNLABELED_STRUCTURAL_HR / UNLABELED_STRUCTURAL_PAGE_BREAK)
rather than merged into one pool. _find_best_sequence groups by style
and picks whichever group scores best -- if a book happens to use both
conventions, whichever one is used consistently throughout naturally
wins rather than the two diluting each other. A book that only uses
one convention just has an empty group for the other, which
_find_best_sequence already handles as "no sequence for this style."

Where the boundary actually starts
------------------------------------
A page-break-before marker sits on the first element of the new
chapter, the same way a heading candidate would -- that element
becomes the candidate's `.element` directly. A page-break-after marker
sits on the *last* element of the outgoing chapter, so the candidate's
`.element` is whatever comes right after it instead (see
page_breaks.closest_following_block). A bare <hr> isn't itself part of
either chapter, so it always resolves the same way a page-break-after
marker does: `.element` is whatever block-level content follows it. If
nothing block-level follows within the same file, the divider is
dropped -- that's effectively already sitting at the file's own
boundary, not a new split point inside it (see
page_breaks.closest_following_block's own docstring).

Content-length pre-filter
---------------------------
Nothing stops a book from using <hr> as pure decoration between every
paragraph, or a forced page-break class applied far more liberally
than actual chapter starts. Left unfiltered, that would flood a
person's review file with dozens of one-paragraph "chapters" instead
of a handful of real ones. MIN_STRUCTURAL_SIDE_WORDS below requires a
reasonable amount of real text on both sides of a divider -- measured
only within the same file, since text_range.py's helpers don't span
files (structure.apply_content_length_check's cross-file version runs
later and is the authoritative check; this is a coarse, cheap filter
to keep the review file itself readable, not the final word on any
one boundary).
"""

from __future__ import annotations

import re

from lxml import etree

from ebook_fix.chapters import (
    BookChapterSummary,
    ChapterCandidate,
    MarkerStyle,
    _find_best_sequence,  # noqa: reused intentionally -- see module docstring
)
from ebook_fix.css import (
    CLASS_SELECTOR_RE,
    COMMENT_RE,
    ID_SELECTOR_RE,
    INJECTED_STYLE_ID_PREFIX,
    RULE_RE,
    read_book_css,
)
from ebook_fix.page_breaks import closest_following_block, is_block
from ebook_fix.text_range import text_before, text_range, text_to_end_of_doc

# Mirrors structure.MIN_CHAPTER_WORDS's own bar. Kept as a separate
# constant instead of importing that one, to avoid a circular import
# (structure.py needs to import this module to call it as a fallback).
MIN_STRUCTURAL_SIDE_WORDS = 50

_WORD_RE = re.compile(r"\S+")

_BREAK_PROPERTY_RE = re.compile(
    r"(page-break-before|page-break-after|break-before|break-after)\s*:\s*([a-zA-Z-]+)"
)
# Values that explicitly do NOT force a break -- everything else
# ("always", "page", "left", "right", etc.) counts as forcing one.
_NON_FORCING_VALUES = {"avoid", "auto", "inherit", "initial", "unset"}

_PREVIEW_WORD_LIMIT = 8


def _words(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def _forcing_declarations(body: str) -> tuple[bool, bool]:
    """Does this rule/style body force a page-break-before, and/or a
    page-break-after? Ignores declarations whose value doesn't
    actually force a break (page-break-before: avoid, etc.)."""
    before = False
    after = False
    for m in _BREAK_PROPERTY_RE.finditer(body.lower()):
        prop, value = m.group(1), m.group(2)
        if value in _NON_FORCING_VALUES:
            continue
        if "before" in prop:
            before = True
        else:
            after = True
    return before, after


def _page_break_selectors(css_text: str) -> tuple[set, set, set, set]:
    """Scan one stylesheet's text for selectors that force a
    page-break-before/after, split into (class_before, class_after,
    id_before, id_after) name sets."""
    class_before: set = set()
    class_after: set = set()
    id_before: set = set()
    id_after: set = set()
    if not css_text:
        return class_before, class_after, id_before, id_after

    text = COMMENT_RE.sub("", css_text)
    for m in RULE_RE.finditer(text):
        selector = m.group(1).strip()
        body = m.group(2)
        if not selector or selector.startswith("@"):
            continue
        before, after = _forcing_declarations(body)
        if not before and not after:
            continue
        classes = [cm.group(1) for cm in CLASS_SELECTOR_RE.finditer(selector)]
        ids = [im.group(1) for im in ID_SELECTOR_RE.finditer(selector)]
        if before:
            class_before.update(classes)
            id_before.update(ids)
        if after:
            class_after.update(classes)
            id_after.update(ids)
    return class_before, class_after, id_before, id_after


def _collect_page_break_selectors(book) -> tuple[set, set, set, set]:
    """Same four-set shape as _page_break_selectors, merged across
    every external stylesheet (book.css) and every chapter's own
    embedded <style> block -- skipping any <style id="ebookfix-...">
    block ebook_fix itself injected (see css.INJECTED_STYLE_ID_PREFIX);
    this is analysis meant to run on a book before any of ebook_fix's
    own repairs have touched it."""
    class_before: set = set()
    class_after: set = set()
    id_before: set = set()
    id_after: set = set()

    for css_text in read_book_css(book).values():
        cb, ca, ib, ia = _page_break_selectors(css_text or "")
        class_before |= cb
        class_after |= ca
        id_before |= ib
        id_after |= ia

    for chapter in getattr(book, "chapters", []) or []:
        tree = getattr(chapter, "document", None)
        if tree is None:
            continue
        root = tree if hasattr(tree, "iter") else tree.getroot()
        if root is None:
            continue
        for el in root.iter():
            if not isinstance(el.tag, str) or etree.QName(el).localname.lower() != "style":
                continue
            style_id = el.get("id", "") or ""
            if style_id.startswith(INJECTED_STYLE_ID_PREFIX):
                continue
            cb, ca, ib, ia = _page_break_selectors(el.text or "")
            class_before |= cb
            class_after |= ca
            id_before |= ib
            id_after |= ia

    return class_before, class_after, id_before, id_after


def _element_forces_break(el, class_before, class_after, id_before, id_after) -> tuple[bool, bool]:
    """Does this specific element carry a forced page-break-before
    and/or page-break-after, whether via its own inline style="" or
    via a class/id a stylesheet rule targets?"""
    before, after = _forcing_declarations(el.get("style") or "")

    classes = (el.get("class") or "").split()
    if any(c in class_before for c in classes):
        before = True
    if any(c in class_after for c in classes):
        after = True

    el_id = el.get("id") or ""
    if el_id and el_id in id_before:
        before = True
    if el_id and el_id in id_after:
        after = True

    return before, after


def _preview_text(el) -> str:
    """A short snippet of whatever comes right after a divider, for a
    person reviewing the case3-boundaries file to recognize the spot
    by -- these candidates have no title of their own to show them."""
    words = "".join(el.itertext()).split()
    if not words:
        return ""
    snippet = " ".join(words[:_PREVIEW_WORD_LIMIT])
    if len(words) > _PREVIEW_WORD_LIMIT:
        snippet += "..."
    return snippet


def _resolved_positions(href: str, root, class_before, class_after, id_before, id_after) -> list:
    """Every candidate divider in this one file, resolved to the
    actual element where the new chapter would start (see module
    docstring's "where the boundary actually starts") and de-duplicated
    by that resolved element -- a bare <hr> immediately followed by a
    forced-page-break element, or a page-break-after element
    immediately followed by a page-break-before one, both resolve to
    the same spot and should only produce one candidate, not two.

    Returns a list of (kind, resolved_element) tuples, `kind` being
    "hr" or "page-break", in document order. Per the "never track seen
    elements using id()" lesson learned elsewhere in this project, de-
    duplication below tracks the elements themselves in a set, not
    their id()s.
    """
    found: list = []
    seen: set = set()

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = etree.QName(el).localname.lower()

        kind = None
        resolved = None
        if local == "hr":
            kind = "hr"
            resolved = closest_following_block(el)
        elif local != "style":
            before, after = _element_forces_break(el, class_before, class_after, id_before, id_after)
            if before:
                kind = "page-break"
                resolved = el
            elif after:
                kind = "page-break"
                resolved = closest_following_block(el)

        if resolved is None or kind is None:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append((kind, resolved))

    # root.iter() already visits in document order, but the mix of
    # <hr>-triggered and page-break-triggered lookups above can resolve
    # to elements out of the order they were *found* in (a page-break-
    # after element resolves to something later than itself) -- sort by
    # actual tree position to be sure.
    order = {el: i for i, el in enumerate(root.iter()) if isinstance(el.tag, str)}
    found.sort(key=lambda pair: order.get(pair[1], 0))
    return found


def _candidates_in_chapter(href: str, tree, class_before, class_after, id_before, id_after) -> list:
    if tree is None:
        return []
    root = tree if hasattr(tree, "iter") else tree.getroot()
    if root is None:
        return []

    positions = _resolved_positions(href, root, class_before, class_after, id_before, id_after)
    if not positions:
        return []

    candidates = []
    for i, (kind, element) in enumerate(positions):
        if i == 0:
            before_text = text_before(root, element)
        else:
            before_text = text_range(positions[i - 1][1], element)

        if i + 1 < len(positions):
            after_text = text_range(element, positions[i + 1][1])
        else:
            after_text = text_to_end_of_doc(element)

        if _words(before_text) < MIN_STRUCTURAL_SIDE_WORDS:
            continue
        if _words(after_text) < MIN_STRUCTURAL_SIDE_WORDS:
            continue

        style = (
            MarkerStyle.UNLABELED_STRUCTURAL_HR if kind == "hr"
            else MarkerStyle.UNLABELED_STRUCTURAL_PAGE_BREAK
        )
        preview = _preview_text(element)
        candidates.append(
            ChapterCandidate(
                href=href,
                tag=etree.QName(element).localname.lower(),
                text=f'[{"divider" if kind == "hr" else "page-break marker"}] "{preview}"' if preview else f"[{kind}]",
                style=style,
                label_kind=kind,
                isolated=False,
                is_heading_tag=False,
                css_hint=(kind == "page-break"),
                score=1.0,
                element=element,
            )
        )
    return candidates


def analyze_case3_structural_chapters(book) -> BookChapterSummary:
    """Structural-divider counterpart to
    chapters.analyze_case3_book_chapters. Callers should only reach
    for this after confirming BOTH the normal analysis and the
    title-based Case 3 path already found nothing -- see
    structure.analyze_case3_structure, which is what map-structure
    actually calls and decides when to fall back to this.
    """
    class_before, class_after, id_before, id_after = _collect_page_break_selectors(book)

    all_candidates: list = []
    order = 0
    for chapter in book.chapters:
        href = getattr(chapter, "href", "")
        tree = getattr(chapter, "document", None)
        chapter_candidates = _candidates_in_chapter(href, tree, class_before, class_after, id_before, id_after)
        for c in chapter_candidates:
            order += 1
            c.book_order = order
        all_candidates.extend(chapter_candidates)

    summary = BookChapterSummary(candidates=all_candidates)
    if not all_candidates:
        return summary

    # Fabricate each style's own 1-based rank in book order as its
    # `number`, so _find_best_sequence's counting-up validation has
    # something to check against -- see module docstring.
    by_style: dict = {}
    for c in sorted(all_candidates, key=lambda c: c.book_order):
        by_style.setdefault(c.style, []).append(c)
    for group in by_style.values():
        for i, c in enumerate(group, start=1):
            c.number = i

    best, all_sequences = _find_best_sequence(all_candidates)
    summary.best_sequence = best
    summary.other_sequences = [s for s in all_sequences if s is not best]

    if best is not None:
        for c in best.candidates:
            c.confirmed = True
        summary.confirmed_boundaries = list(best.candidates)

    return summary
