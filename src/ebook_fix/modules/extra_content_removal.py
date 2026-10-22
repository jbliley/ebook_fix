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

from ebook_fix.extra_partial import (
    find_ad_lines,
    find_ad_pictures,
    ad_picture_page_reason,
    pictures,
)
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

# The switches in the order the GUI and the command line show them, with
# the plain words used for each kind of page (singular, plural).
CATEGORY_SWITCHES = (
    ("remove_advertisements", "advertisement", "advertisements"),
    ("remove_newsletter_signups", "newsletter sign-up", "newsletter sign-ups"),
    ("remove_teaser_chapters", "teaser chapter", "teaser chapters"),
    ("remove_books_by_author", "\"Also by\" page", "\"Also by\" pages"),
    ("remove_about_author", "About the Author page", "About the Author pages"),
    ("remove_publisher_pages", "publisher page", "publisher pages"),
    ("remove_ad_lines", "ad line on a page", "ad lines on pages"),
    ("remove_ad_pictures", "ad picture", "ad pictures"),
)
SWITCH_NAMES = {switch for switch, _one, _many in CATEGORY_SWITCHES}


# Labels that can be removed at all, and the plain name of each kind.
EXTRA_LABELS = frozenset(_CATEGORIES)


def kind_of(label: str) -> str:
    return _CATEGORIES[label][1] if label in _CATEGORIES else ""


def rank_of(confidence: str) -> int:
    """0 low, 1 medium, 2 high."""
    return _CONFIDENCE_RANK.get(confidence, 0)


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

# For ad lines and pictures inside a page the copyright page is the main
# place to look, so only the other book parts are protected by name.
_PARTIAL_PROTECTED_NAME = re.compile(
    r"title|toc|contents|nav|cover|dedic|epigraph|prolog|epilog",
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


def remove_block(el) -> None:
    """Takes one block (an ad line, or the picture and what holds only it)
    out of its page. Text that follows the element stays, an element
    left empty by it goes too, and a rule or separator right before the
    last content of the page goes with it, so no stray line is left
    hanging at the bottom."""
    # Climb to the largest ancestor that holds nothing but this element.
    target = el
    while True:
        parent = target.getparent()
        if parent is None or _local(parent) in ("body", "html"):
            break
        others = [c for c in parent if c is not target and isinstance(c.tag, str) and _local(c) not in ("br", "span")]
        own = (parent.text or "").strip() or any((c.tail or "").strip() for c in parent if c is target) \
            or any((c.tail or "").strip() or (c.text or "").strip() for c in parent if c is not target)
        if others or own:
            break
        target = parent
    parent = target.getparent()
    if parent is None:
        return
    following = target.getnext()
    previous = target.getprevious()
    nothing_after = following is None or (
        not any(isinstance(n.tag, str) and ("".join(n.itertext()).strip() or _local(n) in ("img", "image", "svg")) for n in target.itersiblings())
    )
    tail = target.tail or ""
    if tail.strip():
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(target)
    if nothing_after and previous is not None and _local(previous) == "hr":
        parent.remove(previous)


def survey(book, analysis, config=None) -> list:
    """What extra pages a book has, grouped by kind, for the Overview and
    the command line report. [{"switch", "one", "many", "on", "pages"}] in
    the fixed order of CATEGORY_SWITCHES, only kinds the book has. "on" is
    the config switch; each page says whether the current settings would
    remove it ("removed") and how many words it has. Nothing is changed."""
    module = ExtraContentRemoval(config)
    entries = module.candidates(book, analysis) + module.partial_candidates(book, analysis)
    out = []
    for switch, one, many in CATEGORY_SWITCHES:
        pages = [dict(e, removed=module.will_remove(e)) for e in entries if e["switch"] == switch]
        if pages:
            out.append({"switch": switch, "one": one, "many": many, "on": module._switch(switch), "pages": pages})
    return out


class ExtraContentRemoval:
    name = "Extra Content Removal"

    def __init__(self, config=None):
        self.config = config
        self._handled_book = None
        # The pages the last repair() took out (dicts as in candidates()),
        # for callers that want more than the report's text.
        self.last_removed = []
        # And the lines and pictures taken out of pages that stayed.
        self.last_partial = []

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

    def _forced(self) -> set:
        """Pages a person ticked "remove this page" for on the Review tab."""
        if self.config is None:
            return set()
        return set(getattr(self.config, "remove_pages", None) or [])

    def candidates(self, book, analysis):
        """Every page that could be removed if its switch were on and its
        label were sure enough: right kind of page, in the right place,
        the right size, and not protected by a safety rule. Ignores the
        switches and the confidence setting (the caller decides those), so
        the Overview and the command line report can show what a book has
        even when nothing will be removed. Pages labeled low are left out
        unless a person asked for them. Each entry says which switch
        controls it and whether a person asked for it by hand."""
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
        forced = self._forced()

        found = []
        for item in matter.chapters:
            category = _CATEGORIES.get(item.label)
            if category is None:
                continue
            href = item.href
            by_hand = href in forced
            chapter = by_href.get(href)
            if chapter is None or chapter.document is None:
                continue
            if href in confirmed or href in nav_hrefs:
                continue
            if _BOOK_PART_NAME.search(posixpath.basename(href)):
                continue
            if rank_of(item.confidence) < 1 and not by_hand:
                continue
            index = order.index(href)
            if first <= index <= last:
                continue
            if item.label == MatterLabel.TEASER_CHAPTER.value and index < first:
                continue
            text = " ".join(_chapter_text(chapter).split())
            words = len(text.split())
            if words <= 3:
                # A page that is only a picture is removable when it is an
                # advertisement picture (see extra_partial.py); it has no
                # words to show, so its first words are the picture.
                shown = pictures(chapter.document)
                if item.label == MatterLabel.ADVERTISEMENT.value and shown and ad_picture_page_reason(chapter):
                    text = "picture: " + posixpath.basename(shown[0].get("src") or shown[0].get(XLINK_HREF) or "")
                    words = 0
                elif words == 0:
                    continue
            if not by_hand:
                limit = _MAX_WORDS.get(item.label)
                if limit is not None and words > limit:
                    continue
                if item.label == MatterLabel.ABOUT_AUTHOR.value and not _ABOUT_HEADING.search(text[:200]):
                    continue
                if item.label in (MatterLabel.PUBLISHER.value, MatterLabel.COLOPHON.value) and words < _MIN_PUBLISHER_WORDS:
                    continue
            found.append({
                "href": href,
                "label": item.label,
                "switch": category[0],
                "kind": category[1],
                "confidence": item.confidence,
                "words": words,
                "first_words": text[:70] + ("..." if len(text) > 70 else ""),
                "reason": item.reason,
                "by_hand": by_hand,
            })
        return found

    # Labels of pages an ad line or ad picture may be taken out of. The
    # page itself stays; only the line or picture goes.
    _PARTIAL_LABELS = frozenset({
        MatterLabel.COPYRIGHT.value, MatterLabel.PUBLISHER.value, MatterLabel.COLOPHON.value,
        MatterLabel.FRONT_MATTER.value, MatterLabel.BACK_MATTER.value, MatterLabel.ABOUT_AUTHOR.value,
        MatterLabel.ACKNOWLEDGMENTS.value, MatterLabel.AFTERWORD.value, MatterLabel.FICTION_DISCLAIMER.value,
    })

    def partial_candidates(self, book, analysis):
        """Ad lines and ad pictures inside pages that stay (step 4). Looked
        for only on the book's front and back matter pages and on its last
        chapter page, never in the middle of the story, and never on the
        cover, title page or contents page. Always high confidence: a line
        or picture is only reported with clear evidence (extra_partial.py).
        Pages that are being removed whole are skipped. Each entry carries
        the live element, so repair() can take it out."""
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
        last_story = order[max(indexes)]
        nav_hrefs = {m.href for m in book.manifest if "nav" in (m.properties or "").split()}
        by_href = {c.href: c for c in ordered}
        going_whole = {e["href"] for e in self._plan(book, analysis)}

        found = []
        for item in matter.chapters:
            href = item.href
            chapter = by_href.get(href)
            if chapter is None or chapter.document is None or href in nav_hrefs or href in going_whole:
                continue
            if _PARTIAL_PROTECTED_NAME.search(posixpath.basename(href)):
                continue
            if item.label not in self._PARTIAL_LABELS and href != last_story:
                continue
            for el, text, reason in find_ad_lines(chapter.document):
                found.append({
                    "href": href, "label": "ad line", "switch": "remove_ad_lines", "kind": "ad line",
                    "confidence": "high", "words": len(text.split()),
                    "first_words": text[:70] + ("..." if len(text) > 70 else ""),
                    "reason": reason, "by_hand": False, "partial": True, "element": el,
                })
            for el, src, reason in find_ad_pictures(chapter.document):
                found.append({
                    "href": href, "label": "ad picture", "switch": "remove_ad_pictures", "kind": "ad picture",
                    "confidence": "high", "words": 0,
                    "first_words": "picture: " + posixpath.basename(src),
                    "reason": reason, "by_hand": False, "partial": True, "element": el,
                })
        return found

    def will_remove(self, entry) -> bool:
        """Whether the current settings remove this page (see candidates())."""
        if entry["by_hand"]:
            return True
        return self._switch(entry["switch"]) and rank_of(entry["confidence"]) >= self._needed_rank()

    def _plan(self, book, analysis):
        """List of dicts, one per page to remove. Empty when nothing
        qualifies or the book's chapters were not confirmed."""
        plan = [entry for entry in self.candidates(book, analysis) if self.will_remove(entry)]
        # The last remaining page of a book is never removed.
        if plan and len(plan) >= len(book.chapters):
            plan = plan[:-1]
        return plan

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        for entry in self._plan(book, analysis):
            report.add(entry["href"], f"Extra page to remove ({entry['kind']})", self._describe(entry, future=True))
        for entry in self.partial_candidates(book, analysis):
            if self.will_remove(entry):
                report.add(entry["href"], f"Ad part to remove ({entry['kind']})", self._describe(entry, future=True))
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
        partial = [e for e in self.partial_candidates(book, analysis) if self.will_remove(e)]
        self.last_removed = plan
        self.last_partial = partial
        if not plan and not partial:
            return report

        remove = {entry["href"] for entry in plan}
        media = self._media_used_by(book, remove)

        if plan:
            _drop_chapters(book, remove)
            _prune_ncx(book, remove)
            _prune_nav(book, remove)
            # book.toc is the in-memory copy a later nav document is built
            # from (EPUB 3 Upgrade), so it must not keep entries for pages
            # that are gone.
            refresh_book_toc(book)
            book.toc = _without_entries(book.toc, remove)

        # Lines and pictures inside pages that stay.
        by_href = {c.href: c for c in book.chapters}
        for entry in partial:
            element = entry["element"]
            if entry["switch"] == "remove_ad_pictures":
                src = element.get("src") or element.get(XLINK_HREF)
                if src:
                    target = _resolve(entry["href"], src)
                    if target:
                        media.add(target)
            remove_block(element)
            chapter = by_href.get(entry["href"])
            if chapter is not None:
                chapter.modified = True

        _prune_unused_media(book, only=media)
        book.mark_modified()

        for entry in plan:
            report.add(entry["href"], f"Extra page removed ({entry['kind']})", self._describe(entry, future=False))
        for entry in partial:
            report.add(entry["href"], f"Ad part removed ({entry['kind']})", self._describe(entry, future=False))
        return report

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    @staticmethod
    def _describe(entry, future: bool) -> str:
        verb = "will be removed" if future else "removed"
        size = f"{entry['words']} words, " if entry["words"] else ""
        return (
            f"{entry['kind'].capitalize()} {verb}: \"{entry['first_words']}\" "
            f"({size}{entry['confidence']} confidence, {entry['reason']})"
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
