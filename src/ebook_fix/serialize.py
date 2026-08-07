"""
ebook_fix.serialize

Saves the analysis report (from analyzer.EPUBAnalyzer) to a JSON file
next to the book, and loads it back. This is the cache that lets
repair logic use what analysis already found instead of re-scanning
the book from scratch.

The cache is plain JSON (a dict), not reconstructed back into the
original dataclasses. That's deliberate: dataclasses evolve as the
analyzer grows, and a dict of the same shape is simpler and more
forgiving to read from than trying to rebuild exact Python objects
every time a field is added or renamed.

One thing intentionally dropped during save: any field named
"element". A couple of the chapter-detection dataclasses (in
chapters.py) carry a live reference to the actual lxml element they
were read from, for convenience while the book is open in memory.
That reference is only good for the lifetime of that one parse, so
it can't be written to a file and isn't dropped here.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path


def _convert(value):
    """Turn one value from the analysis report into something json.dump can write."""

    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _convert(getattr(value, f.name))
            for f in fields(value)
            if f.name != "element"
        }

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Counter):
        return dict(value)

    if isinstance(value, dict):
        return {str(k): _convert(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_convert(v) for v in value]

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    # Anything else (live lxml elements, etc.) can't be written to
    # JSON and isn't something a cached report should hold onto.
    return None


def to_dict(report) -> dict:
    """Convert an analyzer.AnalysisReport into a plain, JSON-safe dict."""
    return _convert(report)


def cache_path_for(epub_path) -> Path:
    """Where the cached analysis for a given book lives, next to the book itself."""
    epub_path = Path(epub_path)
    return epub_path.with_name(epub_path.stem + ".ebookfix-analysis.json")


def save_report(report, epub_path) -> Path:
    """
    Save an analysis report as JSON, named after the book it came
    from. Returns the path it was written to.
    """
    path = cache_path_for(epub_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_dict(report), f, indent=2, ensure_ascii=False)
    return path


def load_report(epub_path) -> dict | None:
    """
    Load a previously cached analysis report for a book, if one
    exists. Returns a plain dict (see module docstring for why),
    or None if no cache file is there yet.
    """
    path = cache_path_for(epub_path)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
