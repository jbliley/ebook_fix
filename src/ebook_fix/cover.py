"""
ebook_fix.cover

Confirms a book's cover image is properly declared and actually
exists -- same "analysis, not repair" pattern as toc.py/gutenberg.py/
images.py. Feeds the single analyzer pass; nothing here touches the
book.

How a cover gets declared
--------------------------
Two independent conventions exist, and real-world books commonly
carry either one or both:

- EPUB2-style: an OPF <meta name="cover" content="ID"/> in
  <metadata>, where ID is a manifest item's id attribute.
- EPUB3-style: the manifest item itself carries
  properties="cover-image".

What this checks
-----------------
- Is a cover declared at all, by either method?
- EPUB2-style: does the id the <meta> tag points at actually exist in
  the manifest (a dangling reference is a common leftover from manual
  editing or a lossy conversion)?
- Whichever item ends up as the declared cover, is its media-type
  actually an image?
- Does that item's file actually exist inside the EPUB zip? (reuses
  the same archive-listing approach images.py already uses, rather
  than re-opening the zip for this too)
- If both methods are present, do they agree on the same manifest
  item? A stale EPUB2 <meta> tag left pointing at an old cover file
  after the real cover was swapped out via properties="cover-image"
  (or vice versa) is the kind of thing that looks fine in one reading
  app and wrong in another.

What this does NOT do
----------------------
Doesn't decide which declaration "wins" for repair purposes, doesn't
add a missing declaration, and doesn't validate that the image itself
opens/decodes cleanly -- just whether the two ways an EPUB can say
"this is the cover" point at something real and agree with each
other. A repair module built on this would need to make those calls
itself.
"""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

OPF_NS = "http://www.idpf.org/2007/opf"


@dataclass
class BookCoverSummary:
    declared: bool = False                 # a cover was found via either method
    meta_content_id: str = ""              # OPF <meta name="cover" content="..."> value, if the tag exists at all
    meta_item: object = None               # the ManifestItem that id resolves to (None if the tag is missing, or dangling)
    meta_id_dangling: bool = False         # the <meta> tag exists but its content id isn't any manifest item's id
    properties_item: object = None         # the ManifestItem carrying properties="cover-image", if any
    cover_item: object = None              # the item this module treats as "the" cover (properties item preferred, see below)
    resolved_href: str = ""                # cover_item's href, resolved to a path inside the zip
    exists_in_archive: bool = False        # whether resolved_href is actually present in the zip
    is_image_media_type: bool = True       # False if cover_item's declared media-type isn't image/*
    mismatched_declarations: bool = False  # meta and properties methods both present but disagree on the item

    @property
    def has_problem(self) -> bool:
        return (
            not self.declared
            or self.meta_id_dangling
            or not self.exists_in_archive
            or not self.is_image_media_type
            or self.mismatched_declarations
        )


def _archive_names(book) -> set:
    with zipfile.ZipFile(book.source, "r") as archive:
        return set(archive.namelist())


def _resolve(base, href) -> str:
    return posixpath.normpath(str(PurePosixPath(base) / href))


def _meta_cover_content_id(opf) -> str:
    if opf is None:
        return ""
    metadata = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        return ""
    for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
        if meta.get("name") == "cover":
            return meta.get("content", "")
    return ""


def analyze_book_cover(book) -> BookCoverSummary:
    summary = BookCoverSummary()

    manifest = getattr(book, "manifest", []) or []
    manifest_by_id = {item.id: item for item in manifest}

    summary.meta_content_id = _meta_cover_content_id(getattr(book, "opf_document", None))
    if summary.meta_content_id:
        summary.meta_item = manifest_by_id.get(summary.meta_content_id)
        summary.meta_id_dangling = summary.meta_item is None

    for item in manifest:
        if "cover-image" in (item.properties or "").split():
            summary.properties_item = item
            break

    if summary.meta_item is not None and summary.properties_item is not None:
        summary.mismatched_declarations = summary.meta_item.id != summary.properties_item.id

    # The EPUB3 properties="cover-image" item wins when both are
    # present, matching the priority reflowable EPUB3 readers
    # themselves give it -- <meta name="cover"> is the older, EPUB2
    # -only signal.
    summary.cover_item = summary.properties_item or summary.meta_item
    summary.declared = summary.cover_item is not None

    if summary.cover_item is not None:
        summary.is_image_media_type = summary.cover_item.media_type.startswith("image/")
        base = PurePosixPath(book.package_path).parent
        summary.resolved_href = _resolve(base, summary.cover_item.href)
        summary.exists_in_archive = summary.resolved_href in _archive_names(book)

    return summary
