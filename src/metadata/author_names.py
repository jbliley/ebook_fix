"""
metadata.author_names

A common, mechanical disagreement between an EPUB's own dc:creator and
a Calibre metadata.opf sidecar is name order: one side has "Smith,
John" and the other has "John Smith" -- the same name, just rendered
the two conventional ways. Per Jacob's preference this should always
resolve to "First Last" rather than being logged as a field mismatch
needing a person's judgment call. See docs/metadata_plan.md, "Open
questions".

detect_reversed_author() only recognizes the mechanical reversal case
(exactly one comma, and the comma-form's parts rearranged match the
other side word-for-word). Anything else -- a genuinely different
name, multiple authors joined together -- still falls through to
metadata.merge's normal mismatch flagging, since guessing at those
would violate the project's "repair only unambiguous cases" principle.

standardize_initials() handles a separate, single-sided concern:
normalizing "AA Milne" / "A.A. Milne" / "A.A.Milne" to "A. A. Milne".
Unlike the reversal check, this never needs a second source to be
confident -- an all-caps initials-shaped token is unambiguous on its
own -- so it's wired up as its own repair
(modules/author_initials_repair.py) that runs on every book, Calibre-
managed or not, the same posture metadata.identifiers already takes.
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


def _is_initials_cluster(token: str) -> bool:
    """A token is treated as an initials cluster when, once its
    periods are stripped, what's left is all uppercase letters --
    but a token with no periods at all only qualifies if it's 1-2
    letters ("A", "AA"). A period already in the token ("A.A.",
    "J.R.R.") is a near-unambiguous signal on its own, so those are
    allowed up to 4 letters. Without a period, though, a 3+ letter
    all-caps cluster is genuinely ambiguous with a real all-caps given
    name (accidental all-caps data entry is common enough that "JOHN
    SMITH" or "ANN SMITH" needs to survive untouched, not become
    "J. O. H. N. SMITH") -- so bare, unpunctuated clusters are kept to
    a length where that collision essentially can't happen."""
    letters = token.replace(".", "")
    if not (letters and letters.isalpha() and letters.isupper()):
        return False
    if "." in token:
        return len(letters) <= 4
    return len(letters) <= 2


def standardize_initials(name: str) -> str | None:
    """Standardizes initials in an author's given name(s) to "X. Y."
    style -- Jacob's preferred convention, so "AA Milne", "A.A. Milne",
    and "A.A.Milne" all become "A. A. Milne". Deliberately only ever
    touches an all-caps initials-shaped token (see _is_initials_cluster
    above); a normal name is never rewritten.

    Only tokens before the final one are considered, on the assumption
    that the last word in a "First Last" name is the surname -- this
    is what keeps a genuinely short all-caps surname (rare, but
    possible) from ever being mistaken for initials. A single-word
    name (a mononym, an organization credited as author) is left
    alone entirely, since there's no leading token to check.

    Returns the standardized form, or None if nothing needed
    changing -- callers should treat None the same as "no rewrite"."""
    if not name or not name.strip():
        return None

    tokens = name.split(" ")
    if len(tokens) < 2:
        return None

    changed = False
    out_tokens = list(tokens)
    for i, tok in enumerate(tokens[:-1]):
        if not _is_initials_cluster(tok):
            continue
        letters = tok.replace(".", "")
        formatted = " ".join(f"{ch}." for ch in letters)
        if formatted != tok:
            changed = True
        out_tokens[i] = formatted

    if not changed:
        return None
    return " ".join(out_tokens)
