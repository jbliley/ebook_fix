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

from .models import TocEntry

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


def build_structure(summary: BookChapterSummary) -> BookStructure:
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
    """
    confirmed_chapters = list(summary.confirmed_boundaries)
    parts = list(summary.parts)

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
                evidence=_evidence_for_chapter(candidate, margin),
            )
            if current_part is not None:
                current_part.children.append(node)
            else:
                nodes.append(node)

    return BookStructure(nodes=nodes, sequence_margin=margin, source_summary=summary)
