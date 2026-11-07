"""
ebook_fix.class_usage

One record per page of how the page uses classes, spans and style blocks, kept
by the analysis store so the repairs that need it read it instead of walking
every page again.

Class Cleanup, Span Cleanup and Unused CSS each used to scan every element of
every page for the same few facts: which class names are used, which of them
sit on spans, which spans carry nothing but a class, and whether the page has a
script or a style block. Those are all plain facts about one page's markup, so
they are worked out once per page and kept while the page is unchanged (see
ebook_fix.finding_cache for exactly when a kept record is trusted).

A page that cannot be loaded (document None) has an empty record with
`loaded=False`, so Unused CSS can still read its raw file as it always did.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Tag names the repairs that read these records treat specially. They live here
# so the records and the repairs cannot drift apart.
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
# Line Height: a class used only on these inline elements does not protect its
# line height.
INLINE_TAGS = frozenset({"a", "span", "em", "i", "b", "strong", "u", "small", "font", "sup", "sub"})
# Text Size: text inside these tags has a size of its own and is not counted.
TEXT_SIZE_SKIP_TAGS = frozenset({
    "sup", "sub", "small", "big", "h1", "h2", "h3", "h4", "h5", "h6",
    "head", "title", "script", "style", "pre", "code", "svg", "math",
})


@dataclass(frozen=True)
class PageClassUsage:
    href: str = ""
    loaded: bool = True
    # Every class name on any element of the page.
    classes: frozenset = frozenset()
    # Class names on <span> elements only.
    span_classes: frozenset = frozenset()
    # <span> elements with no attribute except (perhaps) class: the only spans
    # Span Cleanup can ever remove or unwrap.
    plain_spans: int = 0
    # An element with a class attribute that holds no names (class=""), which
    # Class Cleanup removes too.
    empty_class_attribute: bool = False
    has_script: bool = False
    # The text of each <style> block, in page order.
    style_texts: tuple = ()
    # Any element named style (ignoring case), the superset that Unused CSS looks at.
    has_style: bool = False
    # Line Height: classes on any element that is not an inline one, classes on
    # a heading or anything inside one, and whether any element has a
    # line-height in its own style attribute.
    block_classes: frozenset = frozenset()
    heading_classes: frozenset = frozenset()
    inline_line_height: bool = False
    # Text Size: an element with a font-size in its own style attribute that
    # also holds text of its own; the number of elements with text of their own
    # and the characters in them; and the pieces of visible text that count
    # (not under a tag with a size of its own or an inline font-size) and their
    # characters.
    inline_font_size_text: bool = False
    text_elements: int = 0
    text_chars: int = 0
    unit_count: int = 0
    unit_chars: int = 0
    # Body Paragraph Look: the class of every <p> that carries exactly one class
    # name and has text of its own.
    single_class_paragraphs: frozenset = frozenset()


@dataclass
class BookClassUsage:
    chapters: list = field(default_factory=list)


def _root(document):
    return document.getroot() if hasattr(document, "getroot") else document


def analyze_class_usage_page(href, document) -> PageClassUsage:
    if document is None:
        return PageClassUsage(href=href, loaded=False)
    classes, span_classes, style_texts = set(), set(), []
    block_classes, heading_classes, single_paragraphs = set(), set(), set()
    plain_spans = text_elements = text_chars = unit_count = unit_chars = 0
    empty_class = has_script = has_style = False
    inline_line_height = inline_font_size_text = False
    skipped = {}  # element -> True when text under it is not counted by Text Size
    for el in _root(document).iter():
        if not isinstance(el.tag, str):
            continue
        local = el.tag.rsplit("}", 1)[-1]
        lowered = local.lower()
        raw = el.get("class")
        names = raw.split() if raw is not None else None
        style = (el.get("style") or "").lower()
        if names is not None:
            if names:
                classes.update(names)
            else:
                empty_class = True
        if names and lowered not in INLINE_TAGS:
            block_classes.update(names)
        if local in HEADING_TAGS:
            for inner in el.iter():
                if isinstance(inner.tag, str):
                    heading_classes.update((inner.get("class") or "").split())
        if local == "span":
            if names:
                span_classes.update(names)
            if not (set(el.attrib) - {"class"}):
                plain_spans += 1
        if lowered == "script":
            has_script = True
        if lowered == "style":
            has_style = True
        if local == "style" and el.text:
            style_texts.append(el.text)
        if "line-height" in style:
            inline_line_height = True
        own = (el.text or "").strip()
        if "font-size" in style and own:
            inline_font_size_text = True
        if own:
            text_elements += 1
            text_chars += len(own)
        if local == "p" and names is not None and len(names) == 1 and "".join(el.itertext()).replace("\u00a0", " ").strip():
            single_paragraphs.add(names[0])
        # Pieces of visible text as Text Size counts them: the element's own
        # text, and the tail that follows it (which belongs to its parent).
        for owner, text in ((el, el.text), (el.getparent(), el.tail)):
            if owner is None or not (text or "").strip():
                continue
            if _text_size_skips(owner, skipped):
                continue
            unit_count += 1
            unit_chars += len(text.strip())
    return PageClassUsage(
        href=href,
        loaded=True,
        classes=frozenset(classes),
        span_classes=frozenset(span_classes),
        plain_spans=plain_spans,
        empty_class_attribute=empty_class,
        has_script=has_script,
        style_texts=tuple(style_texts),
        has_style=has_style,
        block_classes=frozenset(block_classes),
        heading_classes=frozenset(heading_classes),
        inline_line_height=inline_line_height,
        inline_font_size_text=inline_font_size_text,
        text_elements=text_elements,
        text_chars=text_chars,
        unit_count=unit_count,
        unit_chars=unit_chars,
        single_class_paragraphs=frozenset(single_paragraphs),
    )


def _text_size_skips(node, memo) -> bool:
    """True when `node` or anything above it is a tag with a size of its own or
    has an inline font-size (the test Text Size makes for each piece of text)."""
    chain = []
    result = False
    while node is not None:
        known = memo.get(node)
        if known is not None:
            result = known
            break
        tag = node.tag.rsplit("}", 1)[-1].lower() if isinstance(node.tag, str) else ""
        if tag in TEXT_SIZE_SKIP_TAGS or "font-size" in (node.get("style") or "").lower():
            memo[node] = True
            result = True
            break
        chain.append(node)
        node = node.getparent()
    for visited in chain:
        memo[visited] = result
    return result


def analyze_book_class_usage(book, cache=None) -> BookClassUsage:
    from ebook_fix.finding_cache import analyze_pages

    return analyze_pages(book, "class_usage", analyze_class_usage_page, BookClassUsage(), cache=cache)


def class_usage_for(book, analysis=None) -> list:
    """The page records for `book` as it is now, in the order of
    `book.chapters`. They come from the analysis store the report carries when
    there is one, so a page that has not changed is not read again; without a
    store every page is read."""
    store = getattr(analysis, "store", None)
    if store is not None:
        return store.findings("class_usage", book).chapters
    return analyze_book_class_usage(book).chapters
