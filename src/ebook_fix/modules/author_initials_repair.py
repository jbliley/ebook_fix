"""
ebook_fix.modules.author_initials_repair

Standardizes an author's initials to "X. Y." style -- see
metadata.author_names.standardize_initials() for the exact rule. Like
modules/identifier_repair.py, this doesn't need a Calibre metadata.opf
sidecar as a second source to be confident: an all-caps initials-
shaped token ("AA", "A.A.") is unambiguous entirely on its own, so
this runs on every book, Calibre-managed or not. It only ever touches
the leading given-name tokens, never the final (surname) token -- see
standardize_initials()'s own docstring for why.

This is independent of modules/metadata_repair.py's reversed-author-
name handling: that one compares two sources to catch a "Last, First"
vs "First Last" ordering difference, this one is a single-sided
formatting pass over whatever the author field already says.
"""
from __future__ import annotations

from ebook_fix.config import AuthorInitialsConfig
from ebook_fix.report import Report
from metadata.author_names import standardize_initials
from metadata.core_fields import write_core_field


class AuthorInitialsRepair:
    name = "Author Initials"

    def __init__(self, config: AuthorInitialsConfig | None = None):
        self.config = config or AuthorInitialsConfig()

    def analyze(self, book, analysis=None) -> Report:
        return self._run(book, write=False)

    def repair(self, book, analysis=None) -> Report:
        return self._run(book, write=True)

    def _run(self, book, write: bool) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        meta = getattr(book, "metadata", None)
        current = getattr(meta, "creator", "") if meta is not None else ""
        if not current:
            return report

        standardized = standardize_initials(current)
        if standardized is None:
            return report

        if write:
            if write_core_field(book, "author", standardized):
                report.add("content.opf", "Author initials standardized", f"{current} -> {standardized}")
        else:
            report.add("content.opf", "Author initials will be standardized", f"{current} -> {standardized}")

        return report
