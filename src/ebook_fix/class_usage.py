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


@dataclass
class BookClassUsage:
    chapters: list = field(default_factory=list)


def _root(document):
    return document.getroot() if hasattr(document, "getroot") else document


def analyze_class_usage_page(href, document) -> PageClassUsage:
    if document is None:
        return PageClassUsage(href=href, loaded=False)
    classes, span_classes, style_texts = set(), set(), []
    plain_spans = 0
    empty_class = has_script = has_style = False
    for el in _root(document).iter():
        if not isinstance(el.tag, str):
            continue
        local = el.tag.rsplit("}", 1)[-1]
        lowered = local.lower()
        raw = el.get("class")
        names = raw.split() if raw is not None else None
        if names is not None:
            if names:
                classes.update(names)
            else:
                empty_class = True
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
    )


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
