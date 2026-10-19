"""
ebook_fix.modules.extra_content_removal

Removes whole pages that are not part of the book itself: advertisements,
newsletter sign-ups, "Books by the author" lists, teaser chapters (a
preview of another book), About the Author and publisher pages. Which
kinds are removed is a set of switches in the config (see
`[extra_content_removal]`); by default only plain advertisements are on,
and the Project Gutenberg boilerplate (a separate module, same group of
switches) stays on as it always was. Everything else is off, because
Jacob would rather keep original content unless he says otherwise.

Where the labels come from
--------------------------
Nothing here looks at page text itself. ebook_fix.frontmatter already
labels every page (zone, label, confidence), and a person's correction on
the Review tab always wins there. This module only decides what to do
with those labels, the same "analysis describes, repair decides" split
the other modules use.

Confidence
----------
`automatic_confidence` says how sure a label must be before the page is
removed without anyone looking: "high" (the default) or "medium". A page
a person labeled on the Review tab counts as high.

Safety rules (not switches)
---------------------------
- Whole pages only. A page that mixes story text with an ad stays.
- A page the chapter detector counted as a chapter is never removed.
- Only pages before the first chapter or after the last chapter are
  considered, and only when the book's chapters were confirmed at all.
  A teaser chapter must come after the last chapter.
- Ads, sign-ups and lists must be short (see _MAX_WORDS).
- The book's contents page (nav document) is never removed, and the last
  remaining page of a book is never removed.
- Removing a page also removes its entries in the contents lists (NCX and
  nav), its guide reference, its manifest entry and any picture that only
  that page used. Links that pointed at the page are left to Dead Link
  Cleanup, which runs afterward.
- The module acts on the first pass only. Later passes re-analyze the book
  without a person's Review tab choices, so a page a person chose to keep
  could otherwise be judged again.

Every removal is reported with the page's first words and the reason, so
the Fix result page can list exactly what was taken out.
"""

from __future__ import annotations

import posixpath
import re

from ebook_fix.frontmatter import MatterLabel, _chapter_text, _spine_ordered_chapters
from ebook_fix.omnibus_split import (
    XLINK_HREF,
    _drop_chapters,
    _prune_nav,
    _prune_ncx,
    _prune_unused_media,
    _resolve,
)
from ebook_fix.report import Report
from ebook_fix.toc_refresh import refresh_book_toc

# Label -> (config switch, plain name used in reports).
_CATEGORIES = {
    MatterLabel.ADVERTISEMENT.value: ("remove_advertisements", "advertisement"),
    MatterLabel.NEWSLETTER.value: ("remove_newsletter_signups", "newsletter sign-up"),
    MatterLabel.TEASER_CHAPTER.value: ("remove_teaser_chapters", "teaser chapter"),
    MatterLabel.BOOKS_BY.value: ("remove_books_by_author", "books-by-the-author page"),
    MatterLabel.ABOUT_AUTHOR.value: ("remove_about_author", "about-the-author page"),
    MatterLabel.PUBLISHER.value: ("remove_publisher_pages", "publisher page"),
    MatterLabel.COLOPHON.value: ("remove_publisher_pages", "publisher page"),
}

# Longest page (in words) each kind may be and still be removed. A teaser
# chapter can be long, so it has no limit; it needs its explicit preview
# wording instead (the label itself requires that).
_MAX_WORDS = {
    MatterLabel.ADVERTISEMENT.value: 400,
    MatterLabel.NEWSLETTER.value: 400,
    MatterLabel.BOOKS_BY.value: 500,
    MatterLabel.ABOUT_AUTHOR.value: 700,
    MatterLabel.PUBLISHER.value: 500,
    MatterLabel.COLOPHON.value: 500,
}

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

# A file named like one of these is a part of the book whatever its text
# looks like (the older labels sometimes call a title page "publisher
# info"), so it is never removed.
_BOOK_PART_NAME = re.compile(
    r"title|toc|contents|nav|cover|copyright|dedic|epigraph|prolog|epilog",
    re.IGNORECASE,
)

# Publisher and About the Author labels are the loosest, so they need a
# little more than the label: an About the Author page must say so near
# its top, and a publisher page must be more than a title-page-sized stub.
_ABOUT_HEADING = re.compile(r"about the (?:author|authors|writer)", re.IGNORECASE)
_MIN_PUBLISHER_WORDS = 40


def _local(el) -> str:
    return el.tag.split("}")[-1].lower() if isinstance(el.tag, str) else ""


def _without_entries(entries, removed: set):
    """A contents list with every entry that points at a removed page
    taken out; an entry's kept children move up a level."""
    kept = []
    for entry in entries:
        children = _without_entries(entry.children, removed)
        target = entry.href.split("#", 1)[0]
        if target in removed:
            kept.extend(children)
        else:
            entry.children = children
            kept.append(entry)
    return kept


class ExtraContentRemoval:
    name = "Extra Content Removal"

    def __init__(self, config=None):
        self.config = config
        self._handled_book = None

    # -----------------------------------------------------
    # Settings
    # -----------------------------------------------------

    def _enabled(self) -> bool:
        return self.config is None or getattr(self.config, "enabled", True)

    def _switch(self, attr: str) -> bool:
        default = attr == "remove_advertisements"
        return bool(getattr(self.config, attr, default)) if self.config is not None else default

    def _needed_rank(self) -> int:
        level = getattr(self.config, "automatic_confidence", "high") if self.config is not None else "high"
        return _CONFIDENCE_RANK.get(level, 2)

    # -----------------------------------------------------
    # Deciding what to remove
    # -----------------------------------------------------

    def _plan(self, book, analysis):
        """List of dicts, one per page to remove. Empty when nothing
        qualifies or the book's chapters were not confirmed."""
        if not self._enabled() or analysis is None:
            return []
        matter = getattr(analysis, "frontmatter", None)
        chapters = getattr(analysis, "chapters", None)
        if matter is None or chapters is None or not matter.boundaries_confirmed:
            return []

        ordered = _spine_ordered_chapters(book)
        order = [c.href for c in ordered]
        confirmed = {c.href for c in (chapters.confirmed_boundaries or [])}
        indexes = [i for i, href in enumerate(order) if href in confirmed]
        if not indexes:
            return []
        first, last = min(indexes), max(indexes)

        nav_hrefs = {m.href for m in book.manifest if "nav" in (m.properties or "").split()}
        by_href = {c.href: c for c in ordered}
        needed = self._needed_rank()

        plan = []
        for item in matter.chapters:
            category = _CATEGORIES.get(item.label)
            if category is None or not self._switch(category[0]):
                continue
            href = item.href
            chapter = by_href.get(href)
            if chapter is None or chapter.document is None:
                continue
            if href in confirmed or href in nav_hrefs:
                continue
            if _BOOK_PART_NAME.search(posixpath.basename(href)):
                continue
            if _CONFIDENCE_RANK.get(item.confidence, 0) < needed:
                continue
            index = order.index(href)
            if first <= index <= last:
                continue
            if item.label == MatterLabel.TEASER_CHAPTER.value and index < first:
                continue
            text = " ".join(_chapter_text(chapter).split())
            words = len(text.split())
            limit = _MAX_WORDS.get(item.label)
            if words == 0 or (limit is not None and words > limit):
                continue
            if item.label == MatterLabel.ABOUT_AUTHOR.value and not _ABOUT_HEADING.search(text[:200]):
                continue
            if item.label in (MatterLabel.PUBLISHER.value, MatterLabel.COLOPHON.value) and words < _MIN_PUBLISHER_WORDS:
                continue
            plan.append({
                "href": href,
                "label": item.label,
                "kind": category[1],
                "confidence": item.confidence,
                "words": words,
                "first_words": text[:70] + ("..." if len(text) > 70 else ""),
                "reason": item.reason,
            })

        # The last remaining page of a book is never removed.
        if len(plan) >= len(order):
            plan = plan[:-1]
        return plan

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        for entry in self._plan(book, analysis):
            report.add(entry["href"], f"Extra page to remove ({entry['kind']})", self._describe(entry, future=True))
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        report = Report(self.name)
        if self._handled_book is book:
            return report
        self._handled_book = book

        plan = self._plan(book, analysis)
        if not plan:
            return report

        remove = {entry["href"] for entry in plan}
        media = self._media_used_by(book, remove)

        _drop_chapters(book, remove)
        _prune_ncx(book, remove)
        _prune_nav(book, remove)
        _prune_unused_media(book, only=media)
        # book.toc is the in-memory copy a later nav document is built
        # from (EPUB 3 Upgrade), so it must not keep entries for pages
        # that are gone.
        refresh_book_toc(book)
        book.toc = _without_entries(book.toc, remove)
        book.mark_modified()

        for entry in plan:
            report.add(entry["href"], f"Extra page removed ({entry['kind']})", self._describe(entry, future=False))
        return report

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    @staticmethod
    def _describe(entry, future: bool) -> str:
        verb = "will be removed" if future else "removed"
        return (
            f"{entry['kind'].capitalize()} {verb}: \"{entry['first_words']}\" "
            f"({entry['words']} words, {entry['confidence']} confidence, {entry['reason']})"
        )

    @staticmethod
    def _media_used_by(book, hrefs: set) -> set:
        """Files (pictures, audio, video) the pages about to go refer to."""
        found = set()
        for chapter in book.chapters:
            if chapter.href not in hrefs or chapter.document is None:
                continue
            for el in chapter.document.iter():
                if not isinstance(el.tag, str):
                    continue
                for attr in ("src", "href", "poster", "data", XLINK_HREF):
                    value = el.get(attr)
                    if value:
                        target = _resolve(chapter.href, value)
                        if target:
                            found.add(target)
        return found
