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

from lxml import etree

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


# ---------------------------------------------------------------------
# Standardized cover filename
# ---------------------------------------------------------------------
#
# Used by both modules/cover_repair.py (renaming an existing verified
# cover) and Engine.replace_cover (naming a newly supplied one) --
# Jacob's call (docs/cover_repair_replace_plan.md): a cover's filename
# should always be "cover.<ext>", the same across every book, rather
# than left as whatever it happened to be named. Only the name is
# standardized, never the image's actual format/bytes -- a supplied
# .png stays a .png, just as "cover.png" instead of anything else.

_EXTENSION_BY_MEDIA_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


def extension_for_media_type(media_type: str, fallback_href: str = "") -> str:
    """Maps an image media-type to the extension ebook_fix
    standardizes cover filenames on. Falls back to whatever extension
    `fallback_href` already has if the media-type isn't one of the
    common ones recognized here, rather than guessing -- an exotic or
    missing media-type shouldn't produce a nonsense filename. Falls
    back to "jpg" only if neither gives an answer."""
    ext = _EXTENSION_BY_MEDIA_TYPE.get((media_type or "").lower().strip())
    if ext:
        return ext
    if fallback_href:
        suffix = PurePosixPath(fallback_href).suffix.lstrip(".")
        if suffix:
            return suffix.lower()
    return "jpg"


def standard_cover_filename(media_type: str, fallback_href: str = "") -> str:
    """The standardized cover filename: always "cover", extension
    matching the image's actual format -- e.g. "cover.jpg" for a
    JPEG cover, "cover.png" for a PNG one."""
    return f"cover.{extension_for_media_type(media_type, fallback_href)}"


def sniff_image_media_type(data: bytes) -> str | None:
    """Identifies an image format from its actual bytes (magic
    numbers), not a filename extension or a server-supplied
    Content-Type header -- either of those can lie or be missing
    entirely, especially for a file downloaded from a URL. Returns
    None if `data` doesn't look like any image format ebook_fix
    recognizes, so the caller can refuse to install it rather than
    guess."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:2000].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        if b"<svg" in head:
            return "image/svg+xml"
    return None


# ---------------------------------------------------------------------
# Shared write-side helpers
# ---------------------------------------------------------------------
#
# Both modules/cover_repair.py (renaming/syncing an already-verified
# cover) and Engine.replace_cover (installing a user-supplied one)
# need the same handful of low-level OPF-editing operations. Kept
# here, alongside the analysis they both build on, rather than
# duplicated in each -- unlike the tiny generic DOM utilities other
# modules copy freely (see modules/scene_break_repair.py's
# _remove_keep_tail comment), this is real cover-domain logic with
# actual behavior worth keeping in exactly one place.

def resolve_href(base, href) -> str:
    """Public wrapper around this module's own href resolution, for
    write-side code that needs the same base+href -> in-zip-path
    logic analysis already uses."""
    return _resolve(base, href)


def archive_names(book) -> set:
    return _archive_names(book)


def find_manifest_item_element(opf, item_id: str):
    """Finds the live <item> element in `opf`'s manifest with a given
    id -- book.manifest itself is a disconnected snapshot (see
    parser.py's _read_manifest), so any actual edit has to happen on
    the real lxml tree instead."""
    manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
    if manifest_el is None:
        return None
    for item in manifest_el.findall(f"{{{OPF_NS}}}item"):
        if item.get("id") == item_id:
            return item
    return None


def swap_filename(href: str, new_filename: str) -> str:
    """Keeps href's directory portion exactly as written, replacing
    only the filename. A cover rename/replace never moves the file to
    a different folder, so there's no need to recompute a full
    relative path, just the last path segment."""
    directory = posixpath.dirname(href)
    return f"{directory}/{new_filename}" if directory else new_filename


def sync_declarations(opf, cover_item) -> bool:
    """Makes sure both the EPUB2 <meta name="cover"> and EPUB3
    properties="cover-image" conventions point at `cover_item` (a
    ManifestItem), adding whichever is missing or fixing one that
    points somewhere else. Returns True if anything actually changed.
    """
    changed = False

    item_el = find_manifest_item_element(opf, cover_item.id)
    if item_el is not None:
        properties = set((item_el.get("properties") or "").split())
        if "cover-image" not in properties:
            properties.add("cover-image")
            item_el.set("properties", " ".join(sorted(properties)))
            changed = True

    metadata = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        return changed

    meta_cover = None
    for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
        if meta.get("name") == "cover":
            meta_cover = meta
            break

    if meta_cover is None:
        meta_cover = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
        meta_cover.set("name", "cover")
        meta_cover.set("content", cover_item.id)
        changed = True
    elif meta_cover.get("content") != cover_item.id:
        meta_cover.set("content", cover_item.id)
        changed = True

    return changed


def rewrite_chapter_image_references(book, old_path: str, new_path: str) -> None:
    """Updates any <img src> or SVG <image xlink:href>/href, in any
    chapter (e.g. a dedicated cover.xhtml page), that points at
    `old_path` to point at `new_path` instead. Marks each touched
    chapter .modified = True; does not itself call
    book.mark_modified() -- the caller does that once, after whatever
    else it's changed in the same pass."""
    base = PurePosixPath(book.package_path).parent
    target_name = PurePosixPath(new_path).name
    xlink_href = "{http://www.w3.org/1999/xlink}href"

    for chapter in book.chapters:
        if chapter.document is None:
            continue
        chapter_dir = PurePosixPath(_resolve(base, chapter.href)).parent
        touched = False

        for img in chapter.document.findall(".//{*}img"):
            src = img.get("src")
            if src and _resolve(chapter_dir, src) == old_path:
                img.set("src", swap_filename(src, target_name))
                touched = True

        for image in chapter.document.findall(".//{*}image"):
            href = image.get(xlink_href) or image.get("href")
            if href and _resolve(chapter_dir, href) == old_path:
                attr = xlink_href if image.get(xlink_href) else "href"
                image.set(attr, swap_filename(href, target_name))
                touched = True

        if touched:
            chapter.modified = True


def unique_manifest_id(opf, preferred: str = "cover-image") -> str:
    """Picks a manifest id that isn't already taken, for a brand-new
    cover item -- starts from `preferred` and only appends a number
    if that's somehow already in use."""
    manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
    existing_ids = set()
    if manifest_el is not None:
        for item in manifest_el.findall(f"{{{OPF_NS}}}item"):
            existing_ids.add(item.get("id"))

    if preferred not in existing_ids:
        return preferred
    n = 2
    while f"{preferred}-{n}" in existing_ids:
        n += 1
    return f"{preferred}-{n}"


def create_cover_item(opf, href: str, media_type: str, item_id: str | None = None):
    """Adds a brand-new manifest <item> for a cover image that didn't
    exist before (Engine.replace_cover's "book has no usable cover
    yet" case), with properties="cover-image" set from the start.
    Returns a plain ManifestItem-like value -- same shape
    analyze_book_cover would have found had this cover existed all
    along -- so sync_declarations() and the rest of the write-side
    helpers here can treat it identically to any other cover_item."""
    from ebook_fix.models import ManifestItem

    manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
    if manifest_el is None:
        manifest_el = etree.SubElement(opf, f"{{{OPF_NS}}}manifest")

    item_id = item_id or unique_manifest_id(opf)
    item_el = etree.SubElement(manifest_el, f"{{{OPF_NS}}}item")
    item_el.set("id", item_id)
    item_el.set("href", href)
    item_el.set("media-type", media_type)
    item_el.set("properties", "cover-image")

    return ManifestItem(id=item_id, href=href, media_type=media_type, properties="cover-image")
