"""
ebook_fix.html

HTML support: turns a single HTML (or XHTML) book file into an EPUB
(reader.py + convert.py), the same way ebook_fix.fb2 and ebook_fix.mobi
do, so the whole existing analysis and repair pipeline can then work on
the result. Imported from its own submodules; this __init__.py only
exposes the file extensions the CLI and GUI check for.
"""
HTML_EXTENSIONS = {".html", ".htm"}

__all__ = ["HTML_EXTENSIONS"]
