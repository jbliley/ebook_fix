"""
ebook_fix.report

A small container for what a repair module found during analysis.
Kept separate from the modules themselves so every module reports
results the same way.

Each issue carries a short, fixed `category` (used for grouping in
the summary view) plus a free-text `description` with the specific
detail (which file, which src, etc). Modules pick the category;
callers who want the full line-by-line list can still get it via
print(details=True).
"""

from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from rich.console import Console
from rich.table import Table

console = Console()


@dataclass(slots=True)
class Issue:
    location: str
    category: str
    description: str


@dataclass(slots=True)
class Report:
    module_name: str
    issues: list[Issue] = field(default_factory=list)

    def add(self, location: str, category: str, description: str | None = None) -> None:
        self.issues.append(Issue(location, category, description or category))

    @property
    def count(self) -> int:
        return len(self.issues)

    @property
    def category_counts(self) -> Counter:
        return Counter(issue.category for issue in self.issues)

    @property
    def locations_affected(self) -> int:
        return len({issue.location for issue in self.issues})

    def print(self, details: bool = False) -> None:
        if details:
            self._print_details()
        else:
            self._print_summary()

    def _print_summary(self) -> None:
        if not self.issues:
            console.print("  No issues found.")
            return

        console.print(
            f"  {self.count} issue(s) found across {self.locations_affected} file(s):"
        )
        table = Table(show_header=True, header_style="bold")
        table.add_column("Issue Type")
        table.add_column("Count", justify="right")
        for category, count in self.category_counts.most_common():
            table.add_row(category, str(count))
        console.print(table)
        console.print("  (run with --details for the full list)")

    def _print_details(self) -> None:
        if not self.issues:
            console.print("  No issues found.")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("Location")
        table.add_column("Issue")
        for issue in self.issues:
            table.add_row(issue.location, issue.description)
        console.print(table)
