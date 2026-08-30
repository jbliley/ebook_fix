"""
ebook_fix.modules.toc_generation

Generates a table of contents from nothing, for a book that has
neither an NCX nor an EPUB3 nav document with any TOC entries at all
(book.toc_source == "" -- see docs/analysis_roadmap.md, "Next: TOC
generation when missing", and case 2 of Jacob's three-case framework
in docs/xhtml_recoder_plan.md: chapter markers exist, no TOC).

Every existing TOC-related module before this one only ever worked
with navigation that already existed: toc.py validates one,
crossref.py's generate_missing_ncx_entries only ever extends an NCX
document that's already there, and modules/epub3_upgrade.py's own
nav-document fallback (which now shares this feature's
ebook_fix.toc_generate building blocks) only ever runs as a side
effect of an EPUB2->3 version upgrade. This module is the one that
actually notices a book with no navigation at all and gives it one,
independent of whether it also needs a version upgrade this run.

Two independent things this can add, either or both depending on
what's missing:
- toc.ncx, if book.ncx_document is None -- every book benefits from
  this (an EPUB2-only reader can't use anything else, and a modern
  EPUB3 reader that gets one anyway is unaffected).
- nav.xhtml, if the book is (already, or was just upgraded to be)
  EPUB3 and has no manifest item with properties="nav" -- required
  for a valid EPUB3 package. modules/epub3_upgrade.py's own repair()
  already adds one, but only as a side effect of bumping the version;
  a book that was already EPUB3 all along and simply never had a nav
  document falls through that module's checks entirely, since nothing
  there needed upgrading.

Both draw their entries from the same place --
ebook_fix.toc_generate.build_toc_entries, which mirrors the book's
own detected chapter/Part structure rather than inventing anything;
see that module's docstring for how fragment anchors get assigned. A
book with nothing confirmed the normal (non-case3) way gets neither
addition, even if it's otherwise missing both documents -- see
Jacob's three-case framework in docs/xhtml_recoder_plan.md: case 3
books are always routed to manual review, never handed an
authoritative-looking generated TOC.

Runs after ChapterMarkupRepair (see engine.py's pipeline order) so
build_toc_entries can reuse the id chapter_markup already assigned to
each confirmed chapter's wrapper div, instead of stamping a second,
redundant one right next to it. Also runs after EPUB3UpgradeRepair,
since whether a nav document is still needed depends on book.version
and book.nav_item as they stand *after* any upgrade this run already
performed, not as the book started out.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from lxml import etree

from ebook_fix.models import ManifestItem
from ebook_fix.report import Report
from ebook_fix.toc_generate import build_toc_entries, build_nav_bytes, build_ncx_document

OPF_NS = "http://www.idpf.org/2007/opf"
NCX_NS_URI = "http://www.daisy.org/z3986/2005/ncx/"
NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
NAV_MEDIA_TYPE = "application/xhtml+xml"


class TocGenerationRepair:
    name = "TOC Generation"

    def __init__(self, config=None):
        self.config = config

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        if self.config is not None and not getattr(self.config, "enabled", True):
            return report
        if not self._eligible(book):
            return report

        location = book.package_path or "content.opf"
        if self._ncx_needs_generating(book):
            report.add(
                location,
                "No table of contents at all",
                "This book has no NCX and no EPUB3 nav document with any TOC entries. "
                "A toc.ncx will be generated from the book's detected chapter structure.",
            )
        if self._needs_nav(book):
            report.add(
                location,
                "No EPUB3 navigation document",
                "This EPUB3 book has no nav.xhtml at all. One will be generated from "
                "the book's detected chapter structure.",
            )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        report = Report(self.name)
        if self.config is not None and not getattr(self.config, "enabled", True):
            return report
        if not self._eligible(book):
            return report

        chapter_summary = analysis.chapters if analysis is not None else None
        entries = build_toc_entries(book, chapter_summary=chapter_summary)
        if not entries:
            # Nothing confirmed the normal way -- see module docstring
            # (case 3 books never get a generated TOC).
            return report

        if self._ncx_needs_generating(book):
            self._add_ncx(book, entries, report)

        if self._needs_nav(book):
            self._add_nav(book, entries, report)

        return report

    # -----------------------------------------------------
    # Eligibility
    # -----------------------------------------------------

    def _eligible(self, book):
        # book.toc_source is set once at parse time and never updated
        # afterward, so this stays a reliable "did this book start out
        # with nothing at all" check across every pass of a multi-pass
        # repair run -- a book with even a sparse or broken existing
        # TOC is left alone here (case 1 of Jacob's three-case
        # framework: preserve the book's own original structure
        # whenever real TOC entries already exist). Whether each
        # individual document still needs adding is checked separately,
        # right where that addition actually happens -- book.ncx_document/
        # book.nav_item DO reflect whatever this module (or
        # EPUB3UpgradeRepair) already added on an earlier pass.
        return not getattr(book, "toc_source", "")

    def _needs_nav(self, book):
        return book.version.startswith("3") and book.nav_item is None

    def _ncx_needs_generating(self, book):
        # Not just "book.ncx_document is None" -- a book can ship an
        # NCX *file* that parses fine but whose navMap has no
        # navPoints in it at all (an empty shell some conversion tool
        # left behind). parser.py's own toc_source check already
        # treats that the same as "no NCX" (see _eligible above), so
        # this needs to as well, or a book like that would keep its
        # empty shell forever. The only way navMap already has real
        # navPoints while toc_source is still "" is this module having
        # added them itself on an earlier pass of this same repair run
        # (parser.py would have set toc_source="ncx" at load time
        # otherwise) -- so this doubles as the idempotency check across
        # a multi-pass run.
        ncx = book.ncx_document
        if ncx is None:
            return True
        nav_map = ncx.find(f"{{{NCX_NS_URI}}}navMap")
        if nav_map is None:
            return True
        return len(nav_map.findall(f"{{{NCX_NS_URI}}}navPoint")) == 0

    # -----------------------------------------------------
    # toc.ncx
    # -----------------------------------------------------

    def _add_ncx(self, book, entries, report):
        opf = getattr(book, "opf_document", None)
        if opf is None:
            return

        # A book can already have an NCX *manifest entry* pointing at
        # a file that parses fine but is just an empty shell (see
        # _ncx_needs_generating above) -- reuse that entry's own
        # href/id and overwrite its content, rather than leaving the
        # empty shell in place and adding a second, competing NCX
        # alongside it.
        existing_item = next((i for i in book.manifest if i.media_type == NCX_MEDIA_TYPE), None)
        if existing_item is not None:
            href, item_id = existing_item.href, existing_item.id
            verb = "regenerated"
        else:
            manifest = opf.find(f"{{{OPF_NS}}}manifest")
            if manifest is None:
                return
            existing_hrefs = {i.href for i in book.manifest}
            existing_ids = {i.id for i in book.manifest}
            href = self._unique(existing_hrefs, "toc.ncx", "toc{n}.ncx")
            item_id = self._unique(existing_ids, "ncx", "ncx{n}")

            item = etree.SubElement(manifest, f"{{{OPF_NS}}}item")
            item.set("id", item_id)
            item.set("href", href)
            item.set("media-type", NCX_MEDIA_TYPE)
            book.manifest.append(ManifestItem(id=item_id, href=href, media_type=NCX_MEDIA_TYPE))
            verb = "generated"

        spine = opf.find(f"{{{OPF_NS}}}spine")
        if spine is not None:
            spine.set("toc", item_id)
        book.opf_modified = True

        book.ncx_document = build_ncx_document(book, entries)
        book.ncx_href = href
        book.ncx_modified = True

        report.add(
            book.package_path or "content.opf",
            f"toc.ncx {verb}",
            f"{'Filled in' if verb == 'regenerated' else 'Created'} {href!r} "
            f"from {len(entries)} detected top-level chapter/part entries.",
        )

    # -----------------------------------------------------
    # nav.xhtml
    # -----------------------------------------------------

    def _add_nav(self, book, entries, report):
        opf = getattr(book, "opf_document", None)
        if opf is None:
            return
        manifest = opf.find(f"{{{OPF_NS}}}manifest")
        if manifest is None:
            return

        existing_hrefs = {i.href for i in book.manifest}
        existing_ids = {i.id for i in book.manifest}
        href = self._unique(existing_hrefs, "nav.xhtml", "nav{n}.xhtml")
        item_id = self._unique(existing_ids, "nav", "nav{n}")

        item = etree.SubElement(manifest, f"{{{OPF_NS}}}item")
        item.set("id", item_id)
        item.set("href", href)
        item.set("media-type", NAV_MEDIA_TYPE)
        item.set("properties", "nav")
        book.manifest.append(
            ManifestItem(id=item_id, href=href, media_type=NAV_MEDIA_TYPE, properties="nav")
        )
        book.opf_modified = True

        base = PurePosixPath(book.package_path).parent
        book.new_files[str(base / href)] = build_nav_bytes(book, entries)

        report.add(
            book.package_path or "content.opf",
            "nav.xhtml generated",
            f"Created {href!r} from {len(entries)} detected top-level chapter/part entries.",
        )

    def _unique(self, existing, first, template):
        if first not in existing:
            return first
        n = 1
        while template.format(n=n) in existing:
            n += 1
        return template.format(n=n)
