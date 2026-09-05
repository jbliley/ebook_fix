"""
gui.app

Phase 1 (analysis) and Phase 2 (Metadata tab) of the GUI -- see
docs/gui_plan.md. A local Flask app that runs entirely on your own
machine (localhost); nothing here is sent over the internet. Launch
it via run_gui.bat / run_gui.py at the repo root, or `python -m
gui.app` once the package is installed.

Session model: each uploaded book gets a session id (a folder name
under the system temp directory) so the Analysis, Metadata, Review,
and Before/After tabs can all work against the same book across
several requests without re-uploading. There's no login and no
cleanup job yet -- this is a single-user local tool, and an old
session folder left in the temp directory is harmless clutter, not a
real problem, but it's a known gap worth fixing before this goes much
further (see docs/gui_plan.md).

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
import tempfile
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from ebook_fix import series as series_metadata
from ebook_fix.analyzer import EPUBAnalyzer
from ebook_fix.config import load_config
from ebook_fix.engine import Engine
from ebook_fix.parser import EPUBParser
from ebook_fix.writer import EPUBWriter
from metadata.core_fields import write_core_field

app = Flask(__name__)
# An EPUB can legitimately run well over 50MB (lots of embedded
# images); 200MB is a generous ceiling that still catches someone
# accidentally uploading the wrong kind of file.
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

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


def _load_analysis(session_dir: Path):
    """Loads the book and runs the same EPUBAnalyzer pass engine.py's
    analyze() uses internally, returning (book, analysis_report). Used
    by every tab that needs structured data rather than printed text."""
    book = EPUBParser().load(session_dir / "original.epub")
    analysis_report = EPUBAnalyzer().analyze(book)
    return book, analysis_report


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


@app.route("/upload", methods=["POST"])
def upload():
    uploaded = request.files.get("epub_file")
    if uploaded is None or uploaded.filename == "":
        return render_template("index.html", error="Choose an EPUB file first.")
    if not uploaded.filename.lower().endswith(".epub"):
        return render_template("index.html", error="That doesn't look like an EPUB file (expected a .epub).")

    session_id = str(uuid.uuid4())
    session_dir = SESSIONS_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    uploaded.save(session_dir / "original.epub")
    (session_dir / "original_filename.txt").write_text(uploaded.filename, encoding="utf-8")

    return redirect(url_for("book_analysis", session_id=session_id))


@app.route("/book/<session_id>")
def book_analysis(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    details = request.args.get("details") == "1"

    config = load_config(None)
    engine = Engine(config=config)
    with _captured_output() as buf:
        engine.analyze(session_dir / "original.epub", details=details)
    output = buf.getvalue()

    # analyze() writes its cache file next to whatever path it's given
    # -- here that's inside the session folder, so it's harmless to
    # leave (cleaned up whenever the whole session folder eventually
    # is), unlike Phase 1's per-request temp file which had nowhere
    # else to live.

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
    )


@app.route("/book/<session_id>/metadata", methods=["POST"])
def save_metadata(session_id):
    session_dir = _session_dir(session_id)
    book = EPUBParser().load(session_dir / "original.epub")

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

    EPUBWriter().save(book, session_dir / "output.epub")

    return redirect(url_for("book_metadata", session_id=session_id, saved="1"))


@app.route("/book/<session_id>/download")
def download(session_id):
    session_dir = _session_dir(session_id)
    output_path = session_dir / "output.epub"
    if not output_path.exists():
        abort(404)
    original_name = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    download_name = Path(original_name).stem + "_fixed.epub"
    return send_file(output_path, as_attachment=True, download_name=download_name)


def _open_browser_soon():
    time.sleep(1.0)
    webbrowser.open("http://127.0.0.1:5000")


def main():
    print("ebook_fix GUI starting at http://127.0.0.1:5000 -- close this window to stop it.")
    threading.Thread(target=_open_browser_soon, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()

