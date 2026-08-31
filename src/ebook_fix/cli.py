#!/usr/bin/env python3
from pathlib import Path
import argparse
import sys
from ebook_fix.engine import Engine
from ebook_fix.config import (
    DEFAULT_CONFIG_FILENAME,
    load_config,
    write_default_config,
)
from ebook_fix.validation import validate_epub
from ebook_fix.container_repair import attempt_repair

def build_parser():

    parser = argparse.ArgumentParser(
        prog="ebook-fixer",
        description="Analyze and repair EPUB files."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Analyze
    analyze = sub.add_parser(
        "analyze",
        help="Analyze an EPUB without modifying it."
    )
    analyze.add_argument(
        "input",
        help="Input EPUB"
    )
    analyze.add_argument(
        "--details",
        action="store_true",
        help="Show the full line-by-line issue list instead of the category summary."
    )
    analyze.add_argument(
        "--config",
        help=(
            "Path to a TOML config file controlling which fixes are "
            f"enabled. Defaults to ./{DEFAULT_CONFIG_FILENAME} if present, "
            "otherwise every fix runs."
        )
    )
    analyze.add_argument(
        "--no-container-repair",
        action="store_true",
        help="Don't attempt to automatically repair a corrupted ZIP/EPUB container; just report the problem."
    )

    # Repair
    repair = sub.add_parser(
        "repair",
        help="Repair an EPUB."
    )
    repair.add_argument(
        "input",
        help="Input EPUB"
    )
    repair.add_argument(
        "-o",
        "--output",
        help="Output EPUB"
    )
    repair.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze repairs without writing a file."
    )
    repair.add_argument(
        "--details",
        action="store_true",
        help="Show the full before/after list of every change instead of the category summary."
    )
    repair.add_argument(
        "--class-mapping",
        metavar="FILE",
        help="Apply a confirmed class-standardization mapping (from `map-css --write-mapping`, reviewed by hand) as part of this repair -- renames chapter-heading/body-text classes and standardizes their CSS."
    )
    repair.add_argument(
        "--case3-boundaries",
        metavar="FILE",
        help="Review and apply Case 3 chapter boundaries (books with no chapter-heading words and no existing TOC -- see docs/xhtml_recoder_plan.md). If FILE doesn't exist yet, detects and writes every candidate boundary to it for review and stops without touching the book. Re-run with the same FILE, after editing it, to physically split on whatever boundaries are still listed."
    )
    repair.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output."
    )
    repair.add_argument(
        "--config",
        help=(
            "Path to a TOML config file controlling which fixes are "
            f"enabled. Defaults to ./{DEFAULT_CONFIG_FILENAME} if present, "
            "otherwise every fix runs."
        )
    )
    repair.add_argument(
        "--no-container-repair",
        action="store_true",
        help="Don't attempt to automatically repair a corrupted ZIP/EPUB container; just report the problem."
    )
    repair.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the output file if it already exists. Without -o/--output, this replaces the original file itself instead of writing <input>_fixed.epub -- you'll be asked to confirm before that happens."
    )

    # Auto-fix
    auto_fix = sub.add_parser(
        "auto-fix",
        help="One-command hands-off repair: normal repair plus high-confidence-only class standardization and book-wide text color removal, with no review step and no mapping file left behind."
    )
    auto_fix.add_argument(
        "input",
        help="Input EPUB"
    )
    auto_fix.add_argument(
        "-o",
        "--output",
        help="Output EPUB (default: <input>_autofixed.epub)"
    )
    auto_fix.add_argument(
        "--details",
        action="store_true",
        help="Show the full before/after list of every change instead of the category summary."
    )
    auto_fix.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output."
    )
    auto_fix.add_argument(
        "--config",
        help=(
            "Path to a TOML config file controlling which fixes are "
            f"enabled. Defaults to ./{DEFAULT_CONFIG_FILENAME} if present, "
            "otherwise every fix runs."
        )
    )
    auto_fix.add_argument(
        "--no-container-repair",
        action="store_true",
        help="Don't attempt to automatically repair a corrupted ZIP/EPUB container; just report the problem."
    )
    auto_fix.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the output file if it already exists. Without -o/--output, this replaces the original file itself instead of writing <input>_autofixed.epub -- you'll be asked to confirm before that happens."
    )

    # Validate
    validate = sub.add_parser(
        "validate",
        help="Run the file-integrity check only, without analyzing or repairing."
    )
    validate.add_argument(
        "input",
        help="Input EPUB"
    )
    validate.add_argument(
        "--repair",
        action="store_true",
        help="If the file fails validation, also try to repair its ZIP/EPUB container and report the result."
    )

    # Map CSS classes
    map_css = sub.add_parser(
        "map-css",
        help="Show a best-guess semantic role for every CSS class used in the book (e.g. 'calibre3' -> likely body-text), for review before renaming."
    )
    map_css.add_argument(
        "input",
        help="Input EPUB"
    )
    map_css.add_argument(
        "--no-container-repair",
        action="store_true",
        help="Don't attempt to automatically repair a corrupted ZIP/EPUB container; just report the problem."
    )
    map_css.add_argument(
        "--write-mapping",
        metavar="FILE",
        help="Also write an editable TOML mapping of high/medium-confidence chapter-heading and body-text classes to FILE, for review ahead of a future standardize/rename repair pass."
    )

    # Map chapter structure
    map_structure = sub.add_parser(
        "map-structure",
        help="Show the detected chapter structure and each boundary's split-confidence, for review before any physical splitting (see docs/xhtml_recoder_plan.md)."
    )
    map_structure.add_argument(
        "input",
        help="Input EPUB"
    )
    map_structure.add_argument(
        "--no-container-repair",
        action="store_true",
        help="Don't attempt to automatically repair a corrupted ZIP/EPUB container; just report the problem."
    )

    # Split chapters (Phase 1 proof of concept)
    split_structure = sub.add_parser(
        "split-structure",
        help="Proof of concept: physically splits any file with 2+ detected chapter boundaries into standalone chapter files and rewrites any affected in-body cross-reference links (see docs/xhtml_recoder_plan.md Phases 1-2). A mechanics test, not a finished conversion -- the TOC/nav documents aren't updated yet."
    )
    split_structure.add_argument(
        "input",
        help="Input EPUB"
    )
    split_structure.add_argument(
        "-o",
        "--output",
        help="Output EPUB (default: <input>_split.epub)"
    )
    split_structure.add_argument(
        "--details",
        action="store_true",
        help="Show the full before/after list of every cross-reference link rewritten, instead of the category summary."
    )
    split_structure.add_argument(
        "--no-container-repair",
        action="store_true",
        help="Don't attempt to automatically repair a corrupted ZIP/EPUB container; just report the problem."
    )
    split_structure.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the output file if it already exists. Without -o/--output, this replaces the original file itself instead of writing <input>_split.epub -- you'll be asked to confirm before that happens."
    )

    # Series metadata
    series = sub.add_parser(
        "series",
        help="Set (or update) a book's series name and position, written using both calibre's and EPUB3's conventions so most reading apps recognize it."
    )
    series.add_argument(
        "input",
        help="Input EPUB"
    )
    series.add_argument(
        "--name",
        help="Series name. If omitted, you'll be prompted for it."
    )
    series.add_argument(
        "--index",
        type=float,
        help="Position within the series, e.g. 3 or 3.5 for a bonus/novella entry. If omitted, you'll be prompted for it."
    )
    series.add_argument(
        "-o",
        "--output",
        help="Output EPUB (default: <input>_series.epub)"
    )
    series.add_argument(
        "--no-container-repair",
        action="store_true",
        help="Don't attempt to automatically repair a corrupted ZIP/EPUB container; just report the problem."
    )
    series.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the output file if it already exists. Without -o/--output, this replaces the original file itself instead of writing <input>_series.epub -- you'll be asked to confirm before that happens."
    )

    # Init-config
    init_config = sub.add_parser(
        "init-config",
        help="Write a default config file you can edit to turn fixes on/off."
    )
    init_config.add_argument(
        "-o",
        "--output",
        default=DEFAULT_CONFIG_FILENAME,
        help=f"Where to write the config file (default: {DEFAULT_CONFIG_FILENAME})"
    )

    return parser

def _confirm_replace_original(path: Path) -> bool:
    """Interactive last-chance confirmation for the one case that's
    actually irreversible: --overwrite with no -o/--output, which
    targets the original file itself instead of a separate _fixed/
    _split copy. Everywhere else, the original is never touched no
    matter what --overwrite does."""
    print(f"WARNING: This will replace '{path}' itself -- there's no separate copy and no undo.")
    answer = input("Type Y to continue, anything else to cancel: ").strip().lower()
    return answer == "y"


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-config":
        output = Path(args.output)
        if output.exists():
            print(f"ERROR: '{output}' already exists. Delete it or choose a different --output path.")
            sys.exit(1)
        write_default_config(output)
        print(f"Wrote default config to: {output}")
        print("Edit it to turn fixes on/off, then pass --config to analyze/repair.")
        return

    epub = Path(args.input)
    if not epub.exists():
        print(f"ERROR: '{epub}' does not exist.")
        sys.exit(1)

    if args.command == "validate":
        result = validate_epub(epub)
        result.print()
        if result.valid:
            sys.exit(0)
        if args.repair:
            print()
            print("Attempting repair...")
            repair = attempt_repair(epub)
            repair.print()
            sys.exit(0 if repair.repaired else 1)
        sys.exit(1)

    try:
        config = load_config(getattr(args, "config", None))
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: Invalid config - {e}")
        sys.exit(1)

    engine = Engine(
        verbose=getattr(args, "verbose", False),
        config=config,
        auto_repair_container=not getattr(args, "no_container_repair", False),
    )
    if args.command == "analyze":
        engine.analyze(epub, details=args.details)
    elif args.command == "map-css":
        engine.map_css(epub, write_mapping=getattr(args, "write_mapping", None))
    elif args.command == "map-structure":
        engine.map_structure(epub)
    elif args.command == "split-structure":
        output = args.output
        if output is None:
            if args.overwrite:
                # No -o given, but --overwrite was -- target the
                # original file directly rather than the usual
                # <input>_split.epub, once the person confirms.
                if not _confirm_replace_original(epub):
                    print("Cancelled.")
                    sys.exit(1)
                output = epub
            else:
                output = epub.with_stem(epub.stem + "_split")
        engine.split_chapters(epub, Path(output), overwrite=args.overwrite, details=args.details)
    elif args.command == "repair":
        output = args.output
        if output is None:
            if args.overwrite and not args.dry_run:
                # Same as above -- --overwrite alone (no -o) means
                # replace the original. A --dry-run never writes
                # anything, so there's nothing to confirm in that case.
                if not _confirm_replace_original(epub):
                    print("Cancelled.")
                    sys.exit(1)
                output = epub
            else:
                output = epub.with_stem(epub.stem + "_fixed")
        engine.repair(
            epub,
            Path(output),
            dry_run=args.dry_run,
            class_mapping=getattr(args, "class_mapping", None),
            case3_boundaries=getattr(args, "case3_boundaries", None),
            overwrite=args.overwrite,
            details=args.details,
        )
    elif args.command == "series":
        name = args.name
        if name is None:
            name = input("Series name: ").strip()
        if not name:
            print("ERROR: Series name can't be empty.")
            sys.exit(1)

        index = args.index
        if index is None:
            raw_index = input("Position in series (e.g. 3 or 3.5, leave blank for none): ").strip()
            if raw_index:
                try:
                    index = float(raw_index)
                except ValueError:
                    print(f"ERROR: '{raw_index}' isn't a valid number.")
                    sys.exit(1)

        output = args.output
        if output is None:
            if args.overwrite:
                if not _confirm_replace_original(epub):
                    print("Cancelled.")
                    sys.exit(1)
                output = epub
            else:
                output = epub.with_stem(epub.stem + "_series")
        engine.set_series(
            epub,
            Path(output),
            name=name,
            index=index,
            overwrite=args.overwrite,
        )
    elif args.command == "auto-fix":
        output = args.output
        if output is None:
            if args.overwrite:
                if not _confirm_replace_original(epub):
                    print("Cancelled.")
                    sys.exit(1)
                output = epub
            else:
                output = epub.with_stem(epub.stem + "_autofixed")
        engine.auto_fix(
            epub,
            Path(output),
            overwrite=args.overwrite,
            details=args.details,
        )

if __name__ == "__main__":
    main()
