"""
metadata.calibredb_write

Writes confidently-resolved metadata into Calibre's own metadata.db,
via the `calibredb` command-line tool Calibre itself ships -- not by
touching metadata.db directly (SQLite files that another live
application might have open are not something to write to by hand).

This is a second, separate write target alongside metadata.calibre_write's
metadata.opf sidecar sync -- both consume the exact same
already-vetted values from metadata.merge (MergedCoreFields.calibre_updates(),
.subjects_for_calibre()), so there's no new confidence logic here.
This module is only responsible for getting those already-decided
values into Calibre's database, and for failing clearly and safely
when it can't.

Genuinely unverified against a real Calibre library as of this
writing -- see docs/metadata_plan.md's calibredb write-back section.
Every design choice below (the field-name mapping, the executable
search, the error handling) is a best-effort reading of `calibredb`'s
documented behavior, not something confirmed against a live `calibredb
set_metadata` call. Off by default in config.py until that
verification happens.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# calibredb's own --field names for the subset of core fields this
# project already resolves confidently. "rights" has no standard
# calibredb field (Calibre has no built-in license/rights column --
# only a custom column could hold it, and this project doesn't know
# the name of any custom column a person may or may not have added),
# so it's deliberately left out of this map rather than guessed at.
_CALIBREDB_FIELD_MAP = {
    "title": "title",
    "author": "authors",
    "publisher": "publisher",
    "date": "pubdate",
    "description": "comments",
    "series": "series",
    "series_index": "series_index",
}

# Common install locations calibredb doesn't always add to PATH,
# especially on Windows where Calibre is often a per-user install.
# Checked only if a plain PATH lookup comes up empty.
_WINDOWS_FALLBACK_PATHS = [
    r"C:\Program Files\Calibre2\calibredb.exe",
    r"C:\Program Files (x86)\Calibre2\calibredb.exe",
]


@dataclass(slots=True)
class CalibreDbWriteResult:
    """What actually happened when trying to reach calibredb. Kept
    separate from raising an exception -- a missing calibredb install
    or a locked library are expected, recoverable situations for a
    tool running unattended against someone else's machine, not
    programming errors, so the caller decides what to do (skip
    quietly, log a note, surface it to a person) rather than this
    module deciding for them."""
    attempted: bool
    succeeded: bool
    fields_written: list[str] = field(default_factory=list)
    error: str | None = None


def find_calibredb() -> str | None:
    """Locates the calibredb executable, or None if it can't be
    found. Checked in this order: PATH first (the common case on
    macOS/Linux, and on Windows when Calibre's own installer added
    itself to PATH), then a couple of well-known Windows install
    locations calibredb doesn't always register on PATH from."""
    on_path = shutil.which("calibredb")
    if on_path:
        return on_path
    for candidate in _WINDOWS_FALLBACK_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def _quote_value(name: str, value: str) -> str:
    """calibredb's --field takes "name:value" as one argument.
    Authors are '&'-separated in Calibre's own convention; this
    project's own "author" value is already a single display string
    (see metadata.core_fields), so it's passed through as-is -- a
    single author works fine either way, and reformatting a
    multi-author string into Calibre's '&'-joined convention isn't
    something this project can do reliably without knowing how the
    EPUB itself separated the names in the first place."""
    return f"{name}:{value}"


def sync_metadata_db(
    library_root: Path,
    book_id: int,
    updates: dict[str, str],
    subjects: list[str] | None = None,
    calibredb_path: str | None = None,
    timeout: int = 30,
) -> CalibreDbWriteResult:
    """Writes `updates` (a metadata.merge.calibre_updates()-shaped
    dict) and, if given, `subjects` (metadata.merge's
    subjects_for_calibre()) into Calibre's metadata.db for one book,
    via `calibredb set_metadata --with-library <library_root> <book_id>
    --field ...` -- one process call for every field, since
    calibredb's own interface takes repeated --field arguments rather
    than a single batch payload.

    Only ever call this after the repaired EPUB has already been
    saved to disk -- same rule as calibre_write.sync_metadata_opf.
    Never call this during a --dry-run.

    Returns a CalibreDbWriteResult rather than raising for any
    calibredb-specific failure (not found, non-zero exit, timeout) --
    those are expected, recoverable conditions when running against
    someone else's live Calibre install, not bugs in this module.
    Genuine misuse (a bad library_root, an invalid book_id type) still
    raises normally, the same as any other function in this project.
    """
    fields_to_write = {}
    for name, value in updates.items():
        calibredb_name = _CALIBREDB_FIELD_MAP.get(name)
        if calibredb_name is None:
            # "rights" (or any other field metadata.merge might one
            # day resolve that calibredb has no matching column for)
            # -- silently skipped, not an error; there's nothing wrong
            # with the value, just nowhere in Calibre's own schema to
            # put it.
            continue
        fields_to_write[calibredb_name] = value

    if subjects:
        fields_to_write["tags"] = ",".join(subjects)

    if not fields_to_write:
        return CalibreDbWriteResult(attempted=False, succeeded=True, fields_written=[])

    resolved_calibredb = calibredb_path or find_calibredb()
    if resolved_calibredb is None:
        return CalibreDbWriteResult(
            attempted=False,
            succeeded=False,
            error=(
                "calibredb was not found (checked PATH and the usual Windows "
                "install locations). Metadata.db was not touched -- the EPUB "
                "and metadata.opf sync (if enabled) still happened normally. "
                "If Calibre is installed somewhere calibredb can't be found "
                "automatically, pass its path explicitly."
            ),
        )

    command = [
        resolved_calibredb,
        "set_metadata",
        "--with-library", str(library_root),
        str(book_id),
    ]
    for calibredb_name, value in fields_to_write.items():
        command += ["--field", _quote_value(calibredb_name, value)]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CalibreDbWriteResult(
            attempted=True,
            succeeded=False,
            error=f"calibredb didn't respond within {timeout}s -- metadata.db was not touched.",
        )
    except OSError as exc:
        return CalibreDbWriteResult(
            attempted=True,
            succeeded=False,
            error=f"Couldn't run calibredb ({exc}) -- metadata.db was not touched.",
        )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        # calibredb's own message when the library's locked (another
        # process, usually the Calibre GUI itself, holds it) mentions
        # a lock file by name -- surfaced as-is rather than
        # re-worded, since calibredb's own wording is normally
        # specific enough to act on directly.
        hint = " Close Calibre and try again if it's currently open." if "lock" in stderr.lower() else ""
        return CalibreDbWriteResult(
            attempted=True,
            succeeded=False,
            error=f"calibredb exited with an error (code {result.returncode}): {stderr or '(no output)'}.{hint}",
        )

    return CalibreDbWriteResult(
        attempted=True,
        succeeded=True,
        fields_written=list(fields_to_write.keys()),
    )
