"""
gui.file_swap

The file moves behind "Replace original file", written to cope with what
Windows does to files inside a Calibre library.

Why this exists: replacing a book means renaming the original to a backup
and renaming the repaired file onto the original name. Windows refuses a
rename when ANY program has the file open (a reading app, Calibre, an
antivirus scan, a OneDrive/Dropbox sync client, or Windows Search), when
the file is marked read-only, or when the path is longer than about 260
characters (common for Calibre, which nests Author/Title (id)/Title -
Author.epub). The old code tried each rename once and gave up with a
guess. This module:

- retries briefly, because most locks clear within a second or two;
- clears a read-only mark on the file first;
- uses the long-path form on Windows when the path is very long;
- falls back to copying when a rename is refused (a copy only needs to
  READ the original, which a program holding it open usually allows);
- when it still fails, names the program that has the file open (on
  Windows, through the Restart Manager) and any Calibre program that is
  still running, instead of a generic "something has it open".
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

ATTEMPTS = 5
DELAY_SECONDS = 0.4
LONG_PATH_LIMIT = 240


def _is_windows() -> bool:
    return os.name == "nt"


def usable_path(path) -> str:
    """The path as a string, in the long-path form on Windows when it is
    long enough to hit the 260 character limit."""
    text = str(path)
    if _is_windows() and len(text) >= LONG_PATH_LIMIT and not text.startswith("\\\\?\\"):
        absolute = os.path.abspath(text)
        if absolute.startswith("\\\\"):
            return "\\\\?\\UNC\\" + absolute[2:]
        return "\\\\?\\" + absolute
    return text


def _clear_read_only(path: str) -> None:
    try:
        if os.path.exists(path) and not os.access(path, os.W_OK):
            os.chmod(path, 0o666)
    except OSError:
        pass


def _retry(action, path_to_unlock: str, attempts: int, delay: float):
    last = None
    for attempt in range(attempts):
        _clear_read_only(path_to_unlock)
        try:
            return action()
        except PermissionError as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    raise last


def rename_with_retry(src, dst, attempts: int = ATTEMPTS, delay: float = DELAY_SECONDS) -> None:
    """os.rename, retried when Windows says the file is in use."""
    s, d = usable_path(src), usable_path(dst)
    _retry(lambda: os.rename(s, d), s, attempts, delay)


def copy_over(src, dst, attempts: int = ATTEMPTS, delay: float = DELAY_SECONDS) -> None:
    """Writes the contents of `src` into `dst` (creating or overwriting
    it), then checks the sizes match."""
    s, d = usable_path(src), usable_path(dst)
    _retry(lambda: shutil.copyfile(s, d), d, attempts, delay)
    if os.path.getsize(s) != os.path.getsize(d):
        raise OSError(f"The copy of {dst} is not the same size as {src}.")


def move_file(src, dst, attempts: int = ATTEMPTS, delay: float = DELAY_SECONDS) -> str:
    """Moves `src` to `dst` (dst must not exist). Tries a rename, and if
    Windows keeps refusing it, copies and then deletes the source.
    Returns "renamed" or "copied". Raises PermissionError/OSError if both
    fail, in which case nothing has been changed."""
    try:
        rename_with_retry(src, dst, attempts, delay)
        return "renamed"
    except PermissionError:
        pass
    copy_over(src, dst, attempts=1, delay=0)
    try:
        os.remove(usable_path(src))
    except OSError:
        # The copy is in place; leaving the source behind is better than
        # failing. The caller sees it still exists and reports it.
        pass
    return "copied"


def overwrite_file(src, dst, attempts: int = ATTEMPTS, delay: float = DELAY_SECONDS) -> str:
    """Puts the contents of `src` onto the existing file `dst` and removes
    `src`: a rename over it when allowed, otherwise an in-place overwrite.
    Returns "renamed" or "copied"."""
    s, d = usable_path(src), usable_path(dst)
    try:
        _retry(lambda: os.replace(s, d), d, attempts, delay)
        return "renamed"
    except PermissionError:
        pass
    copy_over(src, dst, attempts=1, delay=0)
    try:
        os.remove(s)
    except OSError:
        pass
    return "copied"


def remove_file(path, attempts: int = ATTEMPTS, delay: float = DELAY_SECONDS) -> None:
    """Deletes a file permanently, retrying when Windows says it is in
    use and clearing a read-only mark first."""
    p = usable_path(path)
    _retry(lambda: os.remove(p), p, attempts, delay)


# ---------------------------------------------------------------------
# Finding out who has the file
# ---------------------------------------------------------------------

def programs_using_file(path) -> list:
    """Names of programs that currently have `path` open, from the
    Windows Restart Manager. Empty on other systems, or when Windows will
    not say."""
    if not _is_windows():
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class RM_UNIQUE_PROCESS(ctypes.Structure):
            _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

        class RM_PROCESS_INFO(ctypes.Structure):
            _fields_ = [
                ("Process", RM_UNIQUE_PROCESS),
                ("strAppName", ctypes.c_wchar * 256),
                ("strServiceShortName", ctypes.c_wchar * 64),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", ctypes.c_ulong),
                ("TSSessionId", wintypes.DWORD),
                ("bRestartable", wintypes.BOOL),
            ]

        rstrtmgr = ctypes.WinDLL("rstrtmgr")
        session = wintypes.DWORD()
        key = ctypes.create_unicode_buffer(33)
        if rstrtmgr.RmStartSession(ctypes.byref(session), 0, key) != 0:
            return []
        try:
            files = (ctypes.c_wchar_p * 1)(str(path))
            if rstrtmgr.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
                return []
            needed = wintypes.UINT(0)
            count = wintypes.UINT(0)
            reasons = wintypes.DWORD(0)
            result = rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
            if result not in (0, 234) or needed.value == 0:
                return []
            infos = (RM_PROCESS_INFO * needed.value)()
            count = wintypes.UINT(needed.value)
            result = rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons))
            if result != 0:
                return []
            names = []
            for i in range(count.value):
                name = infos[i].strAppName.strip()
                if name and name not in names:
                    names.append(name)
            return names
        finally:
            rstrtmgr.RmEndSession(session)
    except Exception:
        return []


def calibre_programs_running() -> list:
    """Calibre programs still running (calibre.exe, calibre-parallel.exe,
    calibre-server.exe, ...), found with tasklist. Closing the Calibre
    window does not always stop all of them."""
    if not _is_windows():
        return []
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        first = line.split('","')[0].strip('"').strip()
        if first.lower().startswith("calibre") and first not in names:
            names.append(first)
    return names


def explain_failure(action: str, path, exc: Exception) -> str:
    """A plain-English reason a file move failed: what Windows said, who
    has the file open, and what to try."""
    parts = [f"{action}: {exc}."]
    holders = programs_using_file(path)
    calibre = calibre_programs_running()
    if holders:
        parts.append("These programs have the file open: " + ", ".join(holders) + ". Close them and try again.")
    elif calibre:
        parts.append(
            "Calibre is still running in the background (" + ", ".join(calibre) + "). "
            "Closing its window does not always stop it. Quit it from the system tray "
            "icon, or end it in Task Manager, then try again."
        )
    else:
        parts.append(
            "Windows did not say what has it open. Things to check: a reading app or "
            "preview pane showing the book, an antivirus scan, a OneDrive/Dropbox sync "
            "client, and Windows Security > Virus & threat protection > Ransomware "
            "protection > Controlled folder access (it can block this silently)."
        )
    parts.append("Nothing was changed.")
    return " ".join(parts)
