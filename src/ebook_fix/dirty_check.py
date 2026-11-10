"""
ebook_fix.dirty_check

The dirty check of a Fix. After the repairs of a pass changed something, the
Fix goes round again to see whether anything is left. Most repairs find
nothing the second time. The tracker remembers, for each repair, the state of
the book at the moment it last found nothing to do. If the book is still in
that state when the repair comes up again, running it would find nothing
again, so the engine skips it.

A repair that changes the book moves the state on, which makes every repair
run again, so the check can only skip work that is certain to find nothing.
(Before it was switched on, it ran alongside the real runs for several
releases and was never wrong: see `docs/single_analysis_plan.md`.)

The tracker also notes when the book changed during a pass although no repair
reported a change (a repair that edits without counting would make every
skip unsafe). It then forgets everything it knew, so nothing is skipped on
stale information.
"""

from __future__ import annotations

from ebook_fix.analysis_store import book_state


class DirtyTracker:
    def __init__(self, book):
        self.book = book
        # A digest of the book as the last repair that changed it left it.
        # Worked out only when a repair reports a change, so a run that finds
        # nothing costs nothing here.
        self.state = book_state(book)
        # repair name -> the book state at which it last found nothing to do
        self._clean_at = {}
        self.runs = 0
        self.skipped = 0
        self.unreported_changes = 0

    def is_clean(self, name) -> bool:
        """True when the repair last found nothing to do and the book is as it
        was then, so a rerun would find nothing."""
        return self._clean_at.get(name) == self.state

    def ran(self, name, count) -> None:
        """Record one repair run and what it changed."""
        self.runs += 1
        if count:
            self.state = book_state(self.book)
            self._clean_at.pop(name, None)
        else:
            self._clean_at[name] = self.state

    def end_of_pass(self) -> bool:
        """Compare the book with the state the tracker knows. Returns True when
        the book changed although no repair reported it."""
        if book_state(self.book) != self.state:
            self.unreported_changes += 1
            self.state = book_state(self.book)
            self._clean_at.clear()
            return True
        return False

    def summary(self) -> str:
        """One short line for the Fix log."""
        return (
            f"{self.runs} repair runs, {self.skipped} skipped as unchanged, "
            f"{self.unreported_changes} unreported change(s)"
        )
