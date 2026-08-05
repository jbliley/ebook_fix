"""
ebook_fix.modules.chapter_markup

Turns the chapter boundaries found by ebook_fix.chapters (a confirmed,
in-order run of "Chapter Four" / "IV" / "FOURTH MACHINATION"-style
markers) into real structure: each chapter's content gets wrapped in
its own <section epub:type="chapter">, with a page break before it so
it starts on a fresh page in reflowable readers.

This is the repair module chapters.py's own docstring points at --
that module deliberately only detects and scores candidates, it
doesn't touch the DOM. This is where the detection actually gets
turned into a split.

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
  wrapped, and that's checked before wrapping again.
"""

from __future__ import annotations
from lxml import etree

from ebook_fix.chapters import analyze_book_chapters
from ebook_fix.report import Report

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

    def analyze(self, book):
        report = Report(self.name)
        if self.config is not None and not getattr(self.config, "enabled", True):
            return report

        summary = analyze_book_chapters(book)
        for i, candidate in enumerate(summary.confirmed_boundaries or [], start=1):
            block = self._find_block(candidate.element)
            if block is not None and block.get(MARKER_ATTR):
                continue
            report.add(
                candidate.href,
                "Chapter boundary to be split out",
                f"Chapter {i} ({candidate.text!r}) will be wrapped in its own "
                f"<section epub:type=\"chapter\">.",
            )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book):
        if self.config is not None and not getattr(self.config, "enabled", True):
            return

        summary = analyze_book_chapters(book)
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

        # Walk in reverse so earlier insertions don't shift the indices
        # of groups we haven't processed yet.
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
                element_tag_qname(parent, "section"),
                nsmap={"epub": EPUB_OPS_NS},
            )
            section.set(f"{{{EPUB_OPS_NS}}}type", "chapter")
            section.set("id", section_id)
            if not is_very_first_chapter:
                section.set("style", "page-break-before: always; break-before: page;")

            marker_block = slice_children[0]
            marker_block.set(MARKER_ATTR, "true")

            parent.insert(start_idx, section)
            for child in slice_children:
                section.append(child)  # lxml re-parents; also carries the child's tail text

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
