"""
gui.app

Phases 1-3 of the GUI (Analysis, Metadata, Review tabs) -- see
docs/gui_plan.md. A local Flask app that runs entirely on your own
machine (localhost); nothing here is sent over the internet. Launch
it via run_gui.bat / run_gui.py at the repo root, or `python -m
gui.app` once the package is installed.

A book is opened by real file path -- picked via a native OS file
dialog (see browse() below), never by browser upload. This isn't just
a UI preference: a browser's own <input type="file"> can only ever
hand the server raw bytes, never a real location, and Calibre
detection needs that real location to walk up looking for
metadata.db. See docs/gui_plan.md, "Bug fix -- Calibre detection and
save location," for the full story of why this app doesn't support
upload-by-bytes at all.

Session model: each opened book gets a session id (a folder name
under the system temp directory, holding just a pointer to the real
file, never a copy of it) so the Analysis, Metadata, Review, and
Before/After tabs can all work against the same book across several
requests. There's no login and no cleanup job yet -- this is a
single-user local tool, and an old session folder left in the temp
directory is harmless clutter, not a real problem, but it's a known
gap worth fixing before this goes much further (see docs/gui_plan.md).

This calls the same building blocks the CLI already uses --
ebook_fix.parser.EPUBParser, ebook_fix.analyzer.EPUBAnalyzer,
ebook_fix.writer.EPUBWriter, metadata.core_fields.write_core_field --
rather than reimplementing any of it. The Analysis tab specifically
still goes through ebook_fix.engine.Engine.analyze() for its printed
summary (see _captured_output() below); the Metadata tab talks to the
parser/analyzer/writer directly, since it needs the structured
AnalysisReport object, not printed text.
"""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for

from ebook_fix import series as series_metadata
from ebook_fix.analyzer import EPUBAnalyzer
from ebook_fix.config import load_config
from ebook_fix.engine import Engine
from ebook_fix.parser import EPUBParser
from ebook_fix.splitter import SplitMarker
from ebook_fix.structure import SplitConfidence, analyze_structure, element_text_preview, iter_chapter_nodes
from ebook_fix.writer import EPUBWriter
from metadata.core_fields import write_core_field

app = Flask(__name__)

SESSIONS_ROOT = Path(tempfile.gettempdir()) / "ebook_fix_gui_sessions"

# Core fields the Metadata tab shows as editable text -- same set
# metadata.merge already tracks as MergedField, minus "language"
# (both sides are already correct for their own format, see
# language_codes.py -- there's never anything to write there).
_EDITABLE_FIELDS = ("title", "author", "publisher", "date", "rights", "description")


def _session_dir(session_id: str) -> Path:
    """Resolves a session id to its folder, refusing anything that
    isn't a plain hex uuid -- session_id comes straight from the URL,
    so this is what keeps a crafted path (e.g. "../../etc") from
    escaping SESSIONS_ROOT."""
    try:
        uuid.UUID(session_id, version=4)
    except ValueError:
        abort(404)
    path = SESSIONS_ROOT / session_id
    if not path.is_dir():
        abort(404)
    return path


def _source_path(session_dir: Path) -> Path:
    """Every session is opened by a real file path now (the Browse
    button gets one from a native OS dialog; see browse() below) --
    a browser's own <input type="file"> can only ever hand over
    bytes, never a real location, which is exactly why Calibre
    detection couldn't work before this existed: calibre_detect.py
    walks up from the book's own path looking for metadata.db and
    Calibre's folder-naming convention, and a copy sitting in a temp
    folder can never be recognized as Calibre-managed no matter what
    it contains. See docs/gui_plan.md, "Bug fix -- Calibre detection
    and save location"."""
    return Path((session_dir / "real_path.txt").read_text(encoding="utf-8"))


def _load_analysis(session_dir: Path):
    """Loads the book and runs the same EPUBAnalyzer pass engine.py's
    analyze() uses internally, returning (book, analysis_report). Used
    by every tab that needs structured data rather than printed text."""
    book = EPUBParser().load(_source_path(session_dir))
    analysis_report = EPUBAnalyzer().analyze(book)
    return book, analysis_report


def _fixed_output_path(session_dir: Path) -> Path:
    """Where a fix gets written: right next to the original file as
    "<name>_fixed.epub" -- the same default the CLI's own repair
    command uses when no -o is given. Since every session now knows
    the book's real location, this is the only path a fix is ever
    written to -- no browser download step, the file's just already
    in the right folder."""
    source = _source_path(session_dir)
    return source.with_name(source.stem + "_fixed" + source.suffix)


def _split_candidate_groups(book):
    """Every chapter-start boundary worth showing a person, grouped by
    the file it's in. A file needs 2+ candidates to be split at all
    (a single boundary has nothing to cut it against) -- same gate
    Engine.split_chapters() already uses -- so a file with just one is
    left out of the list entirely; there's genuinely nothing to review
    there yet.

    Each item carries the live StructureNode (under "node") alongside
    the template-facing fields, so save_review() below can rebuild the
    exact same groups from a fresh copy of the book and match the
    person's accepted checkbox ids back to real elements to split at
    -- the node itself never round-trips through the browser."""
    tree = analyze_structure(book)
    by_href: dict[str, list] = {}
    for node in iter_chapter_nodes(tree):
        if node.evidence is None or node.evidence.confidence == SplitConfidence.NONE:
            continue
        by_href.setdefault(node.start_href, []).append(node)

    groups = []
    for href, nodes in by_href.items():
        if len(nodes) < 2:
            continue
        candidates = []
        for i, node in enumerate(nodes):
            confidence = node.evidence.confidence
            candidates.append({
                "id": f"{href}::{i}",
                "title": node.title or "(untitled)",
                "confidence": confidence.value,
                # CORROBORATED is the only level the project's own
                # split-safety-bar considers safe to apply without a
                # person looking (see docs/split_safety_bar.md) -- so
                # it's the only one pre-checked. SEQUENCE_ONLY and
                # NEEDS_REVIEW still show up, but require an active
                # choice.
                "auto_checked": confidence == SplitConfidence.CORROBORATED,
                "notes": node.evidence.notes,
                "preview": element_text_preview(node.evidence.candidate.element),
                "node": node,
            })
        groups.append({"href": href, "candidates": candidates})
    return groups


@contextmanager
def _captured_output():
    """Engine.analyze() and the modules it calls into
    (report.py/validation.py/container_repair.py) each construct
    their own module-level rich Console and print straight to it --
    written for a terminal, since that's all that existed before this
    GUI. Rather than touching any of that printing code, this
    temporarily points all four of those Console objects at one
    dedicated, color-disabled buffer for the duration of a single
    request, then puts the originals back. What analyze() actually
    does doesn't change at all; only where its existing output goes.
    """
    import ebook_fix.container_repair as container_repair_mod
    import ebook_fix.engine as engine_mod
    import ebook_fix.report as report_mod
    import ebook_fix.validation as validation_mod
    from rich.console import Console

    buf = io.StringIO()
    capture_console = Console(file=buf, no_color=True, force_terminal=False, width=100)

    modules = (engine_mod, report_mod, validation_mod, container_repair_mod)
    originals = [m.console for m in modules]
    for m in modules:
        m.console = capture_console
    try:
        yield buf
    finally:
        for m, original in zip(modules, originals):
            m.console = original


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/browse", methods=["POST"])
def browse():
    """Opens a real OS file-picker dialog on this machine and returns
    the chosen path as JSON, so opening a book means one familiar
    Browse button rather than a typed path -- a browser's own
    <input type="file"> can never hand back a real path (a deliberate
    browser security restriction), which is exactly why Calibre
    detection couldn't work before this existed; see the "Bug fix --
    Calibre detection and save location" entry in docs/gui_plan.md.

    Runs the dialog in a short-lived subprocess (a plain `python -c`
    call with tkinter, which ships with a standard Python install)
    rather than in-process, since tkinter's own event loop doesn't mix
    well with Flask's request-handling threads. If tkinter isn't
    available at all, this fails with a clear message rather than a
    silent hang."""
    script = (
        "import tkinter, tkinter.filedialog, sys\n"
        "root = tkinter.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "path = tkinter.filedialog.askopenfilename(\n"
        "    title='Choose an EPUB file',\n"
        "    filetypes=[('EPUB files', '*.epub'), ('All files', '*.*')],\n"
        ")\n"
        "sys.stdout.write(path)\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=180,
        )
    except Exception as exc:
        return {"error": f"Couldn't open the file browser: {exc}"}

    if result.returncode != 0:
        return {"error": "Couldn't open the file browser -- is tkinter installed with your Python?"}

    return {"path": result.stdout.strip()}


@app.route("/upload", methods=["POST"])
def upload():
    typed_path = request.form.get("path", "").strip().strip('"')
    if not typed_path:
        return render_template("index.html", error="No file was chosen.")

    path_obj = Path(typed_path)
    if not path_obj.is_file():
        return render_template("index.html", error=f"Can't find that file: {typed_path}")
    if path_obj.suffix.lower() != ".epub":
        return render_template("index.html", error="That doesn't look like an EPUB file (expected a .epub).")

    session_id = str(uuid.uuid4())
    session_dir = SESSIONS_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "real_path.txt").write_text(str(path_obj.resolve()), encoding="utf-8")
    (session_dir / "original_filename.txt").write_text(path_obj.name, encoding="utf-8")
    return redirect(url_for("book_analysis", session_id=session_id))


@app.route("/book/<session_id>")
def book_analysis(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    details = request.args.get("details") == "1"

    config = load_config(None)
    engine = Engine(config=config)
    with _captured_output() as buf:
        engine.analyze(_source_path(session_dir), details=details)
    output = buf.getvalue()

    # analyze() writes its cache file next to whatever path it's given.
    # For an uploaded session that's inside the session folder,
    # harmless to leave. For a real-path session, that's a real
    # ".ebookfix-analysis.json" sitting next to the actual book on
    # disk -- exactly the same file the CLI's own `analyze` command
    # already leaves behind next to any book it's pointed at, so this
    # isn't new GUI-only clutter, just the existing convention.

    return render_template(
        "book.html",
        active_tab="analysis",
        session_id=session_id,
        filename=filename,
        output=output,
        details=details,
    )


@app.route("/book/<session_id>/metadata")
def book_metadata(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    book, analysis_report = _load_analysis(session_dir)
    merged = analysis_report.merged_core_fields

    fields = []
    for name in _EDITABLE_FIELDS:
        mf = getattr(merged, name)
        fields.append({
            "name": name,
            "label": name.replace("_", " ").title(),
            "value": mf.display_value,
            "mismatch": mf.mismatch,
            "epub_value": mf.epub_value,
            "calibre_value": mf.calibre_value,
            "note": mf.note,
            "multiline": name == "description",
        })

    series_info = series_metadata.read(book)
    calibre_ctx = analysis_report.calibre_context

    return render_template(
        "metadata.html",
        active_tab="metadata",
        session_id=session_id,
        filename=filename,
        fields=fields,
        language=merged.language.display_value or "(none found)",
        series_name=series_info.name or "",
        series_index=series_info.index,
        saved=request.args.get("saved") == "1",
        saved_path=request.args.get("saved_path", ""),
        is_calibre_managed=calibre_ctx.is_calibre_managed,
    )


@app.route("/book/<session_id>/metadata", methods=["POST"])
def save_metadata(session_id):
    session_dir = _session_dir(session_id)
    book = EPUBParser().load(_source_path(session_dir))

    for name in _EDITABLE_FIELDS:
        value = request.form.get(name, "")
        write_core_field(book, name, value)

    series_name = request.form.get("series_name", "").strip()
    series_index_raw = request.form.get("series_index", "").strip()
    if series_name:
        series_index = None
        if series_index_raw:
            try:
                series_index = float(series_index_raw)
            except ValueError:
                series_index = None
        series_metadata.write(book, series_name, series_index)

    output_path = _fixed_output_path(session_dir)
    EPUBWriter().save(book, output_path)
    return redirect(url_for("book_metadata", session_id=session_id, saved="1", saved_path=str(output_path)))


@app.route("/book/<session_id>/review")
def book_review(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    book = EPUBParser().load(_source_path(session_dir))
    groups = _split_candidate_groups(book)

    return render_template(
        "review.html",
        active_tab="review",
        session_id=session_id,
        filename=filename,
        groups=groups,
        has_candidates=bool(groups),
        split_result=None,
    )


@app.route("/book/<session_id>/review", methods=["POST"])
def save_review(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    book = EPUBParser().load(_source_path(session_dir))
    groups = _split_candidate_groups(book)

    accepted_ids = set(request.form.getlist("accept"))

    markers_by_href = {}
    skipped_hrefs = []
    for group in groups:
        accepted_nodes = [item["node"] for item in group["candidates"] if item["id"] in accepted_ids]
        if not accepted_nodes:
            continue
        if len(accepted_nodes) < 2:
            # Splitting needs at least 2 boundaries in the same file --
            # one accepted checkbox alone has nothing to cut against.
            skipped_hrefs.append(group["href"])
            continue
        markers_by_href[group["href"]] = [
            SplitMarker(element=node.evidence.candidate.element, title=node.title, number=node.evidence.candidate.number)
            for node in accepted_nodes
        ]

    engine = Engine(config=load_config(None))
    output_path = _fixed_output_path(session_dir)
    with _captured_output() as buf:
        split_count, _reports = engine.split_marked(book, output_path, markers_by_href, details=False)
    output = buf.getvalue()

    return render_template(
        "review.html",
        active_tab="review",
        session_id=session_id,
        filename=filename,
        groups=_split_candidate_groups(EPUBParser().load(_source_path(session_dir))),
        has_candidates=bool(groups),
        split_result={
            "split_count": split_count,
            "skipped_hrefs": skipped_hrefs,
            "output": output,
            "saved_path": str(output_path) if split_count > 0 else "",
        },
    )


def _open_browser_soon():
    time.sleep(1.0)
    webbrowser.open("http://127.0.0.1:5000")


def main():
    print("ebook_fix GUI starting at http://127.0.0.1:5000 -- close this window to stop it.")
    threading.Thread(target=_open_browser_soon, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()

