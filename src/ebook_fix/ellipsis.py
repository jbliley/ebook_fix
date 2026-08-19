"""
ebook_fix.ellipsis

DOM-aware ellipsis analysis: finds two common ways an ellipsis ends up
represented in converted text instead of the single Unicode ellipsis
character (…):

  1. The three-dot ASCII stand-in ("...")
  2. A "spaced" ellipsis (". . ." or similar), which usually comes
     from an old print convention, or from an OCR/conversion pass
     that put a space between every character it treated as its own
     "word"

Both get normalized to the same target, either the single Unicode
ellipsis character or a clean three-dot ASCII ellipsis with no
internal spaces, depending on config (see
ebook_fix.modules.ellipsis_repair). The Unicode character is the
recommended default: an EPUB's HTML content is always UTF-8 text, so
it displays correctly on every reading system regardless of device,
and it reads better to text-to-speech / accessibility tools, which
otherwise announce "dot dot dot" for the ASCII version.

Uses the same text/tail tree walker as ebook_fix.whitespace
(iter_text_slots) so protected content (pre/code/script/style/svg/math)
is skipped here too, without this module needing its own copy of that
walk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ebook_fix.whitespace import iter_text_slots

UNICODE_ELLIPSIS = "\u2026"    # …
ASCII_ELLIPSIS = "..."

VALID_TARGET_STYLES = ("unicode", "ascii")

# A spaced-dot ellipsis: three or more periods, each separated from
# the next by at least one space or tab. Requires at least three dots
# total (two gaps) so a bare ". ." doesn't accidentally match -- that
# shape is far more likely to be something else than a mangled
# ellipsis, and is left alone.
_SPACED_RE = r'\.(?:[ \t]+\.){2,}'

# The plain three-dot stand-in -- same shape ebook_fix.typography
# already counts for the read-only overview. Deliberately excludes
# runs of 4+ dots, which are a different kind of artifact (see
# typography.REPEATED_PUNCT_RE) and shouldn't get silently folded
# into an ellipsis here.
_ASCII_RE = r'(?<!\.)\.\.\.(?!\.)'

_COMBINED_RE = re.compile(f'(?P<spaced>{_SPACED_RE})|(?P<ascii>{_ASCII_RE})')


@dataclass
class EllipsisNormalizeResult:
    text: str
    ascii_count: int = 0
    spaced_count: int = 0
    changed: bool = False


def normalize_ellipsis_text(text: str, target_style: str = "unicode") -> EllipsisNormalizeResult:
    """
    Pure, unit-testable normalization of one piece of text (an
    element's .text or .tail). Replaces every ASCII ("...") and
    spaced (". . .") ellipsis with `target_style`'s form. A real
    Unicode ellipsis character already in the text is left alone
    either way.
    """
    if target_style not in VALID_TARGET_STYLES:
        raise ValueError(f"Unknown ellipsis target style: {target_style!r}")
    result = EllipsisNormalizeResult(text=text)
    if not text:
        return result

    replacement = UNICODE_ELLIPSIS if target_style == "unicode" else ASCII_ELLIPSIS

    def _repl(m: re.Match) -> str:
        if m.group("spaced") is not None:
            result.spaced_count += 1
        else:
            result.ascii_count += 1
        return replacement

    new_text = _COMBINED_RE.sub(_repl, text)
    result.text = new_text
    result.changed = new_text != text
    return result


# ---------------------------------------------------------------------
# Report data model
# ---------------------------------------------------------------------

@dataclass
class EllipsisIssue:
    href: str = ""
    element: object = None    # live host element; not saved to the JSON cache
    attr: str = ""             # "text" or "tail"
    category: str = ""         # "ASCII ellipsis (...)" or "Spaced-dot ellipsis"
    before: str = ""
    after: str = ""


@dataclass
class ChapterEllipsisSummary:
    href: str = ""
    ascii_count: int = 0
    spaced_count: int = 0
    issues: list = field(default_factory=list)   # [EllipsisIssue], live refs -- see serialize.py


@dataclass
class BookEllipsisSummary:
    chapters: list = field(default_factory=list)   # [ChapterEllipsisSummary]

    def _total(self, field_name: str) -> int:
        return sum(getattr(c, field_name) for c in self.chapters)

    @property
    def total_ascii_count(self) -> int:
        return self._total("ascii_count")

    @property
    def total_spaced_count(self) -> int:
        return self._total("spaced_count")

    @property
    def total_issue_count(self) -> int:
        return sum(len(c.issues) for c in self.chapters)

    @property
    def chapters_with_issues(self) -> list:
        return [c.href for c in self.chapters if c.issues]


def _category(result: EllipsisNormalizeResult) -> str:
    # A single node can contain both shapes; the detail list shows
    # whichever this node has more of -- same "pick one representative
    # label" approach ebook_fix.whitespace uses for its own detail view.
    if result.spaced_count >= result.ascii_count:
        return "Spaced-dot ellipsis"
    return "ASCII ellipsis (...)"


def analyze_chapter_ellipsis(href: str, tree) -> ChapterEllipsisSummary:
    summary = ChapterEllipsisSummary(href=href)
    if tree is None:
        return summary

    for host, attr, text, protected in iter_text_slots(tree):
        if protected or "." not in text:
            continue

        # Analysis always normalizes toward the Unicode ellipsis to
        # find everything there is to find and capture a stable
        # `before`; the repair module recomputes the actual `after`
        # from `before` using whatever target style the config has
        # active -- same pattern as ebook_fix.whitespace.
        result = normalize_ellipsis_text(text, target_style="unicode")
        if not result.changed:
            continue

        summary.ascii_count += result.ascii_count
        summary.spaced_count += result.spaced_count
        summary.issues.append(
            EllipsisIssue(
                href=href, element=host, attr=attr,
                category=_category(result),
                before=text, after=result.text,
            )
        )

    return summary


def analyze_book_ellipsis(book) -> BookEllipsisSummary:
    summary = BookEllipsisSummary()
    for chapter in book.chapters:
        summary.chapters.append(
            analyze_chapter_ellipsis(getattr(chapter, "href", ""), getattr(chapter, "document", None))
        )
    return summary
