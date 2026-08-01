"""
ebook_fix.report

A small container for what a repair module found during analysis.
Kept separate from the modules themselves so every module reports
results the same way.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from rich.console import Console
from rich.table import Table

console = Console()


@dataclass(slots=True)
class Issue:
    location: str
    description: str


@dataclass(slots=True)
class Report:
    module_name: str
    issues: list[Issue] = field(default_factory=list)

    def add(self, location: str, description: str) -> None:
        self.issues.append(Issue(location, description))

    @property
    def count(self) -> int:
        return len(self.issues)

    def print(self) -> None:
        if not self.issues:
            console.print("  No issues found.")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("Location")
        table.add_column("Issue")
        for issue in self.issues:
            table.add_row(issue.location, issue.description)
        console.print(table)
