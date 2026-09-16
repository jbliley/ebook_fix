"""Chapter split file generator.

Generates new XHTML files for chapters detected by structure.py analysis.
This is completely optional - must be explicitly enabled, never runs by default.

Takes a BookStructure with confirmed chapters and generates split files by:
1. Extracting content between chapter boundaries
2. Creating new XHTML files with proper DOCTYPE and namespaces
3. Registering new files in book.new_files for the writer to include
4. Updating book's internal tracking for manifest/spine updates (separate modules)

Does NOT:
- Modify manifest.opf (separate module)
- Update spine ordering (separate module)
- Modify TOC/NCX (separate module)
- Run by default (must be explicitly called)

This isolation ensures split generation can be reviewed and verified
before any actual EPUB changes are committed.
"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from lxml import etree
from typing import Any, Optional


XHTML_DOCTYPE = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>"""

XHTML_NAMESPACES = {
    'html': 'http://www.w3.org/1999/xhtml',
    'epub': 'http://www.idpf.org/2007/ops',
    'opf': 'http://www.idpf.org/2007/opf',
}


@dataclass
class SplitFileInfo:
    """Info about a generated split file."""
    original_chapter_id: str
    original_chapter_href: str
    new_filename: str
    new_file_path: str  # Relative path in package (e.g., "OEBPS/chapter_1.xhtml")
    content_element: Any  # The lxml element containing the split content
    word_count: int
    is_continuation: bool  # True if this is a second+ part of a split chapter


def _sanitize_filename(text: str) -> str:
    """Create a safe filename from chapter text.
    
    Removes/replaces unsafe characters, keeps first ~30 chars of text.
    """
    # Keep only alphanumeric, spaces, hyphens
    safe = "".join(c if c.isalnum() or c in " -" else "" for c in text[:40])
    # Replace spaces with underscores
    safe = safe.replace(" ", "_").lower()
    # Remove leading/trailing hyphens
    safe = safe.strip("-")
    return safe if safe else "chapter"


def generate_split_files(
    book: Any,
    tree: Any,
    output_dir: Optional[str] = None,
    dry_run: bool = False
) -> list[SplitFileInfo]:
    """Generate split XHTML files for confirmed chapters.
    
    Args:
        book: Parsed Book object
        tree: BookStructure from analyze_structure()
        output_dir: Directory for new files (defaults to same dir as original)
        dry_run: If True, don't actually create files, just plan them
    
    Returns:
        List of SplitFileInfo describing generated files
        
    Raises:
        ValueError: If tree has no confirmed chapters or invalid structure
    """
    from ebook_fix.structure import _walk_chapters, _content_word_count
    
    chapter_nodes = list(_walk_chapters(tree.nodes))
    if not chapter_nodes:
        raise ValueError("No chapters found in BookStructure")
    
    if not hasattr(book, 'new_files'):
        book.new_files = {}
    
    # Determine output directory for file paths
    if output_dir is None:
        base_dir = PurePosixPath(book.package_path).parent
    else:
        base_dir = PurePosixPath(output_dir)
    
    split_files = []
    split_count = {}  # Track how many splits per original chapter
    
    for i, node in enumerate(chapter_nodes):
        if node.evidence is None or node.evidence.candidate is None:
            continue
        
        candidate = node.evidence.candidate
        start_element = candidate.element
        if start_element is None:
            continue
        
        # Determine end point (next chapter or end of file)
        next_node = chapter_nodes[i + 1] if i + 1 < len(chapter_nodes) else None
        end_element = next_node.evidence.candidate.element if next_node else None
        
        # Extract content between boundaries
        content_element = _extract_chapter_content(start_element, end_element)
        if content_element is None:
            continue
        
        # Generate filename
        chapter_text = candidate.text[:30] if candidate.text else f"chapter_{i}"
        base_filename = _sanitize_filename(chapter_text)
        
        # Handle multiple splits of same chapter (append _a, _b, etc)
        split_num = split_count.get(i, 0) + 1
        split_count[i] = split_num
        
        if split_num > 1:
            filename = f"{base_filename}_{chr(96 + split_num)}.xhtml"  # _b, _c, ...
        else:
            filename = f"{base_filename}.xhtml"
        
        # Build file path relative to package root
        file_path = str(base_dir / filename)
        relative_path = str(PurePosixPath(filename))
        
        # Count words in this split
        word_count = _content_word_count(
            book,
            node.start_href,
            start_element,
            next_node.start_href if next_node else None,
            end_element
        )
        
        split_info = SplitFileInfo(
            original_chapter_id=candidate.chapter_number if hasattr(candidate, 'chapter_number') else str(i),
            original_chapter_href=node.start_href,
            new_filename=filename,
            new_file_path=relative_path,
            content_element=content_element,
            word_count=word_count,
            is_continuation=split_num > 1
        )
        split_files.append(split_info)
        
        if not dry_run:
            # Serialize to XHTML and store in book.new_files
            xhtml_content = _serialize_split_file(content_element, filename)
            book.new_files[file_path] = xhtml_content
    
    return split_files


def _extract_chapter_content(
    start_element: Any,
    end_element: Optional[Any] = None
) -> Optional[Any]:
    """Extract content between two chapter boundaries.
    
    Creates a new document containing all text and elements from start_element
    up to (but not including) end_element.
    
    Returns the root element of a new document, or None if extraction failed.
    """
    # Create new HTML document with proper structure
    root = etree.Element(
        f"{{{XHTML_NAMESPACES['html']}}}html",
        nsmap={'epub': XHTML_NAMESPACES['epub']}
    )
    head = etree.SubElement(root, f"{{{XHTML_NAMESPACES['html']}}}head")
    body = etree.SubElement(root, f"{{{XHTML_NAMESPACES['html']}}}body")
    
    # Deep copy start_element into body
    content_copy = etree.fromstring(etree.tostring(start_element))
    body.append(content_copy)
    
    # Walk through following siblings and parent's siblings until we hit end_element
    current = start_element
    while current is not None:
        # Move to next sibling
        next_sibling = current.getnext()
        
        if next_sibling is None:
            # No more siblings, try parent's next sibling
            parent = current.getparent()
            if parent is not None:
                current = parent.getnext()
            else:
                break
        else:
            # Add sibling to body if not end_element
            if next_sibling is not end_element:
                sibling_copy = etree.fromstring(etree.tostring(next_sibling))
                body.append(sibling_copy)
            else:
                # Hit end_element, stop
                break
            current = next_sibling
    
    return root


def _serialize_split_file(root_element: Any, filename: str) -> str:
    """Serialize a split chapter into proper XHTML format.
    
    Returns the complete XHTML document as a string, ready to write to file.
    """
    xhtml_bytes = etree.tostring(
        root_element,
        encoding='utf-8',
        xml_declaration=False,
        pretty_print=True
    )
    
    # Decode and prepend proper XHTML declaration
    xhtml_str = xhtml_bytes.decode('utf-8')
    
    # Add DOCTYPE and XML declaration
    result = f'{XHTML_DOCTYPE}\n{xhtml_str}'
    
    return result


def plan_splits(
    book: Any,
    tree: Any
) -> dict:
    """Plan chapter splits without actually creating files.
    
    Useful for review/verification before committing to actual split.
    
    Returns a summary dict with:
    - split_count: How many new files would be created
    - total_chapters: Original chapter count
    - new_files: List of SplitFileInfo objects
    - verification_ready: Whether this is safe to split
    """
    try:
        split_files = generate_split_files(book, tree, dry_run=True)
    except ValueError as e:
        return {
            'error': str(e),
            'split_count': 0,
            'total_chapters': 0,
            'new_files': [],
            'verification_ready': False
        }
    
    from ebook_fix.structure import _walk_chapters
    chapter_nodes = list(_walk_chapters(tree.nodes))
    
    return {
        'error': None,
        'split_count': len(split_files),
        'total_chapters': len(chapter_nodes),
        'new_files': split_files,
        'total_new_words': sum(f.word_count for f in split_files),
        'verification_ready': len(split_files) > 0
    }
