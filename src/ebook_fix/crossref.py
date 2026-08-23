"""
ebook_fix.crossref

Phase 2 of the XHTML Recoder plan (see docs/xhtml_recoder_plan.md):
cross-reference rewriting after a split. Phase 1 (splitter.py) cuts a
file into several standalone files but leaves every link that used to
point at the original file exactly as it was -- this module finds
those links (Phase 2b, below) and rewrites the ones it safely can
(Phase 2c, further down).

Deliberately in-body links only: a footnote, an endnote backlink, a
"see Chapter 5" cross-reference, anything living inside a chapter's
own document tree, which is the only kind of link this project can
currently mutate. The NCX and EPUB3 nav documents are parsed read-only
today (see parser.py) -- not live editable trees -- so an existing TOC
entry or nav link into a split file is a known, left-alone limitation
here, not a bug. Regenerating/repairing those is Phase 3's job.

Mirrors structure.py's _internal_link_targets in NOT resolving
relative directory paths -- same flat-directory assumption already
relied on (and tested against every sample book) there. If a real book
ever turns up with content files split across subdirectories, both
places would need updating together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lxml import etree

from .report import Report

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")


@dataclass(slots=True)
class LinkReference:
    """One <a> element found somewhere in the book whose target
    resolves to a file that was split during this run.

    `origin_href` is the file the link's target was originally written
    for, before any of this run's splits happened -- not necessarily
    where the <a> element itself currently lives (see home_href). A
    same-file link ("#fragment", no path) resolves to whatever file
    its own chapter *originally* was, which matters when a footnote
    and its backlink started in the same file but landed in different
    segments after the split.
    """

    home_href: str          # href of the chapter document the <a> currently lives in
    element: object          # the live <a> element (mutate href= directly to rewrite)
    origin_href: str         # href the link's target was originally written for
    fragment: str            # target fragment id, "" for a whole-file link


def find_links_into(book: Any, split_hrefs: set, current_href_origin: dict) -> list:
    """
    Scans every chapter currently in the book for <a href="..."> links
    whose target originally pointed into one of `split_hrefs` (the set
    of original hrefs split during this run).

    `current_href_origin` maps every href now in the book that came
    out of one of this run's splits back to the original href it was
    split from (including the original href itself, mapped to itself)
    -- needed to correctly resolve a same-file link that's no longer
    living in the file it was originally part of. A chapter untouched
    by this run's splits simply won't be a key in that dict, which is
    fine: its own href is its own origin.

    Returns every match, including whole-file links (no fragment) --
    it's Phase 2c's job to decide what, if anything, to do with those;
    this function only finds candidates, it doesn't rewrite anything.
    """
    results: list = []
    for chapter in getattr(book, "chapters", []) or []:
        doc = getattr(chapter, "document", None)
        home_href = getattr(chapter, "href", "")
        if doc is None:
            continue

        origin_of_home = current_href_origin.get(home_href, home_href)

        for el in doc.iter():
            if not isinstance(el.tag, str) or etree.QName(el).localname.lower() != "a":
                continue
            href = el.get("href") or ""
            if not href or href.startswith(EXTERNAL_PREFIXES):
                continue

            path, _, fragment = href.partition("#")
            origin_href = path if path else origin_of_home
            if origin_href not in split_hrefs:
                continue

            results.append(
                LinkReference(
                    home_href=home_href,
                    element=el,
                    origin_href=origin_href,
                    fragment=fragment,
                )
            )

    return results


# ---------------------------------------------------------------------
# Phase 2c -- rewriting the links Phase 2b found
# ---------------------------------------------------------------------


def rewrite_links(refs: list, href_by_id_by_origin: dict) -> Report:
    """
    Rewrites each LinkReference's href to point at wherever its target
    id now lives, using href_by_id_by_origin[origin_href] -- the
    per-split id map Phase 2a (splitter.build_href_by_id) produced for
    that particular original file. Keyed per-origin rather than
    merged into one flat dict on purpose: two different original files
    could each happen to use the same id string, and a flat map would
    silently cross-wire them.

    Two cases are deliberately left untouched rather than guessed at,
    each reported under its own category so a person reviewing the
    output can see them and decide by hand if either matters for their
    book:
    - Whole-file links (no fragment). The original file still exists
      (holding segment 0), so the link isn't broken -- it just no
      longer points at everything it used to. Picking a single "right"
      new target isn't this module's call to make.
    - A fragment whose id isn't in the relevant split's map at all.
      Shouldn't happen -- Phase 2a tracks every id that existed in the
      original file -- but checked defensively rather than trusted
      blindly, since a silent wrong guess would be worse than leaving
      it alone and saying so.

    A link whose target id maps back to the exact href it's already
    pointing at (link and target both stayed in the same file) is left
    alone too, without being reported -- there's nothing to fix.
    """
    report = Report("Cross-Reference Rewriter")

    for ref in refs:
        if not ref.fragment:
            report.add(
                ref.home_href,
                "Whole-file link left as-is",
                f"link into {ref.origin_href} has no fragment -- still resolves "
                f"to the original file, not rewritten",
            )
            continue

        id_map = href_by_id_by_origin.get(ref.origin_href)
        target_href = id_map.get(ref.fragment) if id_map else None
        if target_href is None:
            report.add(
                ref.home_href,
                "Unresolved target",
                f"#{ref.fragment}: id not found among {ref.origin_href}'s tracked ids",
            )
            continue

        old_href = ref.element.get("href")
        new_href = f"#{ref.fragment}" if target_href == ref.home_href else f"{target_href}#{ref.fragment}"
        if new_href == old_href:
            continue

        ref.element.set("href", new_href)
        report.add(
            ref.home_href,
            "Link rewritten",
            f"{old_href!r} -> {new_href!r}",
        )

    return report
