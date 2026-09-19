"""ISBN and title/author metadata lookup from Open Library API.

Provides functions to:
- Extract ISBN from EPUB metadata
- Validate ISBN format (basic check)
- Fetch book metadata from Open Library by ISBN
- Fetch book metadata by title+author search
- Compare current EPUB metadata with lookup results
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional
from ebook_fix.models import Book


# ISBN validation: basic check for 10 or 13 digits (ignoring hyphens/spaces)
_ISBN_PATTERN = re.compile(r'^[\d\-\s]+$')


def extract_isbn_from_epub(book: Book) -> Optional[str]:
    """Extract ISBN from EPUB's dc:identifier metadata.
    
    Looks for standard ISBN-10 or ISBN-13 format in the book's metadata.
    Returns the ISBN if found and valid, or None otherwise.
    
    Args:
        book: Book object with metadata
        
    Returns:
        ISBN string (digits only, no formatting) or None
    """
    if not book.metadata or not book.metadata.identifier:
        return None
    
    identifier_str = book.metadata.identifier.strip()
    if not identifier_str:
        return None
    
    # Clean the identifier (remove hyphens/spaces)
    clean_isbn = identifier_str.replace('-', '').replace(' ', '')
    
    # Check if it's a valid ISBN (10 or 13 digits)
    if _ISBN_PATTERN.match(clean_isbn) and len(clean_isbn) in (10, 13):
        return clean_isbn
    
    return None


def _fetch_url(url: str, timeout: int = 10) -> Optional[dict]:
    """Fetch JSON from URL with timeout and error handling.
    
    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        
    Returns:
        Parsed JSON dict or None on error
    """
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'ebook-fix/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        return None


def fetch_isbn_metadata(isbn: str) -> Optional[dict]:
    """Fetch book metadata from Open Library by ISBN.
    
    Args:
        isbn: ISBN string (10 or 13 digits, hyphens/spaces ignored)
        
    Returns:
        Dict with keys: title, author, publisher, publish_date, description, isbn
        Or None if not found
    """
    if not isbn:
        return None
    
    # Clean ISBN
    clean_isbn = isbn.replace('-', '').replace(' ', '')
    if not _ISBN_PATTERN.match(clean_isbn) or len(clean_isbn) not in (10, 13):
        return None
    
    url = f"https://openlibrary.org/isbn/{clean_isbn}.json"
    data = _fetch_url(url)
    
    if not data:
        return None
    
    # Parse the response
    result = {
        'title': data.get('title'),
        'author': None,
        'publisher': None,
        'publish_date': None,
        'description': None,
        'isbn': clean_isbn,
    }
    
    # Extract author (first author if multiple)
    authors = data.get('authors', [])
    if authors and len(authors) > 0:
        author_key = authors[0].get('key')
        if author_key:
            # author_key is like "/authors/OL123A" - extract name separately
            result['author'] = data.get('authors', [{}])[0].get('name')
    
    # Extract other fields
    publishers = data.get('publishers', [])
    if publishers:
        result['publisher'] = publishers[0]
    
    result['publish_date'] = data.get('publish_date')
    
    description = data.get('description')
    if description:
        if isinstance(description, dict):
            result['description'] = description.get('value')
        else:
            result['description'] = description
    
    return result


def fetch_title_author_metadata(title: str, author: Optional[str] = None) -> Optional[dict]:
    """Fetch book metadata from Open Library by title and author.
    
    Args:
        title: Book title
        author: Book author (optional, improves search precision)
        
    Returns:
        Dict with keys: title, author, publisher, publish_date, description, isbn
        Or None if not found
    """
    if not title:
        return None
    
    # Build search query
    query_parts = [f"title={urllib.parse.quote(title)}"]
    if author:
        query_parts.append(f"author={urllib.parse.quote(author)}")
    
    url = f"https://openlibrary.org/search.json?{'&'.join(query_parts)}&limit=1"
    data = _fetch_url(url)
    
    if not data or 'docs' not in data or len(data['docs']) == 0:
        return None
    
    doc = data['docs'][0]
    
    # Extract ISBN (prefer ISBN-13)
    isbn = None
    isbns = doc.get('isbn', [])
    if isbns:
        # Try to find ISBN-13 first
        for isbn_candidate in isbns:
            clean = isbn_candidate.replace('-', '')
            if len(clean) == 13:
                isbn = clean
                break
        # Fall back to first ISBN if no ISBN-13
        if not isbn and isbns:
            isbn = isbns[0].replace('-', '')
    
    result = {
        'title': doc.get('title'),
        'author': None,
        'publisher': None,
        'publish_date': None,
        'description': None,
        'isbn': isbn,
    }
    
    # Extract first author
    authors = doc.get('author_name', [])
    if authors:
        result['author'] = authors[0]
    
    # Extract publisher (first one)
    publishers = doc.get('publisher', [])
    if publishers:
        result['publisher'] = publishers[0]
    
    result['publish_date'] = doc.get('first_publish_year')
    
    return result


def compare_metadata(current: dict, lookup: dict) -> dict:
    """Compare current EPUB metadata with lookup result.
    
    Args:
        current: Current EPUB metadata dict with keys: title, author, publisher, publish_date, description
        lookup: Lookup result dict (same keys)
        
    Returns:
        Dict with:
        - 'status': 'exact_match', 'partial_match', or 'no_match'
        - 'matches': dict of field -> bool (which fields match)
        - 'differences': dict of field -> (current_value, lookup_value)
        - 'all_fields': dict of field -> {current, lookup, match}
    """
    # Fields to compare (non-empty, non-None)
    compare_fields = ['title', 'author', 'publisher']
    
    matches = {}
    differences = {}
    all_fields = {}
    
    for field in compare_fields:
        current_val = (current.get(field) or '').strip()
        lookup_val = (lookup.get(field) or '').strip()
        
        # Consider match if both exist and are equal (case-insensitive)
        is_match = (current_val and lookup_val and 
                   current_val.lower() == lookup_val.lower())
        
        matches[field] = is_match
        
        all_fields[field] = {
            'current': current_val,
            'lookup': lookup_val,
            'match': is_match,
        }
        
        if not is_match and (current_val or lookup_val):
            differences[field] = (current_val, lookup_val)
    
    # Determine overall status
    if all(matches.values()):
        status = 'exact_match'
    elif any(matches.values()) or differences:
        status = 'partial_match'
    else:
        status = 'no_match'
    
    return {
        'status': status,
        'matches': matches,
        'differences': differences,
        'all_fields': all_fields,
        'lookup_isbn': lookup.get('isbn'),
    }
