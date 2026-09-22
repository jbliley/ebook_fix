"""
ebook_fix.fb2

FB2 (FictionBook) support: analysis (analyzer.py, the original
module -- kept importable from here, ebook_fix.fb2, so existing
callers don't need to change) plus the newer reader/markup/convert
modules that turn an FB2 into an EPUB (imported directly from their
own submodules, the same way ebook_fix.mobi's reader/markup/convert
are -- this __init__.py re-exports only the pre-existing analysis
API, nothing more).
"""
from ebook_fix.fb2.analyzer import FB2_EXTENSIONS, analyze_fb2, print_fb2_report

__all__ = ["FB2_EXTENSIONS", "analyze_fb2", "print_fb2_report"]
