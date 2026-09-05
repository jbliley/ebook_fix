"""
metadata.calibre_write

Writes confidently-resolved core-field values back into a Calibre
library's metadata.opf sidecar, so a correction doesn't just live in
memory for one run -- it persists on disk, and shows up in Calibre and
Calibre-Web too. See docs/metadata_plan.md.

Only ever called with values metadata.merge has already decided are
safe to write -- see MergedCoreFields.epub_updates()/.calibre_updates()
and .subjects_for_epub()/.subjects_for_calibre(). A genuine
disagreement is never written here; those stay in
identifier_review.csv for a person (or eventually the GUI) to resolve.

metadata.opf is a small, unlocked sidecar file, so it's edited and
written back directly here -- unlike metadata.db, which is Calibre's
live database and (per the original plan in docs/metadata_plan.md)
should only ever be touched through the `calibredb` command-line tool,
not raw sqlite writes. Syncing metadata.db is still future work, not
attempted by this module.

Called from engine.py's `repair`/`auto_fix`, after a book has actually
been written to its output path -- never during a --dry-run, matching
how the EPUB output itself is never written for one either.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from lxml import etree


class MetadataOpfWriteError(Exception):
    """Raised when metadata.opf couldn't be written back to disk after
    retrying -- see _atomic_write(). Callers (engine.py) catch this
    and log a warning rather than letting it crash the run, since by
    the time this is called the actual EPUB has already been repaired
    and saved; a sidecar sync failure shouldn't take that down with
    it."""

from metadata.calibre_backend import OpfShim
from metadata.core_fields import apply_core_field_updates, write_subjects
from metadata.identifiers import IdentifierRewrite, rewrite_identifiers


def sync_metadata_opf(
    opf_path,
    field_updates: dict[str, str],
    subjects: list[str] | None = None,
) -> list[str]:
    """Applies field_updates (from MergedCoreFields.calibre_updates())
    and, optionally, a replacement subject list (from
    .subjects_for_calibre()) to a metadata.opf sidecar, in place.

    Returns the list of field names actually changed. Returns an empty
    list -- rather than raising -- if metadata.opf is missing or fails
    to parse, or if nothing actually needed updating; a sidecar being
    unreadable shouldn't block the rest of repair, and "nothing to do"
    isn't an error."""
    opf_path = Path(opf_path)
    if not opf_path.is_file():
        return []

    try:
        tree = etree.parse(str(opf_path))
    except etree.XMLSyntaxError:
        return []

    shim = OpfShim(opf_document=tree.getroot())

    changed = apply_core_field_updates(shim, field_updates)
    if subjects is not None and write_subjects(shim, subjects):
        changed.append("subjects")

    if not changed:
        return []

    _atomic_write(opf_path, etree.tostring(tree, xml_declaration=True, encoding="UTF-8"))
    return changed


def clean_metadata_opf_identifiers(opf_path) -> list[IdentifierRewrite]:
    """Applies metadata.identifiers.rewrite_identifiers() to a
    metadata.opf sidecar's own <dc:identifier> entries -- the same
    cleanup modules/identifier_repair.py already applies to the EPUB
    itself, kept separate from sync_metadata_opf() above since it
    doesn't depend on MergedCoreFields at all (a scheme's own regex is
    the confidence check here, not agreement between two sources).

    Returns the list of changes actually made; empty (never raises)
    if metadata.opf is missing, fails to parse, or was already
    clean."""
    opf_path = Path(opf_path)
    if not opf_path.is_file():
        return []

    try:
        tree = etree.parse(str(opf_path))
    except etree.XMLSyntaxError:
        return []

    shim = OpfShim(opf_document=tree.getroot())
    changes = rewrite_identifiers(shim)
    if changes:
        _atomic_write(opf_path, etree.tostring(tree, xml_declaration=True, encoding="UTF-8"))
    return changes


def _atomic_write(path: Path, data: bytes) -> None:
    """Write-to-temp-then-replace, the same convention every other
    on-disk write in this project follows (see docs/metadata_plan.md's
    lxml gotchas) -- avoids leaving a half-written metadata.opf behind
    if something interrupts the write partway through.

    On Windows, replacing a file that's momentarily locked by another
    program (Calibre itself, an antivirus scanner, a OneDrive/Dropbox
    sync client, etc.) raises PermissionError even though the lock
    usually clears within a second or two. _replace_with_retry() below
    retries briefly before giving up. If the write still fails, the
    leftover .tmp file is cleaned up here rather than left behind, and
    MetadataOpfWriteError is raised so the caller can decide how to
    handle it (engine.py logs a warning and continues, since the EPUB
    itself is already safely saved by this point)."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_bytes(data)
        _replace_with_retry(tmp_path, path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise MetadataOpfWriteError(
            f"Could not write to {path}: {exc}. It may be open in another "
            "program (Calibre, an antivirus scanner, OneDrive/Dropbox sync, "
            "etc). Close anything that might have it open and run repair "
            "again -- your EPUB itself has already been saved successfully."
        ) from exc


def _replace_with_retry(tmp_path: Path, path: Path, attempts: int = 5, delay: float = 0.4) -> None:
    """Attempts tmp_path.replace(path) a few times before giving up.
    Also tries clearing a read-only attribute on the destination file
    first, since that's a common reason Windows refuses the replace
    (e.g. a file Calibre or a sync client marked read-only)."""
    last_error = None
    for attempt in range(attempts):
        if path.exists() and not os.access(path, os.W_OK):
            try:
                path.chmod(0o666)
            except OSError:
                pass
        try:
            tmp_path.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    raise last_error
