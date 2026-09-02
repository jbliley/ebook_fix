"""
metadata.calibre_backend

Reads the metadata carried in a Calibre library's metadata.opf sidecar
file -- a standalone OPF file, not something inside an EPUB container,
so it can't be loaded through ebook_fix.parser's normal book-loading
pipeline. This module parses it directly.

Read-only for now, matching identifiers.py and core_fields.py -- this
records what metadata.opf says, for metadata.merge to compare against
what the EPUB itself says. Writing normalized/reconciled values back
out to metadata.opf (and syncing metadata.db via calibredb) is future
work, once a reconciliation decision exists to write.
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
class _OpfShim:
    """A minimal stand-in for a real Book object, exposing just enough
    (.opf_document) for ebook_fix.series.read() to work unmodified
    against a bare metadata.opf file, the same way it already works
    against a real Book's own internal OPF."""
    opf_document: object = None


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

    series_info = series_metadata.read(_OpfShim(opf_document=opf_root))
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
