"""
ebook_fix.dirty_check

A "shadow check" for the confirming pass of a Fix. It changes nothing in the
book and nothing a repair does: it only watches and writes to the Fix log.

Background: after the repairs of a pass changed something, the Fix runs every
repair again (the confirming pass) to see whether anything is left. Most of
those runs find nothing. The plan in `docs/single_analysis_plan.md` ends with
replacing that pass by a dirty check: a repair whose input has not changed
since it last ran and found nothing to do is skipped, because running it again
would find nothing again.

That is only safe if the prediction is right, so before anything is skipped
the prediction is made next to the real runs and compared. For every repair
run, the tracker asks: "would the dirty check have skipped this run?" (the
repair last found nothing to do and the book has not changed since). If the
run then did change something, the prediction was wrong, and that is logged as
a disagreement. A run the check would have skipped that also found nothing is
an agreement, and counts towards the work a skip would save.

The tracker also notes when the book changed during a pass although no repair
reported a change (a repair that edits without counting would make every
prediction unsafe).
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
        self.would_skip = 0  # agreed: predicted clean, and found nothing
        self.disagreements = []  # (name, count) predicted clean, but changed something
        self.unreported_changes = 0

    def is_clean(self, name) -> bool:
        """True when the repair last found nothing to do and the book is as it
        was then, so a rerun would be skipped."""
        return self._clean_at.get(name) == self.state

    def ran(self, name, count, was_clean) -> bool:
        """Record one repair run (`was_clean` is `is_clean(name)` from just
        before it ran). Returns True when the prediction was wrong."""
        self.runs += 1
        wrong = False
        if count:
            self.state = book_state(self.book)
            self._clean_at.pop(name, None)
            if was_clean:
                wrong = True
                self.disagreements.append((name, count))
        else:
            self._clean_at[name] = self.state
            if was_clean:
                self.would_skip += 1
        return wrong

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
        """One short line for the Fix log: how many runs the dirty check would
        have skipped, how many predictions were wrong, and how many passes
        changed the book without a repair reporting it."""
        return (
            f"{self.would_skip} of {self.runs} runs skippable, "
            f"{len(self.disagreements)} wrong, {self.unreported_changes} unreported"
        )
