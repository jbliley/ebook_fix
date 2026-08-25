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
from pathlib import PurePosixPath
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


# ---------------------------------------------------------------------
# Phase 3c -- generating new NCX entries for chapters that split apart
# with no entry of their own
# ---------------------------------------------------------------------


def _ncx_target_hrefs(ncx_document) -> dict:
    """Maps every distinct file path a <content src="..."> already
    points at (fragment stripped) to the last top-level navPoint
    element found targeting it, in document order. "Last" rather than
    "first" so an href with several existing fragment entries (a file
    that already had multiple chapters listed) anchors on the one
    closest to wherever a new entry needs to be inserted."""
    nav_map = ncx_document.find(f"{{{NCX_NS_URI}}}navMap")
    if nav_map is None:
        return {}
    by_href = {}
    for point in nav_map.findall(f"{{{NCX_NS_URI}}}navPoint"):
        content = point.find(f"{{{NCX_NS_URI}}}content")
        src = content.get("src") if content is not None else None
        if not src:
            continue
        path, _, _ = src.partition("#")
        if path:
            by_href[path] = point
    return by_href


def _renumber_play_order(nav_map) -> None:
    """Resequences every top-level navPoint's playOrder 1, 2, 3... in
    its current document order. Only touches playOrder if at least one
    existing navPoint already used it -- a minimal NCX with no
    playOrder attributes at all is left in that same minimal style."""
    points = nav_map.findall(f"{{{NCX_NS_URI}}}navPoint")
    if not any(p.get("playOrder") for p in points):
        return
    for i, point in enumerate(points, start=1):
        point.set("playOrder", str(i))


def generate_missing_ncx_entries(book: Any, split_hrefs: set, new_hrefs_by_origin: dict) -> Report:
    """
    Phase 3c of the XHTML Recoder plan (see docs/xhtml_recoder_plan.md):
    when a file with an existing NCX entry splits into several chapter
    files, only whichever file kept that entry's target (after Phase
    3b's rewriting) ends up listed -- every other new file is a real,
    distinct chapter with no entry of its own. This adds one.

    Deliberately narrow in scope, matching Jacob's three-case framework
    (see the memory note from the session this was built in):
    - A file whose split produced no NCX coverage at all (none of its
      resulting hrefs match any existing <content src>) is left alone
      here -- that book has no TOC to extend in the first place, which
      is a bigger job (generating one from nothing) than filling a gap
      next to an entry that already exists. Reported as skipped so
      it's visible, not silently dropped.
    - Assumes a flat NCX (no nested Parts/sections) -- same assumption
      find_ncx_links_into/rewrite_ncx_links already make about a
      single book's worth of navPoints. A book with a nested TOC would
      need Phase 4's structure work first.

    New entries reuse the chapter's own already-detected title
    (chapter.title, set from the structure analyzer's marker text
    during the split -- see splitter.py's SplitSegment/_wire_into_book)
    rather than inventing one, per Jacob's preference to keep as much
    of a book's own original structure as the analysis already found.
    Whole-file entries only (no fragment) -- a newly created split file
    has no finer internal structure of its own to point a fragment at.

    Sets book.ncx_modified when anything is actually added, so the
    writer re-serializes book.ncx_document (see Phase 3a).
    """
    report = Report("NCX Entry Generator")
    ncx = getattr(book, "ncx_document", None)
    if ncx is None:
        return report

    nav_map = ncx.find(f"{{{NCX_NS_URI}}}navMap")
    if nav_map is None:
        return report

    location = getattr(book, "ncx_href", "") or "toc.ncx"
    chapters_by_href = {c.href: c for c in getattr(book, "chapters", []) or []}

    # A style template to copy onto generated navPoints -- whatever
    # attribute/id-prefix convention this book's own NCX already uses,
    # so a generated entry doesn't stand out from the ones the book
    # shipped with.
    existing_points = nav_map.findall(f"{{{NCX_NS_URI}}}navPoint")
    template_class = next((p.get("class") for p in existing_points if p.get("class")), None)
    existing_ids = {p.get("id") for p in existing_points if p.get("id")}

    for origin_href in split_hrefs:
        new_hrefs = new_hrefs_by_origin.get(origin_href, [])
        all_hrefs = [origin_href] + list(new_hrefs)

        target_hrefs = _ncx_target_hrefs(ncx)
        if not any(href in target_hrefs for href in all_hrefs):
            report.add(
                location,
                "Skipped -- no existing entry to extend",
                f"{origin_href}: none of this split's files had an existing "
                f"NCX entry; generating a TOC from nothing is out of scope here",
            )
            continue

        anchor = None
        for href in all_hrefs:
            existing = target_hrefs.get(href)
            if existing is not None:
                anchor = existing
                continue

            chapter = chapters_by_href.get(href)
            title = chapter.title if chapter is not None else ""
            if not title:
                report.add(
                    location,
                    "Skipped -- no title to use",
                    f"{href}: split produced this file with no detected "
                    f"chapter title, nothing usable for a new entry's label",
                )
                continue

            point = etree.SubElement(nav_map, f"{{{NCX_NS_URI}}}navPoint")
            if template_class:
                point.set("class", template_class)
            new_id = "navpoint-" + PurePosixPath(href).stem
            if new_id in existing_ids:
                new_id = _unique_id(existing_ids, new_id)
            existing_ids.add(new_id)
            point.set("id", new_id)

            label_el = etree.SubElement(point, f"{{{NCX_NS_URI}}}navLabel")
            text_el = etree.SubElement(label_el, f"{{{NCX_NS_URI}}}text")
            text_el.text = title
            content_el = etree.SubElement(point, f"{{{NCX_NS_URI}}}content")
            content_el.set("src", href)

            if anchor is not None:
                anchor.addnext(point)
            else:
                nav_map.insert(0, point)
            anchor = point

            book.ncx_modified = True
            report.add(location, "Entry generated", f"{href!r} labeled {title!r}")

    if report.count:
        _renumber_play_order(nav_map)

    return report


def _unique_id(existing: set, candidate: str) -> str:
    """Same de-duplication idiom used elsewhere in this project
    (splitter._unique) -- appends _2, _3, ... until free."""
    if candidate not in existing:
        return candidate
    n = 2
    while f"{candidate}_{n}" in existing:
        n += 1
    return f"{candidate}_{n}"
