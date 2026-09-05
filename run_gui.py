#!/usr/bin/env python3
"""Launches the ebook_fix GUI from the repo root, no install step
needed -- mirrors src/cli.py's existing "run without installing"
wrapper for the CLI. Double-click run_gui.bat on Windows, or run this
directly: `python run_gui.py`.

A browser tab opens automatically at http://127.0.0.1:5000. Leave
this window open while using the GUI; closing it (or pressing Ctrl+C)
stops the program. Nothing here is sent over the internet -- it's a
normal program that happens to display itself in your browser.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from gui.app import main

if __name__ == "__main__":
    main()
