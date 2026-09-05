"""
metadata.title_match

Recognizes the common, purely-cosmetic ways the exact same title ends
up looking different between an EPUB and a Calibre metadata.opf
sidecar -- extra/irregular whitespace, straight vs "smart" punctuation
(quotes, apostrophes, dashes, ellipses), and one side being rendered
in ALL CAPS (almost always a source-data artifact from an OCR'd or
Gutenberg-sourced text, never a deliberate title style) -- and
resolves them the same way merge.py already resolves the
language-code and reversed-author-name non-issues: as the same value,
not a disagreement.

This module deliberately does NOT try to resolve a genuine subtitle or
series-annotation difference (e.g. "Sidewinders" vs "Sidewinders: A
Western Novel", or "Sidewinders" vs "Sidewinders (Sidewinders, #1)").
Picking a side there is a real editorial call -- should the subtitle
be kept? is baking the series into the title even wanted, given series
is already its own tracked field? -- not a formatting cleanup, so
detect_subtitle_relationship() below only *flags* the relationship
with an explanatory note for a person to weigh in on quickly; it never
resolves it. See docs/metadata_plan.md.
"""
from __future__ import annotations

import re

# Maps "smart"/typeset punctuation to its plain ASCII equivalent, for
# comparison only -- never applied to a value that actually gets
# written anywhere. Covers the punctuation marks a title is actually
# likely to contain; body-text punctuation normalization (this
# project's own apostrophe/ellipsis repair) is a separate, larger
# concern this module doesn't need to duplicate.
_PUNCTUATION_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2013": "-", "\u2014": "-", "\u2012": "-",
    "\u2026": "...",
})

_SUBTITLE_SEPARATORS = (":", " - ", " \u2013 ", " \u2014 ")


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _fold_punctuation(text: str) -> str:
    return text.translate(_PUNCTUATION_FOLD)


def _comparison_form(text: str) -> str:
    """Collapses whitespace, folds smart punctuation to plain ASCII,
    and case-folds -- for comparison only, never returned as an actual
    value."""
    return _fold_punctuation(_collapse_whitespace(text)).casefold()


def _is_shouting(text: str) -> bool:
    """True if every cased character in text is uppercase and there's
    at least one -- Python's str.isupper() already means exactly
    this, ignoring digits/punctuation along the way."""
    return bool(text) and text.isupper()


def _quality(text: str) -> tuple[int, int]:
    """Higher is a "cleaner" rendering to prefer as the canonical
    value once two titles are already confirmed equivalent: not
    ALL CAPS, and already has collapsed whitespace."""
    return (0 if _is_shouting(text) else 1, 1 if _collapse_whitespace(text) == text else 0)


def titles_equivalent(epub_value: str, calibre_value: str) -> str | None:
    """If epub_value and calibre_value are the same title once
    whitespace/smart-punctuation-style/case are normalized away,
    returns the cleaner of the two renderings to standardize on.
    Returns None if they're not the same title this way (including a
    genuine subtitle/content difference -- see
    detect_subtitle_relationship() for that case instead)."""
    if not epub_value or not calibre_value:
        return None
    if _comparison_form(epub_value) != _comparison_form(calibre_value):
        return None

    # Tie-break toward calibre_value, matching MergedField.display_value's
    # existing "calibre wins when otherwise equal" convention elsewhere.
    winner = epub_value if _quality(epub_value) > _quality(calibre_value) else calibre_value
    # Collapsing whitespace on the winner (a no-op if it's already
    # clean) covers the case where BOTH sides have some whitespace
    # irregularity -- picking "the less-bad of two dirty renderings"
    # would still leave it dirty; the resolved value should actually
    # be clean.
    return _collapse_whitespace(winner)


def detect_subtitle_relationship(epub_value: str, calibre_value: str) -> str | None:
    """If one of epub_value/calibre_value looks like the other with a
    subtitle (a colon/dash-separated clause) or a trailing
    parenthetical (often a series annotation) appended -- and the
    leading portion matches the shorter title once normalized the same
    way titles_equivalent() does -- returns a short note describing
    the relationship and naming the longer rendering, for a person to
    weigh in on. Never auto-resolved; see module docstring for why."""
    if not epub_value or not calibre_value:
        return None

    shorter, longer = (epub_value, calibre_value) if len(epub_value) <= len(calibre_value) else (calibre_value, epub_value)
    if shorter == longer:
        return None
    norm_shorter = _comparison_form(shorter)

    for sep in _SUBTITLE_SEPARATORS:
        idx = longer.find(sep)
        if idx <= 0:
            continue
        lead, rest = longer[:idx], longer[idx + len(sep):].strip()
        if rest and _comparison_form(lead) == norm_shorter:
            return (
                f"'{shorter}' looks like the same title with a subtitle "
                f"('{rest}') added in '{longer}' -- left as a mismatch for "
                "a person to decide whether to keep it."
            )

    m = re.match(r"^(.*\S)\s*\(([^()]+)\)\s*$", longer)
    if m:
        lead, inner = m.group(1), m.group(2).strip()
        if inner and _comparison_form(lead) == norm_shorter:
            return (
                f"'{shorter}' looks like the same title with '({inner})' "
                f"added in '{longer}' (often a series annotation) -- left "
                "as a mismatch for a person to decide."
            )

    return None
