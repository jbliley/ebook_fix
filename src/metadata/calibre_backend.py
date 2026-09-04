"""
metadata.calibre_backend

Reads the metadata carried in a Calibre library's metadata.opf sidecar
file -- a standalone OPF file, not something inside an EPUB container,
so it can't be loaded through ebook_fix.parser's normal book-loading
pipeline. This module parses it directly.

This module itself is still read-only: it records what metadata.opf
says, for metadata.merge to compare against what the EPUB itself
says. Writing confidently-resolved values back out to metadata.opf
lives in metadata.calibre_write instead (it needs its own file-write
handling, atomic replace, etc.) -- but that module reuses this one's
OpfShim to do it, so both reading and writing a bare metadata.opf go
through the same lightweight stand-in for a real Book. Syncing
metadata.db via calibredb is still future work, once there's a way to
verify it against a real Calibre library.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ebook_fix import series as series_metadata
from metadata.core_fields import BookCoreFieldsSummary
from metadata.identifiers import BookIdentifierSummary, extract_identifiers_from_opf

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"


@dataclass(slots=True)
class OpfShim:
    """A minimal stand-in for a real Book object, exposing just enough
    (.opf_document, .opf_modified, .mark_modified()) for ebook_fix.series
    and metadata.core_fields' write functions to work unmodified
    against a bare metadata.opf file, the same way they already work
    against a real Book's own internal OPF. Used both for reading here
    (core_fields read only needs .opf_document) and for writing, in
    metadata.calibre_write."""
    opf_document: object = None
    opf_modified: bool = False

    def mark_modified(self) -> None:
        # No-op: a bare metadata.opf sidecar isn't part of an EPUB
        # archive that needs re-zipping, so there's nothing to mark.
        # metadata.calibre_write serializes opf_document straight back
        # out to the file itself once all the writes are done.
        pass


@dataclass(slots=True)
class CalibreOpfSnapshot:
    identifiers: BookIdentifierSummary = field(default_factory=BookIdentifierSummary)
    core_fields: BookCoreFieldsSummary = field(default_factory=BookCoreFieldsSummary)


def _text(el) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _read_core_fields(opf_root) -> BookCoreFieldsSummary:
    result = BookCoreFieldsSummary()

    metadata_el = opf_root.find(f"{{{OPF_NS}}}metadata")
    if metadata_el is not None:
        result.title = _text(metadata_el.find(f"{{{DC_NS}}}title"))
        result.author = _text(metadata_el.find(f"{{{DC_NS}}}creator"))
        result.language = _text(metadata_el.find(f"{{{DC_NS}}}language"))
        result.publisher = _text(metadata_el.find(f"{{{DC_NS}}}publisher"))
        result.date = _text(metadata_el.find(f"{{{DC_NS}}}date"))
        result.rights = _text(metadata_el.find(f"{{{DC_NS}}}rights"))
        result.description = _text(metadata_el.find(f"{{{DC_NS}}}description"))
        result.subjects = [
            _text(el) for el in metadata_el.findall(f"{{{DC_NS}}}subject")
            if _text(el)
        ]

    series_info = series_metadata.read(OpfShim(opf_document=opf_root))
    result.series = series_info.name
    result.series_index = series_info.index

    return result


def read_metadata_opf(opf_path) -> CalibreOpfSnapshot:
    """Parses a metadata.opf file and returns its identifiers and core
    fields, classified/grouped the same way an EPUB's own internal OPF
    would be. Returns an empty snapshot (rather than raising) if the
    file is missing or fails to parse, since a Calibre sidecar being
    unreadable shouldn't take down the whole analysis pass -- the
    EPUB's own metadata is still perfectly usable on its own."""
    opf_path = Path(opf_path)
    if not opf_path.is_file():
        return CalibreOpfSnapshot()

    try:
        tree = etree.parse(str(opf_path))
    except etree.XMLSyntaxError:
        return CalibreOpfSnapshot()

    root = tree.getroot()
    return CalibreOpfSnapshot(
        identifiers=BookIdentifierSummary(identifiers=extract_identifiers_from_opf(root)),
        core_fields=_read_core_fields(root),
    )
