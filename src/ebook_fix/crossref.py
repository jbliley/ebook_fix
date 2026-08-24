"""
ebook_fix.crossref

Phase 2 and Phase 3b of the XHTML Recoder plan (see
docs/xhtml_recoder_plan.md): cross-reference rewriting after a split.
Phase 1 (splitter.py) cuts a file into several standalone files but
leaves every link that used to point at the original file exactly as
it was -- this module finds those links and rewrites the ones it
safely can.

Two independent halves, sharing the same href_by_id/current_href_origin
machinery Phase 2 built:

- In-body links (Phase 2, find_links_into / rewrite_links): a
  footnote, an endnote backlink, a "see Chapter 5" cross-reference,
  anything living inside a chapter's own document tree -- including
  the EPUB3 nav document itself, since nav.xhtml's media_type already
  makes it a normal Chapter with a live, editable tree like any other.
- NCX entries (Phase 3b, find_ncx_links_into / rewrite_ncx_links): a
  navPoint's <content src="..."> pointing into a file that got split.
  This half didn't exist until Phase 3a made book.ncx_document a live
  tree instead of read-only parsed data -- before that, an existing
  NCX entry into a split file was a known, left-alone limitation.

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

    `chapter` is the live Chapter object the <a> element lives in --
    needed so rewrite_links can flag it modified when it actually
    changes a link. Without this, a rewrite to a chapter that wasn't
    itself newly created by this run's split (nav.xhtml, or any other
    untouched chapter with a stray cross-reference into a split file)
    would mutate the in-memory tree correctly but never get written
    out, since the writer only re-serializes chapters flagged
    modified.
    """

    home_href: str          # href of the chapter document the <a> currently lives in
    element: object          # the live <a> element (mutate href= directly to rewrite)
    origin_href: str         # href the link's target was originally written for
    fragment: str            # target fragment id, "" for a whole-file link
    chapter: object = None   # the live Chapter the <a> element belongs to


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
                    chapter=chapter,
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
        if ref.chapter is not None:
            ref.chapter.modified = True
        report.add(
            ref.home_href,
            "Link rewritten",
            f"{old_href!r} -> {new_href!r}",
        )

    return report


# ---------------------------------------------------------------------
# Phase 3b -- NCX <content src="..."> rewriting
# ---------------------------------------------------------------------

NCX_NS_URI = "http://www.daisy.org/z3986/2005/ncx/"


@dataclass(slots=True)
class NcxReference:
    """One <content src="..."> element inside the book's NCX whose
    target resolves to a file that was split during this run.

    No home_href/chapter fields the way LinkReference has: a
    <content> element doesn't live inside one of the book's own
    chapter documents the way an <a> does, so there's no "which
    chapter is this link written for" question to resolve -- src is
    always a full path already, resolved against the NCX file's own
    location once, back in parser.py. A single book has (at most) one
    NCX, so there's nothing per-reference to track beyond the element
    itself and what it's pointing at.
    """

    element: object   # the live <content> element (mutate src= directly to rewrite)
    origin_href: str  # href the link's target was originally written for
    fragment: str     # target fragment id, "" for a whole-file link


def find_ncx_links_into(book: Any, split_hrefs: set) -> list:
    """
    Scans the book's NCX (book.ncx_document, see Phase 3a) for every
    <content src="..."> element whose target originally pointed into
    one of `split_hrefs`.

    No current_href_origin lookup needed here the way find_links_into
    needs one for in-body links: an NCX <content> src is always
    written as a full path (there's no "bare #fragment" shorthand the
    way a same-file <a href="#frag"> can use, since a navPoint isn't
    "inside" any particular content file itself), so origin resolution
    is a direct split_hrefs membership check on whatever path is
    already there.

    Returns every match, including whole-file entries (no fragment) --
    same "found, not yet judged" split find_links_into already uses;
    it's rewrite_ncx_links's job to decide what to do with those.
    """
    ncx = getattr(book, "ncx_document", None)
    if ncx is None:
        return []

    results: list = []
    for content in ncx.iter():
        if not isinstance(content.tag, str):
            continue
        if etree.QName(content).localname != "content":
            continue
        src = content.get("src") or ""
        if not src:
            continue

        path, _, fragment = src.partition("#")
        if path not in split_hrefs:
            continue

        results.append(
            NcxReference(element=content, origin_href=path, fragment=fragment)
        )

    return results


def rewrite_ncx_links(book: Any, refs: list, href_by_id_by_origin: dict) -> Report:
    """
    Rewrites each NcxReference's src to point at wherever its target
    id now lives, using href_by_id_by_origin[origin_href] -- the same
    per-split id map rewrite_links already reads. Sets book.ncx_modified
    when anything actually changes, so the writer knows to serialize
    book.ncx_document back out (see Phase 3a) instead of copying the
    original NCX bytes through untouched.

    Same two deliberately-untouched cases as rewrite_links, for the
    same reasons -- a whole-file entry isn't broken (the original
    file still exists as segment 0), and an unresolved fragment is
    reported rather than guessed at:
    """
    report = Report("NCX Rewriter")

    for ref in refs:
        location = getattr(book, "ncx_href", "") or "toc.ncx"

        if not ref.fragment:
            report.add(
                location,
                "Whole-file entry left as-is",
                f"navPoint into {ref.origin_href} has no fragment -- still "
                f"resolves to the original file, not rewritten",
            )
            continue

        id_map = href_by_id_by_origin.get(ref.origin_href)
        target_href = id_map.get(ref.fragment) if id_map else None
        if target_href is None:
            report.add(
                location,
                "Unresolved target",
                f"#{ref.fragment}: id not found among {ref.origin_href}'s tracked ids",
            )
            continue

        old_src = ref.element.get("src")
        new_src = f"{target_href}#{ref.fragment}"
        if new_src == old_src:
            continue

        ref.element.set("src", new_src)
        book.ncx_modified = True
        report.add(
            location,
            "Entry rewritten",
            f"{old_src!r} -> {new_src!r}",
        )

    return report
