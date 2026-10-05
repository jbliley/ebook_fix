"""
ebook_fix.scene_openers

Finds scene changes that have no visible marker in the book, only a
telltale way the new scene starts: the first few words in capitals,

    THERE'S AN UNLIT parking lot behind an out-of-business movie multiplex.

Found in "Sandman Slim" (Richard Kadrey), whose 56 scene changes are each
a paragraph styled by the publisher as a "separator" (extra space above
it, no indent), with its opening words in capitals. The book has no
"* * *", no rule and no chapter headings, so Scene Break Normalizer and
the chapter detector both found nothing, and a reader whose stylesheet is
stripped (or a reader that ignores margins) runs every scene into the
last.

This module only finds them. ebook_fix.modules.scene_opener_repair puts a
centered "* * *" in front of each one.

Why a scene break and not a chapter: a chapter needs a label or a number
to be recognized as one (Chapter 7, VII, a title), and these have
neither. The lengths in Sandman Slim also say scene: they run from 78
words to over 6,000, with 15 of the 56 under 500 words, and the book's
own stylesheet calls the paragraph class "separator".

What counts as an opener. A paragraph that begins with at least two
capitalized words (and at least five letters in all) followed by
ordinary lower-case text:

    I WAIT FOR an hour upstairs, until the store fills ...
    IT'S WEIRD starting over from zero. ...

A paragraph that is entirely capitals is not one (a shout or a title),
and neither is one that starts with a single capitalized word.

A paragraph too short for that rule (a whole-capitals line such as "I WAS
DEAD.", or "A FEW DAYS later.") still counts, but only if it has exactly the
same distinctive look as the openers that do pass it.

Only a mid-chapter opener is marked. Not marked: one that is the first
thing in its file, or right under a chapter header, a rule, an existing
scene-break marker or a blank spacer line (those already show a change),
or one in the front or back matter.

How the book is judged as a whole, so ordinary prose is never touched:

- At least MIN_OPENERS openers in the main text. A book that has one or
  two paragraphs starting with capitals ("FBI AGENTS stormed in") is not
  using them to mark scenes.
- No more than MAX_OPENER_RATIO of the book's paragraphs. A style that
  capitalizes the start of many paragraphs is a typographic habit, not a
  scene marker.
- The openers have to look alike: the same paragraph class and the same
  kind of wrapper around the capital words. Only the dominant look is
  marked, and it has to cover at least MIN_DOMINANT_SHARE of the openers.
- That look has to be distinctive. Capitals at the start of a paragraph
  that is styled exactly like every other paragraph are indistinguishable
  from a letter's dateline ("SAN FRANCISCO, 18--. DEAR CHING-FOO:"), a
  copyright line ("BERKLEY MEDALLION BOOKS are published by") or the
  capitalized first words of a chapter, so they are never marked. The
  openers' paragraph class must be rare in the book, or the wrapper
  around their capital words must be.

Each finding carries a live reference to the paragraph so repair can act
on it directly; references are not saved to the JSON cache.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ebook_fix.headings import HEADING_TAGS, is_chapter_header

MIN_OPENERS = 5
MAX_OPENER_RATIO = 0.10
MIN_DOMINANT_SHARE = 0.75
# The openers must be styled differently from ordinary text: their
# paragraph class is used by little else in the book (at most this many
# times the number of openers), or the element wrapped around their
# capital words is (a small-caps span, say).
MAX_CLASS_SPREAD = 2
MAX_WRAPPER_SPREAD = 3
WRAPPER_TAGS = frozenset(("span", "b", "strong", "i", "em", "small", "font"))

MIN_LEAD_WORDS = 2
MIN_LEAD_LETTERS = 5
MAX_LEAD_WORDS = 12
MIN_REST_WORDS = 3
MIN_REST_LOWER_SHARE = 0.6
MIN_STORY_WORDS = 3

_EDGE_PUNCT = "\"'()[]{}\u201c\u201d\u2018\u2019\u2014\u2013-.,;:!?\u2026"
_STORY_PARENTS = frozenset(("body", "div", "section", "article", "main"))


@dataclass
class SceneOpener:
    href: str = ""
    lead: str = ""             # the capitalized opening words
    preview: str = ""
    signature: tuple = ()
    element: object = None     # live <p>; not saved to the JSON cache


@dataclass
class ChapterSceneOpenerSummary:
    href: str = ""
    openers: list = field(default_factory=list)


@dataclass
class BookSceneOpenerSummary:
    chapters: list = field(default_factory=list)
    candidate_count: int = 0       # looked like openers, before any gating
    paragraph_count: int = 0
    dominant_signature: tuple = ()
    qualifies: bool = False        # the book as a whole passed its checks
    reason_skipped: str = ""

    @property
    def opener_count(self) -> int:
        return sum(len(c.openers) for c in self.chapters)


def _local(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _text(el) -> str:
    return " ".join("".join(el.itertext()).replace("\u00a0", " ").split())


def _is_caps_word(token: str) -> bool:
    letters = [c for c in token.strip(_EDGE_PUNCT) if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def caps_lead(text: str):
    """(lead_text, rest_text) if `text` opens with a run of capitalized
    words followed by ordinary text, else None."""
    tokens = text.split()
    lead = []
    for token in tokens[:MAX_LEAD_WORDS + 1]:
        if _is_caps_word(token):
            lead.append(token)
        else:
            break
    if len(lead) < MIN_LEAD_WORDS or len(lead) > MAX_LEAD_WORDS:
        return None
    letters = sum(1 for tok in lead for c in tok if c.isalpha())
    if letters < MIN_LEAD_LETTERS:
        return None
    rest = tokens[len(lead):]
    if len(rest) < MIN_REST_WORDS:
        return None
    rest_letters = [c for tok in rest for c in tok if c.isalpha()]
    if not rest_letters:
        return None
    lower = sum(1 for c in rest_letters if c.islower())
    if lower / len(rest_letters) < MIN_REST_LOWER_SHARE:
        return None
    return " ".join(lead), " ".join(rest)


def _signature(p) -> tuple:
    """What the opener looks like in the markup: its own class, and the
    kind of element (and class) wrapped around the capital words, if
    any."""
    wrapper = ""
    if len(p) and not (p.text or "").strip():
        first = p[0]
        if isinstance(first.tag, str) and _is_caps_word(_text(first).split(" ")[0] if _text(first) else ""):
            wrapper = _local(first) + "." + (first.get("class") or "")
    return (p.get("class") or "", wrapper)


def _is_blank(el) -> bool:
    return not _text(el) and not el.findall(".//{*}img")


def analyze_book_scene_openers(book, frontmatter_summary=None, chapter_markers=None) -> BookSceneOpenerSummary:
    summary = BookSceneOpenerSummary()

    if frontmatter_summary is None:
        from ebook_fix.frontmatter import analyze_book_frontmatter
        frontmatter_summary = analyze_book_frontmatter(book)

    main_hrefs = None
    if frontmatter_summary.boundaries_confirmed:
        from ebook_fix.frontmatter import MAIN_ZONE
        main_hrefs = {cm.href for cm in frontmatter_summary.chapters if cm.zone == MAIN_ZONE}

    from ebook_fix.linebreaks import is_scene_break_marker

    class_counts: dict = {}     # paragraph class -> paragraphs using it
    wrapper_counts: dict = {}   # "span.smallCaps" -> elements using it
    per_chapter = []   # (href, [(opener, convertible_position_ok)])
    for chapter in book.chapters:
        if chapter.document is None:
            continue
        if main_hrefs is not None and chapter.href not in main_hrefs:
            continue
        body = chapter.document.find(".//{*}body")
        if body is None:
            continue

        found = []
        prev_block = None      # the previous heading / rule / paragraph in reading order
        seen_story = False
        for el in body.iter():
            name = _local(el)
            if name in HEADING_TAGS:
                if _text(el):
                    prev_block = el
                continue
            if name == "hr":
                prev_block = el
                continue
            if name in WRAPPER_TAGS:
                wrapper_class = el.get("class")
                if wrapper_class:
                    key = f"{name}.{wrapper_class}"
                    wrapper_counts[key] = wrapper_counts.get(key, 0) + 1
                continue
            if name != "p":
                continue

            text = _text(el)
            if text:
                summary.paragraph_count += 1
                paragraph_class = el.get("class") or ""
                class_counts[paragraph_class] = class_counts.get(paragraph_class, 0) + 1

            parent = el.getparent()
            parent_ok = parent is not None and _local(parent) in _STORY_PARENTS
            lead = caps_lead(text) if (text and parent_ok) else None
            signature = _signature(el) if (text and parent_ok) else ()

            # A "weak" candidate is too short for the capitals rule (a
            # whole-capitals line like "I WAS DEAD.", or "A FEW DAYS
            # later.") but starts with a capital word inside a wrapper
            # element. It only counts if it turns out to share the exact
            # distinctive look of the strong openers (see below).
            weak = (
                lead is None
                and bool(signature)
                and bool(signature[1])
                and _is_caps_word(text.split()[0])
            )

            if lead is not None or weak:
                marks = (
                    seen_story
                    and prev_block is not None
                    and not is_chapter_header(prev_block, chapter_markers)
                    and _local(prev_block) != "hr"
                    and not is_scene_break_marker(prev_block)
                    and not _is_blank(prev_block)
                )
                found.append((
                    SceneOpener(
                        href=chapter.href,
                        lead=lead[0] if lead is not None else " ".join(text.split()[:4]),
                        preview=text[:60],
                        signature=signature,
                        element=el,
                    ),
                    marks,
                    lead is not None,
                ))

            prev_block = el
            if (
                not is_chapter_header(el, chapter_markers)
                and text
                and len(text.split()) >= MIN_STORY_WORDS
                and not is_scene_break_marker(el)
            ):
                seen_story = True
        per_chapter.append((chapter.href, found))

    all_found = [f for _href, items in per_chapter for f in items]
    strong = [f for f in all_found if f[2]]
    summary.candidate_count = len(strong)

    if summary.candidate_count < MIN_OPENERS:
        summary.reason_skipped = (
            f"only {summary.candidate_count} paragraph(s) open with capitals "
            f"(needs {MIN_OPENERS})"
        )
        return summary
    if summary.paragraph_count and summary.candidate_count / summary.paragraph_count > MAX_OPENER_RATIO:
        summary.reason_skipped = "capitalized openings are too common to be scene markers"
        return summary

    counts: dict = {}
    for opener, _marks, _strong in strong:
        counts[opener.signature] = counts.get(opener.signature, 0) + 1
    dominant, dominant_count = max(counts.items(), key=lambda kv: kv[1])
    summary.dominant_signature = dominant
    if dominant_count / summary.candidate_count < MIN_DOMINANT_SHARE:
        summary.reason_skipped = "the capitalized openings do not share one look"
        return summary

    paragraph_class, wrapper = dominant
    class_is_rare = bool(paragraph_class) and class_counts.get(paragraph_class, 0) <= MAX_CLASS_SPREAD * dominant_count
    wrapper_is_rare = bool(wrapper) and not wrapper.endswith(".") and wrapper_counts.get(wrapper, 0) <= MAX_WRAPPER_SPREAD * dominant_count
    if not (class_is_rare or wrapper_is_rare):
        summary.reason_skipped = (
            "the capitalized openings are styled like ordinary paragraphs, so they "
            "cannot be told apart from datelines, headings or chapter starts"
        )
        return summary

    summary.qualifies = True
    for href, items in per_chapter:
        chosen = [o for o, marks, _strong in items if marks and o.signature == dominant]
        if chosen:
            summary.chapters.append(ChapterSceneOpenerSummary(href=href, openers=chosen))
    return summary
