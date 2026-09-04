"""
ebook_fix.modules.identifier_repair

Writes back the clean, correctly-scoped <dc:identifier> form
metadata.identifiers.rewrite_identifiers() already knows how to
produce -- see docs/metadata_plan.md's "Processing logic
(identifiers)" for the exact rules (opf:scheme attribute match first,
then an embedded text prefix like "ISBN: ", then the value's own
shape; no match at all leaves an honest, unscoped bare-DC entry).

Unlike modules/metadata_repair.py, this doesn't need a Calibre
metadata.opf sidecar as a second source to be confident -- a scheme's
own value_regex is the confidence check, the same way it already is
for reading, so this runs on every book, Calibre-managed or not.
Nothing here ever guesses at an identifier that doesn't cleanly match
a known scheme; it's left as a plain, unscoped <dc:identifier> exactly
as before, still logged to identifier_review.csv by the `analyze`
command if it's a genuine fallback.

Keeping a Calibre metadata.opf sidecar's own identifiers just as clean
is a separate step -- see metadata/calibre_write.py, invoked from
engine.py only after a repair has actually been written to disk, same
timing as modules/metadata_repair.py's sync.
"""
from __future__ import annotations

from ebook_fix.config import IdentifierRepairConfig
from ebook_fix.report import Report
from metadata.identifiers import rewrite_identifiers


class IdentifierStandardizeRepair:
    name = "Identifier Standardize"

    def __init__(self, config: IdentifierRepairConfig | None = None):
        self.config = config or IdentifierRepairConfig()

    def analyze(self, book, analysis=None) -> Report:
        return self._run(book, write=False)

    def repair(self, book, analysis=None) -> Report:
        return self._run(book, write=True)

    def _run(self, book, write: bool) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report

        opf = getattr(book, "opf_document", None)
        if opf is None:
            return report

        if write:
            changes = rewrite_identifiers(book)
        else:
            # analyze() previews without mutating the book -- run the
            # same rewrite against a throwaway deep copy of the OPF
            # tree instead of a hand-rolled dry-run path, so "what
            # would change" can never drift out of sync with what
            # repair() actually does.
            from copy import deepcopy
            from metadata.calibre_backend import OpfShim
            shim = OpfShim(opf_document=deepcopy(opf))
            changes = rewrite_identifiers(shim)

        for change in changes:
            category = "Identifier removed (duplicate)" if change.action == "removed" else "Identifier rewritten"
            report.add("content.opf", category, f"{change.before} -> {change.after}")

        return report
