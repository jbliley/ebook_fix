"""
ebook_fix.series

Reads and writes series metadata on a book: which series it belongs
to, and its position within that series. See
docs/series_metadata_plan.md for the background and open questions
this resolves.

Two independent real-world conventions exist, and this module
supports both -- the same posture ebook_fix.cover already takes
toward EPUB2's <meta name="cover"> vs EPUB3's
properties="cover-image": write (and recognize) both signals rather
than picking one and leaving readers that only check the other
convention unable to show it.

- calibre's convention: <meta name="calibre:series" content="Name"/>
  and <meta name="calibre:series_index" content="3"/> in the OPF
  <metadata> block. Not part of the actual EPUB spec, but it's the de
  facto standard -- calibre itself, and most reading apps/devices
  that show series info at all, recognize this exact pair.
- EPUB3's actual standard: the belongs-to-collection mechanism -- a
  <meta property="belongs-to-collection"> element holding the series
  name, refined by <meta refines="#id" property="collection-type">
  series</meta> and <meta refines="#id" property="group-position">
  3</meta>.

This is a hand-supplied fact, not something the analyzer detects on
its own -- there's no reliable signal in a book's own content that
says what series it belongs to. read() reports whatever is already
on the book (used by `analyze` to show it, read-only); write() is
only ever called with a name + index a person, or eventually a GUI
field, explicitly provided (used by the `series` command).

write() is idempotent: running it again updates the existing tags in
place rather than adding a duplicate pair.
"""

from __future__ import annotations
from dataclasses import dataclass
from lxml import etree

OPF_NS = "http://www.idpf.org/2007/opf"
COLLECTION_ID = "series-collection"


@dataclass(slots=True)
class SeriesInfo:
    name: str = ""
    index: float | None = None
    has_calibre: bool = False
    has_epub3: bool = False

    @property
    def present(self) -> bool:
        return bool(self.name)


def format_index(index: float | None) -> str:
    """Renders 3.0 as '3' and 3.5 as '3.5' -- a whole-number position
    shouldn't show a trailing '.0' just because the field allows
    decimals under the hood (to support bonus/novella numbering like
    a "Book 3.5")."""
    if index is None:
        return ""
    if index == int(index):
        return str(int(index))
    return str(index)


def read(book) -> SeriesInfo:
    """Reads whatever series metadata is already on `book`, from
    either convention. If both are present and disagree, calibre's
    tag wins for display here (it's the one most tools show first),
    but write() below always keeps both conventions in sync going
    forward regardless of which one existed before."""
    opf = getattr(book, "opf_document", None)
    if opf is None:
        return SeriesInfo()

    metadata = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        return SeriesInfo()

    info = SeriesInfo()
    all_meta = metadata.findall(f"{{{OPF_NS}}}meta")

    for meta in all_meta:
        name_attr = meta.get("name")
        if name_attr == "calibre:series":
            info.has_calibre = True
            info.name = (meta.get("content") or "").strip()
        elif name_attr == "calibre:series_index":
            info.has_calibre = True
            info.index = _parse_index(meta.get("content"))

    collection_id = None
    for meta in all_meta:
        if meta.get("property") == "belongs-to-collection":
            info.has_epub3 = True
            collection_id = meta.get("id")
            if not info.name:
                info.name = (meta.text or "").strip()
            break

    if collection_id:
        for meta in all_meta:
            if (
                meta.get("refines") == f"#{collection_id}"
                and meta.get("property") == "group-position"
                and info.index is None
            ):
                info.index = _parse_index(meta.text)

    return info


def _parse_index(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def write(book, name: str, index: float | None = None) -> None:
    """Writes (or updates) series metadata on `book`, in both
    conventions. Idempotent: an existing calibre:series /
    calibre:series_index pair, or belongs-to-collection block, is
    updated in place rather than duplicated. `index=None` removes any
    existing position tag while still setting the series name.

    Sets book.opf_modified and calls book.mark_modified(), the same
    way every other module that edits the OPF tree directly does
    (see e.g. ebook_fix.modules.epub3_upgrade).
    """
    opf = getattr(book, "opf_document", None)
    if opf is None:
        return

    metadata = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        return

    _write_calibre(metadata, name, index)
    _write_epub3_collection(metadata, name, index)

    book.opf_modified = True
    book.mark_modified()


def _write_calibre(metadata, name: str, index: float | None) -> None:
    name_meta = None
    index_meta = None
    for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
        if meta.get("name") == "calibre:series":
            name_meta = meta
        elif meta.get("name") == "calibre:series_index":
            index_meta = meta

    if name_meta is None:
        name_meta = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
        name_meta.set("name", "calibre:series")
    name_meta.set("content", name)

    if index is not None:
        if index_meta is None:
            index_meta = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
            index_meta.set("name", "calibre:series_index")
        index_meta.set("content", format_index(index))
    elif index_meta is not None:
        metadata.remove(index_meta)


def _write_epub3_collection(metadata, name: str, index: float | None) -> None:
    collection_meta = None
    for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
        if meta.get("property") == "belongs-to-collection":
            collection_meta = meta
            break

    if collection_meta is None:
        collection_meta = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
        collection_meta.set("property", "belongs-to-collection")
        collection_meta.set("id", COLLECTION_ID)
    collection_meta.text = name
    collection_id = collection_meta.get("id") or COLLECTION_ID
    collection_meta.set("id", collection_id)

    type_meta = None
    position_meta = None
    for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
        if meta.get("refines") == f"#{collection_id}":
            if meta.get("property") == "collection-type":
                type_meta = meta
            elif meta.get("property") == "group-position":
                position_meta = meta

    if type_meta is None:
        type_meta = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
        type_meta.set("refines", f"#{collection_id}")
        type_meta.set("property", "collection-type")
    type_meta.text = "series"

    if index is not None:
        if position_meta is None:
            position_meta = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
            position_meta.set("refines", f"#{collection_id}")
            position_meta.set("property", "group-position")
        position_meta.text = format_index(index)
    elif position_meta is not None:
        metadata.remove(position_meta)
