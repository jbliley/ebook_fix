"""Manifest and spine updater for split chapters.

Updates package.opf after chapter splits to register new files:
1. Adds manifest entries for new XHTML files
2. Updates spine to include new files in reading order
3. Handles ID generation and uniqueness
4. Preserves existing manifest/spine structure

Works in conjunction with split_generator.py - takes the SplitFileInfo objects
and registers them in the package document.

Does NOT modify the actual file content - just the manifest/spine references.
The EPUBWriter handles actually writing updated package.opf.
"""

from lxml import etree
from typing import Any, Optional
from pathlib import PurePosixPath


NAMESPACES = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
}


def _generate_unique_id(book: Any, base_id: str) -> str:
    """Generate a unique ID for manifest entry.
    
    Appends suffixes (_1, _2, etc) until a unique ID is found.
    """
    # Get existing IDs from manifest
    existing_ids = set()
    opf_tree = book.opf_document
    if opf_tree is not None:
        manifest_items = opf_tree.findall('.//opf:manifest/opf:item', NAMESPACES)
        existing_ids = {item.get('id') for item in manifest_items if item.get('id')}
    
    if base_id not in existing_ids:
        return base_id
    
    # Add suffix until unique
    counter = 1
    while f"{base_id}_{counter}" in existing_ids:
        counter += 1
    return f"{base_id}_{counter}"


def _get_spine_position(book: Any, original_href: str) -> Optional[int]:
    """Find the position in spine where split files should be inserted.
    
    Returns the index after the original chapter's last occurrence in spine,
    or None if original_href not found.
    """
    opf_tree = book.opf_document
    if opf_tree is None:
        return None
    
    # Find original chapter in spine
    spine_items = opf_tree.findall('.//opf:spine/opf:itemref', NAMESPACES)
    
    # Get manifest to map idref back to href
    manifest_items = opf_tree.findall('.//opf:manifest/opf:item', NAMESPACES)
    id_to_href = {item.get('id'): item.get('href') for item in manifest_items}
    
    # Find last occurrence of original href in spine
    last_position = None
    for i, itemref in enumerate(spine_items):
        idref = itemref.get('idref')
        href = id_to_href.get(idref, '')
        
        # Match full href or just filename
        if href == original_href or href.endswith(PurePosixPath(original_href).name):
            last_position = i
    
    return last_position + 1 if last_position is not None else None


def update_manifest(
    book: Any,
    split_files: list[Any]
) -> dict:
    """Add new manifest entries for split files.
    
    Args:
        book: Book object with opf_document
        split_files: List of SplitFileInfo objects from split_generator
    
    Returns:
        Dict with results: {
            'added': int (count of manifest items added),
            'id_map': dict (new_filename -> manifest_id),
            'errors': list (any errors encountered)
        }
    """
    opf_tree = book.opf_document
    if opf_tree is None:
        return {
            'added': 0,
            'id_map': {},
            'errors': ['No OPF document found']
        }
    
    manifest = opf_tree.find('.//opf:manifest', NAMESPACES)
    if manifest is None:
        return {
            'added': 0,
            'id_map': {},
            'errors': ['No manifest element found']
        }
    
    id_map = {}
    errors = []
    
    for split_info in split_files:
        try:
            # Generate safe ID from filename (remove extension, ensure unique)
            base_id = PurePosixPath(split_info.new_filename).stem
            unique_id = _generate_unique_id(book, base_id)
            
            # Create manifest item
            item = etree.SubElement(manifest, f"{{{NAMESPACES['opf']}}}item")
            item.set('id', unique_id)
            item.set('href', split_info.new_file_path)
            item.set('media-type', 'application/xhtml+xml')
            
            id_map[split_info.new_filename] = unique_id
            
        except Exception as e:
            errors.append(f"Failed to add {split_info.new_filename}: {str(e)}")
    
    book.opf_modified = True
    
    return {
        'added': len(id_map),
        'id_map': id_map,
        'errors': errors
    }


def update_spine(
    book: Any,
    split_files: list[Any],
    id_map: dict
) -> dict:
    """Update spine to include new split files.
    
    Args:
        book: Book object with opf_document
        split_files: List of SplitFileInfo objects (in reading order)
        id_map: Result from update_manifest (new_filename -> manifest_id)
    
    Returns:
        Dict with results: {
            'added': int (count of spine items added),
            'insert_position': int (where items were inserted),
            'errors': list
        }
    """
    opf_tree = book.opf_document
    if opf_tree is None:
        return {
            'added': 0,
            'insert_position': None,
            'errors': ['No OPF document found']
        }
    
    spine = opf_tree.find('.//opf:spine', NAMESPACES)
    if spine is None:
        return {
            'added': 0,
            'insert_position': None,
            'errors': ['No spine element found']
        }
    
    errors = []
    insert_position = None
    
    for split_info in split_files:
        try:
            # Find where to insert (after original chapter in spine, first time only)
            if insert_position is None:
                insert_position = _get_spine_position(book, split_info.original_chapter_href)
                if insert_position is None:
                    # Original not found, append to end
                    insert_position = len(spine)
            
            # Get manifest ID for this split file
            manifest_id = id_map.get(split_info.new_filename)
            if not manifest_id:
                errors.append(f"No manifest ID found for {split_info.new_filename}")
                continue
            
            # Create spine itemref
            itemref = etree.Element(f"{{{NAMESPACES['opf']}}}itemref")
            itemref.set('idref', manifest_id)
            itemref.set('linear', 'yes')
            
            # Insert at calculated position
            spine.insert(insert_position, itemref)
            insert_position += 1
            
        except Exception as e:
            errors.append(f"Failed to add {split_info.new_filename} to spine: {str(e)}")
    
    book.opf_modified = True
    
    return {
        'added': len([f for f in split_files if id_map.get(f.new_filename)]),
        'insert_position': insert_position,
        'errors': errors
    }


def apply_split_updates(
    book: Any,
    split_files: list[Any]
) -> dict:
    """Apply all manifest and spine updates for split files.
    
    Calls update_manifest and update_spine in correct order.
    
    Returns:
        Dict with results from both operations:
        {
            'manifest': {...},
            'spine': {...},
            'total_added': int,
            'success': bool
        }
    """
    if not split_files:
        return {
            'manifest': {'added': 0, 'id_map': {}, 'errors': []},
            'spine': {'added': 0, 'insert_position': None, 'errors': []},
            'total_added': 0,
            'success': True
        }
    
    # Update manifest first (generates IDs)
    manifest_result = update_manifest(book, split_files)
    
    # Then update spine (uses IDs from manifest)
    spine_result = update_spine(book, split_files, manifest_result['id_map'])
    
    return {
        'manifest': manifest_result,
        'spine': spine_result,
        'total_added': manifest_result['added'],
        'success': (
            len(manifest_result['errors']) == 0 and 
            len(spine_result['errors']) == 0
        )
    }


def verify_manifest_spine_integrity(book: Any) -> dict:
    """Verify that manifest and spine are consistent.
    
    Checks:
    - All spine idrefs exist in manifest
    - All manifest items have IDs
    - No duplicate IDs
    - Spine is not empty
    
    Returns:
        {
            'valid': bool,
            'issues': list of strings describing any issues found
        }
    """
    opf_tree = book.opf_document
    if opf_tree is None:
        return {
            'valid': False,
            'issues': ['No OPF document found']
        }
    
    issues = []
    
    # Check manifest
    manifest_items = opf_tree.findall('.//opf:manifest/opf:item', NAMESPACES)
    manifest_ids = set()
    for item in manifest_items:
        item_id = item.get('id')
        if not item_id:
            issues.append("Manifest item without id attribute")
        elif item_id in manifest_ids:
            issues.append(f"Duplicate manifest id: {item_id}")
        else:
            manifest_ids.add(item_id)
    
    # Check spine
    spine_items = opf_tree.findall('.//opf:spine/opf:itemref', NAMESPACES)
    if not spine_items:
        issues.append("Spine is empty")
    
    spine_idrefs = set()
    for itemref in spine_items:
        idref = itemref.get('idref')
        if not idref:
            issues.append("Spine itemref without idref attribute")
        elif idref not in manifest_ids:
            issues.append(f"Spine itemref '{idref}' not found in manifest")
        else:
            spine_idrefs.add(idref)
    
    return {
        'valid': len(issues) == 0,
        'issues': issues
    }
