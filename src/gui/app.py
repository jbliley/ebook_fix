"""
gui.app

Phase 1 of the GUI -- see docs/gui_plan.md. A local Flask app that
runs entirely on your own machine (localhost); nothing here is sent
over the internet. Launch it via run_gui.bat / run_gui.py at the repo
root, or `python -m gui.app` once the package is installed.

This calls the exact same ebook_fix.engine.Engine.analyze() the CLI's
`analyze` command already calls -- see cli.py's own dispatch -- so
there's a single source of truth for what "analyze" means. The GUI is
a new way to drive engine.py, not a second implementation of it.

Phase 1 deliberately stops at "can the browser show what analyze
already knows": the analysis output is captured exactly as the CLI
would print it and shown as plain text, rather than being reshaped
into structured JSON yet. That reshaping is Phase 2/3's job, once the
Metadata and Review tabs exist and it's clear exactly what each one
needs from the analysis report.
"""
from __future__ import annotations

import io
import os
import tempfile
import threading
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, render_template, request

from ebook_fix.config import load_config
from ebook_fix.engine import Engine

app = Flask(__name__)
# An EPUB can legitimately run well over 50MB (lots of embedded
# images); 200MB is a generous ceiling that still catches someone
# accidentally uploading the wrong kind of file.
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024


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


@app.route("/analyze", methods=["POST"])
def analyze():
    uploaded = request.files.get("epub_file")
    if uploaded is None or uploaded.filename == "":
        return render_template("index.html", error="Choose an EPUB file first.")
    if not uploaded.filename.lower().endswith(".epub"):
        return render_template("index.html", error="That doesn't look like an EPUB file (expected a .epub).")

    # mkstemp (create + close immediately) rather than
    # NamedTemporaryFile, which keeps its own handle open -- Windows
    # won't let Werkzeug's .save() write to a path that's still held
    # open by another handle in the same process.
    fd, tmp_name = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        uploaded.save(tmp_path)

        config = load_config(None)
        engine = Engine(config=config)
        with _captured_output() as buf:
            engine.analyze(tmp_path, details=True)
        output = buf.getvalue()
    finally:
        cache_path = tmp_path.with_name(tmp_path.stem + ".ebookfix-analysis.json")
        cache_path.unlink(missing_ok=True)
        tmp_path.unlink(missing_ok=True)

    return render_template("results.html", filename=uploaded.filename, output=output)


def _open_browser_soon():
    time.sleep(1.0)
    webbrowser.open("http://127.0.0.1:5000")


def main():
    print("ebook_fix GUI starting at http://127.0.0.1:5000 -- close this window to stop it.")
    threading.Thread(target=_open_browser_soon, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
