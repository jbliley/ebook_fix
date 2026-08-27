"""
ebook_fix.structure

Data shapes for a book's "structure tree" -- front matter, chapters,
and back matter, possibly nested under Parts/Books/Volumes -- along
with the per-boundary confidence and evidence needed before the
eventual splitter is allowed to trust a boundary enough to physically
cut a file there.

This file is Phase 0c of the XHTML Recoder plan (see
docs/xhtml_recoder_plan.md): blueprint only. Nothing in here detects
anything yet -- that's Phase 0d onward, which will assemble a
BookStructure by reading chapters.py's output (see chapters.py /
BookChapterSummary) and filling in BoundaryEvidence from there.

The confidence requirements these shapes exist to hold are proposed in
docs/split_safety_bar.md: a boundary needs to be part of the winning
sequence *and* corroborated by something outside the marker text
itself (an existing TOC entry or internal bookmark) *and* clear a
minimum-content and structural-cleanliness check before it's ever
eligible to be split on. A book with no corroborating signal at all
is never auto-split -- see that doc's "Decided" section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lxml import etree

from .models import TocEntry

# ---------------------------------------------------------------------
# Thresholds used by the confidence scoring in Phase 0g (see the
# bottom of this file). Both are explicitly placeholders -- as
# docs/split_safety_bar.md says for requirement 3, "exact number TBD
# once we have more sample books to test against". Pick these back up
# once the splitter (Phase 1+) has run against more real books.
# ---------------------------------------------------------------------

# Requirement 3: how much the winning sequence's score_sum has to beat
# the runner-up's before the book is trusted as unambiguous. Below
# this, BoundaryEvidence.confidence routes every boundary in that
# sequence to NEEDS_REVIEW regardless of corroboration -- see that
# property below.
MIN_SEQUENCE_MARGIN = 3.0

# Requirement 4: the minimum word count a chapter's resulting slice
# needs to clear before it's treated as a real chapter rather than a
# stray heading or scene-divider that happened to score well. See
# apply_content_length_check below.
MIN_CHAPTER_WORDS = 50

# ---------------------------------------------------------------------
# What kind of thing a structure node represents
# ---------------------------------------------------------------------


class NodeKind(Enum):
    FRONT_MATTER = "front matter"
    CHAPTER = "chapter"
    BACK_MATTER = "back matter"
    PART = "part"  # a Book/Part/Volume grouping multiple chapters


# ---------------------------------------------------------------------
# Confidence, as a single readable label rather than a raw number.
# See docs/split_safety_bar.md for what each level is meant to mean.
# ---------------------------------------------------------------------


class SplitConfidence(Enum):
    # No usable boundary here at all (e.g. the book edge, or a
    # candidate that never made it into any sequence).
    NONE = "none"
    # Part of chapters.py's winning sequence, nothing more -- today's
    # existing bar. Not enough to split on by itself.
    SEQUENCE_ONLY = "sequence only"
    # Sequence membership plus at least one corroborating signal
    # (matching TOC entry or existing internal bookmark) and it clears
    # the minimum-content and structural-cleanliness checks. This is
    # the only level that should ever be eligible for an automatic
    # split.
    CORROBORATED = "corroborated"
    # Some evidence exists but it's mixed or borderline (e.g. right at
    # the sequence-margin cutoff, or corroborated but structurally
    # unclean) -- always route to a person rather than guessing.
    NEEDS_REVIEW = "needs review"


# ---------------------------------------------------------------------
# Evidence behind one boundary
# ---------------------------------------------------------------------


@dataclass(slots=True)
class BoundaryEvidence:
    """Everything backing up (or failing to back up) one candidate
    chapter-start boundary. One of these belongs to each StructureNode
    that isn't the very first node in the book (the book's own start
    isn't itself a "boundary" that needed detecting)."""

    # Link back to the chapters.py candidate this boundary came from,
    # so downstream code can still reach its score, marker text, style,
    # and live element reference without this module re-deriving them.
    candidate: Any = None

    # -- Requirement 1: sequence membership (today's existing bar) --
    in_winning_sequence: bool = False
    sequence_score: float = 0.0

    # -- Requirement 2: corroboration from outside the marker text --
    # (Phase 0e / 0f fill these in; both False until then.)
    matched_toc_entry: TocEntry | None = None
    matched_anchor_id: str = ""

    # -- Requirement 3: margin over the runner-up sequence --
    # Lives here rather than only at the book level so a boundary
    # carries its own reasoning even if inspected in isolation; should
    # match BookStructure.sequence_margin for the sequence this
    # boundary belongs to.
    sequence_margin: float | None = None

    # -- Requirement 4: minimum content on each side of the cut --
    content_length_ok: bool | None = None  # None = not yet checked

    # -- Requirement 5: structurally safe to cut at --
    structurally_clean: bool | None = None  # None = not yet checked

    # Case 3 only (see chapters.analyze_case3_book_chapters and
    # build_case3_structure below): True marks this evidence as coming
    # from the weaker "no label word, no TOC" detection path. Books
    # this evidence belongs to have already been sorted, per Jacob's
    # three-case framework, into "nothing reliable enough to
    # corroborate against" -- so this hard-caps confidence at
    # NEEDS_REVIEW below regardless of anything else, rather than
    # relying on corroboration simply never firing (which would be
    # true today but silently stop being true if a future change ever
    # ran TOC/anchor corroboration over case 3 evidence too).
    case3: bool = False

    # Free-text notes explaining *why* confidence landed where it did
    # -- meant to surface directly in a review command/GUI, not just
    # for debugging.
    notes: list[str] = field(default_factory=list)

    @property
    def has_corroboration(self) -> bool:
        return self.matched_toc_entry is not None or bool(self.matched_anchor_id)

    @property
    def confidence(self) -> SplitConfidence:
        """Derived, not stored -- always a live reflection of the
        fields above rather than a value that could drift out of sync
        with them."""
        if not self.in_winning_sequence:
            return SplitConfidence.NONE
        if self.case3:
            # Never CORROBORATED, no matter what else is true -- see
            # the case3 field's docstring above and the "Decided" note
            # in docs/split_safety_bar.md (a book with no
            # corroborating signal at all always stops at "reviewed,
            # not split").
            return SplitConfidence.NEEDS_REVIEW
        # Requirement 3 -- checked before corroboration, and it can
        # push a boundary to NEEDS_REVIEW even without corroboration.
        # A weak margin means the *book itself* is ambiguous about
        # where its chapters are, which corroboration on one boundary
        # doesn't fix.
        if self.sequence_margin is not None and self.sequence_margin < MIN_SEQUENCE_MARGIN:
            return SplitConfidence.NEEDS_REVIEW
        if not self.has_corroboration:
            return SplitConfidence.SEQUENCE_ONLY
        if self.content_length_ok is False or self.structurally_clean is False:
            return SplitConfidence.NEEDS_REVIEW
        if self.content_length_ok is None or self.structurally_clean is None:
            # Corroborated but the remaining checks haven't run yet --
            # not a confident "yes" until they have.
            return SplitConfidence.NEEDS_REVIEW
        return SplitConfidence.CORROBORATED


# ---------------------------------------------------------------------
# One node in the structure tree
# ---------------------------------------------------------------------


@dataclass(slots=True)
class StructureNode:
    """One section of the book: a single chapter, a front/back-matter
    block, or a Part grouping several chapters underneath it. Ordered
    lists of these, in book order, form the tree via `children`."""

    kind: NodeKind = NodeKind.CHAPTER
    title: str = ""

    # Where this node starts, in the same terms chapters.py already
    # uses: the file it's in plus that book's book_order numbering
    # (see ChapterCandidate.href / book_order), so this stays
    # consistent with the analysis this is built from rather than
    # inventing a second position scheme.
    start_href: str = ""
    start_book_order: int = 0

    # Evidence for *this node's starting boundary*. None for the very
    # first node in the book (nothing precedes it to draw a boundary
    # against) and for nodes whose start was inferred rather than
    # detected (e.g. "everything before the first confirmed chapter is
    # front matter" needs no marker of its own).
    evidence: BoundaryEvidence | None = None

    # Nested chapters, for a PART-kind node. Empty for an ordinary
    # chapter or front/back matter block.
    children: list[StructureNode] = field(default_factory=list)

    @property
    def split_eligible(self) -> bool:
        """Whether this node's *starting* boundary alone clears the
        bar to be cut on. Does not consider its children -- a PART
        node being split-eligible says nothing about whether the
        chapters inside it are."""
        if self.evidence is None:
            return False
        return self.evidence.confidence == SplitConfidence.CORROBORATED


# ---------------------------------------------------------------------
# The whole book
# ---------------------------------------------------------------------


@dataclass(slots=True)
class BookStructure:
    """Top-level result: the book's structure tree plus book-wide
    stats needed to interpret it. This is what a future `map-structure`
    review command (Phase 0h) would display, and what the eventual
    splitter (Phase 1+) would read -- neither should need to touch
    chapters.py's output directly once this exists."""

    nodes: list[StructureNode] = field(default_factory=list)

    # How far the winning sequence's score beat the runner-up's, from
    # chapters.py's BookChapterSummary.other_sequences. None if there
    # was no sequence at all. A small margin is exactly the "book
    # itself is ambiguous" signal described in
    # docs/split_safety_bar.md, requirement 3.
    sequence_margin: float | None = None

    # Kept for traceability back to the raw analysis this tree was
    # built from (chapters.py's BookChapterSummary) -- so a review
    # command can show "here's the underlying evidence" without
    # needing to re-run the analysis.
    source_summary: Any = None

    @property
    def has_any_split_eligible_boundary(self) -> bool:
        def _walk(nodes: list[StructureNode]) -> bool:
            for n in nodes:
                if n.split_eligible:
                    return True
                if _walk(n.children):
                    return True
            return False

        return _walk(self.nodes)


# ---------------------------------------------------------------------
# Phase 0d -- building a first-draft tree from today's existing signal
# ---------------------------------------------------------------------
#
# This wires chapters.py's BookChapterSummary into the shapes above.
# It intentionally uses *only* sequence membership -- no TOC or anchor
# corroboration (that's Phase 0e/0f), no minimum-content check, no
# structural-cleanliness check. Every node this produces will read as
# SEQUENCE_ONLY or NONE confidence, never CORROBORATED, until later
# phases fill in the rest of BoundaryEvidence. That's expected: this
# step exists to prove the tree-assembly wiring works, not to produce
# split-eligible output yet.


def _evidence_for_chapter(candidate: Any, margin: float | None) -> BoundaryEvidence:
    return BoundaryEvidence(
        candidate=candidate,
        in_winning_sequence=bool(getattr(candidate, "confirmed", False)),
        sequence_score=getattr(candidate, "score", 0.0),
        sequence_margin=margin,
    )


def _evidence_for_case3_chapter(candidate: Any, margin: float | None) -> BoundaryEvidence:
    return BoundaryEvidence(
        candidate=candidate,
        in_winning_sequence=bool(getattr(candidate, "confirmed", False)),
        sequence_score=getattr(candidate, "score", 0.0),
        sequence_margin=margin,
        case3=True,
        notes=[
            "Case 3 detection: no label word (\"Chapter\", \"Part\") and no "
            "TOC exists in this book to corroborate against, so this can "
            "never be more than \"needs review\" -- see Jacob's three-case "
            "framework in docs/xhtml_recoder_plan.md."
        ],
    )


def _evidence_for_part(candidate: Any) -> BoundaryEvidence:
    # Deliberately in_winning_sequence=False, always -- see note below.
    return BoundaryEvidence(
        candidate=candidate,
        in_winning_sequence=False,
        sequence_score=getattr(candidate, "score", 0.0),
        notes=[
            "Part/Book/Volume markers aren't run through chapters.py's "
            "sequence search today (_find_best_sequence only sees "
            "chapter_candidates, not part_candidates) -- so unlike a "
            "chapter boundary, a part boundary currently has no "
            "sequence-level check that detected parts actually count "
            "up sensibly. Treated as unconfirmed until that gap is "
            "addressed."
        ],
    )


def build_structure(summary: BookChapterSummary, case3: bool = False) -> BookStructure:
    """Assemble a first-draft BookStructure from an already-computed
    BookChapterSummary (see chapters.py:analyze_book_chapters).

    Only chapters.py's confirmed chapter boundaries and detected part
    markers are used. Everything before the first detected boundary is
    collapsed into a single placeholder FRONT_MATTER node with no
    evidence -- this is *not* real front-matter detection (that's
    frontmatter.py's job, and wiring the two together is still an open
    question, see xhtml_recoder_plan.md); it's just a placeholder so
    the tree doesn't silently start mid-book. There is deliberately no
    equivalent trailing back-matter placeholder yet either.

    Pass case3=True when `summary` came from
    chapters.analyze_case3_book_chapters instead of the normal
    analyze_book_chapters -- this routes every chapter's evidence
    through _evidence_for_case3_chapter instead, and skips Part/Book/
    Volume handling entirely, since that concept is built on label
    words that case 3 text has none of by definition (see
    analyze_case3_structure below, which is the actual entry point
    map-structure calls).
    """
    confirmed_chapters = list(summary.confirmed_boundaries)
    parts = [] if case3 else list(summary.parts)

    if not confirmed_chapters and not parts:
        return BookStructure(nodes=[], sequence_margin=None, source_summary=summary)

    margin: float | None = None
    if summary.best_sequence is not None and summary.other_sequences:
        margin = summary.best_sequence.score_sum - summary.other_sequences[0].score_sum

    # Merge parts and confirmed chapters into one book-order timeline.
    events: list[tuple[int, str, Any]] = []
    for c in parts:
        events.append((c.book_order, "part", c))
    for c in confirmed_chapters:
        events.append((c.book_order, "chapter", c))
    events.sort(key=lambda e: e[0])

    nodes: list[StructureNode] = [
        StructureNode(
            kind=NodeKind.FRONT_MATTER,
            title="(unclassified opening content)",
            evidence=None,
        )
    ]

    current_part: StructureNode | None = None
    for _, event_kind, candidate in events:
        if event_kind == "part":
            node = StructureNode(
                kind=NodeKind.PART,
                title=candidate.text,
                start_href=candidate.href,
                start_book_order=candidate.book_order,
                evidence=_evidence_for_part(candidate),
            )
            nodes.append(node)
            current_part = node
        else:
            node = StructureNode(
                kind=NodeKind.CHAPTER,
                title=candidate.text,
                start_href=candidate.href,
                start_book_order=candidate.book_order,
                evidence=(
                    _evidence_for_case3_chapter(candidate, margin) if case3
                    else _evidence_for_chapter(candidate, margin)
                ),
            )
            if current_part is not None:
                current_part.children.append(node)
            else:
                nodes.append(node)

    return BookStructure(nodes=nodes, sequence_margin=margin, source_summary=summary)


# ---------------------------------------------------------------------
# Phase 0e -- corroborating against the book's own existing TOC
# ---------------------------------------------------------------------
#
# Fills in BoundaryEvidence.matched_toc_entry for chapters already in
# a BookStructure (see build_structure above). This is the first of
# the two corroborating signals required by docs/split_safety_bar.md,
# requirement 2 -- a chapter with a matched TOC entry (or, later, a
# matched anchor from 0f) can reach CORROBORATED confidence; one with
# neither stays at SEQUENCE_ONLY no matter how well it scored.
#
# PART nodes are deliberately left untouched here. Parts already carry
# a documented gap (no sequence validation at all, see build_structure
# and chapter_detection_signals.md) -- TOC-matching a Part on top of
# that gap is deferred rather than papered over with one signal while
# the other is still missing.


def _walk_chapters(nodes: list[StructureNode]):
    """Yields every CHAPTER node in a structure tree, including those
    nested under PART nodes, in book order."""
    for node in nodes:
        if node.kind == NodeKind.CHAPTER:
            yield node
        yield from _walk_chapters(node.children)


def iter_chapter_nodes(tree: BookStructure):
    """Public wrapper around _walk_chapters, for callers outside this
    module (e.g. engine.py's split-structure command, or splitter.py's
    future real wiring) that need every CHAPTER node in book order
    without reaching into this module's private helper directly."""
    yield from _walk_chapters(tree.nodes)


def _candidate_ids(candidate: Any, max_ancestor_levels: int = 2) -> set:
    """id attributes on a candidate's own element and its nearest few
    ancestors. Checking ancestors too because a book's own id (the one
    a TOC fragment link points at) is sometimes placed on a wrapping
    container rather than directly on the heading/paragraph the marker
    text was found in."""
    ids = set()
    el = getattr(candidate, "element", None)
    levels = 0
    while el is not None and levels <= max_ancestor_levels:
        el_id = el.get("id") if hasattr(el, "get") else None
        if el_id:
            ids.add(el_id)
        el = el.getparent() if hasattr(el, "getparent") else None
        levels += 1
    return ids


def _flatten_toc(entries: list) -> list:
    flat = []
    for entry in entries:
        flat.append(entry)
        if entry.children:
            flat.extend(_flatten_toc(entry.children))
    return flat


def apply_toc_corroboration(book: Any, tree: BookStructure) -> BookStructure:
    """Matches confirmed chapters in an already-built BookStructure
    against the book's own existing NCX/nav TOC (book.toc, as loaded
    by parser.py -- see toc.py for the equivalent broken-link check
    this reuses the same href/fragment reading of). Mutates the tree's
    nodes in place and also returns it, for chaining after
    build_structure(). A book with no TOC at all (book.toc empty)
    leaves every chapter's matched_toc_entry as None, unchanged.

    Matching heuristic, since a TOC entry doesn't always point at the
    exact element a chapter marker was found on:
    - An entry with a #fragment counts as a match only when that
      fragment equals an id on the candidate's own element or one of
      its nearest ancestors (see _candidate_ids).
    - An entry with no fragment (a whole-file link) counts as a match
      against the *first* confirmed chapter found in that file, since
      that's the most reasonable reading of a file-level TOC link.
    Each chapter can only be matched once; if more than one TOC entry
    could plausibly match the same chapter, the first one found in TOC
    document order wins and the rest are left unmatched rather than
    reassigned.
    """
    entries = list(getattr(book, "toc", None) or [])
    if not entries:
        return tree

    flat_entries = _flatten_toc(entries)
    chapter_nodes = list(_walk_chapters(tree.nodes))
    if not chapter_nodes:
        return tree

    first_by_href: dict = {}
    for node in sorted(chapter_nodes, key=lambda n: n.start_book_order):
        first_by_href.setdefault(node.start_href, node)

    matched_node_ids: set = set()

    for entry in flat_entries:
        if not entry.href:
            continue
        path, _, fragment = entry.href.partition("#")
        target: StructureNode | None = None

        if fragment:
            for node in chapter_nodes:
                if id(node) in matched_node_ids or node.start_href != path:
                    continue
                candidate = node.evidence.candidate if node.evidence else None
                if candidate is not None and fragment in _candidate_ids(candidate):
                    target = node
                    break
        else:
            node = first_by_href.get(path)
            if node is not None and id(node) not in matched_node_ids:
                target = node

        if target is not None and target.evidence is not None:
            target.evidence.matched_toc_entry = entry
            matched_node_ids.add(id(target))

    return tree


# ---------------------------------------------------------------------
# Phase 0f -- corroborating against existing internal cross-reference
# anchors (distinct from the TOC, which is Phase 0e above)
# ---------------------------------------------------------------------
#
# A book's own TOC isn't the only place something in the book might
# already point at a chapter start -- footnotes, an index, "see
# Chapter 5" cross-references, or other in-body hyperlinks can too.
# This scans the actual chapter content (not the NCX/nav document,
# which 0e already covers) for internal fragment links and matches
# their targets against a candidate's own id, the same way 0e matches
# TOC fragments. A chapter matched by *either* signal reaches
# BoundaryEvidence.has_corroboration -- they're independent checks, so
# both get applied even if one already matched, rather than skipping
# a chapter early once the first signal is found.
#
# One thing this deliberately does NOT treat as corroboration:
# per-page bookmark ids some converters stamp onto *every* page (the
# calibre_pb_N pattern documented in analysis_roadmap.md). Those exist
# on virtually every page regardless of chapter boundaries, so a link
# happening to target one says nothing chapter-specific -- it would be
# false corroboration. This only counts a link as corroborating when
# its target id actually matches a *candidate's own* id (or a close
# ancestor's), the same standard 0e's TOC matching uses.


def _internal_link_targets(book: Any) -> dict:
    """href path -> set of fragment ids that some in-book hyperlink
    (scanned from actual chapter content, not the NCX/nav TOC) points
    at within that file. Same-file links (href="#some-id" with no
    path) are resolved against the chapter they were found in."""
    targets: dict = {}
    for chapter in getattr(book, "chapters", []) or []:
        doc = getattr(chapter, "document", None)
        chapter_href = getattr(chapter, "href", "")
        if doc is None:
            continue
        for el in doc.iter():
            if not isinstance(el.tag, str) or etree.QName(el).localname.lower() != "a":
                continue
            href = el.get("href") or ""
            if not href or href.startswith(("http://", "https://", "mailto:")):
                continue
            path, _, fragment = href.partition("#")
            if not fragment:
                continue
            resolved_path = path or chapter_href
            targets.setdefault(resolved_path, set()).add(fragment)
    return targets


def apply_anchor_corroboration(book: Any, tree: BookStructure) -> BookStructure:
    """Matches confirmed chapters in an already-built BookStructure
    against existing internal cross-reference anchors found in the
    book's own content (footnotes, an index, "see Chapter N" links,
    etc.) -- independent of the TOC-based check in
    apply_toc_corroboration. Mutates the tree's nodes in place and
    also returns it, for chaining. A book with no internal
    cross-reference links at all leaves matched_anchor_id unchanged on
    every node."""
    targets = _internal_link_targets(book)
    if not targets:
        return tree

    for node in _walk_chapters(tree.nodes):
        if node.evidence is None or node.evidence.candidate is None:
            continue
        possible = targets.get(node.start_href)
        if not possible:
            continue
        matched = _candidate_ids(node.evidence.candidate) & possible
        if matched:
            node.evidence.matched_anchor_id = sorted(matched)[0]

    return tree


# ---------------------------------------------------------------------
# Phase 0g -- combining every signal into one confidence score
# ---------------------------------------------------------------------
#
# 0d through 0f built the pieces: sequence membership, TOC
# corroboration, anchor corroboration. Requirement 3 (a healthy margin
# over the runner-up sequence) is now folded into the `confidence`
# property above. What's still missing before `confidence` can ever
# actually reach CORROBORATED is requirements 4 and 5 -- minimum
# content per piece, and a structurally-clean cut point -- which is
# what the two functions below fill in. Once both have run,
# `confidence` on every node is a complete, live combination of every
# signal split_safety_bar.md asked for; nothing downstream (Phase 0h's
# review command, the eventual splitter) should need to re-derive
# anything from chapters.py's raw candidates itself.


def _words(text: str) -> int:
    return len(text.split())


def _text_from(element) -> list:
    """Every text chunk from `element` (inclusive of its own content)
    through the end of its document, as lxml "smart string" objects,
    in true document reading order.

    This is `.//text()` (descendant text) unioned with `following::
    text()` (everything after this element closes) -- lxml/libxml2
    keep `|` unions in document order, so this correctly includes
    things a naive iter()-based walk misses: an *ancestor's* tail text
    that falls after `element` in reading order but is only ever
    visited, in a plain pre-order walk, when that ancestor itself is
    reached (which happens before `element`, not after).
    """
    return element.xpath(".//text() | following::text()")


def _text_range(start_element, end_element) -> str:
    """Text strictly from `start_element` (inclusive) up to
    `end_element` (exclusive), for two elements in the same document.

    Since `_text_from(end_element)` is always a document-order suffix
    of `_text_from(start_element)` (end comes later), the range is
    just the length difference between the two -- no need to compare
    individual nodes for identity/equality, which smart strings don't
    make reliable anyway.
    """
    at_or_after_start = _text_from(start_element)
    at_or_after_end = _text_from(end_element) if end_element is not None else []
    n = len(at_or_after_start) - len(at_or_after_end)
    return "".join(str(t) for t in at_or_after_start[:n])


def _text_to_end_of_doc(element) -> str:
    return "".join(str(t) for t in _text_from(element))


def _text_before(doc_root, end_element) -> str:
    """Everything in `doc_root`'s document before `end_element`
    (exclusive) -- the same suffix-length trick as `_text_range`, just
    anchored at the start of the document instead of at another
    element."""
    full = doc_root.xpath(".//text()")
    at_or_after_end = _text_from(end_element)
    n = len(full) - len(at_or_after_end)
    return "".join(str(t) for t in full[:n])


def _content_word_count(book: Any, start_href: str, start_element,
                         end_href: str | None, end_element) -> int:
    """Word count of the content that would end up in one chapter's
    slice: from `start_element` (inclusive) up to `end_element`
    (exclusive). `end_href`/`end_element` are None for the last
    confirmed chapter -- see the scoping note on
    `apply_content_length_check` for what that means here.

    Walks `book.chapters` in its existing iteration order to cover any
    files that lie fully between `start_href` and `end_href`. That's
    the same ordering chapters.py's book_order numbering already
    relies on (see the documented manifest-vs-spine-order gap in
    docs/analysis_roadmap.md) -- this doesn't fix that gap, just stays
    consistent with it rather than inventing a second ordering.
    """
    chapters = list(getattr(book, "chapters", []) or [])
    href_index = {c.href: i for i, c in enumerate(chapters)}
    by_href = {c.href: c for c in chapters}

    start_chapter = by_href.get(start_href)
    if start_chapter is None or start_chapter.document is None:
        return 0

    if end_href is None:
        return _words(_text_to_end_of_doc(start_element))

    if end_href == start_href:
        return _words(_text_range(start_element, end_element))

    end_chapter = by_href.get(end_href)
    start_idx = href_index.get(start_href)
    end_idx = href_index.get(end_href)
    if end_chapter is None or end_chapter.document is None or start_idx is None or end_idx is None:
        return _words(_text_to_end_of_doc(start_element))

    text_parts = [_text_to_end_of_doc(start_element)]
    for c in chapters[start_idx + 1:end_idx]:
        if c.document is not None:
            text_parts.append("".join(c.document.itertext()))
    text_parts.append(_text_before(end_chapter.document, end_element))
    return _words("".join(text_parts))


def apply_content_length_check(book: Any, tree: BookStructure,
                                min_words: int = MIN_CHAPTER_WORDS) -> BookStructure:
    """Fills in BoundaryEvidence.content_length_ok for every confirmed
    chapter (split_safety_bar.md requirement 4): a boundary whose
    resulting slice comes in under `min_words` reads as a stray
    heading or scene-divider rather than a chapter's worth of content,
    and shouldn't be split-eligible on its own -- it should fold into
    the section it sits within instead. Mutates the tree's nodes in
    place and also returns it, for chaining.

    Scoping note for the very last confirmed chapter in the book
    (nothing after it to bound the slice against): this only counts to
    the end of that chapter's own file, not onward through whatever
    follows later in the spine. Whether trailing files are more of
    that same chapter or back matter is exactly the front/back-matter
    boundary question already flagged as open in
    xhtml_recoder_plan.md -- this doesn't try to resolve it. An
    under-count here can only make a fine last chapter look short,
    never the reverse, which is the safe direction for this check to
    err in.

    PART nodes are skipped, same as 0e/0f -- they're never sequence-
    confirmed today (see build_structure), so this wouldn't change
    their confidence either way.
    """
    chapter_nodes = list(_walk_chapters(tree.nodes))
    for i, node in enumerate(chapter_nodes):
        if node.evidence is None or node.evidence.candidate is None:
            continue
        start_element = node.evidence.candidate.element
        if start_element is None:
            continue

        next_node = chapter_nodes[i + 1] if i + 1 < len(chapter_nodes) else None
        end_href = None
        end_element = None
        if next_node is not None and next_node.evidence is not None and next_node.evidence.candidate is not None:
            end_href = next_node.start_href
            end_element = next_node.evidence.candidate.element

        count = _content_word_count(book, node.start_href, start_element, end_href, end_element)
        node.evidence.content_length_ok = count >= min_words
        if count < min_words:
            node.evidence.notes.append(
                f"Only ~{count} word{'s' if count != 1 else ''} before the next boundary "
                f"(minimum {min_words}) -- reads as a stray heading rather than a full chapter."
            )
    return tree


# ---------------------------------------------------------------------
# Requirement 5 -- structurally clean cut points
# ---------------------------------------------------------------------
#
# A marker being textually confirmed doesn't guarantee it's sitting
# somewhere a file could actually be cut. Two kinds of "unsafe to cut
# here" this checks for: sitting inside a table or list (splitting
# there would break the row/cell/item across two files), and sitting
# inside something that reads as a footnote/endnote block (splitting
# there would separate a note from the content that references it, or
# vice versa).

_EPUB_OPS_NS = "http://www.idpf.org/2007/ops"
_EPUB_TYPE_ATTR = f"{{{_EPUB_OPS_NS}}}type"

_UNSAFE_ANCESTOR_TAGS = {
    "table", "thead", "tbody", "tfoot", "tr", "td", "th",
    "ul", "ol", "li", "dl", "dt", "dd",
}
_NOTE_KEYWORDS = ("footnote", "endnote", "note", "noteref")


def _element_tag(el) -> str:
    if not hasattr(el, "tag") or not isinstance(el.tag, str):
        return ""
    return etree.QName(el).localname.lower()


def _note_container_keyword(el) -> str | None:
    epub_type = (el.get(_EPUB_TYPE_ATTR) or "").lower()
    css_class = (el.get("class") or "").lower()
    for kw in _NOTE_KEYWORDS:
        if kw in epub_type or kw in css_class:
            return kw
    return None


def apply_structural_cleanliness_check(tree: BookStructure) -> BookStructure:
    """Fills in BoundaryEvidence.structurally_clean for every
    confirmed chapter (split_safety_bar.md requirement 5), by walking
    up from the marker element through its ancestors and checking for
    a table/list container or a footnote-shaped block along the way.
    Mutates the tree's nodes in place and also returns it, for
    chaining.

    This is a structural check only -- it says nothing about whether
    the *content* is long enough (that's
    apply_content_length_check above) or corroborated (0e/0f). PART
    nodes are skipped, same as elsewhere in this file.
    """
    for node in _walk_chapters(tree.nodes):
        if node.evidence is None or node.evidence.candidate is None:
            continue
        element = node.evidence.candidate.element
        if element is None:
            continue

        clean = True
        reason = None
        el = element
        while el is not None:
            tag = _element_tag(el)
            if tag in _UNSAFE_ANCESTOR_TAGS:
                clean = False
                reason = f"sits inside a <{tag}>, which would be broken across two files if cut here"
                break
            keyword = _note_container_keyword(el)
            if keyword:
                clean = False
                reason = f"sits inside what looks like a {keyword} block ({keyword!r} in its epub:type or class)"
                break
            el = el.getparent() if hasattr(el, "getparent") else None

        node.evidence.structurally_clean = clean
        if not clean:
            node.evidence.notes.append(f"Not structurally clean: this boundary {reason}.")
    return tree


def score_confidence(book: Any, tree: BookStructure,
                      min_words: int = MIN_CHAPTER_WORDS) -> BookStructure:
    """0g's entry point: runs the two remaining per-boundary checks
    (minimum content, structural cleanliness). Requirement 3 (sequence
    margin) doesn't need a separate pass -- it's already read live off
    `sequence_margin` by the `confidence` property above. After this
    runs, `confidence` on every node is the fully combined signal;
    nothing downstream should need to re-derive it.
    """
    apply_content_length_check(book, tree, min_words=min_words)
    apply_structural_cleanliness_check(tree)
    return tree


def analyze_structure(book: Any) -> BookStructure:
    """Runs the whole Phase 0 pipeline end to end: chapters.py's
    detection, a first-draft tree (0d), TOC corroboration (0e), anchor
    corroboration (0f), and the content-length/structural-cleanliness
    checks (0g) -- leaving `confidence` fully informed on every node.
    This is what Phase 0h's review command calls, and what any future
    splitter should call too, rather than re-assembling the pipeline
    by hand.
    """
    from .chapters import analyze_book_chapters

    summary = analyze_book_chapters(book)
    tree = build_structure(summary)
    apply_toc_corroboration(book, tree)
    apply_anchor_corroboration(book, tree)
    score_confidence(book, tree)
    return tree


def analyze_case3_structure(book: Any) -> BookStructure:
    """Case 3 counterpart to analyze_structure above -- for a book
    where the normal pipeline found no confirmed chapters at all (see
    Jacob's three-case framework, case 3, in xhtml_recoder_plan.md).

    Callers should check analyze_structure's result first and only
    call this when it came back empty; running this on a book that
    already detects cleanly the normal way would be pointless at best.

    Skips TOC and anchor corroboration entirely -- case 3 is defined
    by having no TOC to corroborate against in the first place, and
    every resulting node's confidence is hard-capped at NEEDS_REVIEW
    regardless (see BoundaryEvidence.case3) -- but still runs the
    content-length and structural-cleanliness checks from 0g, since
    those are useful signal for a person reviewing the result even
    though they can't push anything to CORROBORATED here.
    """
    from .chapters import analyze_case3_book_chapters

    summary = analyze_case3_book_chapters(book)
    tree = build_structure(summary, case3=True)
    score_confidence(book, tree)
    return tree


# ---------------------------------------------------------------------
# Phase 0h -- plain-text review output
# ---------------------------------------------------------------------
#
# A dry-run view of the structure tree, same manual-review posture as
# class_map.format_class_map: nothing here touches the book, it just
# renders what analyze_structure already found so a person can sign
# off on it before Phase 1 ever exists to act on it. The underlying
# BookStructure this reads stays the structured object -- this
# function is just one way of presenting it, so a later GUI (see
# xhtml_recoder_plan.md) can render the same tree differently without
# this module needing to change.


def format_structure_report(tree: BookStructure) -> str:
    lines: list[str] = []

    if not tree.nodes:
        return "No chapter structure detected -- nothing to review."

    if tree.sequence_margin is None:
        lines.append("Sequence margin: n/a (no runner-up sequence found).")
    else:
        flag = "" if tree.sequence_margin >= MIN_SEQUENCE_MARGIN else "  <-- below the review threshold"
        lines.append(f"Sequence margin over runner-up: {tree.sequence_margin:.1f}{flag}")
    lines.append("")

    def _describe(node: StructureNode, indent: int) -> None:
        # Parentheses rather than square brackets for the kind label --
        # this text goes through rich's console.print (see
        # engine.map_structure), which treats [foo] as a markup tag
        # and silently swallows it.
        pad = "  " * indent
        label = node.title or "(untitled)"
        if node.evidence is None:
            lines.append(f"{pad}- ({node.kind.value}) {label}")
        else:
            conf = node.evidence.confidence.value
            lines.append(f"{pad}- ({node.kind.value}) {label}  -- {conf}")
            corroboration = []
            if node.evidence.matched_toc_entry is not None:
                corroboration.append("TOC entry")
            if node.evidence.matched_anchor_id:
                corroboration.append(f"anchor '{node.evidence.matched_anchor_id}'")
            if corroboration:
                lines.append(f"{pad}    corroborated by: {', '.join(corroboration)}")
            for note in node.evidence.notes:
                lines.append(f"{pad}    note: {note}")
        for child in node.children:
            _describe(child, indent + 1)

    for node in tree.nodes:
        _describe(node, 0)

    eligible = tree.has_any_split_eligible_boundary
    lines.append("")
    lines.append(
        "At least one boundary is split-eligible." if eligible
        else "No boundary currently clears the split-eligible bar -- review only, nothing to split yet."
    )
    return "\n".join(lines)
