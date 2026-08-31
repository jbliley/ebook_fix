#!/usr/bin/env python3
"""Backward-compatible wrapper.

The real command-line implementation now lives in
ebook_fix/cli.py, so it can be packaged into an installable
`ebook-fix` command (see pyproject.toml's [project.scripts]).

This file is kept purely so the existing `python cli.py <command>
[options]`, run from inside src/, keeps working exactly as before
for anyone who hasn't installed the package -- no new syntax to
learn, no PATH setup required.
"""
import sys
from pathlib import Path

# Make sure the ebook_fix package (living right next to this file)
# is importable even if this script is invoked from somewhere other
# than src/ itself.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ebook_fix.cli import main

if __name__ == "__main__":
    main()
