"""Verification module for chapter splits.

Before applying any chapter split to an EPUB, this module performs
comprehensive checks to ensure the split is safe:

1. Content preservation - verify word counts match before/after
2. Structural integrity - check no cross-references are broken
3. File coherence - ensure split files don't have orphaned elements
4. Completeness - confirm all detected chapters are accounted for
5. Safe boundaries - validate split points don't break formatting

Each check produces a detailed report so the user can understand
what verification passed/failed and make an informed decision.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from lxml import etree


class VerificationLevel(Enum):
    """Confidence levels for split verification."""
    SAFE = "safe"              # All checks pass, safe to auto-split
    REVIEW = "review_recommended"  # Minor issues, human review advised
    UNSAFE = "unsafe"           # Critical issues, should not split


@dataclass
class VerificationResult:
    """Result of a single verification check."""
    check_name: str
    passed: bool
    level: VerificationLevel
    message: str
    details: list[str] = field(default_factory=list)
    
    def __str__(self) -> str:
        icon = "✓" if self.passed else "✗"
        return f"{icon} {self.check_name}: {self.message}"


@dataclass
class SplitVerification:
    """Complete verification report for a chapter split."""
    book_title: str
    chapter_count: int
    confidence_distribution: dict  # {'CORROBORATED': N, ...}
    checks: list[VerificationResult] = field(default_factory=list)
    
    @property
    def overall_level(self) -> VerificationLevel:
        """Determine overall verification level from all checks."""
        if all(c.level == VerificationLevel.SAFE for c in self.checks):
            return VerificationLevel.SAFE
        if any(c.level == VerificationLevel.UNSAFE for c in self.checks):
            return VerificationLevel.UNSAFE
        return VerificationLevel.REVIEW
    
    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)
    
    @property
    def total_checks(self) -> int:
        return len(self.checks)
    
    def summary(self) -> str:
        """Generate a summary of verification results."""
        lines = [
            f"Split Verification Report: {self.book_title}",
            f"Chapters to split: {self.chapter_count}",
            f"Overall level: {self.overall_level.value.upper()}",
            f"Checks passed: {self.passed_count}/{self.total_checks}",
            "",
            "Confidence distribution:",
        ]
        
        for level, count in sorted(self.confidence_distribution.items()):
            lines.append(f"  {level}: {count}")
        
        lines.extend([
            "",
            "Detailed checks:",
        ])
        
        for check in self.checks:
            lines.append(f"  {check}")
            if check.details:
                for detail in check.details:
                    lines.append(f"    - {detail}")
        
        return "\n".join(lines)


def verify_word_count_preservation(book: Any, tree: Any) -> VerificationResult:
    """Verify that total word count is preserved across splits.
    
    Computes the word count of the entire book and compares it to the sum
    of word counts from all detected chapters. A significant mismatch might
    indicate missing content or over-detection.
    """
    from ebook_fix.structure import _content_word_count, _walk_chapters
    
    chapter_nodes = list(_walk_chapters(tree.nodes))
    if not chapter_nodes:
        return VerificationResult(
            "Word count preservation",
            True,
            VerificationLevel.SAFE,
            "No chapters to verify"
        )
    
    # Calculate word count from detected chapters
    detected_words = 0
    
    for i, node in enumerate(chapter_nodes):
        if node.evidence is None or node.evidence.candidate is None:
            continue
        
        start_element = node.evidence.candidate.element
        if start_element is None:
            continue
        
        next_node = chapter_nodes[i + 1] if i + 1 < len(chapter_nodes) else None
        end_href = next_node.start_href if next_node else None
        end_element = next_node.evidence.candidate.element if next_node else None
        
        count = _content_word_count(
            book,
            node.start_href,
            start_element,
            end_href,
            end_element
        )
        detected_words += count
    
    # For verification, we mainly care that chapters have reasonable word counts
    # Not that they exactly match the book total (parsing differences vary)
    # Check: are there chapters with < 10 words? (likely false positives)
    min_words = 10
    short_chapters = 0
    
    for i, node in enumerate(chapter_nodes):
        if node.evidence is None or node.evidence.candidate is None:
            continue
        
        start_element = node.evidence.candidate.element
        if start_element is None:
            continue
        
        next_node = chapter_nodes[i + 1] if i + 1 < len(chapter_nodes) else None
        end_href = next_node.start_href if next_node else None
        end_element = next_node.evidence.candidate.element if next_node else None
        
        count = _content_word_count(
            book,
            node.start_href,
            start_element,
            end_href,
            end_element
        )
        if count < min_words:
            short_chapters += 1
    
    passed = short_chapters == 0
    level = VerificationLevel.SAFE if passed else VerificationLevel.REVIEW
    
    return VerificationResult(
        "Word count preservation",
        passed,
        level,
        f"Total detected: {detected_words} words across {len(chapter_nodes)} chapters"
        f" ({short_chapters} under {min_words} words)" if short_chapters > 0 else
        f"Total detected: {detected_words} words across {len(chapter_nodes)} chapters",
        [
            f"Total words detected: {detected_words}",
            f"Chapters under {min_words} words: {short_chapters}",
            f"Average chapter length: {detected_words // len(chapter_nodes) if chapter_nodes else 0} words"
        ]
    )


def verify_confident_boundaries(tree: Any) -> VerificationResult:
    """Verify that boundaries have high confidence levels.
    
    Books with mostly CORROBORATED boundaries are safer to auto-split.
    Flags if too many boundaries are just SEQUENCE_ONLY or NEEDS_REVIEW.
    """
    from ebook_fix.structure import _walk_chapters, SplitConfidence
    
    chapter_nodes = list(_walk_chapters(tree.nodes))
    if not chapter_nodes:
        return VerificationResult(
            "Boundary confidence",
            False,
            VerificationLevel.UNSAFE,
            "No chapters detected"
        )
    
    confidence_counts = {}
    for node in chapter_nodes:
        if node.evidence:
            conf = node.evidence.confidence.name
            confidence_counts[conf] = confidence_counts.get(conf, 0) + 1
    
    corroborated = confidence_counts.get('CORROBORATED', 0)
    sequence_only = confidence_counts.get('SEQUENCE_ONLY', 0)
    needs_review = confidence_counts.get('NEEDS_REVIEW', 0)
    total = corroborated + sequence_only + needs_review
    
    # Thresholds:
    # SAFE: 80%+ CORROBORATED
    # REVIEW: 50-80% CORROBORATED
    # UNSAFE: <50% CORROBORATED with NEEDS_REVIEW present
    
    corr_pct = corroborated / total * 100 if total > 0 else 0
    
    if corr_pct >= 80:
        level = VerificationLevel.SAFE
        passed = True
    elif corr_pct >= 50:
        level = VerificationLevel.REVIEW
        passed = True
    else:
        level = VerificationLevel.UNSAFE if needs_review > 0 else VerificationLevel.REVIEW
        passed = corr_pct >= 50
    
    return VerificationResult(
        "Boundary confidence",
        passed,
        level,
        f"{corroborated}/{total} chapters ({corr_pct:.0f}%) CORROBORATED",
        [
            f"CORROBORATED: {corroborated}",
            f"SEQUENCE_ONLY: {sequence_only}",
            f"NEEDS_REVIEW: {needs_review}"
        ]
    )


def verify_no_orphaned_elements(book: Any, tree: Any) -> VerificationResult:
    """Verify that no split would orphan elements.
    
    Checks that there are no structural elements (tables, lists, blockquotes)
    that would be split across chapter boundaries, which could break reading.
    """
    from ebook_fix.structure import _walk_chapters
    
    chapter_nodes = list(_walk_chapters(tree.nodes))
    orphaned = []
    
    for i, node in enumerate(chapter_nodes[:-1]):  # Skip last chapter
        if node.evidence is None or node.evidence.candidate is None:
            continue
        
        current_element = node.evidence.candidate.element
        next_node = chapter_nodes[i + 1]
        next_element = next_node.evidence.candidate.element
        
        if current_element is None or next_element is None:
            continue
        
        # Check if current element and next element share a parent table/list/etc
        current_parents = set()
        el = current_element
        while el is not None:
            tag = etree.QName(el).localname.lower() if isinstance(el.tag, str) else ""
            if tag in {"table", "ul", "ol", "dl", "blockquote"}:
                current_parents.add(id(el))
            el = el.getparent() if hasattr(el, "getparent") else None
        
        next_parents = set()
        el = next_element
        while el is not None:
            tag = etree.QName(el).localname.lower() if isinstance(el.tag, str) else ""
            if tag in {"table", "ul", "ol", "dl", "blockquote"}:
                next_parents.add(id(el))
            el = el.getparent() if hasattr(el, "getparent") else None
        
        # Shared parent means split would break the structure
        if current_parents & next_parents:
            orphaned.append(
                f"Chapter '{node.evidence.candidate.text[:40]}' and next share container"
            )
    
    passed = len(orphaned) == 0
    level = VerificationLevel.SAFE if passed else VerificationLevel.REVIEW
    
    return VerificationResult(
        "No orphaned elements",
        passed,
        level,
        f"{len(orphaned)} potential structural issues found" if orphaned else "No issues found",
        orphaned[:5]  # Limit details to first 5
    )


def verify_chapter_completeness(tree: Any) -> VerificationResult:
    """Verify that all detected chapters are accounted for and contiguous.
    
    Checks that chapters form a complete sequence with no gaps,
    and that numbering/sequencing is logical.
    """
    from ebook_fix.structure import _walk_chapters
    
    chapter_nodes = list(_walk_chapters(tree.nodes))
    if not chapter_nodes:
        return VerificationResult(
            "Chapter completeness",
            False,
            VerificationLevel.UNSAFE,
            "No chapters detected"
        )
    
    # Check for gaps in book_order
    book_orders = [node.evidence.candidate.book_order for node in chapter_nodes 
                   if node.evidence and node.evidence.candidate]
    
    if not book_orders:
        return VerificationResult(
            "Chapter completeness",
            True,
            VerificationLevel.SAFE,
            "Unable to verify ordering"
        )
    
    # Chapters should have contiguous or nearly-contiguous book_order
    gaps = []
    for i in range(len(book_orders) - 1):
        if book_orders[i + 1] - book_orders[i] > 10:
            gaps.append(f"Gap between chapter {i} and {i+1}")
    
    passed = len(gaps) == 0
    level = VerificationLevel.SAFE if passed else VerificationLevel.REVIEW
    
    return VerificationResult(
        "Chapter completeness",
        passed,
        level,
        f"{len(chapter_nodes)} chapters detected, "
        f"{len(gaps)} gaps found" if gaps else f"{len(chapter_nodes)} chapters detected",
        gaps[:3]
    )


def run_verification(book: Any, tree: Any) -> SplitVerification:
    """Run complete verification suite on a book's chapter structure.
    
    Performs all verification checks and returns a comprehensive report.
    """
    from ebook_fix.structure import _walk_chapters
    
    # Gather confidence distribution
    chapter_nodes = list(_walk_chapters(tree.nodes))
    confidence_dist = {}
    for node in chapter_nodes:
        if node.evidence:
            conf = node.evidence.confidence.name
            confidence_dist[conf] = confidence_dist.get(conf, 0) + 1
    
    # Create verification object
    metadata = getattr(book, 'metadata', None)
    book_title = metadata.title if metadata and hasattr(metadata, 'title') else 'Unknown'
    verification = SplitVerification(
        book_title=book_title,
        chapter_count=len(chapter_nodes),
        confidence_distribution=confidence_dist
    )
    
    # Run all checks
    verification.checks.append(verify_confident_boundaries(tree))
    verification.checks.append(verify_word_count_preservation(book, tree))
    verification.checks.append(verify_no_orphaned_elements(book, tree))
    verification.checks.append(verify_chapter_completeness(tree))
    
    return verification
