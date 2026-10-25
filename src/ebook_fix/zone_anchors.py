"""Zone anchors for books whose chapters are not a confirmed sequence.

frontmatter.py normally anchors the front, main and back zones on the
first and last confirmed chapter. Two kinds of book leave it without an
anchor, and this module gives each an honest answer (Phase 4 of
docs/detection_confidence_plan.md):

1. Chapters that share a file. When every confirmed chapter marker
   lands in one big file (Watermarks-SmallChapterNumbers: 24 chapters in
   one 63,000 word file), the old guard treated the sequence as a contents
   page and gave up. A contents page is short, though, and a file that
   holds real chapters is long. `markers_are_prose` tells the two apart by
   the number of words per marker.

2. Books with no chapter numbering at all, but with Project Gutenberg's
   own start and end markers (GutenbergText-HRule, The Call of Cthulhu).
   The markers are written by the Project Gutenberg itself and are the
   most reliable statement of "the book starts here, and ends here" that
   such a file has. Zones stay file-granular: the file that holds the
   start marker counts as main content when the story begins inside it,
   and likewise for the end marker. Otherwise the zone starts at the next
   (or ends at the previous) file.

Nothing here runs when a normal confirmed chapter sequence exists, so
every book that already has confirmed zones is untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A marker file must have at least this many words of story on the story
# side of the marker, or the story is taken to begin (or end) in the next
# (or previous) file. Tom Sawyer's END marker has 315 words after it, and
# Cthulhu's START marker has 153 words before it, both on the boilerplate
# side, so a few hundred words is a safe line.
MIN_STORY_WORDS = 300

# Words per chapter marker above which a run of markers sharing one file
# is real prose and not a contents page. A contents page lists a number
# or title per line, a dozen words per marker at most; the smallest real
# case seen is 2,600. 150 leaves a wide margin on both sides.
MIN_WORDS_PER_MARKER = 150

_START_PATTERNS = (
    # Modern: *** START OF THE PROJECT GUTENBERG EBOOK TITLE ***
    re.compile(r"\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG[^*]*\*{3}", re.I),
    # Old etexts: *END*THE SMALL PRINT! FOR PUBLIC DOMAIN ETEXTS*Ver.xx*END*
    re.compile(r"\*END\*\s*THE SMALL PRINT.*?\*END\*", re.I),
    re.compile(r"\*END THE SMALL PRINT.*?\*END\*", re.I),
)
_END_PATTERNS = (
    re.compile(r"\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG[^*]*\*{3}", re.I),
    re.compile(r"\bEnd of (?:the|this) Project Gutenberg (?:Etext|E-?text|eBook)\b", re.I),
)


@dataclass
class ZoneAnchor:
    first_idx: int
    last_idx: int
    # Plain-English description of what the zone edges were anchored on,
    # reused in the reason shown for each page.
    source: str
    # True when both edges came from evidence, False when one edge is
    # just the start or end of the book.
    both_edges: bool


def markers_are_prose(word_count: int, marker_count: int) -> bool:
    """True when `marker_count` chapter markers inside one file of
    `word_count` words are real chapters, not a contents list."""
    if marker_count < 2:
        return False
    return word_count / marker_count >= MIN_WORDS_PER_MARKER


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _first_match(patterns, text):
    best = None
    for pat in patterns:
        m = pat.search(text)
        if m is not None and (best is None or m.start() < best.start()):
            best = m
    return best


def _last_match(patterns, text):
    best = None
    for pat in patterns:
        for m in pat.finditer(text):
            if best is None or m.start() > best.start():
                best = m
    return best


def find_gutenberg_anchor(page_texts: list[str]) -> ZoneAnchor | None:
    """`page_texts` is the visible text of every spine entry, in reading
    order. Returns the zone edges from the Project Gutenberg start and
    end markers, or None when the book has no start marker (an end marker
    alone is not enough to say where the story begins)."""
    texts = [_normalise(t) for t in page_texts]

    start_idx = None
    start_after = 0
    for i, text in enumerate(texts):
        m = _first_match(_START_PATTERNS, text)
        if m is not None:
            start_idx = i
            start_after = len(text[m.end():].split())
            break
    if start_idx is None:
        return None

    first_idx = start_idx if start_after >= MIN_STORY_WORDS else start_idx + 1

    end_idx = None
    end_before = 0
    for i in range(len(texts) - 1, start_idx - 1, -1):
        m = _last_match(_END_PATTERNS, texts[i])
        if m is not None:
            end_idx = i
            end_before = len(texts[i][:m.start()].split())
            break

    if end_idx is None:
        last_idx = len(texts) - 1
        both = False
    else:
        last_idx = end_idx if end_before >= MIN_STORY_WORDS else end_idx - 1
        both = True

    if first_idx >= len(texts) or last_idx < first_idx:
        return None
    return ZoneAnchor(
        first_idx=first_idx,
        last_idx=last_idx,
        source="the Project Gutenberg start and end markers" if both
        else "the Project Gutenberg start marker",
        both_edges=both,
    )
