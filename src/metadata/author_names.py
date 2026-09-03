"""
metadata.author_names

A common, mechanical disagreement between an EPUB's own dc:creator and
a Calibre metadata.opf sidecar is name order: one side has "Smith,
John" and the other has "John Smith" -- the same name, just rendered
the two conventional ways. Per Jacob's preference this should always
resolve to "First Last" rather than being logged as a field mismatch
needing a person's judgment call. See docs/metadata_plan.md, "Open
questions".

This module only recognizes the mechanical reversal case (exactly one
comma, and the comma-form's parts rearranged match the other side
word-for-word). Anything else -- a genuinely different name, multiple
authors joined together, initials rendered differently -- still falls
through to metadata.merge's normal mismatch flagging, since guessing
at those would violate the project's "repair only unambiguous cases"
principle.
"""
from __future__ import annotations

import re


def _collapse(text: str) -> str:
    """Lowercases and collapses whitespace, for comparison only."""
    return re.sub(r"\s+", " ", text.strip()).casefold()


def _as_first_last(comma_form: str) -> str | None:
    """If comma_form looks like 'Last, First' (exactly one comma,
    non-empty on both sides), returns the 'First Last' rendering.
    Returns None otherwise (no comma, or more than one -- e.g. a
    suffix like 'Smith, John, Jr.' isn't touched)."""
    if comma_form.count(",") != 1:
        return None
    last, first = (part.strip() for part in comma_form.split(","))
    if not last or not first:
        return None
    return f"{first} {last}"


def detect_reversed_author(value_a: str, value_b: str) -> str | None:
    """Checks whether value_a and value_b are the same author name in
    'Last, First' vs 'First Last' order. If so, returns the
    canonical 'First Last' form (built from the comma side's own
    words, so accents/capitalization/middle names are preserved
    exactly as written). Returns None if neither side looks like a
    reversal of the other."""
    for comma_form, plain_form in ((value_a, value_b), (value_b, value_a)):
        if not comma_form or not plain_form:
            continue
        candidate = _as_first_last(comma_form)
        if candidate is not None and _collapse(candidate) == _collapse(plain_form):
            return candidate
    return None
