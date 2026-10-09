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
import base64
import json
import mimetypes
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
import zipfile
from contextlib import contextmanager
from functools import wraps
from pathlib import Path, PurePosixPath

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

from ebook_fix import series as series_metadata
from ebook_fix.analyzer import EPUBAnalyzer
from ebook_fix.config import load_config
from ebook_fix.engine import Engine, FIXED_LAYOUT_RISKY_MODULE_TYPES
from ebook_fix.parser import EPUBParser
from ebook_fix.splitter import SplitMarker, marker_number
from ebook_fix.structure import SplitConfidence, analyze_structure, element_text_preview, _walk_structure_nodes, NodeKind
from ebook_fix.writer import EPUBWriter
from ebook_fix.apostrophes import analyze_book_possessives, apply_possessive_resolutions
from ebook_fix.color import analyze_book_color
from ebook_fix.fonts import analyze_book_font_usage
from ebook_fix.frontmatter import analyze_book_frontmatter, MatterLabel, FRONT_ZONE, BACK_ZONE
from ebook_fix import cover as cover_module
from ebook_fix import isbn_lookup
from ebook_fix.mobi.analyzer import MOBI_EXTENSIONS
from ebook_fix.mobi.convert import convert_mobi_to_epub
from ebook_fix.mobi.reader import MobiError
from ebook_fix.fb2.analyzer import FB2_EXTENSIONS
from ebook_fix.fb2.convert import convert_fb2_to_epub
from ebook_fix.fb2.reader import Fb2Error
from ebook_fix.modules.epub3_upgrade import EPUB3UpgradeRepair
from ebook_fix.modules.paragraph import ParagraphRepair
from ebook_fix.modules.linebreak_repair import LineBreakRepair
from ebook_fix.modules.chapter_markup import ChapterMarkupRepair
from ebook_fix.modules.chapter_title_cleanup import ChapterTitleCleanupRepair
from ebook_fix.modules.dead_link_repair import DeadLinkRepair
from ebook_fix.modules.toc_cleanup import TocCleanupRepair
from ebook_fix.modules.toc_generation import TocGenerationRepair
from ebook_fix.modules.images import ImageRepair
from ebook_fix.modules.cover_repair import CoverRepair
from ebook_fix.modules.running_title_repair import RunningTitleRepair
from ebook_fix.modules.metadata_repair import MetadataSyncRepair
from ebook_fix.modules.identifier_repair import IdentifierStandardizeRepair
from ebook_fix.modules.author_initials_repair import AuthorInitialsRepair
from ebook_fix.modules.title_cleanup_repair import TitleCleanupRepair
from ebook_fix.modules.whitespace import WhitespaceRepair
from ebook_fix.modules.gutenberg_repair import GutenbergRepair
from ebook_fix.modules.ellipsis_repair import EllipsisRepair
from ebook_fix.modules.scene_break_repair import SceneBreakRepair
from ebook_fix.modules.scene_opener_repair import SceneOpenerRepair
from ebook_fix.modules.paragraph_spacing_repair import ParagraphSpacingRepair
from ebook_fix.modules.apostrophe_repair import ApostropheRepair
from ebook_fix.modules.color_strip import ColorStripRepair
from ebook_fix.modules.font_strip import FontStripRepair
from gui import metadata_preview as metadata_preview_module
from gui import overview_view
from metadata.core_fields import write_core_field
from metadata import calibre_detect
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
    ("linebreak_repair", "Stray Line Break Removal"),
    ("paragraph_repair", "Paragraph Repair"),
    ("chapter_markup", "Chapter Markup"),
    ("chapter_title_cleanup", "Chapter Title Cleanup"),
    ("toc_cleanup", "TOC Cleanup"),
    ("dead_link_repair", "Dead Link Cleanup"),
    ("epub3_upgrade", "EPUB 3 Upgrade"),
    ("toc_generation", "TOC Generation"),
    ("scene_break_repair", "Scene Break Normalizer"),
    ("scene_opener_repair", "Scene Opener Markers"),
    ("paragraph_spacing_repair", "Paragraph Spacing"),
    ("image_repair", "Image Repair"),
    ("cover_repair", "Cover Repair"),
    ("color_repair", "Color Strip"),
    ("font_repair", "Font Strip"),
    ("ellipsis_repair", "Ellipsis Normalizer"),
    ("apostrophe_repair", "Apostrophe Repair"),
    ("whitespace_repair", "Whitespace Normalizer"),
    ("title_cleanup", "Title Cleanup"),
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
    "linebreak_repair": LineBreakRepair,
    "paragraph_repair": ParagraphRepair,
    "chapter_markup": ChapterMarkupRepair,
    "chapter_title_cleanup": ChapterTitleCleanupRepair,
    "toc_cleanup": TocCleanupRepair,
    "dead_link_repair": DeadLinkRepair,
    "epub3_upgrade": EPUB3UpgradeRepair,
    "toc_generation": TocGenerationRepair,
    "scene_break_repair": SceneBreakRepair,
    "scene_opener_repair": SceneOpenerRepair,
    "paragraph_spacing_repair": ParagraphSpacingRepair,
    "image_repair": ImageRepair,
    "cover_repair": CoverRepair,
    "color_repair": ColorStripRepair,
    "font_repair": FontStripRepair,
    "ellipsis_repair": EllipsisRepair,
    "apostrophe_repair": ApostropheRepair,
    "whitespace_repair": WhitespaceRepair,
    "title_cleanup": TitleCleanupRepair,
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


def _handle_missing_source(view_func):
    """Decorator for any /book/<session_id>/... route that reads the
    book off disk via _source_path()/_load_analysis(). Both of those
    trust real_path.txt without checking the file's still there -- if
    it's been deleted, moved, or renamed while this session's tabs
    were still open (cleaning up downloads, "Replace original file"'s
    own backup/rename dance, or anything else touching the file
    outside this program), that surfaces as a bare FileNotFoundError
    deep inside zipfile.ZipFile(), i.e. a raw 500 page instead of a
    real one. Caught here and turned into the same clean "upload a
    book" page a person would see at / , with a plain message, rather
    than fixing this up separately in every route that loads a book --
    same posture as the locked-file handling in replace_original()
    below, just for "gone" instead of "locked." See docs/gui_plan.md,
    "Bug fix -- GUI errors if the original file is removed mid-
    session."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        try:
            return view_func(*args, **kwargs)
        except FileNotFoundError as exc:
            missing = exc.filename or "This session's book file"
            return render_template(
                "index.html",
                error=(
                    f"Can't find {missing} -- it may have been moved, renamed, "
                    "or deleted since this tab was opened. Choose the file "
                    "again to continue."
                ),
            )
    return wrapped


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


def _is_background_request() -> bool:
    """True when a page saved something in the background (fetch) rather
    than by submitting a form, so the route should answer with a small
    JSON reply instead of a redirect or a whole page."""
    return request.headers.get("X-Requested-With") == "fetch"


def _review_index_path(session_dir: Path) -> Path:
    """{href: [candidate ids]} for the chapter-start boundaries the Review
    tab last showed. Written when the tab loads so a background save can
    work out "N files will split" without re-analysing the whole book on
    every click."""
    return session_dir / "review_index.json"


def _staged_modules_path(session_dir: Path) -> Path:
    """Which repairs the person has ticked on the Overview tab (a list of
    module attrs). Absent until they change a checkbox, in which case
    the defaults apply: every repair that is on in the config file, has
    something to do on this book, and is not skipped by the fixed-layout
    guard."""
    return session_dir / "staged_modules.json"


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


# A file whose only chapter start comes after at least this many words of
# other text (a copyright page, a title page, an epigraph) is worth
# offering for a split: cutting at that chapter separates the front matter
# from the chapter. Below it, the lone chapter start is simply the top of
# its own file and there is nothing to cut.
LONE_BOUNDARY_MIN_PREAMBLE_WORDS = 15


def _apply_staged_metadata(book, staged_metadata) -> None:
    """Writes the person's saved metadata edits (staged_metadata.json)
    onto `book`. Shared by Fix This Book and the Metadata tab's "what
    Fix will change" preview, so the preview starts from exactly what
    Fix starts from. Does nothing when nothing is staged."""
    if staged_metadata is None:
        return
    for name, value in staged_metadata.get("fields", {}).items():
        write_core_field(book, name, value)
    # .get(), not [] -- a session staged before Phase 5 shipped
    # won't have a "language" key at all, and that should just mean
    # "nothing to change" rather than a KeyError at apply time.
    language_value = staged_metadata.get("language")
    if language_value:
        write_core_field(book, "language", language_value)
    series_name = staged_metadata.get("series_name")
    if series_name:
        series_index = staged_metadata.get("series_index")
        series_metadata.write(book, series_name, series_index)


def _words_before(element) -> int:
    """Words of ordinary text that come before `element` in its own
    document (the contents of <style>, <script> and <title> do not count)."""
    parts = element.xpath(
        "preceding::text()[not(ancestor::*[local-name()='style' or local-name()='script' "
        "or local-name()='title' or local-name()='head'])]"
    )
    return len(" ".join(parts).split())


def _split_candidate_groups(book):
    """Every chapter and part boundary worth showing a person, grouped by
    the file it's in. A file normally needs 2+ candidates to be split at
    all (a single boundary is just the top of its own file, with nothing
    to cut it against) -- the same gate Engine.split_chapters() uses -- so
    a file with just one is left out of the list.

    The exception is the book's FIRST chapter start when it is alone in
    its file and comes after real text (at least
    LONE_BOUNDARY_MIN_PREAMBLE_WORDS words): a copyright page followed by
    a Prologue, say. That text is front matter, so the single boundary does
    have something to cut against, and splitting gives the front matter and
    the chapter a file each (found in Pilgrimage to Hell). Such a group is
    marked lone=True, needs only that one boundary accepted
    (min_accept=1), and is never pre-checked, even if corroborated: it
    always takes an active choice.

    A lone boundary later in the book is deliberately NOT offered, however
    much text precedes it. There the text before it is the tail of the
    previous chapter (a file cut by size), a different situation that
    split_fragments handles, and offering it would put dozens of items in
    front of a person in books that were never asked about (17 in
    OmnibusExample alone).

    Each item carries the live StructureNode (under "node") alongside
    the template-facing fields, so save_review() below can rebuild the
    exact same groups from a fresh copy of the book and match the
    person's accepted checkbox ids back to real elements to split at
    -- the node itself never round-trips through the browser. Part nodes
    (prologues/epilogues) are marked with is_part=True so the template
    can label them differently in the TOC."""
    tree = analyze_structure(book)
    by_href: dict[str, list] = {}
    for node in _walk_structure_nodes(tree.nodes, include_parts=True):
        if node.evidence is None or node.evidence.confidence == SplitConfidence.NONE:
            continue
        by_href.setdefault(node.start_href, []).append(node)

    first_href = next(iter(by_href), None)   # the book's first chapter start, in reading order

    groups = []
    for href, nodes in by_href.items():
        lone = len(nodes) == 1
        if lone and (
            href != first_href
            or _words_before(nodes[0].evidence.candidate.element) < LONE_BOUNDARY_MIN_PREAMBLE_WORDS
        ):
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
                "auto_checked": confidence == SplitConfidence.CORROBORATED and not lone,
                "notes": node.evidence.notes,
                "preview": element_text_preview(node.evidence.candidate.element),
                "is_part": node.kind == NodeKind.PART,
                "node": node,
            })
        groups.append({"href": href, "candidates": candidates, "lone": lone, "min_accept": 1 if lone else 2})
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


def _font_review_groups(book, analysis_report=None):
    """Every review-bucket (not confident) embedded-font finding worth
    showing a person, grouped by href -- same shape and same posture
    as _color_review_groups above. Confident findings aren't included
    here at all: those are already handled unattended by
    FontStripRepair, nothing to review. See ebook_fix.fonts's module
    docstring for the confident/review split. Unlike color, accepting
    a review-bucket font finding here only ever removes the CSS-level
    declaration -- never the @font-face rule or the font file itself,
    see FontStripRepair.apply_review_removals() for why."""
    fonts = analysis_report.fonts if analysis_report is not None else analyze_book_font_usage(book)
    by_href: dict = {}
    for finding in fonts.review:
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
                    "value": f.family,
                    "reason": f.reason,
                }
                for f in findings
            ],
        })
    return groups


def _frontmatter_review_groups(book):
    """Every front/back-matter page whose classification wasn't
    confident enough to trust unattended -- confidence "medium" or
    "low" -- worth a person's own look, same confident-enough-to-skip
    posture as _color_review_groups above. Confident classifications
    (zone MAIN always included, since analyze_book_frontmatter always
    scores that "high") aren't included here at all: nothing to
    review there.

    Only ever considers a book where chapters.py actually confirmed a
    chapter sequence to anchor zones on. With no confirmed sequence at
    all, every single page in the book falls into frontmatter.py's
    "unknown zone" fallback at once -- that's a chapter-detection
    problem, not a front/back-matter labeling one, and Case 3's own
    review flow is where that already belongs, not here.

    Flat list rather than href-grouped-with-multiple-items shape the
    other three review kinds use above: each entry here already is
    one whole page, so there's nothing to group further within it.
    """
    summary = analyze_book_frontmatter(book)
    if not summary.boundaries_confirmed:
        return []
    chapters_by_href = {c.href: c for c in book.chapters}
    items = []
    for m in summary.chapters:
        if m.zone not in (FRONT_ZONE, BACK_ZONE) or m.confidence == "high":
            continue
        chapter = chapters_by_href.get(m.href)
        preview = element_text_preview(chapter.document, word_limit=40) if chapter is not None else ""
        items.append({
            "href": m.href,
            "zone": m.zone,
            "label": m.label,
            "confidence": m.confidence,
            "reason": m.reason,
            "preview": preview,
        })
    return items


# Ordered (value, display name) pairs for the Review tab's front/back-
# matter dropdown -- MatterLabel's own declaration order already reads
# front-to-back-to-main, so no separate ordering is needed here.
FRONTMATTER_LABEL_CHOICES = [(label.value, label.value.title()) for label in MatterLabel]


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


# A small tab icon (a blue book), drawn as an SVG so there is no image
# file to keep in the repo. Both pages point at /favicon.svg; /favicon.ico
# answers with the same picture for any browser that asks for it by the
# old name, so the console log never fills with "not found" lines.
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="12" fill="#2b6cb0"/>'
    '<path d="M12 16h18c3 0 2 2 2 2v30s1-2-2-2H12z" fill="#fff"/>'
    '<path d="M52 16H34c-3 0-2 2-2 2v30s-1-2 2-2h18z" fill="#bee3f8"/>'
    '</svg>'
)


@app.route("/favicon.svg")
@app.route("/favicon.ico")
def favicon():
    response = Response(_FAVICON_SVG, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


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
        "    title='Choose a book',\n"
        "    filetypes=[\n"
        "        ('Books', ('*.epub', '*.mobi', '*.azw', '*.azw3', '*.prc', '*.fb2')),\n"
        "        ('EPUB files', '*.epub'),\n"
        "        ('All files', '*.*'),\n"
        "    ],\n"
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


def _converted_epub_path(source: Path) -> Path:
    """Where a converted MOBI's or FB2's EPUB goes: next to the
    original, named the same. Never replaces a file that's already
    there (it could be a different edition, or an earlier conversion
    Jacob has since repaired in place) -- picks "<name> (converted).epub",
    then "<name> (converted 2).epub", and so on, instead."""
    candidate = source.with_suffix(".epub")
    n = 1
    while candidate.exists():
        label = "(converted)" if n == 1 else f"(converted {n})"
        candidate = source.with_name(f"{source.stem} {label}.epub")
        n += 1
    return candidate


@app.route("/upload", methods=["POST"])
def upload():
    typed_path = request.form.get("path", "").strip().strip('"')
    if not typed_path:
        return render_template("index.html", error="No file was chosen.")

    path_obj = Path(typed_path)
    if not path_obj.is_file():
        return render_template("index.html", error=f"Can't find that file: {typed_path}")
    suffix = path_obj.suffix.lower()
    if suffix in MOBI_EXTENSIONS or suffix in FB2_EXTENSIONS:
        # A MOBI/AZW/AZW3/PRC or FB2 book is converted to an EPUB first (see
        # ebook_fix.mobi.convert / ebook_fix.fb2.convert; an AZW3 is routed
        # to ebook_fix.mobi.kf8_convert by convert_mobi_to_epub), and that EPUB
        # is what gets opened -- everything else in the GUI only knows
        # how to work on an EPUB.
        target = _converted_epub_path(path_obj)
        try:
            if suffix in FB2_EXTENSIONS:
                convert_fb2_to_epub(path_obj, target)
            else:
                convert_mobi_to_epub(path_obj, target)
        except (MobiError, Fb2Error) as exc:
            return render_template("index.html", error=f"Couldn't convert that book to EPUB: {exc}")
        except Exception as exc:
            traceback.print_exc()
            return render_template("index.html", error=f"Something went wrong converting that book to EPUB: {exc}")
        path_obj = target
    elif suffix != ".epub":
        return render_template(
            "index.html",
            error="That doesn't look like a supported book file (expected a .epub, or a MOBI/AZW/AZW3/PRC/FB2 to convert to EPUB).",
        )

    session_id = str(uuid.uuid4())
    session_dir = SESSIONS_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "real_path.txt").write_text(str(path_obj.resolve()), encoding="utf-8")
    (session_dir / "original_filename.txt").write_text(path_obj.name, encoding="utf-8")
    return redirect(url_for("book_overview", session_id=session_id))


def _overview_cache_path(session_dir: Path) -> Path:
    return session_dir / "overview_cache.json"


def _overview_cache_key(session_dir: Path, config) -> str:
    """Changes whenever the book file or which repairs are turned on
    changes, so a stale Overview is never shown. Staged choices don't
    affect the Overview (it is read-only), so they are not part of it."""
    stat = _source_path(session_dir).stat()
    enabled = ",".join(
        f"{attr}={int(bool(getattr(getattr(config, attr), 'enabled', True)))}"
        for attr, _label in _REPAIR_MODULES
    )
    # "v2" because the cached data gained safe_splits and review_visible;
    # an older cache written without them is simply rebuilt.
    return f"v2-{stat.st_mtime_ns}-{stat.st_size}-{enabled}"


def _review_summary(book) -> dict:
    """What the Review tab holds, for the Overview and the tab bar.

    counts: how many items a person has to decide on, by Overview box
    (the same lists the Review tab renders). Chapter-start boundaries the
    project already treats as safe (pre-checked) are not counted: Fix This
    Book applies them on its own (see fix_book), so they need no decision.
    safe_splits: how many of those safe boundaries Fix will split at.
    visible: whether the Review tab has anything at all to show, which
    includes safe boundaries (the tab is where someone turns them off)."""
    groups = _split_candidate_groups(book)
    structure = sum(1 for g in groups for c in g["candidates"] if not c.get("auto_checked"))
    structure += len(_frontmatter_review_groups(book))
    text = sum(len(g["candidates"]) for g in _possessive_groups(book))
    styling = sum(len(g["findings"]) for g in _color_review_groups(book))
    styling += sum(len(g["findings"]) for g in _font_review_groups(book))
    counts = {"structure": structure, "text": text, "styling": styling}
    return {
        "counts": counts,
        "safe_splits": len(_default_accepted_split_ids(groups)),
        "visible": bool(sum(counts.values())) or any(c.get("auto_checked") for g in groups for c in g["candidates"]),
    }


def _default_accepted_split_ids(groups) -> set:
    """The boundaries Fix This Book splits at when nobody has made any
    choice on the Review tab: the pre-checked (safe) ones, in files where
    enough of them are checked for a split to happen at all (the same
    min_accept gate the staged path applies)."""
    ids = set()
    for group in groups:
        checked = [item["id"] for item in group["candidates"] if item.get("auto_checked")]
        if len(checked) >= group.get("min_accept", 2):
            ids.update(checked)
    return ids


_overview_locks: dict = {}
_overview_locks_guard = threading.Lock()


def _overview_lock(session_dir: Path) -> threading.Lock:
    with _overview_locks_guard:
        return _overview_locks.setdefault(str(session_dir), threading.Lock())


def _overview_data(session_dir: Path, filename: str) -> dict:
    """The Overview page's data, cached in the session folder as JSON
    (it is built from a full analysis plus every repair module's own
    analyze(), which is slow on a big book).

    The Fix This Book button asks for this in the background from every
    tab, so two requests can arrive at once on a cold cache; a lock per
    session makes the second one wait for the first's result instead of
    repeating the whole analysis."""
    config = load_config(None)
    key = _overview_cache_key(session_dir, config)
    cache_path = _overview_cache_path(session_dir)
    cached = _read_staged(cache_path)
    if cached is not None and cached.get("key") == key:
        return cached["data"]

    with _overview_lock(session_dir):
        cached = _read_staged(cache_path)
        if cached is not None and cached.get("key") == key:
            return cached["data"]
        return _compute_overview_data(session_dir, filename, config, key, cache_path)


def _compute_overview_data(session_dir: Path, filename: str, config, key: str, cache_path: Path) -> dict:
    book, analysis_report = _load_analysis(session_dir)
    module_reports = {}
    module_enabled = {}
    for attr, _label in _REPAIR_MODULES:
        module_config = getattr(config, attr)
        report = _REPAIR_MODULE_CLASSES[attr](module_config).analyze(book, analysis_report)
        module_reports[attr] = {
            "count": report.count,
            "issues": [
                {"location": i.location, "category": i.category, "description": i.description}
                for i in report.issues
            ],
        }
        module_enabled[attr] = bool(getattr(module_config, "enabled", True))

    # Repairs the engine will skip on this book: the same fixed-layout
    # guard Engine._apply_fixed_layout_guard() applies at repair time,
    # so the Overview never promises a fix that will not run.
    module_skipped = set()
    guard = getattr(config, "fixed_layout_guard", None)
    guard_on = guard is None or getattr(guard, "enabled", True)
    layout = getattr(analysis_report, "layout", None)
    if guard_on and layout is not None and getattr(layout, "confirmed_fixed_layout", False):
        module_skipped = {
            attr for attr, _label in _REPAIR_MODULES
            if issubclass(_REPAIR_MODULE_CLASSES[attr], FIXED_LAYOUT_RISKY_MODULE_TYPES)
        }

    review = _review_summary(book)
    data = overview_view.build_overview_page(
        book, analysis_report, module_reports, _REPAIR_MODULES,
        module_enabled, review["counts"], filename, module_skipped,
    )
    data["safe_splits"] = review["safe_splits"]
    data["review_visible"] = review["visible"]
    cache_path.write_text(json.dumps({"key": key, "data": data}), encoding="utf-8")
    return data


@app.context_processor
def _review_tab_state():
    """What the Review tab looks like in the tab bar on every page: a
    count badge for the items that need a decision, and hidden entirely
    when the tab would have nothing on it. Read straight from the cached
    Overview data when it is there (never computed here, so no page waits
    on a full analysis); when it is not there yet, the tab shows without
    a badge and the page's own script fixes it up as soon as the Fix
    button's background check (fix_state) has the numbers."""
    session_id = (request.view_args or {}).get("session_id")
    state = {"review_count": 0, "review_hidden": False}
    if not session_id:
        return state
    try:
        session_dir = _session_dir(session_id)
        cached = _read_staged(_overview_cache_path(session_dir))
        if cached is not None and cached.get("key") == _overview_cache_key(session_dir, load_config(None)):
            data = cached["data"]
            state["review_count"] = data.get("review_total", 0)
            state["review_hidden"] = not data.get("review_visible", True)
    except Exception:
        # The tab bar is a convenience; never let it break a page.
        pass
    return state


def _default_module_selection(ov: dict) -> set:
    """Repairs ticked until the person changes a checkbox: on in the
    config file, something to do on this book, and not skipped by the
    fixed-layout guard (the same pre-check the old Repair tab used)."""
    return {m["attr"] for m in ov["advanced"] if m["enabled"] and m["count"]}


def _selected_modules(session_dir: Path, filename: str) -> set:
    valid = {attr for attr, _label in _REPAIR_MODULES}
    staged = _read_staged(_staged_modules_path(session_dir))
    if staged is not None:
        return set(staged.get("selected", [])) & valid
    return _default_module_selection(_overview_data(session_dir, filename))


@app.route("/book/<session_id>/overview")
@_handle_missing_source
def book_overview(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    ov = dict(_overview_data(session_dir, filename))
    selected = _selected_modules(session_dir, filename)

    # The totals line follows what is ticked, not just the defaults.
    counts = {m["attr"]: m["count"] for m in ov["advanced"]}
    ov["ready_modules"] = sum(1 for attr in selected if counts.get(attr))
    ov["ready_changes"] = sum(counts.get(attr, 0) for attr in selected)
    return render_template(
        "overview.html",
        active_tab="overview",
        session_id=session_id,
        filename=filename,
        ov=ov,
        selected_modules=sorted(selected),
        # Once the Review tab has saved its ticks they decide the splits,
        # and the safe-boundary sentence on this page would no longer be true.
        review_touched=_read_staged(_staged_review_path(session_dir)) is not None,
    )


@app.route("/book/<session_id>/modules", methods=["POST"])
@_handle_missing_source
def save_modules(session_id):
    """Remembers which repairs are ticked (the Overview tab saves this in
    the background whenever a checkbox changes), so Fix This Book on any
    tab uses the same choices."""
    session_dir = _session_dir(session_id)
    valid = {attr for attr, _label in _REPAIR_MODULES}
    selected = [m for m in request.form.getlist("modules") if m in valid]
    _staged_modules_path(session_dir).write_text(json.dumps({"selected": selected}), encoding="utf-8")
    return jsonify({"ok": True, "selected": len(selected)})


@app.route("/book/<session_id>/fix-state")
@_handle_missing_source
def fix_state(session_id):
    """What the Fix This Book button needs to know, asked for in the
    background by every tab: is there anything for it to do? There is if
    a ticked repair has something to change on this book, or a metadata
    edit, a cover replacement or a review choice is waiting."""
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    ov = _overview_data(session_dir, filename)
    selected = _selected_modules(session_dir, filename)
    counts = {m["attr"]: m["count"] for m in ov["advanced"]}
    repairs = sum(1 for attr in selected if counts.get(attr))
    changes = sum(counts.get(attr, 0) for attr in selected)

    staged_metadata = _staged_metadata_path(session_dir).exists()
    staged_cover = _read_staged(_staged_cover_path(session_dir))
    has_cover = bool(staged_cover and (staged_cover.get("cover_source") or staged_cover.get("cover_url")))
    staged_review_raw = _read_staged(_staged_review_path(session_dir))
    staged_review = staged_review_raw or {}
    has_review = any(
        staged_review.get(key)
        for key in ("accepted_ids", "possessive_resolutions", "accepted_color_ids", "accepted_font_ids", "frontmatter_labels")
    )
    # Until the Review tab has saved anything, Fix splits at the safe
    # (pre-ticked) chapter boundaries on its own, which is something to do.
    safe_splits = ov.get("safe_splits", 0) if staged_review_raw is None else 0

    parts = []
    if repairs:
        parts.append(f"{repairs} repair{'s' if repairs != 1 else ''} ({changes} change{'s' if changes != 1 else ''})")
    if staged_metadata:
        parts.append("your metadata edits")
    if has_cover:
        parts.append("a new cover")
    if safe_splits:
        parts.append(f"{safe_splits} chapter start{'s' if safe_splits != 1 else ''} split into their own pages")
    if has_review:
        parts.append("your review choices")
    return jsonify({
        "can_fix": bool(repairs or staged_metadata or has_cover or has_review or safe_splits),
        "summary": "Will apply: " + ", ".join(parts) if parts else "Nothing to fix",
        "review_total": ov.get("review_total", 0),
        "review_visible": ov.get("review_visible", True),
    })


@app.route("/book/<session_id>")
@_handle_missing_source
def book_analysis(session_id):
    """The old Analysis tab is gone: everything it showed is on the
    Overview tab (checked line by line against it). This stays only so an
    old bookmark or link to /book/<id> lands somewhere useful."""
    return redirect(url_for("book_overview", session_id=session_id))


def _cover_preview(data: bytes, media_type: str) -> dict:
    """A small dict the Metadata tab's cover section renders directly:
    a data-URI (no separate route needed to serve the bytes -- covers
    are small enough that inlining them is simpler than adding a
    dedicated asset endpoint just for this), pixel dimensions when
    they're knowable (see cover.image_dimensions -- always None for
    SVG, occasionally None for a malformed image), and a human file
    size. Shared by every preview the Metadata tab shows: the book's
    current cover, a just-uploaded replacement, and a Calibre folder's
    own cover.jpg, so all three render identically."""
    dims = cover_module.image_dimensions(data)
    size = len(data)
    size_label = f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
    return {
        "data_uri": f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}",
        "dimensions": f"{dims[0]} \u00d7 {dims[1]} px" if dims else None,
        "size_label": size_label,
    }


def _staged_cover_path(session_dir: Path) -> Path:
    # Deliberately its own file, not a key inside staged_metadata.json
    # -- save_metadata() below replaces that whole dict on every save
    # (one form, no partial-field merging), so a cover choice living
    # in there would get silently wiped out the next time a person
    # edited an unrelated text field and clicked Stage Changes. Same
    # reasoning as staged_review.json already being separate from
    # staged_metadata.json for the Review tab's own unrelated form.
    return session_dir / "staged_cover.json"


def _staged_cover_dir(session_dir: Path) -> Path:
    return session_dir / "staged_cover"



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

    if _is_background_request():
        return jsonify({"ok": True})
    return redirect(url_for("book_details", session_id=session_id))


@app.route("/book/<session_id>/metadata/cover", methods=["POST"])
def save_cover(session_id):
    """Stages a cover replacement -- either an uploaded file, "use
    Calibre's cover", or a URL -- into its own staged_cover.json (see
    _staged_cover_path's own docstring for why that's separate from
    staged_metadata.json). Nothing about the book itself changes until
    apply_repair actually runs; this only ever writes to this
    session's own temp folder.
    """
    session_dir = _session_dir(session_id)

    use_calibre = request.form.get("use_calibre_cover") == "1"
    cover_url = request.form.get("cover_url", "").strip()
    upload = request.files.get("cover_file")

    if use_calibre:
        calibre_ctx = calibre_detect.detect(_source_path(session_dir))
        if calibre_ctx.is_calibre_managed and calibre_ctx.book_folder is not None:
            calibre_cover_path = calibre_ctx.book_folder / "cover.jpg"
            if calibre_cover_path.is_file():
                staged = {"cover_source": str(calibre_cover_path)}
                _staged_cover_path(session_dir).write_text(json.dumps(staged), encoding="utf-8")
        return redirect(url_for("book_details", session_id=session_id))

    if cover_url:
        # Store the URL for fetching during apply_repair
        staged = {"cover_url": cover_url}
        _staged_cover_path(session_dir).write_text(json.dumps(staged), encoding="utf-8")
        return redirect(url_for("book_details", session_id=session_id))

    if upload is not None and upload.filename:
        data = upload.read()
        media_type = cover_module.sniff_image_media_type(data)
        if media_type is not None:
            # Saved under this session's own folder with a name based
            # on the sniffed format, not the browser-supplied filename
            # -- an uploaded file's own name is only ever a hint, and
            # sniffing already confirmed what this actually is.
            cover_dir = _staged_cover_dir(session_dir)
            cover_dir.mkdir(exist_ok=True)
            ext = cover_module.extension_for_media_type(media_type)
            for old in cover_dir.glob("staged_cover.*"):
                old.unlink(missing_ok=True)
            saved_path = cover_dir / f"staged_cover.{ext}"
            saved_path.write_bytes(data)
            staged = {"cover_source": str(saved_path)}
            _staged_cover_path(session_dir).write_text(json.dumps(staged), encoding="utf-8")

    return redirect(url_for("book_details", session_id=session_id))


@app.route("/book/<session_id>/metadata/cover/clear", methods=["POST"])
def clear_cover(session_id):
    """Cancels a staged cover replacement -- back to the book's actual
    current cover, same as never having picked one."""
    session_dir = _session_dir(session_id)
    _staged_cover_path(session_dir).unlink(missing_ok=True)
    for old in _staged_cover_dir(session_dir).glob("staged_cover.*"):
        old.unlink(missing_ok=True)
    return redirect(url_for("book_details", session_id=session_id))


@app.route("/book/<session_id>/lookup", methods=["POST"])
def book_lookup(session_id):
    """Handles ISBN/title+author metadata lookup from Open Library.
    
    Request body (JSON):
    - search_type: 'isbn' or 'title_author'
    - isbn: (if search_type='isbn')
    - title: (if search_type='title_author')
    - author: (if search_type='title_author', optional)
    
    Returns JSON:
    - status: 'success', 'not_found', 'offline', 'error'
    - result: lookup result dict (if success)
    - comparison: comparison result (if success)
    - message: error message (if not success)
    """
    try:
        session_dir = _session_dir(session_id)
        book = EPUBParser().load(_source_path(session_dir))
    except FileNotFoundError:
        return jsonify({'status': 'error', 'message': 'Book file not found. Session may have expired.'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Failed to load book: {str(e)}'}), 500
    
    try:
        data = request.get_json() or {}
    except Exception:
        return jsonify({'status': 'error', 'message': 'Invalid request'}), 400
    
    search_type = data.get('search_type', 'isbn').strip().lower()
    result = None
    
    if search_type == 'isbn':
        isbn_input = (data.get('isbn') or '').strip()
        if not isbn_input:
            return jsonify({'status': 'error', 'message': 'No ISBN provided'}), 400
        result = isbn_lookup.fetch_isbn_metadata(isbn_input)
    
    elif search_type == 'title_author':
        title = (data.get('title') or '').strip()
        author = (data.get('author') or '').strip() or None
        if not title:
            return jsonify({'status': 'error', 'message': 'No title provided'}), 400
        result = isbn_lookup.fetch_title_author_metadata(title, author)
    
    else:
        return jsonify({'status': 'error', 'message': 'Invalid search type'}), 400
    
    if result is None:
        return jsonify({'status': 'not_found', 'message': 'No metadata found'}), 200
    
    # Compare with current metadata
    # Read staged metadata if available to compare against edited values
    staged = _read_staged(_staged_metadata_path(session_dir))
    
    current_metadata = {
        'title': staged.get('fields', {}).get('title') or book.metadata.title or '',
        'author': staged.get('fields', {}).get('author') or book.metadata.creator or '',
        'publisher': staged.get('fields', {}).get('publisher') or book.metadata.publisher or '',
        'publish_date': staged.get('fields', {}).get('publish_date') or book.metadata.date or '',
        'description': staged.get('fields', {}).get('description') or book.metadata.description or '',
        'series_name': staged.get('series_name') or '',
    }
    
    comparison = isbn_lookup.compare_metadata(current_metadata, result)
    
    return jsonify({
        'status': 'success',
        'result': result,
        'comparison': comparison,
    }), 200


@app.route("/book/<session_id>/review")
@_handle_missing_source
def book_review(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    book = EPUBParser().load(_source_path(session_dir))
    groups = _split_candidate_groups(book)
    possessive_groups = _possessive_groups(book)
    color_groups = _color_review_groups(book)
    font_groups = _font_review_groups(book)
    frontmatter_items = _frontmatter_review_groups(book)

    _review_index_path(session_dir).write_text(
        json.dumps({
            group["href"]: {"ids": [item["id"] for item in group["candidates"]], "min": group.get("min_accept", 2)}
            for group in groups
        }),
        encoding="utf-8",
    )

    staged = _read_staged(_staged_review_path(session_dir))
    staged_possessive_resolutions = {}
    staged_color_ids = set()
    staged_font_ids = set()
    staged_frontmatter_labels = {}
    if staged is not None:
        staged_ids = set(staged.get("accepted_ids", []))
        for group in groups:
            for item in group["candidates"]:
                item["auto_checked"] = item["id"] in staged_ids
        staged_possessive_resolutions = staged.get("possessive_resolutions", {})
        staged_color_ids = set(staged.get("accepted_color_ids", []))
        staged_font_ids = set(staged.get("accepted_font_ids", []))
        staged_frontmatter_labels = staged.get("frontmatter_labels", {})

    for group in possessive_groups:
        for item in group["candidates"]:
            item["resolution"] = staged_possessive_resolutions.get(item["id"], "")
    for group in color_groups:
        for item in group["findings"]:
            item["accepted"] = item["id"] in staged_color_ids
    for group in font_groups:
        for item in group["findings"]:
            item["accepted"] = item["id"] in staged_font_ids
    for item in frontmatter_items:
        item["resolution"] = staged_frontmatter_labels.get(item["href"], "")

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
        font_groups=font_groups,
        has_font_findings=bool(font_groups),
        frontmatter_items=frontmatter_items,
        has_frontmatter_items=bool(frontmatter_items),
        frontmatter_label_choices=FRONTMATTER_LABEL_CHOICES,
        is_staged=staged is not None,
        split_result=None,
    )


def _save_review_in_background(session_dir: Path):
    """The Review tab saves every change as it is made. Same staged file
    as the full-page save below, but built straight from the posted form
    (no re-analysis of the book on every click): the apply step re-derives
    candidates fresh and ignores any id that no longer matches, so there
    is nothing to validate here beyond the small fixed choices. The
    split preview comes from the index the tab wrote when it loaded."""
    form = request.form
    valid_labels = {value for value, _display in FRONTMATTER_LABEL_CHOICES}

    accepted_ids = set(form.getlist("accept"))
    possessive_resolutions = {
        key[len("poss_"):]: value
        for key, value in form.items()
        if key.startswith("poss_") and value in ("possessive", "plural")
    }
    frontmatter_labels = {
        key[len("fm_"):]: value
        for key, value in form.items()
        if key.startswith("fm_") and value in valid_labels
    }
    staged = {
        "accepted_ids": sorted(accepted_ids),
        "possessive_resolutions": possessive_resolutions,
        "accepted_color_ids": sorted(set(form.getlist("color_accept"))),
        "accepted_font_ids": sorted(set(form.getlist("font_accept"))),
        "frontmatter_labels": frontmatter_labels,
    }
    _staged_review_path(session_dir).write_text(json.dumps(staged), encoding="utf-8")

    index = _read_staged(_review_index_path(session_dir)) or {}
    would_split = 0
    skipped = 0
    for _href, entry in index.items():
        # (An older index held just the list of ids; two were needed.)
        ids = entry["ids"] if isinstance(entry, dict) else entry
        minimum = entry.get("min", 2) if isinstance(entry, dict) else 2
        picked = [i for i in ids if i in accepted_ids]
        if not picked:
            continue
        if len(picked) < minimum:
            skipped += 1
        else:
            would_split += 1
    return jsonify({"ok": True, "would_split": would_split, "skipped": skipped, "picked": len(accepted_ids)})


@app.route("/book/<session_id>/review", methods=["POST"])
@_handle_missing_source
def save_review(session_id):
    session_dir = _session_dir(session_id)
    if _is_background_request():
        return _save_review_in_background(session_dir)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    book = EPUBParser().load(_source_path(session_dir))
    groups = _split_candidate_groups(book)
    possessive_groups = _possessive_groups(book)
    color_groups = _color_review_groups(book)
    font_groups = _font_review_groups(book)
    frontmatter_items = _frontmatter_review_groups(book)

    accepted_ids = set(request.form.getlist("accept"))
    accepted_color_ids = set(request.form.getlist("color_accept"))
    accepted_font_ids = set(request.form.getlist("font_accept"))
    possessive_resolutions = {}
    for group in possessive_groups:
        for item in group["candidates"]:
            resolution = request.form.get(f"poss_{item['id']}", "")
            if resolution in ("possessive", "plural"):
                possessive_resolutions[item["id"]] = resolution

    valid_labels = {value for value, _display in FRONTMATTER_LABEL_CHOICES}
    frontmatter_labels = {}
    for item in frontmatter_items:
        resolution = request.form.get(f"fm_{item['href']}", "")
        if resolution in valid_labels:
            frontmatter_labels[item["href"]] = resolution

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
        if len(accepted_nodes) < group.get("min_accept", 2):
            skipped_hrefs.append(group["href"])
        else:
            would_split += 1

    staged = {
        "accepted_ids": sorted(accepted_ids),
        "possessive_resolutions": possessive_resolutions,
        "accepted_color_ids": sorted(accepted_color_ids),
        "accepted_font_ids": sorted(accepted_font_ids),
        "frontmatter_labels": frontmatter_labels,
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
    for group in font_groups:
        for item in group["findings"]:
            item["accepted"] = item["id"] in accepted_font_ids
    for item in frontmatter_items:
        item["resolution"] = frontmatter_labels.get(item["href"], "")

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
        font_groups=font_groups,
        has_font_findings=bool(font_groups),
        frontmatter_items=frontmatter_items,
        has_frontmatter_items=bool(frontmatter_items),
        frontmatter_label_choices=FRONTMATTER_LABEL_CHOICES,
        is_staged=True,
        split_result={
            "would_split": would_split,
            "skipped_hrefs": skipped_hrefs,
        },
    )


@app.route("/book/<session_id>/repair")
def book_repair(session_id):
    """The old Repair tab is gone (the Overview tab now holds the repair
    choices and the Fix This Book button, the Metadata tab holds the
    metadata and cover). Kept only so an old bookmark lands somewhere
    useful."""
    return redirect(url_for("book_details", session_id=session_id))


@app.route("/book/<session_id>/metadata-preview")
@_handle_missing_source
def metadata_preview(session_id):
    """What Fix This Book will change in each metadata field, for the
    notes on the Metadata tab (asked for in the background when the tab
    opens and after every autosave). Runs the ticked metadata repairs
    against a throwaway copy of the book that already has the person's
    saved edits applied; see gui.metadata_preview."""
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    selected = _selected_modules(session_dir, filename)

    book = EPUBParser().load(_source_path(session_dir))
    _apply_staged_metadata(book, _read_staged(_staged_metadata_path(session_dir)))
    changes = metadata_preview_module.preview_repairs(book, load_config(None), selected)
    return jsonify({"changes": changes})


@app.route("/book/<session_id>/details")
@_handle_missing_source
def book_details(session_id):
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")

    # A book's own analysis, so each module's checkbox can show how
    # many issues it actually found in THIS book (not just whether
    # it's enabled in ebook_fix.toml) -- see docs/gui_plan.md, Phase 6
    # follow-up. Reusing the module classes here rather than
    # Engine.modules, since that only ever builds the ones already
    # enabled; a disabled module's count still needs to show up if a
    # person decides to check it on.
    book, analysis_report = _load_analysis(session_dir)

    # --- METADATA SECTION (merged from book_metadata) ---
    merged = analysis_report.merged_core_fields
    staged_metadata = _read_staged(_staged_metadata_path(session_dir))

    fields = []
    for name in _EDITABLE_FIELDS:
        mf = getattr(merged, name)
        value = staged_metadata.get("fields", {}).get(name, mf.display_value) if staged_metadata else mf.display_value
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

    # Current cover: read straight from the book's own zip archive
    # (analyze_book_cover only ever returns which manifest item/path
    # is the cover, never the bytes) rather than book.images, which --
    # like every other Resource on Book -- only ever holds id/href/
    # media-type, never binary content. None if the book has no usable
    # cover at all rather than raising, same as everywhere else a
    # missing cover gets treated as "nothing to show," not an error.
    current_cover_preview = None
    cover_summary = cover_module.analyze_book_cover(book)
    if cover_summary.cover_item is not None and cover_summary.exists_in_archive:
        try:
            with zipfile.ZipFile(book.source, "r") as archive:
                current_cover_preview = _cover_preview(
                    archive.read(cover_summary.resolved_href),
                    cover_summary.cover_item.media_type,
                )
        except (KeyError, OSError):
            current_cover_preview = None

    # A Calibre-managed book's own folder conventionally has its own
    # cover.jpg alongside the EPUB -- offered as a one-click "use this
    # instead" option distinct from uploading a file by hand. Only
    # ever read for a preview here; nothing about this book's actual
    # cover changes until a person explicitly picks it AND applies the
    # repair pass (see save_cover/apply_repair).
    calibre_cover_preview = None
    if calibre_ctx.is_calibre_managed and calibre_ctx.book_folder is not None:
        calibre_cover_path = calibre_ctx.book_folder / "cover.jpg"
        if calibre_cover_path.is_file():
            try:
                data = calibre_cover_path.read_bytes()
                media_type = cover_module.sniff_image_media_type(data)
                if media_type is not None:
                    calibre_cover_preview = _cover_preview(data, media_type)
                    calibre_cover_preview["source_path"] = str(calibre_cover_path)
            except OSError:
                calibre_cover_preview = None

    # A staged replacement (uploaded file, or "use Calibre's cover"
    # picked on an earlier visit to this tab) previews as "pending"
    # rather than replacing current_cover_preview above -- the actual
    # book on disk hasn't changed yet, so showing both side by side is
    # more honest than swapping the "current" one out early.
    staged_cover_preview = None
    staged_cover = _read_staged(_staged_cover_path(session_dir))
    staged_cover_source = staged_cover.get("cover_source") if staged_cover else None
    if staged_cover_source:
        try:
            data = Path(staged_cover_source).read_bytes()
            media_type = cover_module.sniff_image_media_type(data)
            if media_type is not None:
                staged_cover_preview = _cover_preview(data, media_type)
        except OSError:
            staged_cover_preview = None

    # The EPUB's own current dc:language value, not merged.display_value
    # -- this dropdown edits the EPUB directly (write_core_field targets
    # the book, same as every other field here), so it should start on
    # what the EPUB actually has, not a value resolved from Calibre's
    # side of a comparison that was never a real disagreement to begin
    # with. Falls back to whichever side has something when the EPUB's
    # own field is simply blank.
    current_language = (staged_metadata.get("language") if staged_metadata else None) or merged.language.epub_value or merged.language.display_value

    return render_template(
        "details.html",
        active_tab="details",
        session_id=session_id,
        filename=filename,
        fields=fields,
        current_cover_preview=current_cover_preview,
        calibre_cover_preview=calibre_cover_preview,
        staged_cover_preview=staged_cover_preview,
        language_value=current_language,
        language_choices=language_options(current_language),
        language_note=merged.language.note,
        series_name=staged_metadata.get("series_name") if staged_metadata else (series_info.name or ""),
        series_index=series_metadata.format_index(staged_metadata.get("series_index") if staged_metadata else series_info.index),
        is_calibre_managed=calibre_ctx.is_calibre_managed,
    )


@app.route("/book/<session_id>/fix", methods=["POST"])
@_handle_missing_source
def fix_book(session_id):
    """Fix This Book: the one action that applies everything -- staged
    metadata, a staged cover, staged review choices and the repairs
    ticked on the Overview tab -- in a single pass into one output
    file, and, if asked, replaces the original with it. (Formerly the
    Repair tab's Apply Everything.)"""
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")

    selected = _selected_modules(session_dir, filename)
    replace_requested = request.form.get("replace_original") == "1"
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
    _apply_staged_metadata(book, staged_metadata)

    # Staged cover replacement, if any -- own staged file, not a key
    # inside staged_metadata (see _staged_cover_path), so it survives
    # independently of whatever's happened on the Metadata tab's own
    # text-field form -- and checked independently of staged_metadata
    # too, for the same reason: a person may have staged only a cover
    # choice and never touched a text field at all, in which case
    # staged_metadata is None but there's still a cover to apply.
    # cover_source is a plain filesystem path: either an uploaded file
    # saved under this session's own folder, or (when the person chose
    # "use Calibre's cover" instead) the Calibre folder's own
    # cover.jpg directly, never copied -- see save_cover below. Read
    # fresh here rather than trusting whatever was true when it was
    # staged, since either kind of path could in principle have moved
    # or vanished since. Failing silently and leaving the book's
    # existing cover untouched is deliberate: one missing staged image
    # shouldn't fail an entire repair pass, and the Metadata tab's own
    # preview will still show the true current cover next time the
    # person looks, so nothing is hidden.
    staged_cover = _read_staged(_staged_cover_path(session_dir))
    cover_source = staged_cover.get("cover_source") if staged_cover else None
    if cover_source:
        try:
            cover_data = Path(cover_source).read_bytes()
            cover_media_type = cover_module.sniff_image_media_type(cover_data)
            if cover_media_type is not None:
                cover_module.apply_cover_replacement(book, cover_data, cover_media_type)
        except (OSError, ValueError):
            pass
    
    # Fetch cover from URL if provided
    cover_url = staged_cover.get("cover_url") if staged_cover else None
    if cover_url:
        try:
            import urllib.request
            with urllib.request.urlopen(cover_url, timeout=10) as response:
                cover_data = response.read()
                cover_media_type = cover_module.sniff_image_media_type(cover_data)
                if cover_media_type is not None:
                    cover_module.apply_cover_replacement(book, cover_data, cover_media_type)
        except Exception:
            # URL fetch failed silently - leave existing cover untouched
            pass

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
        # Once someone has used the Review tab, its saved ticks decide
        # which boundaries are split at. Until then, the boundaries the
        # tab would pre-tick (the ones the project's split-safety bar
        # calls safe to apply without a person looking) are split at,
        # so whether a book's chapters get their own pages never depends
        # on whether the Review tab happened to be opened.
        groups = _split_candidate_groups(book)
        if staged_review is not None:
            accepted_ids = set(staged_review.get("accepted_ids", []))
        else:
            accepted_ids = _default_accepted_split_ids(groups)
        markers_by_href = {}
        for group in groups:
            accepted_nodes = [item["node"] for item in group["candidates"] if item["id"] in accepted_ids]
            if len(accepted_nodes) < group.get("min_accept", 2):
                continue
            markers_by_href[group["href"]] = [
                SplitMarker(element=node.evidence.candidate.element, title=node.title, number=marker_number(node.evidence.candidate))
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
        font_review_count = 0
        if staged_review is not None:
            possessive_resolutions = staged_review.get("possessive_resolutions", {})
            if possessive_resolutions:
                possessive_resolved_count = apply_possessive_resolutions(book, possessive_resolutions)

            accepted_color_ids = staged_review.get("accepted_color_ids", [])
            if accepted_color_ids:
                color_review_report = ColorStripRepair(config.color_repair).apply_review_removals(book, accepted_color_ids)
                color_review_count = color_review_report.count

            accepted_font_ids = staged_review.get("accepted_font_ids", [])
            if accepted_font_ids:
                font_review_report = FontStripRepair(config.font_repair).apply_review_removals(book, accepted_font_ids)
                font_review_count = font_review_report.count

        # Fresh analysis, reflecting any staged metadata/split changes
        # applied above -- the repair modules below need to see the
        # book as it actually is right now, not as it was when the
        # Metadata/Review tabs were first opened.
        #
        # frontmatter_labels, if any were staged, get threaded straight
        # into this one analysis call rather than applied as a separate
        # patch step the way possessive/color resolutions are above --
        # analyze_book_frontmatter's own overrides feed r.color,
        # r.scene_breaks, and r.paragraphs for free since all three
        # already reuse this same r.frontmatter object (see
        # EPUBAnalyzer.analyze's docstring), so a person's correction
        # here actually changes what those modules do this pass, not
        # just what the Review tab displayed.
        frontmatter_labels = staged_review.get("frontmatter_labels", {}) if staged_review is not None else {}
        analysis_report = EPUBAnalyzer().analyze(book, frontmatter_overrides=frontmatter_labels)

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

    module_summaries = [
        {"name": name, "count": report.count}
        for name, report in reports_by_module.items()
        if report.count > 0
    ]

    # Replace the original now if the person asked for that up front (the
    # checkbox next to the Fix button). Staged choices are only cleared
    # once the original has been replaced: a plain Fix leaves the original
    # file untouched, so a second Fix has to start from the same choices
    # or it would silently drop the person's edits. After a replace, the
    # file on disk already contains them (and the staged review ids refer
    # to a book that no longer exists), so they are cleared then.
    replace_outcome = None
    if replace_requested:
        replace_outcome = _replace_original_files(session_dir)
        if replace_outcome["ok"]:
            _staged_metadata_path(session_dir).unlink(missing_ok=True)
            _staged_review_path(session_dir).unlink(missing_ok=True)

    already_replaced = _replaced_flag_path(session_dir).exists()
    return render_template(
        "fix_result.html",
        active_tab="overview",
        session_id=session_id,
        filename=filename,
        result={
            "output_path": str(output_path),
            "split_count": split_count,
            "possessive_resolved_count": possessive_resolved_count,
            "color_review_count": color_review_count,
            "font_review_count": font_review_count,
            "module_summaries": module_summaries,
            "pass_count": pass_num,
            "engine_output": engine_output,
            "opf_sync": opf_sync_status,
            "db_sync": db_sync_status,
        },
        has_fixed=True,
        already_replaced=already_replaced,
        replace_outcome=replace_outcome,
        replaced_backup=_replaced_flag_path(session_dir).read_text(encoding="utf-8") if already_replaced else None,
    )


def _replace_original_files(session_dir: Path) -> dict:
    """Swaps a repaired book back onto its own original filename, so
    Calibre (or anything else pointed at that exact path) picks up the
    fix without a person manually renaming anything themselves. Two
    plain os-level renames, in this order: the untouched original ->
    "<n>_original.epub" (a backup, never deleted automatically),
    then "<n>_fixed.epub" -> the original filename. Both files
    already live in the same folder, so each rename is a same-
    filesystem move -- atomic on every OS this project supports,
    never a copy-then-delete that could leave things half-done if
    interrupted.

    Refuses if a backup already exists at that name rather than
    overwriting it -- a leftover "_original.epub" almost always means
    this session already replaced once before, and silently
    overwriting it would throw away whichever version came before
    that. A person can rename or delete the old backup by hand and
    retry if that's genuinely what they want.

    Returns {"ok": bool, "error": str | None, "source": str, "backup": str}.
    Used by both the Replace original button on the result page and the
    Replace original checkbox next to Fix This Book."""
    fixed_path = _fixed_output_path(session_dir)
    source_path = _source_path(session_dir)
    backup_path = _original_backup_path(session_dir)
    outcome = {"ok": False, "error": None, "source": str(source_path), "backup": str(backup_path)}

    if not fixed_path.exists():
        outcome["error"] = "There is no repaired file to put in place yet."
        return outcome

    if backup_path.exists():
        outcome["error"] = (
            f"A backup already exists at {backup_path} -- not overwriting it. "
            "Move or delete that file first if you're sure you want to replace again."
        )
        return outcome

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
        outcome["error"] = (
            f"Couldn't rename the original file to make a backup: {exc}. "
            "This usually means something else has the book open -- Calibre's "
            "own viewer, another reader, or an antivirus scan -- close it and "
            "try again. Nothing was changed."
        )
        return outcome

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
        outcome["error"] = (
            f"Couldn't move the repaired file into place: {exc}.{rollback_note} "
            "This usually means something else has the book open -- close it and "
            "try again."
        )
        return outcome

    _replaced_flag_path(session_dir).write_text(str(backup_path), encoding="utf-8")
    outcome["ok"] = True
    return outcome


@app.route("/book/<session_id>/replace-original", methods=["POST"])
def replace_original(session_id):
    """The Replace original button on the result page (for someone who
    did not tick the checkbox beside Fix This Book first)."""
    session_dir = _session_dir(session_id)
    filename = (session_dir / "original_filename.txt").read_text(encoding="utf-8")
    if not _fixed_output_path(session_dir).exists():
        abort(404)

    outcome = _replace_original_files(session_dir)
    if outcome["ok"]:
        _staged_metadata_path(session_dir).unlink(missing_ok=True)
        _staged_review_path(session_dir).unlink(missing_ok=True)

    already_replaced = _replaced_flag_path(session_dir).exists()
    return render_template(
        "fix_result.html",
        active_tab="overview",
        session_id=session_id,
        filename=filename,
        result=None,
        has_fixed=_fixed_output_path(session_dir).exists(),
        already_replaced=already_replaced,
        replace_outcome=outcome,
        replaced_backup=_replaced_flag_path(session_dir).read_text(encoding="utf-8") if already_replaced else None,
    )


@app.route("/book/<session_id>/before-after")
@_handle_missing_source
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
            after_options = [h for h in [c.href] + split_mapping[c.href] if h in fixed_hrefs]
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
    try:
        from waitress import serve
    except ImportError:
        # Waitress is a small, ordinary web server that is meant for real
        # use. When it is not installed, fall back to the server that
        # comes with Flask: it works the same here, it just prints a
        # "development server" warning in this window.
        print("(Waitress is not installed, so Flask's built-in server is being used. "
              "It works fine; to remove the warning, run: pip install waitress)")
        app.run(host="127.0.0.1", port=5000, debug=False)
        return
    # channel_timeout is raised well above its default of two minutes so a
    # long Fix on a very large book never has its connection dropped.
    serve(app, host="127.0.0.1", port=5000, threads=8, channel_timeout=900)


if __name__ == "__main__":
    main()

