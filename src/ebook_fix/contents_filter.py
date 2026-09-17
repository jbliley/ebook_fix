"""
ebook_fix.modules.contents_filter

Detects table of contents / chapter list pages and filters out their
candidate chapter markers from the chapter detection analysis.

Problem: A book's plain-text contents page (e.g., "CONTENTS: One, Two,
Three, ... Forty") looks like chapter markers to the chapter detector.
Since these are listed sequentially, they score as "candidate" markers
even though they're not real chapters -- they're just a list. When a
user tries to split the book, the tool incorrectly suggests splitting
the contents page itself.

Solution: Detect files that are clearly contents/chapter-list pages,
then filter out any candidate markers found within them from the
analysis, keeping only the confirmed, high-confidence chapter markers
in other files.

Detection signals (any combination suggests a contents page):
1. Contains a heading or early-file text saying "CONTENTS", "TABLE OF
   CONTENTS", "CHAPTER LIST", "CHAPTERS", or similar
2. Followed by a repetitive list of chapter numbers/names (One through
   Forty, Chapter One through Chapter Forty, etc.)
3. The list items follow a clear numeric or ordinal pattern
4. Minimal other content (short file, few paragraphs, no typical body
   text structure)
5. Located early in the book (usually in first few files)

This is a pre-analysis filter: it runs after chapter detection but
before the candidates are evaluated, and simply removes candidate
markers found in detected contents pages.

Implementation approach:
- Scan early files (first 5 spine entries or so)
- Look for "CONTENT" or similar keywords
- Check if the file has a list of sequential chapter-like items
- If detected, mark the file as "contents page"
- Remove any candidate markers from that file
- Return the filtered candidate list

Scope: Handles the common case of plain-text contents pages. Does not
attempt to handle every possible variations (e.g., a book where the
contents page is embedded in a larger file with other content, or
where it's a complex HTML table with links). Those can be manually
reviewed or handled on a case-by-case basis.
"""

from __future__ import annotations
import re
from typing import Set, List, Optional
from lxml import etree

XHTML_NS = "http://www.w3.org/1999/xhtml"


class ContentsPageDetector:
    """Detects and filters out false-positive chapter markers in
    table of contents / chapter list pages."""

    # Keywords that strongly suggest a contents/TOC page
    CONTENTS_KEYWORDS = [
        r'\bcontents?\b',           # "Contents" or "Content"
        r'\btable\s+of\s+contents\b',  # "Table of Contents"
        r'\bchapter\s+list\b',
        r'\bchapters?\b',           # Could be "Chapters" at the start
        r'\bchapter\s+guide\b',
    ]

    # Patterns for sequential chapter items
    # These detect things like "One", "Two", "Chapter One", etc.
    CHAPTER_NUMBER_PATTERNS = [
        r'^(One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|'
        r'Eleven|Twelve|Thirteen|Fourteen|Fifteen|Sixteen|'
        r'Seventeen|Eighteen|Nineteen|Twenty|'
        r'Twenty-one|Twenty-two|Twenty-three|Twenty-four|'
        r'Twenty-five|Twenty-six|Twenty-seven|Twenty-eight|'
        r'Twenty-nine|Thirty|Thirty-one|Thirty-two|Thirty-three|'
        r'Thirty-four|Thirty-five|Thirty-six|Thirty-seven|'
        r'Thirty-eight|Thirty-nine|Forty)$',
        r'^(Chapter\s+)?(\d+|One|Two|Three)$',  # "Chapter 1" or just "1"
    ]

    def __init__(self):
        self.compiled_keywords = [
            re.compile(keyword, re.IGNORECASE)
            for keyword in self.CONTENTS_KEYWORDS
        ]
        self.compiled_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.CHAPTER_NUMBER_PATTERNS
        ]

    def detect_contents_files(self, book) -> Set[str]:
        """Scan the book's files and identify which ones are contents
        pages. Returns a set of href strings for detected contents files.

        Args:
            book: The Book object with chapters and content

        Returns:
            Set[str]: hrefs of files detected as contents pages
        """
        contents_files = set()

        # Only check the first few files in spine order (contents pages
        # are almost always near the beginning)
        chapters_to_check = book.chapters[:10] if len(book.chapters) > 10 else book.chapters

        for chapter in chapters_to_check:
            if self._looks_like_contents_page(chapter):
                contents_files.add(chapter.href)

        return contents_files

    def _looks_like_contents_page(self, chapter) -> bool:
        """Check if a single file looks like a contents page.

        Args:
            chapter: A Chapter object with an xhtml document

        Returns:
            bool: True if the file looks like a contents page
        """
        if not hasattr(chapter, 'document') or chapter.document is None:
            return False

        doc = chapter.document
        text_content = ''.join(doc.itertext()).strip()

        # Quick filter: file should be relatively short (contents pages
        # don't have thousands of words of body text)
        if len(text_content) > 10000:
            return False

        # Look for contents keywords
        has_contents_keyword = any(
            kw.search(text_content) for kw in self.compiled_keywords
        )
        if not has_contents_keyword:
            return False

        # Count chapter-number-like items
        # Look at all text nodes to see if they match chapter number patterns
        chapter_count = self._count_chapter_items(doc)

        # If we found a "contents" keyword AND a significant number of
        # chapter-like items, it's probably a contents page
        return chapter_count >= 5  # At least 5 chapter items

    def _count_chapter_items(self, doc) -> int:
        """Count how many elements in the doc match chapter number
        patterns."""
        count = 0
        
        # Get all paragraph and list item texts
        for elem in doc.findall(f'.//{{{XHTML_NS}}}p') + \
                    doc.findall(f'.//{{{XHTML_NS}}}li') + \
                    doc.findall(f'.//{{{XHTML_NS}}}div'):
            text = ''.join(elem.itertext()).strip()
            
            # Skip empty or very long elements
            if not text or len(text) > 100:
                continue
            
            # Check if this looks like a chapter number/name
            if any(pat.match(text) for pat in self.compiled_patterns):
                count += 1
        
        return count

    def filter_candidates(self, candidates: List, contents_files: Set[str]) -> List:
        """Remove candidate chapter markers found in contents pages.
        
        Only removes candidates that are clearly part of the sequential
        chapter list pattern (e.g., the "One through Forty" list items),
        not all candidates in the file. A Contents page may have legitimate
        structural elements (title headings, etc.) that shouldn't be
        removed entirely.

        Args:
            candidates: List of ChapterCandidate objects
            contents_files: Set of hrefs that are contents pages

        Returns:
            List: Filtered candidates with false-positive list items removed
        """
        # For candidates in contents pages, only remove the low-scoring ones
        # that are clearly part of a sequential chapter list (score 1.5-2.5 range,
        # typically from plain text matching). Keep high-scoring ones (h1/h2
        # headings that scored 4.5+) as they might be legitimate title elements.
        filtered = []
        for cand in candidates:
            if cand.href not in contents_files:
                # Not in a contents file, keep it
                filtered.append(cand)
            elif cand.score >= 4.0:
                # High-scoring candidate in contents file - likely a title or
                # structural heading, not part of the chapter list, keep it
                filtered.append(cand)
            # else: low-scoring candidate in contents file is filtered out
        
        return filtered


def filter_contents_page_candidates(book, candidates: List) -> List:
    """Public function to detect and filter contents page candidates.
    
    Args:
        book: The Book object
        candidates: List of ChapterCandidate objects from analysis
        
    Returns:
        List: Filtered candidates with false positives removed
    """
    detector = ContentsPageDetector()
    contents_files = detector.detect_contents_files(book)
    
    if not contents_files:
        return candidates
    
    filtered = detector.filter_candidates(candidates, contents_files)
    
    # Log what was filtered (optional)
    removed_count = len(candidates) - len(filtered)
    if removed_count > 0:
        print(f"[Contents Filter] Removed {removed_count} false-positive candidates from {len(contents_files)} contents page(s)")
    
    return filtered
