#!/usr/bin/env python3
from pathlib import Path
import argparse
import sys
from engine import Engine
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
    elif args.command == "repair":
        output = args.output
        if output is None:
            output = epub.with_stem(epub.stem + "_fixed")
        engine.repair(
            epub,
            Path(output),
            dry_run=args.dry_run
        )

if __name__ == "__main__":
    main()
