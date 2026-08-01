#!/usr/bin/env python3
from pathlib import Path
import argparse
import sys
from engine import Engine

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
    return parser

def main():
    parser = build_parser()
    args = parser.parse_args()
    epub = Path(args.input)
    if not epub.exists():
        print(f"ERROR: '{epub}' does not exist.")
        sys.exit(1)
    engine = Engine(verbose=getattr(args, "verbose", False))
    if args.command == "analyze":
        engine.analyze(epub)
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
