"""
ebook_fix.text_lines

One record per page of the page's text as the text repairs read it, kept by the
analysis store so those repairs open only the pages that can have something to
do.

Mid-Word Hyphen Repair, Quote Style, Div Paragraphs, Extra Page Break Removal
and Chapter Title Cleanup each used to walk every element of every page, often
several times in one run, to find facts about the page's text: which words and
hyphenated pairs it holds, how many straight and curly quotes, which divs are
written as paragraphs, whether any element forces a page break, and what its
leading headings say. Those are plain facts about one page's markup, so they
are worked out once per page and kept while the page is unchanged (see
ebook_fix.finding_cache for exactly when a kept record is trusted).

The record holds no elements. Where the repairs need the book as a whole (the
words of the whole book, the quote totals, the div paragraph plan) they add up
the records, which gives exactly what walking every page gave. A repair that
finds from the records that a page may need work opens that page and does what
it always did there.

The small helpers the five repairs share (what counts as story text, a
paragraph div, a leading heading) live here so the records and the repairs
cannot drift apart.

A page that cannot be loaded (document None) has an empty record with
`loaded=False`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from ebook_fix.page_breaks import forced_break_props
from ebook_fix.split_fragments import _find_body, _local

# ---------------------------------------------------------------------
# Mid-Word Hyphen Repair
# ---------------------------------------------------------------------

# The hyphen-minus (escaped so it is a plain character inside a [...] class)
# and the Unicode hyphen, written as escapes so the file stays plain ASCII.
HYPHENS = "\\-\u2010"

# A whole hyphen-joined chain of letters: "word", "well-known",
# "mother-in-law". Anything glued to other word characters, an apostrophe
# (so the pieces of "I've" and "dog's" are not mistaken for words) or a
# double hyphen does not match, so "word--word" is ignored.
CHAIN = re.compile(
    rf"(?<![\w{HYPHENS}'\u2019])[^\W\d_]+(?:[{HYPHENS}][^\W\d_]+)*(?![\w{HYPHENS}'\u2019])"
)
SPLIT_ON_HYPHEN = re.compile(f"[{HYPHENS}]")

# Text inside any of these (or inside a heading) is never changed.
SKIPPED_ANCESTORS = frozenset({
    "head", "title", "style", "script", "pre", "code", "svg", "math",
    "h1", "h2", "h3", "h4", "h5", "h6",
})


def hyphen_allowed(element, memo=None) -> bool:
    """True when `element` and everything above it is outside the tags whose
    text is never changed. `memo` (a dict) remembers the answer per element."""
    if memo is None:
        memo = {}
    chain = []
    result = True
    node = element
    while node is not None:
        known = memo.get(node)
        if known is not None:
            result = known
            break
        if isinstance(node.tag, str) and node.tag.rsplit("}", 1)[-1].lower() in SKIPPED_ANCESTORS:
            memo[node] = False
            result = False
            break
        chain.append(node)
        node = node.getparent()
    for visited in chain:
        memo[visited] = result
    return result


def hyphen_slots(document):
    """(element, "text" or "tail", string) for every piece of story text on a
    page, in reading order."""
    memo = {}
    for element in document.iter():
        if not isinstance(element.tag, str):
            # A comment or processing instruction: its tail is
            # still story text, its own text is not.
            if element.tail and hyphen_allowed(element.getparent(), memo):
                yield element, "tail", element.tail
            continue
        if element.text and hyphen_allowed(element, memo):
            yield element, "text", element.text
        if element.tail and hyphen_allowed(element.getparent(), memo):
            yield element, "tail", element.tail


# ---------------------------------------------------------------------
# Quote Style
# ---------------------------------------------------------------------

OPEN_D, CLOSE_D = "\u201c", "\u201d"
OPEN_S, CLOSE_S = "\u2018", "\u2019"
QUOTE_SKIP = frozenset({"pre", "code", "script", "style", "svg", "math"})


def _lower_local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def quote_slots(el):
    """Yield (element, 'text'|'tail') in document order for the text inside
    el, skipping do-not-touch subtrees (their tail still counts)."""
    if _lower_local(el) in QUOTE_SKIP:
        return
    yield el, "text"
    for child in el:
        yield from quote_slots(child)
        yield child, "tail"


def slot_text(el, which):
    return (el.text if which == "text" else el.tail) or ""


# ---------------------------------------------------------------------
# Div Paragraphs
# ---------------------------------------------------------------------

DIV_BLOCKS = frozenset({
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "blockquote", "pre", "section",
    "article", "aside", "header", "footer", "nav", "figure", "figcaption", "hr", "form",
    "fieldset", "address", "main", "caption",
})


def text_len(el) -> int:
    return len(re.sub(r"\s+", " ", "".join(el.itertext())).strip())


def is_leaf_text_div(el) -> bool:
    if not isinstance(el.tag, str) or _lower_local(el) != "div":
        return False
    if any(isinstance(d.tag, str) and _lower_local(d) in DIV_BLOCKS for d in el.iterdescendants()):
        return False
    return text_len(el) > 0


def div_protected(el) -> bool:
    if any(k.startswith("data-ebookfix") for k in el.attrib):
        return True
    return "ebookfix" in (el.get("class") or "").lower()


# ---------------------------------------------------------------------
# Chapter Title Cleanup
# ---------------------------------------------------------------------

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
HEADING_CLASS_HINTS = ("title", "chapter", "head")
LEADING_BLOCKS_SCANNED = 4
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")

# An optional opening quote or bracket, a capital, then at least one more
# letter -- so "3-D glasses" (a lone capital) is never read as a title.
TITLE_START = "[\"'\u201c\u2018(]?[A-Z][A-Za-z]"
LEAD = r"(?:(?i:chapter|part|book|section)\s+)?"
# The start of one text node, leading whitespace allowed.
NODE_START_RE = re.compile(
    rf"^(?P<ws>\s*)(?P<lead>{LEAD})(?P<num>\d{{1,4}})(?P<sep>\s*-\s*)(?={TITLE_START})"
)


def normalize(text: str) -> str:
    return " ".join((text or "").split())


def class_hint(el) -> bool:
    node = el
    while node is not None and isinstance(node.tag, str):
        if _local(node) == "body":
            break
        classes = (node.get("class") or "").lower()
        if any(hint in classes for hint in HEADING_CLASS_HINTS):
            return True
        node = node.getparent()
    return False


def leading_headings(document):
    """Heading-like blocks within the first few text-bearing blocks of a
    file: (element, normalized text) pairs."""
    body = _find_body(document)
    if body is None:
        return []
    found = []
    seen_blocks = 0
    for el in body.iter():
        if not isinstance(el.tag, str):
            continue
        name = _local(el)
        if name not in HEADING_TAGS and name != "p":
            continue
        text = normalize("".join(el.itertext()))
        if not text:
            continue
        seen_blocks += 1
        if name in HEADING_TAGS or class_hint(el):
            found.append((el, text))
        if seen_blocks >= LEADING_BLOCKS_SCANNED:
            break
    return found


def text_nodes(el):
    """(owner, 'text' or 'tail') slots inside `el` in true document
    order, not including el's own tail."""
    yield el, "text"
    for child in el:
        if isinstance(child.tag, str):
            yield from text_nodes(child)
        yield child, "tail"


def first_text_slot(el):
    for owner, attr in text_nodes(el):
        value = getattr(owner, attr)
        if value and value.strip():
            return owner, attr, value
    return None


# ---------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class PageTextLines:
    href: str = ""
    loaded: bool = True
    # Mid-Word Hyphen Repair: how many times each lowercase word stands alone
    # in the page's story text, and how many times each lowercase
    # (first, second) pair joined by one hyphen does.
    words: dict = field(default_factory=dict)
    pairs: dict = field(default_factory=dict)
    # Quote Style: the double and single quote marks in the page's text, curly
    # and straight, and whether any piece of text holds an opening single quote.
    curly_double: int = 0
    straight_double: int = 0
    curly_single: int = 0
    straight_single: int = 0
    open_single: bool = False
    # Div Paragraphs: the number of <p> elements with text, and the (class
    # attribute, length of text) of every <div> that holds only text and
    # inline tags and is not one of this tool's own.
    paragraphs_with_text: int = 0
    text_divs: tuple = ()
    # Extra Page Break Removal: an element in the body has a forced page break
    # in its own style.
    forced_break: bool = False
    # Chapter Title Cleanup: for each leading heading, its text and the
    # separator after a leading number in its first piece of text (None when
    # that text does not open with a numbered title); and the files the
    # page's links go to as a whole (no spot named).
    headings: tuple = ()
    whole_file_links: frozenset = frozenset()


@dataclass
class BookTextLines:
    chapters: list = field(default_factory=list)


def _root(document):
    return document.getroot() if hasattr(document, "getroot") else document


def analyze_text_lines_page(href, document) -> PageTextLines:
    if document is None:
        return PageTextLines(href=href, loaded=False)
    root = _root(document)

    # Mid-Word Hyphen Repair.
    words, pairs = {}, {}
    for _element, _kind, text in hyphen_slots(document):
        if "-" not in text and "\u2010" not in text:
            # No hyphen anywhere, so every chain is a single word.
            for word in CHAIN.findall(text):
                key = word.lower()
                words[key] = words.get(key, 0) + 1
            continue
        for match in CHAIN.finditer(text):
            parts = SPLIT_ON_HYPHEN.split(match.group(0))
            if len(parts) == 1:
                key = parts[0].lower()
                words[key] = words.get(key, 0) + 1
            elif len(parts) == 2:
                key = (parts[0].lower(), parts[1].lower())
                pairs[key] = pairs.get(key, 0) + 1

    # Quote Style: the same pieces of text quote_slots(root) gives, counted
    # in one walk. Text inside a skipped tag does not count, but the tail of
    # the skipped tag itself does.
    curly_d = straight_d = curly_s = straight_s = 0
    open_single = False
    skipping = 0
    counted = []
    for event, el in etree.iterwalk(root, events=("start", "end", "comment", "pi")):
        if event == "start":
            if _lower_local(el) in QUOTE_SKIP:
                skipping += 1
            elif not skipping and el.text:
                counted.append(el.text)
        elif event == "end":
            if _lower_local(el) in QUOTE_SKIP:
                skipping -= 1
            if not skipping and el is not root and el.tail:
                counted.append(el.tail)
        elif not skipping:
            if el.text:
                counted.append(el.text)
            if el.tail:
                counted.append(el.tail)
    for s in counted:
        curly_d += s.count(OPEN_D) + s.count(CLOSE_D)
        straight_d += s.count('"')
        curly_s += s.count(OPEN_S) + s.count(CLOSE_S)
        straight_s += s.count("'")
        if OPEN_S in s:
            open_single = True

    # Div Paragraphs, links to a whole file, and forced page breaks.
    paragraphs = 0
    divs = []
    whole_links = set()
    body = None
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        name = tag.rsplit("}", 1)[-1].lower()
        if name == "p":
            # Any text that is not only white space (what text_len > 0 means).
            if any(piece.strip() for piece in el.itertext()):
                paragraphs += 1
        elif name == "div":
            if is_leaf_text_div(el) and not div_protected(el):
                divs.append(((el.get("class") or "").strip(), text_len(el)))
        elif body is None and name == "body":
            body = el
        if name == "a":
            link = el.get("href") or ""
            if not link.startswith(EXTERNAL_PREFIXES):
                path, _, fragment = link.partition("#")
                if not fragment:
                    whole_links.add(path)
    forced = False
    if body is not None:
        for el in body.iter():
            if isinstance(el.tag, str) and el.get("style") and forced_break_props(el):
                forced = True
                break

    # Chapter Title Cleanup.
    headings = []
    for el, text in leading_headings(document):
        slot = first_text_slot(el)
        sep = None
        if slot is not None:
            m = NODE_START_RE.match(slot[2])
            if m is not None:
                sep = m.group("sep")
        headings.append((text, sep))

    return PageTextLines(
        href=href,
        loaded=True,
        words=words,
        pairs=pairs,
        curly_double=curly_d,
        straight_double=straight_d,
        curly_single=curly_s,
        straight_single=straight_s,
        open_single=open_single,
        paragraphs_with_text=paragraphs,
        text_divs=tuple(divs),
        forced_break=forced,
        headings=tuple(headings),
        whole_file_links=frozenset(whole_links),
    )


def analyze_book_text_lines(book, cache=None) -> BookTextLines:
    from ebook_fix.finding_cache import analyze_pages

    return analyze_pages(book, "text_lines", analyze_text_lines_page, BookTextLines(), cache=cache)


def text_lines_for(book, analysis=None) -> list:
    """The page records for `book` as it is now, in the order of
    `book.chapters`. They come from the analysis store the report carries when
    there is one, so a page that has not changed is not read again; without a
    store every page is read."""
    store = getattr(analysis, "store", None)
    if store is not None:
        return store.findings("text_lines", book).chapters
    return analyze_book_text_lines(book).chapters
