"""
gui.bulk

Fix a Folder: the plumbing for working through every book in a folder
with the same repairs the Fix This Book button applies, one book after
another, while the page shows what is happening.

This file knows nothing about how a book is fixed. It finds the books
(find_books), keeps the list of what happened to each (BulkRun), and calls
whatever function it is given for each one (the GUI passes in a function
built from the same code the Fix This Book button uses, see
bulk_fix_one in app.py). One book failing never stops the others.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

# Statuses an item can have. "waiting" and "working" are in progress; the
# rest are final.
WAITING = "waiting"
WORKING = "working"
FIXED = "fixed"
NOTHING = "nothing"
SKIPPED = "skipped"
FAILED = "failed"
NOT_RUN = "not_run"

FINAL_STATUSES = {FIXED, NOTHING, SKIPPED, FAILED, NOT_RUN}


@dataclass
class BulkOptions:
    folder: str
    include_subfolders: bool = True
    replace_originals: bool = False
    include_html: bool = False


def _is_own_output(path: Path) -> bool:
    """Files this program wrote itself: the fixed copy, the kept original
    and a conversion from another format. Never treated as books to fix."""
    stem = path.stem.lower()
    return stem.endswith("_fixed") or stem.endswith("_original") or "(converted" in stem


def find_books(options: BulkOptions, epub_suffixes: Iterable[str],
               convertible_suffixes: Iterable[str], html_suffixes: Iterable[str]) -> list:
    """Every book in the folder, in path order, as dicts:
    {"path": Path, "name": str (path relative to the folder), "status": ...,
    "detail": ...}. Files that should not be fixed are included with status
    "skipped" and the reason, so the results list accounts for everything.

    Skipped, with a reason: the program's own output files; a book whose
    "_fixed" copy already sits next to it (it was done on an earlier run);
    a MOBI, FB2 or similar when an EPUB of the same name sits beside it.
    Folders whose name starts with a dot are not looked into."""
    root = Path(options.folder)
    epubs = {s.lower() for s in epub_suffixes}
    convertible = {s.lower() for s in convertible_suffixes}
    html = {s.lower() for s in html_suffixes}
    wanted = epubs | convertible | (html if options.include_html else set())

    found = []
    walker = root.rglob("*") if options.include_subfolders else root.glob("*")
    for path in sorted(walker, key=lambda p: str(p).lower()):
        try:
            if not path.is_file() or path.suffix.lower() not in wanted:
                continue
            relative = path.relative_to(root)
        except (OSError, ValueError):
            continue
        if any(part.startswith(".") for part in relative.parts[:-1]):
            continue
        item = {"path": path, "name": str(relative), "status": WAITING, "detail": ""}
        suffix = path.suffix.lower()
        if _is_own_output(path):
            continue   # not a book of the person's; leave it out of the list altogether
        if suffix in epubs and path.with_name(path.stem + "_fixed" + path.suffix).exists():
            item.update(status=SKIPPED, detail="A fixed copy is already next to it.")
        elif suffix not in epubs and path.with_suffix(".epub").exists():
            item.update(status=SKIPPED, detail="The same book is already here as an EPUB.")
        elif suffix not in epubs and path.with_name(path.stem + "_fixed.epub").exists():
            item.update(status=SKIPPED, detail="A fixed copy is already next to it.")
        found.append(item)
    return found


def _explain(exc: Exception) -> str:
    """A failure in plain words for the results list."""
    if isinstance(exc, zipfile.BadZipFile):
        return "Could not be opened: the file is damaged or is not really an EPUB."
    if isinstance(exc, PermissionError):
        return f"Could not be changed: another program may have it open ({exc})."
    return f"{type(exc).__name__}: {exc}"


class BulkRun:
    """One pass over a folder, run on a background thread. The page asks
    for snapshot() every second or so."""

    def __init__(self, options: BulkOptions, items: list):
        self.id = uuid.uuid4().hex
        self.options = options
        self.items = items
        self.state = "ready"          # ready -> running -> done | stopped
        self.current = None           # index of the book being worked on
        self.started = None
        self.finished = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    # -- control ----------------------------------------------------------
    def start(self, process_one: Callable) -> None:
        """process_one(path: Path, options: BulkOptions) -> dict with at
        least "status" (fixed, nothing or skipped) and optionally "detail",
        "session_id", "changes", "needs_look" and "note". It may raise; that
        marks the book as failed."""
        self.state = "running"
        self.started = time.time()
        self._thread = threading.Thread(target=self._work, args=(process_one,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Finish the book being worked on, then stop."""
        self._stop.set()

    @property
    def running(self) -> bool:
        return self.state == "running"

    # -- the worker -------------------------------------------------------
    def _work(self, process_one: Callable) -> None:
        for index, item in enumerate(self.items):
            if item["status"] != WAITING:
                continue
            if self._stop.is_set():
                break
            with self._lock:
                self.current = index
                item["status"] = WORKING
            began = time.time()
            try:
                outcome = process_one(item["path"], self.options) or {}
                status = outcome.get("status", FIXED)
                extra = {k: v for k, v in outcome.items() if k != "status"}
            except Exception as exc:   # one book must never stop the rest
                traceback.print_exc()
                status = FAILED
                extra = {"detail": _explain(exc)}
            with self._lock:
                item.update(extra)
                item["status"] = status
                item["seconds"] = round(time.time() - began, 1)
        with self._lock:
            stopped = self._stop.is_set()
            for item in self.items:
                if item["status"] == WAITING:
                    item["status"] = NOT_RUN
                    item["detail"] = "Stopped before this book."
            self.current = None
            self.finished = time.time()
            self.state = "stopped" if stopped else "done"

    # -- what the page shows ---------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            counts = {s: 0 for s in (WAITING, WORKING, FIXED, NOTHING, SKIPPED, FAILED, NOT_RUN)}
            rows = []
            for item in self.items:
                counts[item["status"]] += 1
                rows.append({
                    "name": item["name"],
                    "status": item["status"],
                    "detail": item.get("detail", ""),
                    "note": item.get("note", ""),
                    "changes": item.get("changes", 0),
                    "needs_look": item.get("needs_look", 0),
                    "session_id": item.get("session_id", ""),
                    "seconds": item.get("seconds"),
                })
            total = len(self.items)
            to_do = total - counts[SKIPPED]
            done = sum(counts[s] for s in (FIXED, NOTHING, FAILED))
            end = self.finished or time.time()
            return {
                "id": self.id,
                "state": self.state,
                "folder": self.options.folder,
                "replace_originals": self.options.replace_originals,
                "total": total,
                "to_do": to_do,
                "done": done,
                "counts": counts,
                "current": self.items[self.current]["name"] if self.current is not None else "",
                "elapsed": round(end - self.started, 0) if self.started else 0,
                "rows": rows,
            }


# Runs started since the program opened. One person on one computer, so a
# plain dictionary is enough.
RUNS: dict = {}
_RUNS_GUARD = threading.Lock()


def register(run: BulkRun) -> None:
    with _RUNS_GUARD:
        RUNS[run.id] = run


def get_run(run_id: str):
    with _RUNS_GUARD:
        return RUNS.get(run_id)


def running_run():
    """The run that is working right now, if any (only one at a time, since
    the books are fixed one after another and the progress line is shared)."""
    with _RUNS_GUARD:
        for run in RUNS.values():
            if run.running:
                return run
    return None
