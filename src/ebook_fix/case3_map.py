"""
ebook_fix.case3_map

Read/write helpers for the editable case-3 boundary review file used
by `repair --case3-boundaries FILE` (see cli.py/engine.py). Same
two-step, opt-out convention as ebook_fix.class_map's mapping file
(`map-css --write-mapping` / `repair --class-mapping`): running the
command with a file that doesn't exist yet generates it and stops
without touching the book; running it again with that file present
applies whatever's still listed in it. Delete a [[boundary]] block
entirely to leave that split point out -- nothing else in the file
controls whether a boundary gets used.

This file *is* the sign-off Case 3 boundaries never get automatically.
A normal (case 1/2) boundary can reach CORROBORATED confidence by
matching an existing TOC entry or in-body anchor (see structure.py);
a Case 3 boundary never can, by definition -- there's no chapter-
heading word and no TOC to check against in the first place (see
chapters.analyze_case3_book_chapters). Per Jacob's three-case
framework (docs/xhtml_recoder_plan.md), that gap is never closed by a
stronger automated signal -- it's closed by a person reviewing every
boundary here instead.

Matching a boundary back up on reload
--------------------------------------
TOML can't hold a live lxml element reference, so re-running the
command has to re-detect Case 3 candidates fresh and match each
surviving [[boundary]] block back to one of them. The match key is
(file, _book_order) -- book_order is chapters.py's own deterministic
position numbering (see ChapterCandidate.book_order), stable across
re-runs as long as the book itself hasn't changed in between. The
detected_number/detected_text fields are written for a person to read,
not for matching, but are double-checked against the freshly-detected
candidate at that position as a sanity check -- if they no longer
match, the book has changed since this file was generated, and that
boundary is skipped with a warning rather than silently split at the
wrong spot.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class Case3MappingError(Exception):
    pass


@dataclass(slots=True)
class Case3Boundary:
    href: str
    book_order: int
    number: str
    text: str


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_case3_boundaries_file(book_title: str, nodes, path) -> None:
    """`nodes` is every CHAPTER-kind structure.StructureNode from
    structure.analyze_case3_structure(book), in book order (see
    structure.iter_chapter_nodes) -- one [[boundary]] block per node.
    """
    path = Path(path)
    lines = [
        "# ebook_fix Case 3 chapter boundaries",
        "#",
        f'# ebook_fix found {len(nodes)} possible chapter-start point(s) in "{book_title}"',
        "# using sequence detection alone -- no chapter-heading words (\"Chapter\",",
        "# \"Part\", etc.) and no existing table of contents to check against. Every",
        "# one of these needs your own sign-off before anything gets split; an",
        "# unlabeled numbered section header can just as easily be something else",
        "# (an epigraph, a list, a footnote marker) as an actual chapter start.",
        "#",
        "# Review every boundary below. Delete a [[boundary]] block entirely to",
        "# leave that split point out -- only whatever's still listed here when",
        "# you re-run this same command gets split on. A file needs at least one",
        "# boundary kept in it to be split at all; keeping fewer boundaries than",
        "# were detected in a file just means fewer, larger pieces.",
        "#",
        "# Applied by: ebook-fixer repair <book> --case3-boundaries <this file>",
        "",
    ]

    if not nodes:
        lines.append("# No Case 3 boundaries were detected in this book -- nothing to review.")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    for node in nodes:
        candidate = node.evidence.candidate if node.evidence is not None else None
        number = str(getattr(candidate, "number", "") or "")
        confidence = node.evidence.confidence.value if node.evidence is not None else ""
        lines += [
            "[[boundary]]",
            f"file = {_toml_str(node.start_href)}",
            f"detected_number = {_toml_str(number)}",
            f"detected_text = {_toml_str(node.title)}",
            f"confidence = {_toml_str(confidence)}",
            "# Internal use -- don't edit; matches this boundary back up when",
            "# you re-run the command.",
            f"_book_order = {node.start_book_order}",
            "",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")


def load_case3_boundaries_file(path) -> list[Case3Boundary]:
    """Read and lightly validate a boundaries file written by
    write_case3_boundaries_file and then edited by hand. Raises
    Case3MappingError on structurally broken input (a block missing
    the fields matching depends on) -- but NOT on a boundary that
    simply no longer matches anything in the book; that's the caller's
    job (see engine.py), since it needs the live book loaded to check,
    and should skip-with-a-warning rather than fail the whole run."""
    path = Path(path)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise Case3MappingError(f"Boundaries file not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise Case3MappingError(f"Boundaries file isn't valid TOML: {exc}")

    raw_entries = data.get("boundary", [])
    if not isinstance(raw_entries, list):
        raise Case3MappingError("Boundaries file's 'boundary' key must be a list of boundary entries.")

    boundaries = []
    for i, raw in enumerate(raw_entries, start=1):
        href = raw.get("file")
        book_order = raw.get("_book_order")
        if not href or book_order is None:
            raise Case3MappingError(
                f"boundary entry #{i} is missing 'file' or '_book_order' -- "
                "don't edit those two fields, only delete whole [[boundary]] blocks."
            )
        boundaries.append(
            Case3Boundary(
                href=href,
                book_order=int(book_order),
                number=raw.get("detected_number", ""),
                text=raw.get("detected_text", ""),
            )
        )
    return boundaries
