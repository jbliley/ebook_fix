"""
ebook_fix.modules.chapter_markup

Turns the chapter boundaries found by ebook_fix.chapters (a confirmed,
in-order run of "Chapter Four" / "IV" / "FOURTH MACHINATION"-style
markers) into real structure: each chapter's content gets wrapped in
its own <div epub:type="chapter">, with a page break so it starts on
a fresh page in reflowable readers.

This is the repair module chapters.py's own docstring points at --
that module deliberately only detects and scores candidates, it
doesn't touch the DOM. This is where the detection actually gets
turned into a split.

The page break itself is page-break-after on the block immediately
before the new chapter (see ebook_fix.page_breaks for the walk/marking
logic and why -after instead of -before). This used to be three
separate page-break-before injections stacked on top of each other --
an embedded <style> block, the wrapper div's inline style, and the
marker element's own inline style -- which was not just redundant but
actively working against the more reliable property. It's now one
property, one location, shared with class_standardize.py's own
chapter-heading handling so the two don't fight each other either.

Scope / limitations
--------------------
- Only chapters chapters.py is confident about (book.chapters via
  analyze_book_chapters(...).best_sequence) get split. Weak or
  ambiguous candidates are left alone rather than guessed at.
- A chapter marker's *block* ancestor (the nearest p/div/li/h1-h6, since
  markers are often an inline <span> sitting inside a <p>) is what
  actually gets wrapped, along with every sibling after it up to the
  next confirmed marker (or the end of that parent) -- this only
  reliably merges markers that share the same parent element, which
  covers the common "one flat sequence of <p> tags" case. A marker
  whose siblings live under a different parent than the previous
  marker starts its own group there instead of being skipped.
- Re-running this module on an already-split book is a no-op: each
  marker's block is stamped with a data attribute the first time it's
  wrapped, and that's checked before wrapping again. The page-break
  marker itself is separately idempotent too (see
  ebook_fix.page_breaks.mark_page_break_after).
- The very first chapter in the book's spine gets no page break at
  all -- it's already the start of the book, there's nothing to break
  away from.
"""

from __future__ import annotations
from lxml import etree

from ebook_fix.chapters import analyze_book_chapters
from ebook_fix.report import Report
from ebook_fix.page_breaks import closest_preceding_block, mark_page_break_after

EPUB_OPS_NS = "http://www.idpf.org/2007/ops"
MARKER_ATTR = "data-ebookfix-chapter"

BLOCK_TAGS = {"p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}


class ChapterMarkupRepair:
    name = "Chapter Markup"

    def __init__(self, config=None):
        self.config = config

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        if self.config is not None and not getattr(self.config, "enabled", True):
            return report

        summary = analysis.chapters if analysis is not None else analyze_book_chapters(book)
        for i, candidate in enumerate(summary.confirmed_boundaries or [], start=1):
            block = self._find_block(candidate.element)
            if block is not None and block.get(MARKER_ATTR):
                continue
            report.add(
                candidate.href,
                "Chapter boundary to be split out",
                f"Chapter {i} ({candidate.text!r}) will be wrapped in its own "
                f"<div epub:type=\"chapter\">.",
            )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        if self.config is not None and not getattr(self.config, "enabled", True):
            return

        summary = analysis.chapters if analysis is not None else analyze_book_chapters(book)
        confirmed = summary.confirmed_boundaries or []
        if not confirmed:
            return

        # Group confirmed markers by (href, parent element) so each
        # group can be sliced into sections independently.
        chapters_by_id = {c.id: c for c in book.chapters}
        groups: dict = {}   # (href, id(parent)) -> {"parent": el, "blocks": [...]}
        order = []           # preserves first-seen order of groups

        for candidate in confirmed:
            block = self._find_block(candidate.element)
            if block is None or block.get(MARKER_ATTR):
                continue  # nothing to anchor on, or already split by an earlier run
            parent = block.getparent()
            if parent is None:
                continue  # can't wrap the document root itself

            key = (candidate.href, id(parent))
            if key not in groups:
                groups[key] = {"parent": parent, "blocks": []}
                order.append(key)
            groups[key]["blocks"].append(block)

        if not groups:
            return

        chapter_number = 0
        for key in order:
            href, _ = key
            chapter = self._chapter_for_href(book, href)
            if chapter is None:
                continue
            group = groups[key]
            changed = self._split_group(book, chapter, group["parent"], group["blocks"], chapter_number)
            chapter_number += len(group["blocks"])
            if changed:
                chapter.modified = True

        book.mark_modified()

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _chapter_for_href(self, book, href):
        for chapter in book.chapters:
            if chapter.href == href:
                return chapter
        return None

    def _find_block(self, element):
        """Walk up from a (possibly inline) marker element to the
        nearest block-level ancestor -- that's the real chapter-start
        paragraph/heading, not just the <span> inside it."""
        node = element
        while node is not None:
            tag = element_tag(node)
            if tag in BLOCK_TAGS:
                return node
            parent = node.getparent()
            if parent is None:
                return node
            node = parent
        return None

    def _split_group(self, book, chapter, parent, blocks, start_number):
        children = list(parent)
        indices = []
        for block in blocks:
            try:
                indices.append(children.index(block))
            except ValueError:
                continue
        if not indices:
            return False

        used_ids = {el.get("id") for el in chapter.document.iter() if el.get("id")}
        changed = False

        for pos, start_idx in reversed(list(enumerate(indices))):
            end_idx = indices[pos + 1] if pos + 1 < len(indices) else len(children)
            slice_children = children[start_idx:end_idx]
            if not slice_children:
                continue

            chapter_number = start_number + pos + 1
            is_very_first_chapter = (
                bool(book.spine) and book.spine[0] == chapter.id
                and start_idx == 0 and chapter_number == 1
            )

            section_id = self._unique_id(used_ids, f"chapter-{chapter_number}")
            used_ids.add(section_id)

            section = etree.Element(
                element_tag_qname(parent, "div"),
                nsmap={"epub": EPUB_OPS_NS},
            )
            section.set(f"{{{EPUB_OPS_NS}}}type", "chapter")
            section.set("id", section_id)
            section.set("class", "ebookfix-chapter-wrapper")

            marker_block = slice_children[0]
            marker_block.set(MARKER_ATTR, "true")

            if not is_very_first_chapter:
                section.set(
                    "style",
                    "display: block !important; margin-top: 0 !important; padding-top: 1px !important;"
                )
                existing_style = marker_block.get("style", "")
                marker_block.set("style", f"clear: both; {existing_style}".strip())

                # The page break itself: page-break-after on the nearest
                # actual block of content before this marker, not
                # page-break-before on the marker. Computed now, while
                # marker_block is still in its original tree position --
                # see ebook_fix.page_breaks for why -after and not
                # -before, and why this has to be a DOM walk rather than
                # a CSS rule.
                break_target = closest_preceding_block(marker_block)

            # --- STEP B: CLEAN UP ANCHORS FROM PRECEDING SIBLING ---
            # Check if the element directly preceding this chapter marker has anchor tags at the end
            preceding_node = marker_block.getprevious()
            anchors_to_move = []

            if preceding_node is not None:
                # Find <a> tags inside the preceding paragraph/block
                for anchor in preceding_node.findall(".//a"):
                    # Check if it looks like a chapter target (has id/name)
                    anchor_id = anchor.get("id") or anchor.get("name") or ""
                    if anchor_id:
                        anchors_to_move.append(anchor)

            # Reparent extracted anchors into the new chapter div
            for anchor in anchors_to_move:
                anchor.getparent().remove(anchor)
                section.append(anchor)
            # -----------------------------------------------------

            if not is_very_first_chapter and break_target is not None:
                mark_page_break_after(break_target)

            parent.insert(start_idx, section)
            for child in slice_children:
                section.append(child)

            changed = True

        return changed

    def _unique_id(self, used_ids, candidate):
        if candidate not in used_ids:
            return candidate
        n = 2
        while f"{candidate}-{n}" in used_ids:
            n += 1
        return f"{candidate}-{n}"


def element_tag(el):
    if not isinstance(el.tag, str):
        return ""
    return etree.QName(el).localname.lower()


def element_tag_qname(sibling, local_name):
    """Build a namespace-qualified tag for a new element so it matches
    the namespace the rest of the document is already using (almost
    always the XHTML namespace), instead of coming out namespace-less."""
    tag = sibling.tag
    if isinstance(tag, str) and tag.startswith("{"):
        ns = tag[1:].split("}", 1)[0]
        return f"{{{ns}}}{local_name}"
    return local_name