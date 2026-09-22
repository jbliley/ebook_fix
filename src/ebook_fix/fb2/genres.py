"""
ebook_fix.fb2.genres

Turns an FB2 genre code (e.g. "sf_epic", "love_history") into a
human-readable subject label (e.g. "Epic Sci-Fi", "Historical
Romance"), for the dc:subject entries a converted book gets.

The lookup table is genres.json, kept as data rather than code so
Jacob (or anyone) can add or correct an entry without touching any
Python -- same convention as
src/metadata/schemes/identifier_schemes.json for identifier types.

A code that isn't in the table is never dropped: it's shown as its own
code with underscores turned to spaces and title-cased (e.g. an
unrecognized "adv_polar" becomes "Adv Polar") rather than silently
losing the genre entirely.
"""
from __future__ import annotations

import json
from pathlib import Path

_SCHEME_PATH = Path(__file__).parent / "genres.json"
_cache: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        with open(_SCHEME_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        _cache = {k: v for k, v in raw.items() if not k.startswith("_")}
    return _cache


def genre_label(code: str) -> str:
    """The human-readable label for one FB2 genre code."""
    code = (code or "").strip()
    if not code:
        return ""
    table = _load()
    if code in table:
        return table[code]
    return code.replace("_", " ").replace("-", " ").strip().title()
