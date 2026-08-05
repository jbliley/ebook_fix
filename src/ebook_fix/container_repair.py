"""
ebook_fix.container_repair

Best-effort, conservative repair of a corrupted ZIP/EPUB container,
run only after validate_epub() has already found a problem. Every
repair here only touches the container structure (never chapter
text) and is skipped -- with a clear explanation -- whenever a fix
can't be made with confidence.

What CAN be repaired:
  - Junk bytes before the start of the ZIP archive (e.g. a corrupted
    copy that picked up extra leading bytes).
  - A missing or damaged central directory / end-of-central-directory
    record, rebuilt from the individual entries -- but only when every
    entry stores its size directly in its own local header (i.e.
    wasn't written in "streaming" mode with the size recorded in a
    trailing data descriptor, which can't be resolved without risking
    data corruption).
  - A missing META-INF/container.xml, or one that doesn't point at a
    valid OPF, when exactly one .opf file exists in the archive to
    point it at.

What CANNOT be repaired (and is reported as such, with a reason):
  - A file with no ZIP signature anywhere in it -- the ZIP structure
    itself is gone, not just damaged, which means the underlying data
    is most likely lost.
  - CRC mismatches inside an entry -- the entry's bytes themselves are
    corrupted; there's nothing to safely reconstruct them from.
  - Entries written with ZIP data descriptors, when the central
    directory/EOCD is also missing.
  - Ambiguous (multiple) or missing OPF candidates.
"""

from __future__ import annotations

import io
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree
from rich.console import Console

from ebook_fix.report import print_header
from ebook_fix.validation import (
    CONTAINER_NAMESPACE,
    CONTAINER_PATH,
    ValidationResult,
    validate_epub_bytes,
)

console = Console()

LOCAL_FILE_HEADER_SIG = b"PK\x03\x04"
CENTRAL_DIR_SIG = b"PK\x01\x02"
EOCD_SIG = b"PK\x05\x06"

# Local file header fields after the 4-byte signature (26 bytes total).
_LOCAL_HEADER_FMT = "<HHHHHIIIHH"
_LOCAL_HEADER_SIZE = struct.calcsize(_LOCAL_HEADER_FMT)

# Central directory header fields after the 4-byte signature.
_CENTRAL_HEADER_FMT = "<HHHHHHIIIHHHHHII"

# End-of-central-directory fields after the 4-byte signature.
_EOCD_FMT = "<HHHHIIH"

DATA_DESCRIPTOR_FLAG = 0x08


@dataclass(slots=True)
class RepairAction:
    description: str
    succeeded: bool


@dataclass(slots=True)
class RepairResult:
    repaired: bool
    data: bytes | None = None
    actions: list[RepairAction] = field(default_factory=list)
    validation: ValidationResult | None = None
    failure_reason: str = ""

    def print(self) -> None:
        if self.actions:
            print_header("Repair attempt")
            for action in self.actions:
                mark = "[green]done[/green]" if action.succeeded else "[red]skipped[/red]"
                console.print(f"  [{mark}] {action.description}")
        if self.repaired:
            console.print("[green]The file was repaired and now passes the integrity check.[/green]")
        elif self.failure_reason:
            console.print(f"[red]Could not repair this file:[/red] {self.failure_reason}")


def attempt_repair(path: str | Path) -> RepairResult:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        return RepairResult(repaired=False, failure_reason=f"File isn't readable: {e}")

    actions: list[RepairAction] = []

    # 1. Trim any junk before the ZIP signature.
    if not data.startswith(b"PK"):
        idx = data.find(LOCAL_FILE_HEADER_SIG)
        if idx <= 0:
            return RepairResult(
                repaired=False,
                actions=actions,
                failure_reason=(
                    "No ZIP signature ('PK') was found anywhere in the file. This "
                    "isn't a case of extra junk bytes -- the ZIP structure itself "
                    "appears to be missing or overwritten, which means the "
                    "underlying data is most likely lost and can't be reconstructed."
                ),
            )
        removed = idx
        data = data[idx:]
        actions.append(RepairAction(
            f"Remove {removed} byte(s) of junk data before the start of the ZIP archive.",
            True,
        ))

    # 2. If the archive doesn't open cleanly, try rebuilding the
    #    central directory / EOCD from the local file headers.
    if not _opens_cleanly(data):
        rebuilt, action = _rebuild_central_directory(data)
        actions.append(action)
        if rebuilt is None:
            return RepairResult(
                repaired=False,
                actions=actions,
                failure_reason=action.description,
            )
        data = rebuilt

    # 3. If META-INF/container.xml is missing or doesn't point at a
    #    valid OPF, try to regenerate it.
    data, container_action = _repair_container_xml(data)
    if container_action is not None:
        actions.append(container_action)
        if not container_action.succeeded:
            return RepairResult(
                repaired=False,
                actions=actions,
                failure_reason=container_action.description,
            )

    validation = validate_epub_bytes(data)
    if validation.valid:
        return RepairResult(repaired=True, data=data, actions=actions, validation=validation)

    failed = validation.failed_check
    return RepairResult(
        repaired=False,
        actions=actions,
        validation=validation,
        failure_reason=(failed.detail or failed.name) if failed else "Unknown validation failure.",
    )


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _opens_cleanly(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return bool(archive.namelist()) and archive.testzip() is None
    except (zipfile.BadZipFile, OSError):
        return False


def _rebuild_central_directory(data: bytes):
    """
    Walk the file sequentially from the first local file header,
    parsing each entry's own size fields to jump straight to the next
    one. If that walk lands cleanly on either the start of an existing
    central directory or the end of the data, we have everything we
    need to write a fresh central directory + EOCD record.
    """
    action_name = "Rebuild the ZIP index from local file headers"

    if not data.startswith(LOCAL_FILE_HEADER_SIG):
        return None, RepairAction(
            f"{action_name} (the file doesn't start with a local file header).",
            False,
        )

    entries = []
    offset = 0
    n = len(data)

    while offset < n:
        sig = data[offset:offset + 4]
        if sig in (CENTRAL_DIR_SIG, EOCD_SIG):
            break
        if sig != LOCAL_FILE_HEADER_SIG:
            return None, RepairAction(
                f"{action_name} (unexpected data at byte {offset}; it doesn't match "
                "what a valid ZIP local file header looks like there, so the layout "
                "can't be trusted from this point on).",
                False,
            )

        header_bytes = data[offset + 4:offset + 4 + _LOCAL_HEADER_SIZE]
        if len(header_bytes) < _LOCAL_HEADER_SIZE:
            return None, RepairAction(f"{action_name} (file ends in the middle of a header).", False)

        (_version_needed, flags, method, mod_time, mod_date, crc32,
         comp_size, uncomp_size, name_len, extra_len) = struct.unpack(_LOCAL_HEADER_FMT, header_bytes)

        if flags & DATA_DESCRIPTOR_FLAG:
            return None, RepairAction(
                f"{action_name} (one or more entries were written in streaming mode, "
                "with their size stored after the data instead of in the header -- "
                "this can't be resolved safely without the original index).",
                False,
            )

        name_start = offset + 4 + _LOCAL_HEADER_SIZE
        name = data[name_start:name_start + name_len]
        data_start = name_start + name_len + extra_len
        data_end = data_start + comp_size

        if data_end > n:
            label = name.decode("utf-8", "replace") or f"entry at byte {offset}"
            return None, RepairAction(
                f"{action_name} ('{label}' claims more data than the file actually contains).",
                False,
            )

        entries.append({
            "name": name,
            "flags": flags,
            "method": method,
            "mod_time": mod_time,
            "mod_date": mod_date,
            "crc32": crc32,
            "comp_size": comp_size,
            "uncomp_size": uncomp_size,
            "header_offset": offset,
        })
        offset = data_end

    if not entries:
        return None, RepairAction(f"{action_name} (no valid entries were found).", False)

    body_end = offset
    central_dir_offset = body_end
    central_dir = bytearray()
    for e in entries:
        central_dir += CENTRAL_DIR_SIG
        central_dir += struct.pack(
            _CENTRAL_HEADER_FMT,
            20,             # version made by
            20,             # version needed to extract
            e["flags"],
            e["method"],
            e["mod_time"],
            e["mod_date"],
            e["crc32"],
            e["comp_size"],
            e["uncomp_size"],
            len(e["name"]),
            0,              # extra field length
            0,              # comment length
            0,              # disk number start
            0,              # internal file attributes
            0,              # external file attributes
            e["header_offset"],
        )
        central_dir += e["name"]

    eocd = EOCD_SIG + struct.pack(
        _EOCD_FMT,
        0, 0,
        len(entries),
        len(entries),
        len(central_dir),
        central_dir_offset,
        0,
    )

    rebuilt = data[:body_end] + bytes(central_dir) + eocd
    count = len(entries)
    return rebuilt, RepairAction(
        f"{action_name} ({count} entr{'y' if count == 1 else 'ies'} recovered).",
        True,
    )


def _repair_container_xml(data: bytes):
    """
    If META-INF/container.xml is missing, or exists but doesn't point
    at a real OPF, and exactly one .opf file exists in the archive,
    write a fresh container.xml pointing at it.

    Returns (possibly-updated data, RepairAction | None). The action
    is None when container.xml already looks fine and nothing changed.
    """
    action_name = "Regenerate META-INF/container.xml"

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            container_ok = False
            if CONTAINER_PATH in names:
                try:
                    tree = etree.fromstring(archive.read(CONTAINER_PATH))
                    rootfile = tree.find(".//c:rootfile", namespaces=CONTAINER_NAMESPACE)
                    opf_path = rootfile.attrib.get("full-path") if rootfile is not None else None
                    container_ok = bool(opf_path) and opf_path in names
                except Exception:
                    container_ok = False

            if container_ok:
                return data, None

            opf_candidates = [n for n in names if n.lower().endswith(".opf")]
            if len(opf_candidates) != 1:
                if not opf_candidates:
                    detail = "no .opf file exists anywhere in the archive to point one at."
                else:
                    detail = (
                        f"{len(opf_candidates)} .opf files exist "
                        f"({', '.join(opf_candidates)}), so it's ambiguous which one "
                        "is the real package document."
                    )
                return data, RepairAction(f"{action_name} ({detail})", False)

            opf_path = opf_candidates[0]
            new_container_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<container version="1.0" '
                'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                "  <rootfiles>\n"
                f'    <rootfile full-path="{opf_path}" media-type="application/oebps-package+xml"/>\n'
                "  </rootfiles>\n"
                "</container>\n"
            ).encode("utf-8")

            new_data = _replace_zip_entry(data, CONTAINER_PATH, new_container_xml)
            return new_data, RepairAction(f"{action_name}, pointing at '{opf_path}'.", True)
    except (zipfile.BadZipFile, OSError) as e:
        return data, RepairAction(f"{action_name} (couldn't reopen the archive: {e}).", False)


def _replace_zip_entry(data: bytes, name: str, content: bytes) -> bytes:
    """Return a copy of the ZIP archive with `name` set to `content`,
    added if it wasn't already present. `mimetype`, if present, is
    kept first and uncompressed, per the EPUB spec."""
    with zipfile.ZipFile(io.BytesIO(data)) as src:
        out_buffer = io.BytesIO()
        with zipfile.ZipFile(out_buffer, "w", zipfile.ZIP_DEFLATED) as dest:
            if "mimetype" in src.namelist():
                dest.writestr(
                    zipfile.ZipInfo("mimetype"),
                    src.read("mimetype"),
                    zipfile.ZIP_STORED,
                )
            for item in src.infolist():
                if item.filename in ("mimetype", name):
                    continue
                dest.writestr(item, src.read(item.filename))
            dest.writestr(name, content)
        return out_buffer.getvalue()
