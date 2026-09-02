"""
ebook_fix.modules.running_title_repair

Removes the running book-title header ebook_fix.running_title already
found (see that module for the detection rule). Reads the recorded
RunningTitleMarker instead of re-scanning the book, same "analysis is
descriptive, repair decides" split every other module in this project
follows.

What actually happens
-----------------------
The marker's own heading element usually isn't the whole thing worth
removing -- in every case seen so far (Calibre-style conversions), the
heading sits alone inside its own wrapper <div>, immediately before a
second wrapper <div> holding the chapter's real heading and content:

    <div class="calibre4">
      <h1 class="calibre5">Battle Of The Mountain Man</h1>
    </div>
    <div class="calibre4">
      <h2 class="calibre6">One</h2>
      ...

This walks up from the matched heading to whichever ancestor is a
direct child of <body> (mirroring gutenberg_repair.py's own
_ancestor_under_body helper -- not shared code between the two modules
today since it's a handful of lines each, but the same idea), and
removes that whole ancestor. If the heading itself already IS a direct
child of <body> (no wrapper div at all), only the heading is removed.
"""

from __future__ import annotations

from ebook_fix.report import Report
from ebook_fix.running_title import analyze_book_running_titles


class RunningTitleRepair:
    name = "Running Title Removal"

    def __init__(self, config=None):
        self.config = config

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        if self.config is not None and not getattr(self.config, "enabled", True):
            return report

        rt = (
            analysis.running_titles if analysis is not None and hasattr(analysis, "running_titles")
            else analyze_book_running_titles(book)
        )
        for marker in rt.markers:
            report.add(
                marker.href,
                "Running book-title header to remove",
                f"Chapter opens with the book's own title ({marker.heading_text!r}) "
                "repeated as a running header before the real chapter content",
            )
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        report = Report(self.name)
        if self.config is not None and not getattr(self.config, "enabled", True):
            return report

        rt = (
            analysis.running_titles if analysis is not None and hasattr(analysis, "running_titles")
            else analyze_book_running_titles(book)
        )
        if not rt.markers:
            return report

        changed = False
        by_href = {getattr(ch, "href", ""): ch for ch in getattr(book, "chapters", []) or []}

        for marker in rt.markers:
            chapter = by_href.get(marker.href)
            if chapter is None or marker.element is None:
                continue
            body = _find_body(chapter.document)
            if body is None:
                continue
            top = _ancestor_under_body(body, marker.element)
            if top is None:
                continue
            _remove_keep_tail(top)
            chapter.modified = True
            changed = True
            report.add(
                marker.href,
                "Running book-title header removed",
                f"Removed repeated book title ({marker.heading_text!r}) from the top of the chapter",
            )

        if changed:
            book.mark_modified()

        return report


# ---------------------------------------------------------------------
# Shared tree helpers -- same small helpers gutenberg_repair.py uses,
# kept local rather than factored out for a second, one-line-different
# caller (see module docstring).
# ---------------------------------------------------------------------

def _find_body(tree):
    if tree is None:
        return None
    return tree.find(".//{*}body")


def _ancestor_under_body(body, el):
    node = el
    while node is not None:
        parent = node.getparent()
        if parent is None:
            return None
        if parent is body:
            return node
        node = parent
    return None


def _remove_keep_tail(el):
    tail = el.tail
    parent = el.getparent()
    if parent is None:
        return
    prev = el.getprevious()
    parent.remove(el)
    if tail and tail.strip():
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
