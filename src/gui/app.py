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
rather than reimplementing any of it. Every tab, including Analysis
(see gui.analysis_view, Phase 6), talks to the parser/analyzer/writer
directly via _load_analysis() below, working from the structured
AnalysisReport object rather than Engine.analyze()'s printed text.
_captured_output() still exists for the Repair tab's own summary of
what a repair pass actually changed.
"""
from __future__ import annotations

import io
import json
import mimetypes
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from ebook_fix import series as series_metadata
from ebook_fix.analyzer import EPUBAnalyzer
from ebook_fix.config import load_config
from ebook_fix.engine import Engine
from ebook_fix.parser import EPUBParser
from ebook_fix.splitter import SplitMarker
from ebook_fix.structure import SplitConfidence, analyze_structure, element_text_preview, iter_chapter_nodes
from ebook_fix.writer import EPUBWriter
from ebook_fix.apostrophes import analyze_book_possessives, apply_possessive_resolutions
from ebook_fix.color import analyze_book_color
from ebook_fix.modules.epub3_upgrade import EPUB3UpgradeRepair
from ebook_fix.modules.paragraph import ParagraphRepair
from ebook_fix.modules.chapter_markup import ChapterMarkupRepair
from ebook_fix.modules.toc_generation import TocGenerationRepair
from ebook_fix.modules.images import ImageRepair
from ebook_fix.modules.cover_repair import CoverRepair
from ebook_fix.modules.running_title_repair import RunningTitleRepair
from ebook_fix.modules.metadata_repair import MetadataSyncRepair
from ebook_fix.modules.identifier_repair import IdentifierStandardizeRepair
from ebook_fix.modules.author_initials_repair import AuthorInitialsRepair
from ebook_fix.modules.whitespace import WhitespaceRepair
from ebook_fix.modules.gutenberg_repair import GutenbergRepair
from ebook_fix.modules.ellipsis_repair import EllipsisRepair
from ebook_fix.modules.scene_break_repair import SceneBreakRepair
from ebook_fix.modules.apostrophe_repair import ApostropheRepair
from ebook_fix.modules.color_strip import ColorStripRepair
from gui import analysis_view
from metadata.core_fields import write_core_field
from metadata.language_codes import language_options

app = Flask(__name__)

SESSIONS_ROOT = Path(tempfile.gettempdir()) / "ebook_fix_gui_sessions"

# Core fields the Metadata tab shows as editable text -- same set
# metadata.merge already tracks as MergedField, minus "language".
# Language is still editable (see below), just not through this
# generic text-field loop -- it's a dropdown, and unlike these fields
# it's never a genuine EPUB-vs-Calibre mismatch to pick between (see
# language_codes.py), so it gets its own small block in both the
# template and the staging/apply logic here.
_EDITABLE_FIELDS = ("title", "author", "publisher", "date", "rights", "description")

# Every standard repair module that's actually toggleable via
# ebook_fix.toml -- i.e. everything Engine._build_modules() checks an
# `enabled` flag for. Order matches _build_modules()'s own pipeline
# order, so the Repair tab's checkbox list reads the same way the CLI
# already applies them. Deliberately excludes Class Standardize, which
# still needs an explicit --class-mapping file (see analysis_roadmap.md
# for why that stays a separate CLI subcommand). Color Strip used to
# be excluded here too -- it was auto-fix-only, unconditional, and
# didn't fit this config-gated checklist. Now that it's confidence-
# gated and lives in Engine._build_modules() like everything else here
# (see analysis_roadmap.md's 2026-09-11 follow-up), it belongs in this
# list the same as any other module.
_REPAIR_MODULES = [
    ("gutenberg_repair", "Gutenberg Boilerplate Removal"),
    ("running_title_repair", "Running Title Removal"),
    ("paragraph_repair", "Paragraph Repair"),
    ("chapter_markup", "Chapter Markup"),
    ("epub3_upgrade", "EPUB 3 Upgrade"),
    ("toc_generation", "TOC Generation"),
    ("scene_break_repair", "Scene Break Normalizer"),
    ("image_repair", "Image Repair"),
    ("cover_repair", "Cover Repair"),
    ("color_repair", "Color Strip"),
    ("ellipsis_repair", "Ellipsis Normalizer"),
    ("apostrophe_repair", "Apostrophe Repair"),
    ("whitespace_repair", "Whitespace Normalizer"),
    ("metadata_repair", "Metadata Sync (Calibre-managed books only)"),
    ("identifier_repair", "Identifier Standardize"),
    ("author_initials", "Author Initials"),
]

# attr (from _REPAIR_MODULES above) -> the same module class
# Engine._build_modules() would instantiate for it. Used by the
# Repair tab (Phase 6 follow-up) to show each module's own .analyze()
# count next to its checkbox and auto-uncheck anything that found
# nothing to do -- built as its own dict here rather than reusing
# Engine.modules, since Engine only builds the modules that are
# currently *enabled* in config, and this needs a count for every
# module regardless of its checked state.
_REPAIR_MODULE_CLASSES = {
    "gutenberg_repair": GutenbergRepair,
    "running_title_repair": RunningTitleRepair,
    "paragraph_repair": ParagraphRepair,
    "chapter_markup": ChapterMarkupRepair,
    "epub3_upgrade": EPUB3UpgradeRepair,
    "toc_generation": TocGenerationRepair,
    "scene_break_repair": SceneBreakRepair,
    "image_repair": ImageRepair,
    "cover_repair": CoverRepair,
    "color_repair": ColorStripRepair,
    "ellipsis_repair": EllipsisRepair,
    "apostrophe_repair": ApostropheRepair,
    "whitespace_repair": WhitespaceRepair,
    "metadata_repair": MetadataSyncRepair,
    "identifier_repair": IdentifierStandardizeRepair,
    "author_initials": AuthorInitialsRepair,
}


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


def _original_backup_path(session_dir: Path) -> Path:
    """Where replace_original() moves the pre-repair file to, so a
    Calibre-managed book can end up back under its own original
    filename (what Calibre's own database points at) without ever
    losing the untouched original outright. See replace_original()."""
    source = _source_path(session_dir)
    return source.with_name(source.stem + "_original" + source.suffix)


def _replaced_flag_path(session_dir: Path) -> Path:
    """Marks that this session has already gone through
    replace_original() -- the session's own real_path.txt still
    points at the same filename, but that file now holds the repaired
    content, not the original. Every tab that reads "before" content
    off _source_path() checks this flag first, so nothing silently
    treats post-replace content as the pre-repair original."""
    return session_dir / "replaced.flag"


def _staged_metadata_path(session_dir: Path) -> Path:
    return session_dir / "staged_metadata.json"


def _staged_review_path(session_dir: Path) -> Path:
    return session_dir / "staged_review.json"


def _split_mapping_path(session_dir: Path) -> Path:
    """Original href -> list of resulting hrefs, written by
    apply_repair() whenever a staged split actually runs -- see the
    Phase 7a scoping note in docs/gui_plan.md for why this can't be
    reconstructed after the fact from the fixed EPUB alone. GUI-only
    bookkeeping, same as staged_metadata.json/staged_review.json;
    the CLI's own split commands never touch this file."""
    return session_dir / "split_mapping.json"


def _read_staged(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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


def _possessive_groups(book):
    """Every possessive candidate worth showing a person, grouped by
    chapter -- same shape as _split_candidate_groups above, and for
    the same reason: ebook_fix.apostrophes.apply_possessive_resolutions
    re-derives these fresh from a live book rather than trusting
    anything that crossed the browser, so this only ever needs to
    produce ids that match what a fresh analyze_book_possessives()
    call against the same book content would produce."""
    summary = analyze_book_possessives(book)
    groups = []
    for chapter_summary in summary.chapters:
        if not chapter_summary.candidates:
            continue
        groups.append({
            "href": chapter_summary.href,
            "candidates": [
                {
                    "id": c.id,
                    "word": c.word,
                    "context": c.context,
                    "possessive_reading": c.possessive_reading,
                    "plural_reading": c.plural_reading,
                }
                for c in chapter_summary.candidates
            ],
        })
    return groups


def _color_review_groups(book, analysis_report=None):
    """Every review-bucket (not confident) color finding worth showing
    a person, grouped by href -- same shape as the two group builders
    above. Confident findings aren't included here at all: those are
    already handled unattended by ColorStripRepair, nothing to review.
    See ebook_fix.color's module docstring for the confident/review
    split and ColorStripRepair.apply_review_removals() for how an
    accepted id here actually gets removed later."""
    color = analysis_report.color if analysis_report is not None else analyze_book_color(book)
    by_href: dict = {}
    for finding in color.review:
        by_href.setdefault(finding.href, []).append(finding)

    groups = []
    for href, findings in by_href.items():
        groups.append({
            "href": href,
            "findings": [
                {
                    "id": f.id,
                    "location_kind": f.location_kind,
                    "context": f.context,
                    "value": f.value,
                    "reason": f.reason,
                }
                for f in findings
            ],
        })
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

    book, analysis_report = _load_analysis(session_dir)
    overview = analysis_view.build_overview(analysis_report)
    issues = analysis_view.build_issues(analysis_report)
    manual_review = analysis_view.build_manual_review(analysis_report)
    issue_count = sum(len(section.lines) for section in issues)

    return render_template(
        "book.html",
        active_tab="analysis",
        session_id=session_id,
        filename=filename,
        overview=overview,
        issues=issues,
        manual_review=manual_review,
        issue_count=issue_count,
    )


@app.route("/book/<session_id>/metadata")
def book_metadata(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    book, analysis_report = _load_analysis(session_dir)
    merged = analysis_report.merged_core_fields
    staged = _read_staged(_staged_metadata_path(session_dir))

    fields = []
    for name in _EDITABLE_FIELDS:
        mf = getattr(merged, name)
        value = staged["fields"][name] if staged else mf.display_value
        fields.append({
            "name": name,
            "label": name.replace("_", " ").title(),
            "value": value,
            "mismatch": mf.mismatch,
            "epub_value": mf.epub_value,
            "calibre_value": mf.calibre_value,
            "note": mf.note,
            "multiline": name == "description",
        })

    series_info = series_metadata.read(book)
    calibre_ctx = analysis_report.calibre_context

    # The EPUB's own current dc:language value, not merged.display_value
    # -- this dropdown edits the EPUB directly (write_core_field targets
    # the book, same as every other field here), so it should start on
    # what the EPUB actually has, not a value resolved from Calibre's
    # side of a comparison that was never a real disagreement to begin
    # with. Falls back to whichever side has something when the EPUB's
    # own field is simply blank.
    current_language = (staged.get("language") if staged else None) or merged.language.epub_value or merged.language.display_value

    return render_template(
        "metadata.html",
        active_tab="metadata",
        session_id=session_id,
        filename=filename,
        fields=fields,
        language_value=current_language,
        language_choices=language_options(current_language),
        language_note=merged.language.note,
        series_name=staged["series_name"] if staged else (series_info.name or ""),
        series_index=staged["series_index"] if staged else series_info.index,
        is_staged=staged is not None,
        is_calibre_managed=calibre_ctx.is_calibre_managed,
    )


@app.route("/book/<session_id>/metadata", methods=["POST"])
def save_metadata(session_id):
    session_dir = _session_dir(session_id)

    series_index_raw = request.form.get("series_index", "").strip()
    series_index = None
    if series_index_raw:
        try:
            series_index = float(series_index_raw)
        except ValueError:
            series_index = None

    staged = {
        "fields": {name: request.form.get(name, "") for name in _EDITABLE_FIELDS},
        "language": request.form.get("language", "").strip(),
        "series_name": request.form.get("series_name", "").strip(),
        "series_index": series_index,
    }
    _staged_metadata_path(session_dir).write_text(json.dumps(staged), encoding="utf-8")

    return redirect(url_for("book_metadata", session_id=session_id))


@app.route("/book/<session_id>/review")
def book_review(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    book = EPUBParser().load(_source_path(session_dir))
    groups = _split_candidate_groups(book)
    possessive_groups = _possessive_groups(book)
    color_groups = _color_review_groups(book)

    staged = _read_staged(_staged_review_path(session_dir))
    staged_possessive_resolutions = {}
    staged_color_ids = set()
    if staged is not None:
        staged_ids = set(staged.get("accepted_ids", []))
        for group in groups:
            for item in group["candidates"]:
                item["auto_checked"] = item["id"] in staged_ids
        staged_possessive_resolutions = staged.get("possessive_resolutions", {})
        staged_color_ids = set(staged.get("accepted_color_ids", []))

    for group in possessive_groups:
        for item in group["candidates"]:
            item["resolution"] = staged_possessive_resolutions.get(item["id"], "")
    for group in color_groups:
        for item in group["findings"]:
            item["accepted"] = item["id"] in staged_color_ids

    return render_template(
        "review.html",
        active_tab="review",
        session_id=session_id,
        filename=filename,
        groups=groups,
        has_candidates=bool(groups),
        possessive_groups=possessive_groups,
        has_possessive_candidates=bool(possessive_groups),
        color_groups=color_groups,
        has_color_findings=bool(color_groups),
        is_staged=staged is not None,
        split_result=None,
    )


@app.route("/book/<session_id>/review", methods=["POST"])
def save_review(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    book = EPUBParser().load(_source_path(session_dir))
    groups = _split_candidate_groups(book)
    possessive_groups = _possessive_groups(book)
    color_groups = _color_review_groups(book)

    accepted_ids = set(request.form.getlist("accept"))
    accepted_color_ids = set(request.form.getlist("color_accept"))
    possessive_resolutions = {}
    for group in possessive_groups:
        for item in group["candidates"]:
            resolution = request.form.get(f"poss_{item['id']}", "")
            if resolution in ("possessive", "plural"):
                possessive_resolutions[item["id"]] = resolution

    # Just a preview of what would happen -- the actual split only
    # happens when the Repair tab applies everything. A file needs 2+
    # accepted boundaries to split at all; anything short of that is
    # shown here so the choice can be corrected before staging it, not
    # silently dropped at apply time.
    would_split = 0
    skipped_hrefs = []
    for group in groups:
        accepted_nodes = [item for item in group["candidates"] if item["id"] in accepted_ids]
        if not accepted_nodes:
            continue
        if len(accepted_nodes) < 2:
            skipped_hrefs.append(group["href"])
        else:
            would_split += 1

    staged = {
        "accepted_ids": sorted(accepted_ids),
        "possessive_resolutions": possessive_resolutions,
        "accepted_color_ids": sorted(accepted_color_ids),
    }
    _staged_review_path(session_dir).write_text(json.dumps(staged), encoding="utf-8")

    for group in groups:
        for item in group["candidates"]:
            item["auto_checked"] = item["id"] in accepted_ids
    for group in possessive_groups:
        for item in group["candidates"]:
            item["resolution"] = possessive_resolutions.get(item["id"], "")
    for group in color_groups:
        for item in group["findings"]:
            item["accepted"] = item["id"] in accepted_color_ids

    return render_template(
        "review.html",
        active_tab="review",
        session_id=session_id,
        filename=filename,
        groups=groups,
        has_candidates=bool(groups),
        possessive_groups=possessive_groups,
        has_possessive_candidates=bool(possessive_groups),
        color_groups=color_groups,
        has_color_findings=bool(color_groups),
        is_staged=True,
        split_result={
            "would_split": would_split,
            "skipped_hrefs": skipped_hrefs,
        },
    )


@app.route("/book/<session_id>/repair")
def book_repair(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    config = load_config(None)

    # A book's own analysis, so each module's checkbox can show how
    # many issues it actually found in THIS book (not just whether
    # it's enabled in ebook_fix.toml) -- see docs/gui_plan.md, Phase 6
    # follow-up. Reusing the module classes here rather than
    # Engine.modules, since that only ever builds the ones already
    # enabled; a disabled module's count still needs to show up if a
    # person decides to check it on.
    book, analysis_report = _load_analysis(session_dir)

    modules = []
    for attr, label in _REPAIR_MODULES:
        module_config = getattr(config, attr)
        module_cls = _REPAIR_MODULE_CLASSES[attr]
        count = module_cls(module_config).analyze(book, analysis_report).count
        modules.append({
            "attr": attr,
            "label": label,
            "count": count,
            # Pre-checked from config, same as before -- but only when
            # there's actually something for it to do against this
            # book. A module config-enabled but with nothing to fix
            # (e.g. EPUB 3 Upgrade on a book that's already EPUB 3)
            # starts unchecked instead of running a no-op pass; still
            # toggleable by hand either way.
            "checked": module_config.enabled and count > 0,
        })

    staged_metadata = _read_staged(_staged_metadata_path(session_dir))
    staged_review = _read_staged(_staged_review_path(session_dir))
    staged_field_count = len(staged_metadata["fields"]) if staged_metadata else 0
    staged_boundary_count = len(staged_review["accepted_ids"]) if staged_review else 0
    staged_possessive_count = len(staged_review.get("possessive_resolutions", {})) if staged_review else 0
    staged_color_count = len(staged_review.get("accepted_color_ids", [])) if staged_review else 0

    replaced_flag = _replaced_flag_path(session_dir)
    already_replaced = replaced_flag.exists()

    return render_template(
        "repair.html",
        active_tab="repair",
        session_id=session_id,
        filename=filename,
        modules=modules,
        has_staged_metadata=staged_metadata is not None,
        has_staged_review=staged_review is not None,
        staged_field_count=staged_field_count,
        staged_boundary_count=staged_boundary_count,
        staged_possessive_count=staged_possessive_count,
        staged_color_count=staged_color_count,
        result=None,
        has_fixed=_fixed_output_path(session_dir).exists(),
        already_replaced=already_replaced,
        replaced_backup=replaced_flag.read_text(encoding="utf-8") if already_replaced else None,
    )


@app.route("/book/<session_id>/repair", methods=["POST"])
def apply_repair(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")

    selected = set(request.form.getlist("modules"))
    config = load_config(None)
    # metadata_repair's config-file `enabled` value controls a second,
    # unrelated thing besides whether MetadataSyncRepair runs against
    # the EPUB this pass: it's also the gate _sync_metadata_opf() and
    # _sync_metadata_db() check before pushing already-confirmed values
    # out to Calibre (see engine.py). The checkbox below auto-unchecks
    # itself whenever MetadataSyncRepair finds nothing to fix in the
    # EPUB -- which happens whenever the EPUB and metadata.opf already
    # agree -- and that says nothing about whether Calibre's own
    # metadata.opf/metadata.db should still get synced. Without this,
    # a book with clean metadata (the common case) would silently never
    # sync to Calibre at all, reporting "metadata_repair is disabled in
    # config" even with sync_calibre_opf/sync_calibre_db turned on.
    metadata_sync_enabled = config.metadata_repair.enabled
    for attr, _label in _REPAIR_MODULES:
        getattr(config, attr).enabled = attr in selected

    book = EPUBParser().load(_source_path(session_dir))

    # Staged metadata edits, if any -- applied directly the same way
    # save_metadata used to write immediately, just deferred to here so
    # it lands in the same file as everything else instead of its own
    # separate "_fixed.epub".
    staged_metadata = _read_staged(_staged_metadata_path(session_dir))
    if staged_metadata is not None:
        for name, value in staged_metadata["fields"].items():
            write_core_field(book, name, value)
        # .get(), not [] -- a session staged before Phase 5 shipped
        # won't have a "language" key at all, and that should just mean
        # "nothing to change" rather than a KeyError at apply time.
        language_value = staged_metadata.get("language")
        if language_value:
            write_core_field(book, "language", language_value)
        series_name = staged_metadata["series_name"]
        if series_name:
            series_metadata.write(book, series_name, staged_metadata["series_index"])

    # Staged split boundaries, if any -- re-derives candidates fresh
    # against the book as it exists right now (post-metadata-edit,
    # though metadata edits never affect chapter structure, so this is
    # really just "fresh" for its own sake, not because anything above
    # could have invalidated it) and applies only the ones that still
    # meet the 2-per-file bar.
    split_count = 0
    new_hrefs_by_origin = {}
    with _captured_output() as buf:
        staged_review = _read_staged(_staged_review_path(session_dir))
        if staged_review is not None:
            accepted_ids = set(staged_review["accepted_ids"])
            groups = _split_candidate_groups(book)
            markers_by_href = {}
            for group in groups:
                accepted_nodes = [item["node"] for item in group["candidates"] if item["id"] in accepted_ids]
                if len(accepted_nodes) < 2:
                    continue
                markers_by_href[group["href"]] = [
                    SplitMarker(element=node.evidence.candidate.element, title=node.title, number=node.evidence.candidate.number)
                    for node in accepted_nodes
                ]
            if markers_by_href:
                engine_for_split = Engine(config=config)
                split_count, _reports, new_hrefs_by_origin = engine_for_split._split_and_rewire(book, markers_by_href, details=False)

        # Staged possessive resolutions and decorative-color removals,
        # if any -- both re-derive fresh against the book as it exists
        # right now (post-split, since a split moves elements between
        # files but never changes their own text/attributes, so
        # candidate ids computed against the pre-split book still
        # resolve to the same live elements either way). See
        # ebook_fix.apostrophes.apply_possessive_resolutions and
        # ebook_fix.modules.color_strip.ColorStripRepair.
        # apply_review_removals for why re-deriving fresh (rather than
        # trusting anything computed back when the Review tab first
        # loaded) is safe and necessary here.
        possessive_resolved_count = 0
        color_review_count = 0
        if staged_review is not None:
            possessive_resolutions = staged_review.get("possessive_resolutions", {})
            if possessive_resolutions:
                possessive_resolved_count = apply_possessive_resolutions(book, possessive_resolutions)

            accepted_color_ids = staged_review.get("accepted_color_ids", [])
            if accepted_color_ids:
                color_review_report = ColorStripRepair(config.color_repair).apply_review_removals(book, accepted_color_ids)
                color_review_count = color_review_report.count

        # Fresh analysis, reflecting any staged metadata/split changes
        # applied above -- the repair modules below need to see the
        # book as it actually is right now, not as it was when the
        # Metadata/Review tabs were first opened.
        analysis_report = EPUBAnalyzer().analyze(book)

        engine = Engine(config=config)
        reports_by_module, pass_num = engine.run_selected_repairs(book, engine.modules, analysis_report, max_passes=5)
    engine_output = buf.getvalue()

    output_path = _fixed_output_path(session_dir)
    EPUBWriter().save(book, output_path)

    # Calibre sync (metadata.opf always, metadata.db only if
    # sync_calibre_db is turned on) -- reuses Engine's own private
    # methods rather than duplicating this logic a second time. This
    # was a real, pre-existing gap: engine.repair()/auto_fix() (the
    # CLI's own entry points) already called _sync_metadata_opf, but
    # the GUI's Apply Everything went through run_selected_repairs()
    # directly and never called it at all -- so Calibre sync silently
    # never happened from the GUI, for any book, until now. Only ever
    # called after the write above, same rule these two methods
    # already enforce themselves.
    #
    # Restore metadata_repair's real config-file `enabled` value first
    # -- see the comment above where this was captured. The checkbox
    # override above answers "did MetadataSyncRepair run against the
    # EPUB this pass," which is a different question from "is Calibre
    # syncing turned on," and these two sync calls only care about the
    # latter.
    config.metadata_repair.enabled = metadata_sync_enabled
    opf_sync_status = engine._sync_metadata_opf(analysis_report)
    db_sync_status = engine._sync_metadata_db(analysis_report)

    # Persisted purely for the Before/After tab (Phase 7b,
    # docs/gui_plan.md) -- a new chapter_004.xhtml's own filename never
    # reveals which original chapter it came from (see the Phase 7a
    # scoping note), so this is the one place that mapping actually
    # exists and the only chance to save it. Written even when empty,
    # overwriting any mapping left over from a previous Apply on this
    # same session, so Before/After never shows stale split info from
    # an earlier run that a person has since re-applied differently.
    _split_mapping_path(session_dir).write_text(json.dumps(new_hrefs_by_origin), encoding="utf-8")

    _staged_metadata_path(session_dir).unlink(missing_ok=True)
    _staged_review_path(session_dir).unlink(missing_ok=True)

    module_summaries = [
        {"name": name, "count": report.count}
        for name, report in reports_by_module.items()
        if report.count > 0
    ]

    modules = [
        {"attr": attr, "label": label, "checked": attr in selected}
        for attr, label in _REPAIR_MODULES
    ]

    return render_template(
        "repair.html",
        active_tab="repair",
        session_id=session_id,
        filename=filename,
        modules=modules,
        has_staged_metadata=False,
        has_staged_review=False,
        staged_field_count=0,
        staged_boundary_count=0,
        staged_possessive_count=0,
        staged_color_count=0,
        result={
            "output_path": str(output_path),
            "split_count": split_count,
            "possessive_resolved_count": possessive_resolved_count,
            "color_review_count": color_review_count,
            "module_summaries": module_summaries,
            "pass_count": pass_num,
            "engine_output": engine_output,
            "opf_sync": opf_sync_status,
            "db_sync": db_sync_status,
        },
        has_fixed=True,
        already_replaced=_replaced_flag_path(session_dir).exists(),
    )


@app.route("/book/<session_id>/replace-original", methods=["POST"])
def replace_original(session_id):
    """Swaps a repaired book back onto its own original filename, so
    Calibre (or anything else pointed at that exact path) picks up the
    fix without a person manually renaming anything themselves. Two
    plain os-level renames, in this order: the untouched original ->
    "<name>_original.epub" (a backup, never deleted automatically),
    then "<name>_fixed.epub" -> the original filename. Both files
    already live in the same folder, so each rename is a same-
    filesystem move -- atomic on every OS this project supports,
    never a copy-then-delete that could leave things half-done if
    interrupted.

    Refuses if a backup already exists at that name rather than
    overwriting it -- a leftover "_original.epub" almost always means
    this session already replaced once before, and silently
    overwriting it would throw away whichever version came before
    that. A person can rename or delete the old backup by hand and
    retry if that's genuinely what they want."""
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")

    fixed_path = _fixed_output_path(session_dir)
    if not fixed_path.exists():
        abort(404)

    def _replace_error(message: str):
        return render_template(
            "repair.html",
            active_tab="repair",
            session_id=session_id,
            filename=filename,
            modules=[
                {"attr": attr, "label": label, "checked": False}
                for attr, label in _REPAIR_MODULES
            ],
            has_staged_metadata=False,
            has_staged_review=False,
            staged_field_count=0,
            staged_boundary_count=0,
            result=None,
            has_fixed=True,
            replace_error=message,
        )

    source_path = _source_path(session_dir)
    backup_path = _original_backup_path(session_dir)
    if backup_path.exists():
        return _replace_error(
            f"A backup already exists at {backup_path} -- not overwriting it. "
            "Move or delete that file first if you're sure you want to replace again."
        )

    # Both renames are same-filesystem moves and should be near-instant,
    # but a book file can still be locked by something else on Windows
    # (Calibre's own viewer, another reader, an antivirus scan, even an
    # Explorer preview pane) -- os.rename surfaces that as a bare
    # PermissionError/OSError with no context. Caught here so a lock
    # shows up as a clear, actionable message instead of a 500 page,
    # the same posture calibredb_write.py already takes toward a
    # locked Calibre library.
    try:
        source_path.rename(backup_path)
    except OSError as exc:
        return _replace_error(
            f"Couldn't rename the original file to make a backup: {exc}. "
            "This usually means something else has the book open -- Calibre's "
            "own viewer, another reader, or an antivirus scan -- close it and "
            "try again. Nothing was changed."
        )

    try:
        fixed_path.rename(source_path)
    except OSError as exc:
        # The backup rename above already succeeded, so roll it back
        # rather than leaving the book missing under its original name
        # with a stray "_original.epub" sitting next to it.
        try:
            backup_path.rename(source_path)
            rollback_note = ""
        except OSError as rollback_exc:
            rollback_note = (
                f" Additionally, restoring the original from {backup_path} also "
                f"failed ({rollback_exc}) -- the original is safe at that path, "
                "but needs to be renamed back by hand."
            )
        return _replace_error(
            f"Couldn't move the repaired file into place: {exc}.{rollback_note} "
            "This usually means something else has the book open -- close it and "
            "try again."
        )

    _replaced_flag_path(session_dir).write_text(str(backup_path), encoding="utf-8")

    return render_template(
        "repair.html",
        active_tab="repair",
        session_id=session_id,
        filename=filename,
        modules=[
            {"attr": attr, "label": label, "checked": False}
            for attr, label in _REPAIR_MODULES
        ],
        has_staged_metadata=False,
        has_staged_review=False,
        staged_field_count=0,
        staged_boundary_count=0,
        result=None,
        has_fixed=False,
        already_replaced=True,
        replaced_path=str(source_path),
        replaced_backup=str(backup_path),
    )


@app.route("/book/<session_id>/before-after")
def book_before_after(session_id):
    """Compares the original book against the most recent
    "<n>_fixed.epub", chapter by chapter -- see docs/gui_plan.md,
    Phase 7a, for the three-case scoping this route implements:
    unchanged (same href both sides), split (one original href, one or
    more resulting hrefs -- see split_mapping.json), and removed
    (dropped entirely by a repair like Gutenberg Boilerplate Removal's
    whole-file cleanup, detected here by simple href-set diffing since
    that case needs no persisted mapping at all)."""
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")

    if _replaced_flag_path(session_dir).exists():
        return render_template(
            "before_after.html",
            active_tab="before_after",
            session_id=session_id,
            filename=filename,
            has_fixed=False,
            already_replaced=True,
            replaced_backup=_replaced_flag_path(session_dir).read_text(encoding="utf-8"),
        )

    fixed_path = _fixed_output_path(session_dir)
    if not fixed_path.exists():
        return render_template(
            "before_after.html",
            active_tab="before_after",
            session_id=session_id,
            filename=filename,
            has_fixed=False,
        )

    original_book = EPUBParser().load(_source_path(session_dir))
    fixed_book = EPUBParser().load(fixed_path)
    fixed_hrefs = {c.href for c in fixed_book.chapters}

    split_mapping = {}
    mapping_path = _split_mapping_path(session_dir)
    if mapping_path.exists():
        split_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))

    chapters = []
    for c in original_book.chapters:
        if c.href in split_mapping:
            after_options = [c.href] + split_mapping[c.href]
            status = "split"
        elif c.href not in fixed_hrefs:
            after_options = []
            status = "removed"
        else:
            after_options = [c.href]
            status = "unchanged"
        chapters.append({
            "href": c.href,
            "title": c.title or c.href,
            "status": status,
            "after_options": after_options,
        })

    if not chapters:
        return render_template(
            "before_after.html",
            active_tab="before_after",
            session_id=session_id,
            filename=filename,
            has_fixed=True,
            chapters=[],
        )

    requested_href = request.args.get("href")
    selected = next((c for c in chapters if c["href"] == requested_href), chapters[0])

    requested_after = request.args.get("after_href")
    if requested_after in selected["after_options"]:
        after_href = requested_after
    else:
        after_href = selected["after_options"][0] if selected["after_options"] else None

    return render_template(
        "before_after.html",
        active_tab="before_after",
        session_id=session_id,
        filename=filename,
        has_fixed=True,
        chapters=chapters,
        selected=selected,
        after_href=after_href,
    )


@app.route("/book/<session_id>/asset/<side>/<path:href>")
def book_asset(session_id, side, href):
    """Serves one file's raw bytes straight out of the original
    ("before") or fixed ("after") EPUB's own zip archive -- used as the
    src for the Before/After tab's two <iframe>s. href is resolved
    relative to that archive's own OPF directory, the same convention
    every href elsewhere in this project already follows (see
    parser.py's _read_toc for the same `base / href` idiom), which is
    what lets a chapter's own relative <img src="../images/x.jpg"> or
    <link href="../css/y.css"> resolve back through this exact route
    automatically -- the browser does that relative-path resolution
    against the iframe's own URL before ever asking the server for
    anything, so this route never needs to know in advance which
    assets belong to which chapter. Read-only, and only ever reads
    from a session's own two known files (the original and its
    "_fixed.epub"), never an arbitrary path."""
    if side not in ("before", "after"):
        abort(404)

    session_dir = _session_dir(session_id)
    epub_path = _source_path(session_dir) if side == "before" else _fixed_output_path(session_dir)
    if not epub_path.exists():
        abort(404)

    book = EPUBParser().load(epub_path)
    base = PurePosixPath(book.package_path).parent
    full_path = str(base / href) if str(base) != "." else href

    try:
        with zipfile.ZipFile(epub_path, "r") as archive:
            data = archive.read(full_path)
    except KeyError:
        abort(404)

    media_type = mimetypes.guess_type(href)[0] or "application/octet-stream"
    return Response(data, mimetype=media_type)


def _open_browser_soon():
    time.sleep(1.0)
    webbrowser.open("http://127.0.0.1:5000")


def main():
    print("ebook_fix GUI starting at http://127.0.0.1:5000 -- close this window to stop it.")
    threading.Thread(target=_open_browser_soon, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()

