"""
ebook_fix.modules.title_cleanup_repair

Cleans up filename-style title and author metadata. Found in "Sandman
Slim" (Richard Kadrey), whose title was literally "Kadrey, Richard - 01
Sandman Slim - Sandman Slim", with the same junk as its sort title and
the author stored as "Kadrey, Richard".

Three separate actions, each with its own switch (see
TitleCleanupConfig), none needing a Calibre sidecar as a second source:

1. strip_author_from_title -- when the first or last " - " piece of the
   title is the book's own author, drop it, and remove any series
   number that came with it. The title is also left alone unless that
   author match is exact, so a title that merely contains a dash is
   never touched. See metadata/title_cleanup.py for the exact patterns.
   If calibre:title_sort held the same junk as the title, it is updated
   to the clean title.
2. read_series_from_title -- when the title spelled out a series name
   and number ("01 Sandman Slim") and the book has no series set yet,
   record it, in both the Calibre and EPUB 3 conventions (ebook_fix.series
   already writes both). A book that already has a series is never
   overwritten.
3. fix_author_order -- "Last, First" becomes "First Last" (Jacob's
   preferred form), only when the OPF's own file-as value is the same
   text or the title started with it; see author_first_last(). The
   file-as sort name keeps the comma form, and is added if missing.

Runs right before Metadata Sync, and works only on dc:title and
dc:creator, so it is independent of every chapter-text module.
"""
from __future__ import annotations

from lxml import etree

from ebook_fix import series as series_metadata
from ebook_fix.config import TitleCleanupConfig
from ebook_fix.report import Report
from metadata.core_fields import write_core_field
from metadata.title_cleanup import suggest_from_filename_title

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"


def _collapse(text: str) -> str:
    return " ".join((text or "").split()).casefold()


class TitleCleanupRepair:
    name = "Title Cleanup"

    def __init__(self, config: TitleCleanupConfig | None = None):
        self.config = config or TitleCleanupConfig()

    def analyze(self, book, analysis=None) -> Report:
        return self._run(book, write=False)

    def repair(self, book, analysis=None) -> Report:
        return self._run(book, write=True)

    # -----------------------------------------------------

    def _run(self, book, write: bool) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        meta = getattr(book, "metadata", None)
        title = getattr(meta, "title", "") if meta is not None else ""
        author = getattr(meta, "creator", "") if meta is not None else ""
        if not title and not author:
            return report

        creators = self._creator_elements(book)
        file_as = ""
        if len(creators) == 1:
            file_as = creators[0].get(f"{{{OPF_NS}}}file-as", "") or ""

        suggestion = suggest_from_filename_title(
            title,
            author,
            file_as=file_as,
            single_author=len(creators) == 1,
            has_series=bool(series_metadata.read(book).name),
            fix_title=self.config.strip_author_from_title,
            fix_series=self.config.read_series_from_title,
            fix_author=self.config.fix_author_order,
        )
        verb = "" if write else " will be"

        if suggestion.title is not None:
            if not write or write_core_field(book, "title", suggestion.title):
                report.add(
                    "content.opf",
                    f"Filename-style title{verb} cleaned",
                    f"{title!r} -> {suggestion.title!r}",
                )
                if write:
                    self._fix_title_sort(book, title, suggestion.title, report)

            if suggestion.series is not None:
                if write:
                    series_metadata.write(book, suggestion.series, suggestion.series_index)
                number = series_metadata.format_index(suggestion.series_index)
                report.add(
                    "content.opf",
                    f"Series{verb} read from the title",
                    f"{suggestion.series!r}, book {number}",
                )

        if suggestion.author is not None:
            self._fix_author(book, author, suggestion.author, creators[0], write, verb, report)

        return report

    # -----------------------------------------------------

    def _creator_elements(self, book):
        opf = getattr(book, "opf_document", None)
        if opf is None:
            return []
        return opf.findall(f".//{{{DC_NS}}}creator")

    def _fix_author(self, book, author, new_author, element, write, verb, report):
        if write:
            if not write_core_field(book, "author", new_author):
                return
            if not element.get(f"{{{OPF_NS}}}file-as"):
                element.set(f"{{{OPF_NS}}}file-as", author)
        report.add(
            "content.opf",
            f"Author name{verb} put in First Last order",
            f"{author!r} -> {new_author!r}",
        )

    def _fix_title_sort(self, book, old_title, new_title, report):
        opf = getattr(book, "opf_document", None)
        if opf is None:
            return
        metadata = opf.find(f"{{{OPF_NS}}}metadata")
        if metadata is None:
            return
        for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
            if meta.get("name") != "calibre:title_sort":
                continue
            if _collapse(meta.get("content")) == _collapse(old_title):
                meta.set("content", new_title)
                book.opf_modified = True
                report.add(
                    "content.opf",
                    "Sort title cleaned",
                    f"{old_title!r} -> {new_title!r}",
                )
