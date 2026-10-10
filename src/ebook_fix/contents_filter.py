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
import statistics
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


# ---------------------------------------------------------------------
# A contents list inside a larger file
# ---------------------------------------------------------------------
# ContentsPageDetector above only recognizes a file that is *entirely* a
# contents page. PDF-to-EPUB conversions often leave the contents list
# as a few lines at the top of the same file as the whole story, with
# no links and nothing but bare numbers ("1", "2", "3" ... "24"). Those
# numbers then turn up again later as the real chapter headings, and
# the sequence finder, which sees two identical runs of numbers, takes
# the first one: the contents list (found in Three Hearts & Three
# Lions, where splitting "the chapters" split the contents list).

# Two neighbouring markers closer together than this (in words of text
# between them) can belong to the same list. Contents lists are usually
# back to back; a watermark or page-number line may sit in the middle.
_LIST_MAX_GAP_WORDS = 15

# The typical gap inside a real list is tiny. Real chapters have pages
# of story between them, so a run with a larger typical gap is not a list.
_LIST_MAX_MEDIAN_GAP_WORDS = 3

# Fewer entries than this could just be a few short chapters in a row.
_LIST_MIN_ENTRIES = 5

# A later marker only counts as the "real" version of a list entry if
# real text follows it.
_REAL_CHAPTER_MIN_WORDS = 30

_CONTENTS_LABEL_RE = re.compile(
    r"^\s*(?:table\s+of\s+)?contents\s*:?\s*$"
    r"|^\s*(?:list\s+of\s+)?chapters\s*:?\s*$",
    re.IGNORECASE,
)

_SKIP_TEXT_TAGS = {"style", "script", "title"}

# root element id -> {element: words of text before it}, plus total words.
# Built once per document; keyed by id(root) and kept only for the
# duration of one analysis call (see drop_embedded_contents_lists).
def _word_positions(root) -> tuple:
    positions = {}
    count = 0
    for event, el in etree.iterwalk(root, events=("start", "end")):
        if not isinstance(el.tag, str):
            continue
        if event == "start":
            positions[el] = count
            if etree.QName(el).localname.lower() not in _SKIP_TEXT_TAGS and el.text:
                count += len(el.text.split())
        elif el.tail:
            count += len(el.tail.split())
    return positions, count


def _own_words(el) -> int:
    return len("".join(el.itertext()).split())


def drop_embedded_contents_lists(candidates: List) -> tuple:
    """Removes chapter-marker candidates that are really the entries of
    a contents list sitting inside a larger file.

    A group of candidates is treated as a contents list when ALL of this
    holds:
    - it is a run of at least 5 markers in the same file, each within a
      few words of the next, with a typical gap of about 3 words or less
      (real chapters have far more text between them), and
    - either a "Contents" / "Table of Contents" / "Chapters" label sits
      just before it, or the same numbering starts again later in the
      book with real text after each marker (the real headings).

    A run that is only close together (a book of very short chapters, a
    poetry collection) is never removed, because it has neither a
    contents label nor a real set of headings elsewhere.

    Returns (kept, removed), both in the original order. Candidates need
    .href, .number, .element and .book_order (a ChapterCandidate).
    """
    ordered = sorted(candidates, key=lambda c: c.book_order)
    caches: dict = {}

    def positions_for(el):
        root = el.getroottree().getroot()
        key = id(root)
        if key not in caches:
            caches[key] = _word_positions(root)
        return caches[key]

    def words_after(cand, nxt) -> int:
        """Words of text from the end of cand up to nxt (or the end of
        its document when nxt is None or in another file)."""
        pos, total = positions_for(cand.element)
        start = pos.get(cand.element)
        if start is None:
            return 0
        start += _own_words(cand.element)
        if nxt is not None and nxt.href == cand.href and nxt.element in pos:
            return max(0, pos[nxt.element] - start)
        return max(0, total - start)

    # 1. Group neighbouring candidates (same file, small gaps) into runs.
    runs: list = []
    current: list = []
    gaps: list = []
    for cand in ordered:
        if cand.element is None:
            if len(current) >= _LIST_MIN_ENTRIES:
                runs.append((current, gaps))
            current, gaps = [], []
            continue
        if current and current[-1].href == cand.href:
            gap = words_after(current[-1], cand)
            # A list counts up. When the number drops back (the "1" that
            # starts the real chapters right after a contents list), that
            # is where a new run begins, never a continuation.
            counts_up = (
                current[-1].number is None
                or cand.number is None
                or cand.number > current[-1].number
            )
            if gap <= _LIST_MAX_GAP_WORDS and counts_up:
                gaps.append(gap)
                current.append(cand)
                continue
        if len(current) >= _LIST_MIN_ENTRIES:
            runs.append((current, gaps))
        current, gaps = [cand], []
    if len(current) >= _LIST_MIN_ENTRIES:
        runs.append((current, gaps))

    removed_ids: set = set()
    for run, run_gaps in runs:
        if statistics.median(run_gaps) > _LIST_MAX_MEDIAN_GAP_WORDS:
            continue

        first, last = run[0], run[-1]

        # (a) a "Contents" label just before the list
        before = first.element.xpath("preceding::text()[normalize-space()]")[-3:]
        labelled = any(_CONTENTS_LABEL_RE.match(str(text)) for text in before)

        # (b) the same numbering starts again later with real text
        later_real = False
        if first.number is not None:
            run_ids = {id(c) for c in run}
            for i, cand in enumerate(ordered):
                if cand.book_order <= last.book_order or id(cand) in run_ids:
                    continue
                if cand.number != first.number or cand.element is None:
                    continue
                nxt = ordered[i + 1] if i + 1 < len(ordered) else None
                if words_after(cand, nxt) >= _REAL_CHAPTER_MIN_WORDS:
                    later_real = True
                    break

        if labelled or later_real:
            removed_ids.update(id(c) for c in run)

    kept = [c for c in candidates if id(c) not in removed_ids]
    removed = [c for c in candidates if id(c) in removed_ids]
    return kept, removed
