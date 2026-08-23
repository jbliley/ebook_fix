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

Output style
------------
All analysis output across the project follows the same plain-text
format: an underlined header, then "Category: Count" lines beneath
it. No tables, no color, except for pass/fail or warning markers.
`print_header()` is the shared helper for that header style -- other
modules (validation, container_repair, engine) import it from here
so every section of output looks the same.
"""

from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from rich.console import Console
from rich.table import Table

console = Console()


def print_header(text: str) -> None:
    """Print a section/category header, underlined, no color."""
    console.print(f"[underline]{text}[/underline]")


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

    def print(self, details: bool = False, verb: str = "found") -> None:
        if details:
            self._print_details(verb=verb)
        else:
            self._print_summary(verb=verb)

    def _print_summary(self, verb: str = "found") -> None:
        if not self.issues:
            console.print(f"  No issues {verb}.")
            return

        console.print(
            f"  {self.count} issue(s) {verb} across {self.locations_affected} file(s):"
        )
        print_header("  Issue Type")
        for category, count in self.category_counts.most_common():
            console.print(f"  {category}: {count}")
        console.print("  (run with --details for the full list)")

    def _print_details(self, verb: str = "found") -> None:
        if not self.issues:
            console.print(f"  No issues {verb}.")
            return

        console.print(
            f"  {self.count} issue(s) {verb} across {self.locations_affected} file(s):"
        )
        print_header("  Location: Issue")
        for issue in self.issues:
            console.print(f"  {issue.location}: {issue.description}")
