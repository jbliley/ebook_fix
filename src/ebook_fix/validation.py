"""
ebook_fix.validation

Pre-flight integrity checks for an EPUB file. These run before
anything else -- before the parser even tries to open the file --
since none of the repair modules can do anything useful with a file
that fails here.

Checks, in order (each one stops at the first failure, since every
later check depends on the ones before it having passed):

1. Is the file readable?
2. Does it begin with the ZIP signature ("PK")?
3. Can zipfile.ZipFile() open it?
4. Is there an intact central directory?
5. Does META-INF/container.xml exist?
6. Can the OPF (package document) be located?

Checks 2-6 work on raw bytes (validate_epub_bytes) so container_repair
can validate a candidate fix in memory before writing anything out;
validate_epub() is the normal entry point and adds the readability
check on top.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree
from rich.console import Console

from ebook_fix.report import print_header

console = Console()

CONTAINER_PATH = "META-INF/container.xml"
CONTAINER_NAMESPACE = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class ValidationResult:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_check(self) -> CheckResult | None:
        for check in self.checks:
            if not check.passed:
                return check
        return None

    def print(self) -> None:
        print_header("File integrity check")
        for check in self.checks:
            status = "[green]PASS[/green]" if check.passed else "[red]FAIL[/red]"
            console.print(f"  {check.name}: {status}")

        failed = self.failed_check
        if failed is not None:
            console.print(f"[red]File failed validation:[/red] {failed.name}")
            if failed.detail:
                console.print(f"  {failed.detail}")


def validate_epub(path: str | Path) -> ValidationResult:
    path = Path(path)
    result = ValidationResult()

    # 1. Is the file readable?
    try:
        data = path.read_bytes()
    except OSError as e:
        result.checks.append(CheckResult("File is readable", False, str(e)))
        return result
    result.checks.append(CheckResult("File is readable", True))

    result.checks.extend(validate_epub_bytes(data).checks)
    return result


def validate_epub_bytes(data: bytes) -> ValidationResult:
    """Run checks 2-6 against in-memory EPUB bytes."""
    result = ValidationResult()

    # 2. Does it begin with the ZIP signature ("PK")?
    if data[:2] != b"PK":
        result.checks.append(CheckResult(
            "File begins with the ZIP signature (PK)",
            False,
            f"First two bytes were {data[:2]!r}, expected b'PK'. "
            "This isn't a ZIP-based file, so it can't be a valid EPUB.",
        ))
        return result
    result.checks.append(CheckResult("File begins with the ZIP signature (PK)", True))

    # 3. Can zipfile.ZipFile() open it?
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as e:
        result.checks.append(CheckResult("ZIP archive can be opened", False, str(e)))
        return result
    result.checks.append(CheckResult("ZIP archive can be opened", True))

    with archive:
        # 4. Is there an intact central directory?
        try:
            names = archive.namelist()
            if not names:
                raise zipfile.BadZipFile("Archive has no entries.")
            bad_entry = archive.testzip()
        except (zipfile.BadZipFile, OSError) as e:
            result.checks.append(CheckResult("Central directory is present and intact", False, str(e)))
            return result
        if bad_entry is not None:
            result.checks.append(CheckResult(
                "Central directory is present and intact",
                False,
                f"CRC check failed for entry: {bad_entry} (the archive's contents are corrupted).",
            ))
            return result
        result.checks.append(CheckResult("Central directory is present and intact", True))

        # 5. Does META-INF/container.xml exist?
        if CONTAINER_PATH not in names:
            result.checks.append(CheckResult(
                f"{CONTAINER_PATH} exists",
                False,
                f"'{CONTAINER_PATH}' isn't present in the archive.",
            ))
            return result
        result.checks.append(CheckResult(f"{CONTAINER_PATH} exists", True))

        # 6. Can the OPF (package document) be located?
        try:
            container_xml = archive.read(CONTAINER_PATH)
            tree = etree.fromstring(container_xml)
            rootfile = tree.find(".//c:rootfile", namespaces=CONTAINER_NAMESPACE)
            if rootfile is None:
                raise ValueError("No <rootfile> entry found in container.xml.")
            opf_path = rootfile.attrib.get("full-path")
            if not opf_path:
                raise ValueError("<rootfile> is missing its 'full-path' attribute.")
            if opf_path not in names:
                raise ValueError(
                    f"container.xml points at '{opf_path}', which isn't in the archive."
                )
        except Exception as e:
            result.checks.append(CheckResult("OPF package document can be located", False, str(e)))
            return result
        result.checks.append(CheckResult("OPF package document can be located", True))

    return result
