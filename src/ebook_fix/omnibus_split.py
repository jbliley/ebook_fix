"""
ebook_fix.omnibus_split

Splits an omnibus EPUB (two or more complete books bound into one file)
into one finished EPUB per book. Built on ebook_fix.omnibus.detect(),
which finds where each book starts and ends; this module does the
writing. See docs/omnibus_splitter_plan.md for the decisions behind it.

Unlike every other repair, this produces several files from one input,
so it is not a pipeline module. It is a standalone operation (like
Engine.replace_cover): `split_omnibus()` for a whole run, used by the
`split-omnibus` command.

The simple version first, as Jacob decided (2026-10-01):

- Each output keeps only its own book's pages. Shared front matter (a
  cover page, title page, copyright page, ad page) goes to Book One only,
  and shared back matter goes to the last book only.
- The omnibus cover picture stays in every output (the picture file and
  its cover declaration), so each book still shows a cover in a library.
  Only Book One keeps the cover page itself.
- Each output gets its own title, taken from the table-of-contents entry
  that names the book (an ALL CAPS label becomes Title Case).
- Each output gets one new unique identifier (a UUID built from the
  source file and the book's position, so splitting the same file again
  gives the same ID). The omnibus's ISBN is never reused on a book that
  is not the omnibus. Other metadata (author, language, publisher, date,
  rights, series name) is carried over. A series position number is
  dropped, since the omnibus's own number is not any one book's. Each book
  gets its own position only when it is known: a series tag in the book's
  own title ("Title (Series #3)", "Series, Book Three: Title"), or a series
  name (and optional starting number) given by the person, which numbers
  the books in order (Book One gets the starting number, then +1 each).
- A page that one book's chapters link to but that sits inside another
  book's span (a shared notes page at the end of the omnibus) moves to
  the book that links to it. A page that several books link to (one
  notes page for the whole omnibus) is kept by each of them, trimmed to
  the notes that book uses, and the notes are renumbered to start at 1.
- The contents (NCX and nav document) keep only that book's entries.
- Pictures that no kept page or stylesheet uses are removed.
- A link from a kept page to a page now in another book can no longer
  work. On a contents-style page (the omnibus's own table of contents
  page) the other book's entries are removed; elsewhere the link is
  turned into plain text (the link text stays).

Added 2026-10-16 (the splitter's loose ends):

- Each book's own ISBN is read from a copyright-style page inside that
  book's own pages and written as a second identifier (the generated
  unique ID stays the main one). The omnibus's own ISBN, a number that
  appears in more than one book, and anything on the shared front or back
  matter are never used.
- Shared front matter (before Book One's own title page: cover, copyright,
  ad pages, author's note) and shared back matter (author-level pages at
  the very end: books by, newsletter, about the author) are copied into
  every other book, in the same place, and listed in each book's
  contents. The omnibus's own title page and contents page are not
  copied, since they describe the whole bundle.
- When a book keeps only its own notes from a shared notes page, the
  visible note numbers count 1, 2, 3 again (only when the numbering is a
  clean count-up; otherwise nothing is touched).
"""
from __future__ import annotations

import copy
import hashlib
import posixpath
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from lxml import etree

from ebook_fix.chapters import _classify
from ebook_fix.frontmatter import MatterLabel, _label_from_href, _label_from_text
from ebook_fix.isbn_lookup import clean_isbn, find_isbn_candidates, is_valid_isbn
from ebook_fix.models import Chapter, ManifestItem, TocEntry
from ebook_fix.omnibus import OmnibusAnalysis, detect
from ebook_fix.parser import EPUBParser
from ebook_fix import series as series_metadata
from ebook_fix.toc_generate import build_nav_bytes, build_ncx_document
from ebook_fix.writer import EPUBWriter
from metadata.core_fields import write_core_field

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with"}


# ---------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------

@dataclass
class SplitOutput:
    number: int
    title: str
    path: Path
    pages: int = 0            # reading-order pages kept
    words: int = 0
    links_unlinked: int = 0   # links to another book turned into plain text
    contents_entries_removed: int = 0   # another book's entries taken off a contents page
    notes_entries_removed: int = 0      # another book's notes taken off a shared notes page
    images_removed: int = 0
    toc_rebuilt: int = 0                # entries written when the TOC had to be rebuilt
    isbn: str = ""                      # this book's own ISBN, when its copyright page had one
    front_copies: int = 0               # shared front matter pages copied into this book
    back_copies: int = 0                # shared back matter pages copied into this book
    notes_renumbered: int = 0           # notes renumbered to start again at 1
    series: str = ""                    # series this book was labeled with (name only when no position known)
    series_number: float | None = None  # its position in that series, when known
    problems: list = field(default_factory=list)   # empty means it checked out


@dataclass
class SplitResult:
    # "split", "dry_run", "needs_review", "not_omnibus"
    status: str = ""
    message: str = ""
    analysis: OmnibusAnalysis | None = None
    outputs: list = field(default_factory=list)       # list[SplitOutput]
    moved_pages: list = field(default_factory=list)   # (page, from book, to book, link count)
    shared_pages: dict = field(default_factory=dict)  # page -> book numbers that each keep a trimmed copy
    front_pages: list = field(default_factory=list)   # shared front matter pages every other book gets a copy of
    back_pages: list = field(default_factory=list)    # shared back matter pages every other book gets a copy of
    words_in: int = 0
    words_out: int = 0


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------

def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _resolve(owner_href: str, ref: str) -> str:
    """A link's target as a path relative to the OPF folder, or "" for
    a link that stays inside its own file (or is not a file at all)."""
    if not ref or _SCHEME.match(ref):
        return ""
    path = unquote(ref.split("#", 1)[0].split("?", 1)[0])
    if not path:
        return ""
    return posixpath.normpath(posixpath.join(posixpath.dirname(owner_href), path))


def _words(chapter) -> int:
    if chapter.document is None:
        return 0
    return len(" ".join(chapter.document.itertext()).split())


def nice_title(label: str) -> str:
    """A table-of-contents label as a book title. Mixed-case labels are
    kept as written; an ALL CAPS label becomes Title Case."""
    text = " ".join((label or "").split())
    if text and text.isupper():
        words = text.lower().split(" ")
        out = []
        for i, word in enumerate(words):
            out.append(word if (i > 0 and word in _SMALL_WORDS) else word[:1].upper() + word[1:])
        text = " ".join(out)
    return text


def output_name(source: Path, number: int, title: str) -> str:
    """`{original}_Book{N}_{TitleInCamelCase}.epub`, as the plan says."""
    title = re.sub(r"[\'\u2019\u2018]", "", title)   # Earth's -> Earths, not Earth S
    camel = "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", title))
    camel = camel[:60] or "Untitled"
    stem = source.stem if len(source.stem) <= 50 else source.stem[:50].rstrip(" ._-")   # keep paths short for Windows
    return f"{stem}_Book{number}_{camel}.epub"


def _unique_id(source: Path, number: int) -> str:
    digest = hashlib.sha1(Path(source).read_bytes()).hexdigest()
    return "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"ebook_fix-omnibus:{digest}:{number}"))


# ---------------------------------------------------------------------
# Cutting one book out
# ---------------------------------------------------------------------

def _drop_chapters(book, remove: set) -> None:
    """Removes whole pages from the book: reading order, manifest, the
    live OPF, the EPUB2 guide, and (through book.removed_files) the
    saved archive. One pass, so it stays fast on a 600-page book."""
    opf = book.opf_document
    manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
    spine_el = opf.find(f"{{{OPF_NS}}}spine")
    ids = {m.id for m in book.manifest if m.href in remove}

    if manifest_el is not None:
        for item in list(manifest_el.findall(f"{{{OPF_NS}}}item")):
            if item.get("id") in ids:
                manifest_el.remove(item)
    if spine_el is not None:
        for ref in list(spine_el.findall(f"{{{OPF_NS}}}itemref")):
            if ref.get("idref") in ids:
                spine_el.remove(ref)
    for ref in list(opf.iter(f"{{{OPF_NS}}}reference")):
        target = posixpath.normpath(unquote((ref.get("href") or "").split("#", 1)[0]))
        if target in remove:
            ref.getparent().remove(ref)

    book.manifest = [m for m in book.manifest if m.href not in remove]
    book.spine = [i for i in book.spine if i not in ids]
    book.chapters = [c for c in book.chapters if c.href not in remove]
    base = PurePosixPath(book.package_path).parent
    for href in remove:
        book.removed_files.add(str(base / href))
    book.opf_modified = True


def _prune_ncx(book, removed: set, title: str, uid: str) -> None:
    ncx = book.ncx_document
    if ncx is None:
        return
    ncx_href = book.ncx_href

    def target_of(point) -> str:
        content = point.find(f"{{{NCX_NS}}}content")
        return _resolve(ncx_href, content.get("src", "")) if content is not None else ""

    def walk(parent) -> None:
        for point in list(parent.findall(f"{{{NCX_NS}}}navPoint")):
            walk(point)
            if target_of(point) in removed:
                kids = point.findall(f"{{{NCX_NS}}}navPoint")
                index = list(parent).index(point)
                parent.remove(point)
                for offset, kid in enumerate(kids):
                    parent.insert(index + offset, kid)   # an entry's kept children move up a level

    nav_map = ncx.find(f"{{{NCX_NS}}}navMap")
    if nav_map is not None:
        walk(nav_map)
        for order, point in enumerate(nav_map.iter(f"{{{NCX_NS}}}navPoint"), start=1):
            if point.get("playOrder") is not None:
                point.set("playOrder", str(order))

    for target in list(ncx.iter(f"{{{NCX_NS}}}pageTarget")):
        content = target.find(f"{{{NCX_NS}}}content")
        if content is not None and _resolve(ncx_href, content.get("src", "")) in removed:
            target.getparent().remove(target)

    text = ncx.find(f"{{{NCX_NS}}}docTitle/{{{NCX_NS}}}text")
    if text is not None:
        text.text = title
    for meta in ncx.iter(f"{{{NCX_NS}}}meta"):
        if meta.get("name") == "dtb:uid":
            meta.set("content", uid)
    book.ncx_modified = True


def _prune_nav(book, removed: set) -> None:
    nav_chapter = book.nav_chapter
    nav_item = book.nav_item
    if nav_chapter is None or nav_item is None or nav_chapter.document is None:
        return
    doc = nav_chapter.document
    items = [el for el in doc.iter() if _local(el) == "li"]
    for li in reversed(items):          # children before their parents
        link = next((c for c in li if _local(c) == "a"), None)
        sub = next((c for c in li if _local(c) == "ol"), None)
        kept_kids = [c for c in sub if _local(c) == "li"] if sub is not None else []
        parent = li.getparent()
        if parent is None:
            continue
        own_gone = link is not None and _resolve(nav_item.href, link.get("href", "")) in removed
        heading_empty = link is None and sub is not None and not kept_kids
        if own_gone or heading_empty:
            index = list(parent).index(li)
            parent.remove(li)
            if own_gone:
                for offset, kid in enumerate(kept_kids):
                    parent.insert(index + offset, kid)
    nav_chapter.modified = True


def _prune_unused_media(book) -> int:
    """Removes pictures (and audio/video) that no kept page, stylesheet
    or cover declaration uses. Returns how many were removed."""
    base = PurePosixPath(book.package_path).parent
    used: set = set()

    for chapter in book.chapters:
        if chapter.document is None:
            continue
        for el in chapter.document.iter():
            if not isinstance(el.tag, str):
                continue
            for attr in ("src", "href", "poster", "data", XLINK_HREF):
                value = el.get(attr)
                if value:
                    used.add(_resolve(chapter.href, value))
            style = el.get("style")
            if style:
                for ref in _CSS_URL.findall(style):
                    used.add(_resolve(chapter.href, ref))
            if _local(el) == "style" and el.text:
                for ref in _CSS_URL.findall(el.text):
                    used.add(_resolve(chapter.href, ref))

    try:
        with zipfile.ZipFile(book.source) as archive:
            for css in book.css:
                try:
                    text = archive.read(str(base / css.href)).decode("utf-8", "ignore")
                except KeyError:
                    continue
                for ref in _CSS_URL.findall(text) + _CSS_IMPORT.findall(text):
                    used.add(_resolve(css.href, ref))
    except (OSError, zipfile.BadZipFile):
        return 0

    opf = book.opf_document
    cover_ids = set()
    for meta in opf.iter(f"{{{OPF_NS}}}meta"):
        if meta.get("name") == "cover" and meta.get("content"):
            cover_ids.add(meta.get("content"))
    for item in book.manifest:
        if "cover-image" in (item.properties or "").split():
            cover_ids.add(item.id)

    manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
    drop = [
        m for m in book.manifest
        if (m.media_type.startswith(("image/", "audio/", "video/")))
        and m.href not in used and m.id not in cover_ids
    ]
    if not drop or manifest_el is None:
        return 0
    drop_ids = {m.id for m in drop}
    for item in list(manifest_el.findall(f"{{{OPF_NS}}}item")):
        if item.get("id") in drop_ids:
            manifest_el.remove(item)
    book.manifest = [m for m in book.manifest if m.id not in drop_ids]
    book.images = [r for r in book.images if r.id not in drop_ids]
    book.audio = [r for r in book.audio if r.id not in drop_ids]
    book.video = [r for r in book.video if r.id not in drop_ids]
    for m in drop:
        book.removed_files.add(str(base / m.href))
    book.opf_modified = True
    return len(drop)


_BLOCKS = {"li", "dt", "dd", "tr", "p"}
_CONTAINERS = {"div", "ol", "ul", "dl", "table", "tbody", "section", "p", "li", "tr", "td"}


def _plain_text(el) -> str:
    return " ".join("".join(el.itertext()).split())


def _is_empty(el) -> bool:
    if (el.text or "").strip():
        return False
    return all(_local(c) == "br" and not (c.tail or "").strip() for c in el)


def _remove_keep_tail(el) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail or ""
    previous = el.getprevious()
    if tail.strip():
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


def _remove_entry(link) -> None:
    """Takes one contents-page entry (a link to another book's page)
    out of the page, along with the line break after it, and any list
    or box that is left empty by it."""
    parent = link.getparent()
    if parent is None:
        return
    if _local(parent) in _BLOCKS and _plain_text(parent) == _plain_text(link):
        link, parent = parent, parent.getparent()
        if parent is None:
            return
    following = link.getnext()
    if following is not None and _local(following) == "br" and not (link.tail or "").strip():
        _remove_keep_tail(following)
    _remove_keep_tail(link)
    while parent is not None and _local(parent) in _CONTAINERS and _is_empty(parent):
        grand = parent.getparent()
        _remove_keep_tail(parent)
        parent = grand


def _handle_dead_links(book, removed: set) -> tuple:
    """Links from a kept page to a page that went to another book can
    never work. On a contents-style page (three or more such links, each
    a short label) the entries are removed, so Book One's own contents
    page no longer lists Book Two. Anywhere else the link becomes a
    plain span (the text and any id stay). Returns (links turned into
    text, contents entries removed)."""
    nav_href = book.nav_item.href if book.nav_item is not None else None
    unlinked = removed_entries = 0
    for chapter in book.chapters:
        if chapter.document is None or chapter.href == nav_href:
            continue
        dead = [
            el for el in chapter.document.iter()
            if isinstance(el.tag, str) and _local(el) == "a" and el.get("href")
            and _resolve(chapter.href, el.get("href")) in removed
        ]
        if not dead:
            continue
        contents_like = len(dead) >= 3 and all(len(_plain_text(a).split()) <= 8 for a in dead)
        for el in dead:
            if contents_like:
                _remove_entry(el)
                removed_entries += 1
            else:
                namespace = etree.QName(el).namespace
                el.tag = f"{{{namespace}}}span" if namespace else "span"
                del el.attrib["href"]
                unlinked += 1
        chapter.modified = True
    return unlinked, removed_entries


def _set_identity(book, title: str, uid: str) -> None:
    """New title and a single new unique identifier; drops the
    omnibus's own ISBN and sort title, and the omnibus's series
    position."""
    write_core_field(book, "title", title)
    opf = book.opf_document
    metadata = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        return

    idents = metadata.findall(f"{{{DC_NS}}}identifier")
    package_id = opf.get("unique-identifier")
    keep = next((e for e in idents if e.get("id") == package_id), idents[0] if idents else None)
    dropped_ids = {e.get("id") for e in idents if e is not keep and e.get("id")}
    for el in idents:
        if el is not keep:
            metadata.remove(el)
    if keep is None:
        keep = etree.SubElement(metadata, f"{{{DC_NS}}}identifier")
        keep.set("id", "bookid")
        opf.set("unique-identifier", "bookid")
    keep.text = uid
    for attr in [a for a in keep.attrib if a.endswith("scheme")]:
        del keep.attrib[attr]

    collection_ids = set()
    for meta in list(metadata.findall(f"{{{OPF_NS}}}meta")):
        name = meta.get("name")
        prop = meta.get("property")
        refines = (meta.get("refines") or "").lstrip("#")
        if name in ("calibre:title_sort", "calibre:series_index"):
            metadata.remove(meta)
        elif prop == "belongs-to-collection" and meta.get("id"):
            collection_ids.add(meta.get("id"))
        elif refines in dropped_ids:
            metadata.remove(meta)   # a type label for an identifier that is gone
    for meta in list(metadata.findall(f"{{{OPF_NS}}}meta")):
        if meta.get("property") == "group-position" and (meta.get("refines") or "").lstrip("#") in collection_ids:
            metadata.remove(meta)

    snapshot = getattr(book, "metadata", None)
    if snapshot is not None:
        snapshot.identifier = uid
    book.opf_modified = True


# ---------------------------------------------------------------------
# Rebuilding a thin table of contents from the book's own contents page
# ---------------------------------------------------------------------

_THIN_TOC = 3          # a split book with fewer entries than this gets a rebuilt TOC
_MIN_CONTENTS_LINKS = 5


def _toc_entry_count(book) -> int:
    count = 0
    if book.ncx_document is not None:
        count = len(list(book.ncx_document.iter(f"{{{NCX_NS}}}navPoint")))
    nav = book.nav_chapter
    if nav is not None and nav.document is not None:
        count = max(count, len([a for a in nav.document.iter() if _local(a) == "a"]))
    return count


def _rebuild_toc_from_contents_page(book, nav_href) -> int:
    """A split book whose table of contents is down to a lone entry (the
    omnibus only listed whole books) gets one rebuilt from the book's own
    contents page: the page with the most links to other kept pages. Those
    links are the author's own chapter list, so they are safer than
    guessing chapters from the text. Returns the number of entries written,
    or 0 when the book already has a real TOC or no contents page exists."""
    if _toc_entry_count(book) >= _THIN_TOC:
        return 0
    pages = [c for c in book.chapters if c.href != nav_href and c.document is not None]
    order = {c.href: i for i, c in enumerate(pages)}

    best, best_links = None, []
    for chapter in pages:
        links, seen = [], set()
        for a in chapter.document.iter():
            if _local(a) != "a" or not a.get("href"):
                continue
            ref = a.get("href")
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", ref):
                continue
            path, _, frag = ref.partition("#")
            target = _resolve(chapter.href, path) if path else chapter.href
            if target not in order or (target == chapter.href and not frag):
                continue
            label = " ".join("".join(a.itertext()).replace("\xa0", " ").split())
            key = (target, frag)
            if not label or key in seen:
                continue
            seen.add(key)
            links.append((label, target + ("#" + frag if frag else "")))
        if len(links) > len(best_links):
            best, best_links = chapter, links
    if best is None or len(best_links) < _MIN_CONTENTS_LINKS:
        return 0

    # Keep what the book already had (its title page entry) in front.
    entries = []
    if book.ncx_document is not None:
        for point in book.ncx_document.iter(f"{{{NCX_NS}}}navPoint"):
            text = point.find(f"{{{NCX_NS}}}navLabel/{{{NCX_NS}}}text")
            content = point.find(f"{{{NCX_NS}}}content")
            if text is not None and content is not None and content.get("src"):
                href = _resolve(book.ncx_href, content.get("src"))
                if href in order:
                    entries.append((" ".join((text.text or "").split()), href))
    seen_hrefs = {h for _l, h in entries}
    entries += [(l, h) for l, h in best_links if h not in seen_hrefs]
    entries.sort(key=lambda e: order.get(e[1].partition("#")[0], 10 ** 6))   # stable: keeps list order

    return _write_toc(book, entries, order)


def _write_toc(book, entries: list, order: dict) -> int:
    """Writes `entries` ([(label, href[#fragment])]) as the book's NCX and
    nav document. Returns the number of entries."""
    tocs = [TocEntry(label=l, href=h) for l, h in entries]

    def relative_to(base_href: str):
        base_dir = posixpath.dirname(base_href)
        return [TocEntry(label=e.label, href=posixpath.relpath(e.href.partition("#")[0], base_dir or ".")
                         + (("#" + e.href.partition("#")[2]) if "#" in e.href else "")) for e in tocs]

    if book.ncx_document is not None:
        old = book.ncx_document
        new = build_ncx_document(book, relative_to(book.ncx_href))
        title = old.find(f"{{{NCX_NS}}}docTitle/{{{NCX_NS}}}text")
        if title is not None:
            new.find(f"{{{NCX_NS}}}docTitle/{{{NCX_NS}}}text").text = title.text
        for meta in old.iter(f"{{{NCX_NS}}}meta"):
            if meta.get("name") == "dtb:uid":
                for m2 in new.iter(f"{{{NCX_NS}}}meta"):
                    if m2.get("name") == "dtb:uid":
                        m2.set("content", meta.get("content", ""))
        book.ncx_document = new
        book.ncx_modified = True

    nav_chapter, nav_item = book.nav_chapter, book.nav_item
    if nav_chapter is not None and nav_item is not None and nav_chapter.document is not None:
        data = build_nav_bytes(book, relative_to(nav_item.href))
        root = etree.fromstring(data)
        doc = nav_chapter.document
        nav_chapter.document = etree.ElementTree(root) if hasattr(doc, "getroot") else root
        nav_chapter.modified = True

    book.toc = tocs
    return len(tocs)




_MIN_CHAPTER_ENTRIES = 3   # fewer confirmed chapters than this is not trusted as a contents list


def _rebuild_toc_from_chapters(book, nav_href) -> int:
    """A split book that is still left with a thin table of contents and
    has no contents page of its own (Book Two onward in an omnibus whose
    only contents page lists whole books) gets one built from its own
    chapter headings, the same chapter detection the Review tab uses.
    Only a complete numbered run counts (at least 3 chapters, 1 to N with
    none missing); anything weaker leaves the book as it is, so
    no guessed headings end up in a contents list. Each heading gets an
    id when it has none, so the entry can point straight at it. Returns
    the number of entries written, or 0."""
    from ebook_fix.chapters import analyze_book_chapters

    if _toc_entry_count(book) >= _THIN_TOC:
        return 0
    pages = [c for c in book.chapters if c.href != nav_href and c.document is not None]
    order = {c.href: i for i, c in enumerate(pages)}
    summary = analyze_book_chapters(book)
    boundaries = [c for c in summary.confirmed_boundaries if c.element is not None and c.href in order]
    if summary.best_sequence is None or len(boundaries) < _MIN_CHAPTER_ENTRIES:
        return 0
    # Only a complete count-up counts: 1, 2, 3 ... N with nothing missing.
    # A run that starts at 4, or skips numbers, means some chapter
    # headings were not recognized, and a contents list with holes in it
    # is worse than none.
    numbers = sorted(c.number for c in summary.best_sequence.candidates if c.number is not None)
    if len(numbers) != len(summary.best_sequence.candidates) or numbers[0] > 1 \
            or numbers != list(range(numbers[0], numbers[-1] + 1)):
        return 0
    boundaries.sort(key=lambda c: (order[c.href], c.book_order))

    # The next number up must not turn up later in the book as something
    # the sequence skipped over (chapters 15 to 32 sitting under headings
    # the sequence did not take).
    last = boundaries[-1]
    last_number = max(numbers)
    if any(c.number in (last_number + 1, last_number + 2) and c.book_order > last.book_order
           and c.href in order and c.element is not None for c in summary.candidates):
        return 0

    # Nothing may be left unrecognized at the end: the text from the last
    # chapter on must not dwarf a typical chapter, or later headings were
    # missed (a run that stops at 10 in a book of 30 passes the count-up
    # test but would list a third of the chapters).
    words = [_words(c) for c in pages]
    first_i, last_i = order[boundaries[0].href], order[boundaries[-1].href]
    if last_i > first_i:
        average_before = sum(words[first_i:last_i]) / max(1, len(boundaries) - 1)
        if sum(words[last_i:]) > 3 * max(average_before, 1500):
            return 0

    used = {href: {e.get("id") for e in c.document.iter() if isinstance(e.tag, str) and e.get("id")}
            for href, c in ((c.href, c) for c in pages)}
    by_href = {c.href: c for c in pages}
    new_entries = []
    for number, cand in enumerate(boundaries, start=1):
        el = cand.element
        frag = el.get("id")
        if not frag:
            base = f"chapter-{number}"
            frag, n = base, 2
            while frag in used[cand.href]:
                frag, n = f"{base}-{n}", n + 1
            el.set("id", frag)
            used[cand.href].add(frag)
            by_href[cand.href].modified = True
        label = " ".join(cand.text.split()) or f"Chapter {number}"
        new_entries.append((label, f"{cand.href}#{frag}"))

    entries = []
    if book.ncx_document is not None:
        for point in book.ncx_document.iter(f"{{{NCX_NS}}}navPoint"):
            text = point.find(f"{{{NCX_NS}}}navLabel/{{{NCX_NS}}}text")
            content = point.find(f"{{{NCX_NS}}}content")
            if text is not None and content is not None and content.get("src"):
                href = _resolve(book.ncx_href, content.get("src"))
                if href in order:
                    entries.append((" ".join((text.text or "").split()), href))
    seen_hrefs = {h.partition("#")[0] for _l, h in entries}
    # An existing entry for a page that now has chapter entries is dropped
    # in favour of them (a lone "book" entry pointing at the same file).
    chapter_files = {h.partition("#")[0] for _l, h in new_entries}
    entries = [(l, h) for l, h in entries if h.partition("#")[0] not in chapter_files or h.partition("#")[0] != h]
    entries += new_entries
    entries.sort(key=lambda e: order.get(e[1].partition("#")[0], 10 ** 6))
    return _write_toc(book, entries, order)


# ---------------------------------------------------------------------
# A book's own ISBN, read from its own copyright page
# ---------------------------------------------------------------------

_ISBN_AT = re.compile(r"isbn(?:-?1[03])?\s*[:#]?\s*((?:97[89][\s\-]?)?[0-9][0-9\s\-]{7,14}[0-9Xx])", re.IGNORECASE)
_COPYRIGHT_CUE = re.compile(r"copyright|©|all rights reserved|first printing|first edition|published by", re.IGNORECASE)
_EBOOK_CUE = re.compile(r"e-?book|epub|digital|electronic|e-?isbn", re.IGNORECASE)
_SKIP_TEXT = {"style", "script", "title"}


def _visible_text(chapter) -> str:
    """The page's text without <style>/<script> contents or the hidden
    <title> (which is often a running title naming every book)."""
    if chapter.document is None:
        return ""
    parts = []
    for el in chapter.document.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el).lower() not in _SKIP_TEXT and el.text:
            parts.append(el.text)
        if el.tail:
            parts.append(el.tail)
    return "".join(parts)


def _isbn13(digits: str) -> str:
    """Any valid ISBN-10 or ISBN-13 as an ISBN-13."""
    if len(digits) == 13:
        return digits
    core = "978" + digits[:9]
    total = sum(int(ch) * (3 if i % 2 else 1) for i, ch in enumerate(core))
    return core + str((10 - total % 10) % 10)


def _valid_prefix(raw: str):
    """The valid ISBN at the start of `raw` (digits and hyphens, maybe
    followed by stray digits), or None."""
    digits = clean_isbn(raw)
    for size in (13, 10):
        candidate = digits[:size]
        if len(candidate) == size and is_valid_isbn(candidate):
            return candidate
    return None


def find_book_isbns(book, hrefs: list, skip_pages: set, titles: list | None = None) -> dict:
    """{book number: ISBN-13} for each book whose own pages carry one.

    Only copyright-style pages inside that book's own span are read, and
    never the shared front matter, the omnibus's own contents, or a
    copyright page that names two or more of the books (that one is the
    omnibus's own copyright page, whatever its ISBN is). A number
    is skipped when it is the omnibus's own ISBN (from its metadata), when
    it shows up in more than one book, or when a book lists several and
    only one is not marked as an e-book number. A book left out here just
    keeps its generated unique ID."""
    try:
        valid, _bad = find_isbn_candidates(book)
        omnibus = {_isbn13(v) for v in valid}
    except Exception:
        omnibus = set()

    by_href = {c.href: c for c in book.chapters}
    per_book = []
    for pages in hrefs:
        found = []
        for href in pages:
            chapter = by_href.get(href)
            if href in skip_pages or chapter is None or chapter.document is None:
                continue
            text = " ".join(_visible_text(chapter).split())
            if not _COPYRIGHT_CUE.search(text):
                continue
            flat = _norm(text)
            if sum(1 for t in (titles or []) if t and t in flat) >= 2:
                continue
            for match in _ISBN_AT.finditer(text):
                digits = _valid_prefix(match.group(1))
                if digits is None:
                    continue
                isbn = _isbn13(digits)
                if isbn not in omnibus:
                    found.append((isbn, text[max(0, match.start() - 60):match.end() + 30]))
        per_book.append(found)

    seen_in: dict = {}
    for i, found in enumerate(per_book):
        for isbn, _ctx in found:
            seen_in.setdefault(isbn, set()).add(i)

    result = {}
    for i, found in enumerate(per_book):
        distinct = []
        for isbn, ctx in found:
            if len(seen_in[isbn]) > 1 or isbn in [d for d, _c in distinct]:
                continue
            distinct.append((isbn, ctx))
        if len(distinct) == 1:
            result[i + 1] = distinct[0][0]
        elif len(distinct) > 1:
            marked = [d for d, ctx in distinct if _EBOOK_CUE.search(ctx)]
            if len(marked) == 1:
                result[i + 1] = marked[0]
    return result


def _add_isbn(book, isbn: str) -> None:
    """Adds the book's own ISBN as a second identifier. The generated
    unique ID stays the package's main identifier."""
    opf = book.opf_document
    metadata = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata is None or not isbn:
        return
    existing = metadata.findall(f"{{{DC_NS}}}identifier")
    el = etree.Element(f"{{{DC_NS}}}identifier")
    if (opf.get("version") or "").startswith("3"):
        el.text = f"urn:isbn:{isbn}"
    else:
        el.set(f"{{{OPF_NS}}}scheme", "ISBN")
        el.text = isbn
    if existing:
        existing[0].addnext(el)
    else:
        metadata.append(el)
    book.opf_modified = True


# ---------------------------------------------------------------------
# Shared front and back matter, copied into every book
# ---------------------------------------------------------------------

# A trailing page counts as shared back matter when its heading (or its
# contents label) is one of these. They are the author-level pages that
# sit after the last book: other books, newsletter, about the author.
_BACK_CUE = re.compile(
    r"about the authors?|also by|books by|other books|more books|other titles|"
    r"newsletter|mailing list|sign up|follow (?:me|the author)",
    re.IGNORECASE,
)
_OMNIBUS_ONLY_LABEL = re.compile(r"title\s*page|^\s*(?:table of )?contents\s*$", re.IGNORECASE)
_SHORT_BACK_PAGE_WORDS = 600


def _toc_labels(book) -> dict:
    """page file -> the label its (first) contents entry gives it."""
    labels: dict = {}

    def walk(entries) -> None:
        for entry in entries:
            labels.setdefault(entry.href.split("#", 1)[0], entry.label)
            walk(entry.children)

    walk(book.toc)
    return labels


def _image_only(chapter, text: str) -> bool:
    """A page that is just a picture (a cover page): almost no text."""
    if len(text.split()) > 5 or chapter.document is None:
        return False
    return any(isinstance(el.tag, str) and _local(el) in ("img", "image", "svg") for el in chapter.document.iter())


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _internal_page_links(chapter, pages: set) -> int:
    count = 0
    for a in chapter.document.iter():
        if isinstance(a.tag, str) and _local(a) == "a" and a.get("href"):
            target = _resolve(chapter.href, a.get("href"))
            if target and target != chapter.href and target in pages:
                count += 1
    return count


_SHORT_TITLE_PAGE_WORDS = 80
_AUTHOR_ONLY_WORDS = 30


def _front_copy_ok(chapter, label, titles: list, creator_words: list, pages: set) -> bool:
    """Shared front matter worth giving to every book: a cover page, a
    copyright page, an ad page, a foreword or author's note. Not the
    omnibus's own title page (even one split over several files), nor its
    contents page, which describe the whole bundle."""
    text = " ".join(_visible_text(chapter).split())
    words = len(text.split())
    if _image_only(chapter, text):
        return True                      # a cover page
    if not words:
        return False
    if label and _OMNIBUS_ONLY_LABEL.search(label):
        return False
    hint = _label_from_href(chapter.href)
    if hint and hint[0] in (MatterLabel.TITLE_PAGE, MatterLabel.TABLE_OF_CONTENTS):
        return False
    found, _conf = _label_from_text(text, words)
    if found == MatterLabel.TABLE_OF_CONTENTS:
        return False
    if re.search(r"\bcontents\b", text[:80], re.IGNORECASE) or _internal_page_links(chapter, pages) >= 3:
        return False                     # a contents page with no entry of its own
    if found == MatterLabel.COPYRIGHT:
        return True                      # copyright pages name the books, and are meant to be copied
    if words <= _SHORT_TITLE_PAGE_WORDS:
        flat = _norm(text)
        if any(t and t in flat for t in titles):
            return False                 # a short page naming a book: part of the omnibus title page
    if words <= _AUTHOR_ONLY_WORDS and creator_words:
        flat = _norm(text)
        if any(w in flat.split() for w in creator_words):
            return False                 # the author's name on its own: part of the omnibus title page
    return True


def _back_copy_ok(chapter, label) -> bool:
    """Shared back matter worth giving to every book."""
    if label and _classify(label) is not None:
        return False                     # a chapter or part label: that is the book itself
    text = _visible_text(chapter)
    words = len(text.split())
    if not words:
        return False
    if label and _BACK_CUE.search(label):
        return True
    if _BACK_CUE.search(text[:400]):
        return True
    if words <= _SHORT_BACK_PAGE_WORDS:
        found, _conf = _label_from_text(text, words)
        return found in (MatterLabel.ABOUT_AUTHOR, MatterLabel.COLOPHON)
    return False


def plan_shared_matter(book, analysis: OmnibusAnalysis, hrefs: list, excluded: set) -> tuple:
    """(front pages, back pages) that every other book gets its own copy
    of. Front matter is what sits before Book One's own title page; back
    matter is the run of author-level pages at the very end of the last
    book. `excluded` holds pages already handled another way (notes)."""
    order = {c.href: i for i, c in enumerate(book.chapters)}
    by_href = {c.href: c for c in book.chapters}
    pages = set(by_href)
    labels = _toc_labels(book)
    titles = [t for t in (_norm(nice_title(b.title)) for b in analysis.books) if len(t) >= 6]
    creator_words = [w for w in _norm(getattr(book.metadata, "creator", "")).split() if len(w) >= 4]

    front = []
    first = analysis.books[0]
    if first.title_index > 0:
        for href in hrefs[0]:
            chapter = by_href.get(href)
            if href in excluded or chapter is None or chapter.document is None:
                continue
            if order.get(href, 10 ** 9) >= first.title_index:
                continue
            if _front_copy_ok(chapter, labels.get(href), titles, creator_words, pages):
                front.append(href)

    back = []
    floor = max(analysis.books[-1].title_index, 0)
    for href in reversed(hrefs[-1]):
        chapter = by_href.get(href)
        if chapter is None or chapter.document is None:
            continue
        if order.get(href, -1) <= floor:
            break                        # never reach into the book's own pages
        if href in excluded:
            continue
        if not _back_copy_ok(chapter, labels.get(href)):
            break
        back.append(href)
    back.reverse()
    return front, back


def _unique(value: str, taken: set) -> str:
    if value not in taken:
        return value
    n = 2
    while f"{value}{n}" in taken:
        n += 1
    return f"{value}{n}"


def _clone_page(book, source_chapter, position: int, taken_hrefs: set, taken_ids: set):
    """Adds a copy of one page to the book's manifest, reading order and
    chapter list. The copy sits in the same folder, so every relative
    link in it still works. `position` is the index in book.chapters to
    insert at. Returns the new Chapter."""
    src = PurePosixPath(source_chapter.href)
    href = _unique(str(src.with_name(f"{src.stem}_copy{src.suffix}")), taken_hrefs)
    new_id = _unique(f"{source_chapter.id}_copy", taken_ids)
    taken_hrefs.add(href)
    taken_ids.add(new_id)

    item = next((m for m in book.manifest if m.id == source_chapter.id), None)
    properties = " ".join(p for p in ((item.properties if item else "") or "").split() if p != "nav")
    media = item.media_type if item else source_chapter.media_type

    opf = book.opf_document
    manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
    item_el = etree.SubElement(manifest_el, f"{{{OPF_NS}}}item")
    item_el.set("id", new_id)
    item_el.set("href", href)
    item_el.set("media-type", media)
    if properties:
        item_el.set("properties", properties)
    book.manifest.append(ManifestItem(id=new_id, href=href, media_type=media, properties=properties))

    chapter = Chapter(
        id=new_id, href=href, media_type=source_chapter.media_type, title=source_chapter.title,
        document=copy.deepcopy(source_chapter.document), modified=True,
    )
    book.chapters.insert(position, chapter)
    book.opf_modified = True
    return chapter


def _spine_insert(book, new_chapters: list, anchor_id: str, after: bool) -> None:
    """Puts the copies in the reading order next to the page `anchor_id`
    (just before it, or just after it), keeping their own order."""
    opf = book.opf_document
    spine_el = opf.find(f"{{{OPF_NS}}}spine")
    if spine_el is None or anchor_id not in book.spine:
        return
    at = book.spine.index(anchor_id) + (1 if after else 0)
    anchor_ref = next((r for r in spine_el.findall(f"{{{OPF_NS}}}itemref") if r.get("idref") == anchor_id), None)
    for offset, chapter in enumerate(new_chapters):
        book.spine.insert(at + offset, chapter.id)
        ref = etree.Element(f"{{{OPF_NS}}}itemref")
        ref.set("idref", chapter.id)
        if anchor_ref is None:
            spine_el.append(ref)
        elif after:
            # each copy goes right after the previous one
            (anchor_ref if offset == 0 else previous_ref).addnext(ref)
        else:
            anchor_ref.addprevious(ref)   # each one lands just before the anchor, so their order is kept
        previous_ref = ref
    book.opf_modified = True


def _add_shared_matter(book, index: int, total: int, keep: set, front: list, back: list) -> tuple:
    """Gives this book its own copy of the shared front matter (every book
    but the first, which already has the originals) and shared back matter
    (every book but the last). Returns (front TOC entries, back TOC
    entries, front pages copied, back pages copied); an entry is
    (label, new file)."""
    labels = _toc_labels(book)
    by_href = {c.href: c for c in book.chapters}
    taken_hrefs = {m.href for m in book.manifest}
    taken_ids = {m.id for m in book.manifest}
    kept = [c for c in book.chapters if c.href in keep]
    if not kept:
        return [], [], 0, 0

    front_entries, back_entries, front_copied, back_copied = [], [], 0, 0
    if index > 0 and front:
        first = kept[0]
        at = book.chapters.index(first)
        made = []
        for href in front:
            source = by_href.get(href)
            if source is None:
                continue
            chapter = _clone_page(book, source, at + len(made), taken_hrefs, taken_ids)
            made.append(chapter)
            if labels.get(href):
                front_entries.append((labels[href], chapter.href))
        _spine_insert(book, made, first.id, after=False)
        front_copied = len(made)
    if index < total - 1 and back:
        last = kept[-1]
        at = book.chapters.index(last) + 1
        made = []
        for href in back:
            source = by_href.get(href)
            if source is None:
                continue
            chapter = _clone_page(book, source, at + len(made), taken_hrefs, taken_ids)
            made.append(chapter)
            if labels.get(href):
                back_entries.append((labels[href], chapter.href))
        _spine_insert(book, made, last.id, after=True)
        back_copied = len(made)
    return front_entries, back_entries, front_copied, back_copied


def _add_toc_entries(book, front_entries: list, back_entries: list) -> None:
    """Lists the copied pages in the book's contents (NCX and nav), the
    way the originals were listed: front copies first, back copies last."""
    if not front_entries and not back_entries:
        return

    ncx = book.ncx_document
    nav_map = ncx.find(f"{{{NCX_NS}}}navMap") if ncx is not None else None
    if nav_map is not None:
        base = posixpath.dirname(book.ncx_href) or "."
        taken = {p.get("id") for p in ncx.iter(f"{{{NCX_NS}}}navPoint")}

        def make(label: str, href: str, number: int):
            pid = _unique(f"navpoint-copy-{number}", taken)
            taken.add(pid)
            point = etree.Element(f"{{{NCX_NS}}}navPoint")
            point.set("id", pid)
            nav_label = etree.SubElement(point, f"{{{NCX_NS}}}navLabel")
            etree.SubElement(nav_label, f"{{{NCX_NS}}}text").text = label
            etree.SubElement(point, f"{{{NCX_NS}}}content").set("src", posixpath.relpath(href, base))
            return point

        children = list(nav_map)
        first_point = next((i for i, c in enumerate(children) if c.tag == f"{{{NCX_NS}}}navPoint"), len(children))
        number = 0
        for offset, (label, href) in enumerate(front_entries):
            number += 1
            nav_map.insert(first_point + offset, make(label, href, number))
        for label, href in back_entries:
            number += 1
            nav_map.append(make(label, href, number))
        for order, point in enumerate(nav_map.iter(f"{{{NCX_NS}}}navPoint"), start=1):
            point.set("playOrder", str(order))
        book.ncx_modified = True

    nav_chapter, nav_item = book.nav_chapter, book.nav_item
    if nav_chapter is not None and nav_item is not None and nav_chapter.document is not None:
        doc = nav_chapter.document
        root = doc.getroot() if hasattr(doc, "getroot") else doc
        toc_ol = None
        for nav in root.iter():
            if isinstance(nav.tag, str) and _local(nav) == "nav":
                kind = nav.get("{http://www.idpf.org/2007/ops}type") or ""
                candidate = next((c for c in nav if isinstance(c.tag, str) and _local(c) == "ol"), None)
                if candidate is not None and ("toc" in kind.split() or toc_ol is None):
                    toc_ol = candidate
                    if "toc" in kind.split():
                        break
        if toc_ol is not None:
            namespace = etree.QName(toc_ol).namespace
            tag = (lambda name: f"{{{namespace}}}{name}") if namespace else (lambda name: name)
            base = posixpath.dirname(nav_item.href) or "."

            def make_li(label: str, href: str):
                li = etree.Element(tag("li"))
                a = etree.SubElement(li, tag("a"))
                a.set("href", posixpath.relpath(href, base))
                a.text = label
                return li

            for offset, (label, href) in enumerate(front_entries):
                toc_ol.insert(offset, make_li(label, href))
            for label, href in back_entries:
                toc_ol.append(make_li(label, href))
            nav_chapter.modified = True


# ---------------------------------------------------------------------
# Renumbering the notes on a notes page a book kept part of
# ---------------------------------------------------------------------

_BARE_NUMBER = re.compile(r"^[\[\(]?\s*(\d+)\s*[\]\)\.:]?$")
_LEADING_NUMBER = re.compile(r"^(\s*[\[\(]?)(\d+)([\]\)\.:]?)(?=\s|$)")


def _number_slot(scopes: list, old: int):
    """The text spot (element, "text" or "tail") that shows note number
    `old` as its own leading number, looking inside each scope in turn."""
    for scope in scopes:
        if scope is None:
            continue
        candidates = [(scope, "tail")]
        for el in scope.iter():
            if not isinstance(el.tag, str):
                continue
            candidates.append((el, "text"))
            for child in el:
                candidates.append((child, "tail"))
        for el, kind in candidates:
            value = el.text if kind == "text" else el.tail
            match = _LEADING_NUMBER.match(value or "")
            if match and int(match.group(2)) == old:
                return el, kind
    return None


def _renumber_notes(book, page_href: str) -> int:
    """After a book has been cut down to its own notes, makes the visible
    numbers count 1, 2, 3... again, both next to the note in the text and
    on the notes page. Only the visible numbers change; every link and
    anchor keeps working as before. All or nothing: if anything about the
    numbering is not plainly a clean 1..N count-up (a note with no number,
    a number used twice, numbers out of order) nothing is changed.
    Returns how many notes were renumbered."""
    page = next((c for c in book.chapters if c.href == page_href), None)
    if page is None or page.document is None:
        return 0
    nav_href = book.nav_item.href if book.nav_item is not None else None

    refs = []
    for chapter in book.chapters:
        if chapter.href in (page_href, nav_href) or chapter.document is None:
            continue
        for a in chapter.document.iter():
            if isinstance(a.tag, str) and _local(a) == "a" and a.get("href") and "#" in a.get("href"):
                if _resolve(chapter.href, a.get("href")) == page_href:
                    refs.append((chapter, a, a.get("href").split("#", 1)[1]))
    if not refs:
        return 0

    ids = {el.get("id"): el for el in page.document.iter() if isinstance(el.tag, str) and el.get("id")}
    olds = []
    plan = []
    for chapter, a, fragment in refs:
        match = _BARE_NUMBER.match("".join(a.itertext()).strip())
        target = ids.get(fragment)
        if match is None or target is None:
            return 0
        olds.append(int(match.group(1)))
        plan.append((chapter, a, target, int(match.group(1))))
    if len(set(olds)) != len(olds) or olds != sorted(olds):
        return 0
    if olds == list(range(1, len(olds) + 1)):
        return 0
    new_of = {old: rank for rank, old in enumerate(olds, start=1)}

    edits = []
    for chapter, a, target, old in plan:
        in_text = _number_slot([a], old)
        in_notes = _number_slot([target, target.getparent()], old)
        if in_text is None or in_notes is None:
            return 0
        edits.append((chapter, in_text, old))
        edits.append((page, in_notes, old))

    for chapter, (el, kind), old in edits:
        value = el.text if kind == "text" else el.tail
        replaced = _LEADING_NUMBER.sub(lambda m: m.group(1) + str(new_of[old]) + m.group(3), value, count=1)
        if kind == "text":
            el.text = replaced
        else:
            el.tail = replaced
        chapter.modified = True
    return len(olds)


def _build_one(source: Path, analysis: OmnibusAnalysis, index: int, hrefs: list, out_path: Path, title: str, uid: str,
               shared: dict | None = None, isbn: str = "", front: list | None = None, back: list | None = None,
               notes_pages: list | None = None, series_name: str = "",
               series_number: float | None = None) -> SplitOutput:
    """Loads a fresh copy of the omnibus and cuts it down to one book."""
    book = EPUBParser().load(source)
    keep = set(hrefs[index])
    nav_href = book.nav_item.href if book.nav_item is not None else None
    removed = {c.href for c in book.chapters if c.href not in keep and c.href != nav_href}

    notes_removed = 0
    for page in (shared or {}):
        if index + 1 in shared[page]:
            notes_removed += _trim_shared_page(book, page, keep - {page} | {page}, removed)

    # Copies of the shared front/back matter are made before the originals
    # go, and are never part of `removed` (they are new files).
    front_entries, back_entries, front_copies, back_copies = _add_shared_matter(
        book, index, len(hrefs), keep, front or [], back or [])

    _drop_chapters(book, removed)
    _prune_ncx(book, removed, title, uid)
    _prune_nav(book, removed)
    unlinked, entries_removed = _handle_dead_links(book, removed)
    renumbered = sum(_renumber_notes(book, page) for page in (notes_pages or []))
    images_removed = _prune_unused_media(book)
    _set_identity(book, title, uid)
    _remove_extra_page_breaks(book)
    labeled_series, labeled_number = _label_series(book, title, series_name, series_number)
    if isbn:
        _add_isbn(book, isbn)
    toc_entries = _rebuild_toc_from_contents_page(book, nav_href)
    if not toc_entries:
        toc_entries = _rebuild_toc_from_chapters(book, nav_href)
    _add_toc_entries(book, front_entries, back_entries)
    book.mark_modified()

    EPUBWriter().save(book, out_path)

    pages = [c for c in book.chapters if c.href != nav_href]
    return SplitOutput(
        number=index + 1,
        title=title,
        path=out_path,
        pages=len(pages),
        words=sum(_words(c) for c in pages),
        links_unlinked=unlinked,
        contents_entries_removed=entries_removed,
        notes_entries_removed=notes_removed,
        images_removed=images_removed,
        toc_rebuilt=toc_entries,
        isbn=isbn,
        front_copies=front_copies,
        back_copies=back_copies,
        notes_renumbered=renumbered,
        series=labeled_series,
        series_number=labeled_number,
    )


def _remove_extra_page_breaks(book) -> None:
    """Splitting can leave a forced page break as the last thing in a
    book's file (the break that used to separate it from the next book's
    chapter), which only adds an empty page. Same cleanup as the Extra
    Page Break Removal repair, run on each new book."""
    from ebook_fix.page_breaks import redundant_forced_breaks, _strip_break_props

    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None:
            continue
        found = redundant_forced_breaks(doc)
        for el, props in found:
            _strip_break_props(el, set(props))
        if found:
            chapter.modified = True


def _label_series(book, title: str, series_name: str, series_number: float | None) -> tuple:
    """Series name and position for one split book. A name given by the
    person wins and numbers the books in order (a tag in the book's own
    title with the same series overrides that position, since the book
    says so itself). With no name given, a series tag in the title labels
    the book; otherwise the carried-over series name stays without a
    position, as before. Returns (name, position) as written ("" and None
    when the book has no series)."""
    from metadata.title_cleanup import _fold, parse_embedded_series

    tag = parse_embedded_series(title)
    if series_name:
        number = series_number
        if tag is not None and tag.series_index is not None and _fold(tag.series) == _fold(series_name):
            number = tag.series_index
        series_metadata.write(book, series_name, number)
        return series_name, number
    if tag is not None and tag.series and tag.series_index is not None:
        series_metadata.write(book, tag.series, tag.series_index)
        return tag.series, tag.series_index
    existing = series_metadata.read(book)
    return existing.name, None


# ---------------------------------------------------------------------
# Checking an output
# ---------------------------------------------------------------------

def verify_output(path: Path) -> list:
    """Opens a finished EPUB and returns a list of plain-English
    problems (empty when it checks out): every manifest file exists, the
    reading order only names manifest items, every page is well-formed,
    and every contents entry points at a file that exists."""
    problems = []
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as err:
        return [f"not a readable zip file: {err}"]
    with archive:
        names = set(archive.namelist())
        try:
            container = etree.fromstring(archive.read("META-INF/container.xml"))
            opf_path = container.find(".//{*}rootfile").get("full-path")
            opf = etree.fromstring(archive.read(opf_path))
        except (KeyError, AttributeError, etree.XMLSyntaxError) as err:
            return [f"container or package file unreadable: {err}"]
        base = posixpath.dirname(opf_path)
        ids = {}
        for item in opf.iter(f"{{{OPF_NS}}}item"):
            href = unquote(item.get("href", ""))
            full = posixpath.normpath(posixpath.join(base, href))
            ids[item.get("id")] = (full, item.get("media-type", ""), item.get("properties", ""))
            if full not in names:
                problems.append(f"manifest lists a missing file: {href}")
        for ref in opf.iter(f"{{{OPF_NS}}}itemref"):
            if ref.get("idref") not in ids:
                problems.append(f"reading order names an unknown item: {ref.get('idref')}")
        for _id, (full, media, props) in ids.items():
            if media == "application/xhtml+xml" and full in names:
                data = archive.read(full)
                try:
                    etree.fromstring(data, etree.XMLParser(recover=False))
                except etree.XMLSyntaxError as err:
                    # &nbsp; and friends are legal when the page declares
                    # the XHTML doctype (readers load it); a strict parser
                    # that skips the doctype can't know them. Not a defect.
                    if "not defined" in str(err) and b"<!DOCTYPE" in data[:1000]:
                        continue
                    problems.append(f"{full} is not well-formed: {err}")
        for _id, (full, media, props) in ids.items():
            if media == "application/x-dtbncx+xml" and full in names:
                ncx = etree.fromstring(archive.read(full))
                for content in ncx.iter(f"{{{NCX_NS}}}content"):
                    target = _resolve(posixpath.relpath(full, base), content.get("src", ""))
                    if target and posixpath.normpath(posixpath.join(base, target)) not in names:
                        problems.append(f"contents (NCX) points at a missing file: {content.get('src')}")
            if "nav" in props.split() and full in names:
                nav = etree.fromstring(archive.read(full))
                for a in nav.iter():
                    if isinstance(a.tag, str) and _local(a) == "a" and a.get("href"):
                        target = _resolve(posixpath.relpath(full, base), a.get("href"))
                        if target and posixpath.normpath(posixpath.join(base, target)) not in names:
                            problems.append(f"contents (nav) points at a missing file: {a.get('href')}")
    return problems


# ---------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------

# A page that three or more links from a book's own chapters point at is
# "used" by that book. A page used by exactly one book that sits in a
# different book's span moves to the book that uses it. A page used by
# several books (one shared notes page is the real case) is kept by each
# of them, and each copy is trimmed to the entries that book uses.
_MIN_OUTSIDE_LINKS = 3
# A page with this many different link targets is a contents page, not a
# reader of other pages; its links do not count as "use".
_CONTENTS_PAGE_TARGETS = 15


def reassign_shared_pages(book, hrefs: list) -> tuple:
    """`hrefs` is one list of page files per book (from the detected
    spans). Returns (new lists, moves, shared): moves are
    (page, from_book_number, to_book_number, link_count) and shared maps
    a page to the list of book numbers that each keep a copy."""
    owner = {}
    for index, pages in enumerate(hrefs):
        for href in pages:
            owner[href] = index

    inbound: dict = {}
    for chapter in book.chapters:
        source = owner.get(chapter.href)
        if source is None or chapter.document is None:
            continue
        targets = []
        for el in chapter.document.iter():
            if isinstance(el.tag, str) and _local(el) == "a" and el.get("href"):
                target = _resolve(chapter.href, el.get("href"))
                if target and target != chapter.href and target in owner:
                    targets.append(target)
        if len(set(targets)) >= _CONTENTS_PAGE_TARGETS:
            continue
        for target in targets:
            counts = inbound.setdefault(target, {})
            counts[source] = counts.get(source, 0) + 1

    new_lists = [list(pages) for pages in hrefs]
    moves = []
    shared = {}
    for page, counts in inbound.items():
        home = owner[page]
        users = sorted(b for b, n in counts.items() if n >= _MIN_OUTSIDE_LINKS)
        if not users or users == [home]:
            continue
        if len(users) == 1:
            best = users[0]
            new_lists[home].remove(page)
            new_lists[best].append(page)
            moves.append((page, home + 1, best + 1, counts[best]))
            continue
        # Several books use this page: each keeps a copy.
        if home not in users:
            new_lists[home].remove(page)
        for b in users:
            if page not in new_lists[b]:
                new_lists[b].append(page)
        shared[page] = [b + 1 for b in users]

    order = {c.href: i for i, c in enumerate(book.chapters)}
    for pages in new_lists:
        pages.sort(key=lambda h: order.get(h, 10**9))
    return new_lists, moves, shared


def _links_into(el, owner_href: str, pages: set) -> list:
    return [
        a for a in el.iter()
        if isinstance(a.tag, str) and _local(a) == "a" and a.get("href")
        and _resolve(owner_href, a.get("href")) in pages
    ]


def _trim_shared_page(book, page_href: str, kept: set, removed: set) -> int:
    """Trims one shared notes page to the entries this book uses. An
    entry is the block that holds one backlink; it goes when every
    backlink in it points at a page that is not in this book. Returns
    how many entries were removed."""
    chapter = next((c for c in book.chapters if c.href == page_href), None)
    if chapter is None or chapter.document is None:
        return 0
    doc = chapter.document
    all_pages = kept | removed

    def holds_backlink(el) -> bool:
        return bool(_links_into(el, page_href, all_pages))

    entries = []
    seen = set()
    for link in _links_into(doc, page_href, all_pages):
        el = link
        while el.getparent() is not None:
            parent = el.getparent()
            siblings = [c for c in parent if isinstance(c.tag, str) and holds_backlink(c)]
            if len(siblings) >= 2:
                if id(el) not in seen:
                    seen.add(id(el))
                    entries.append(el)
                break
            el = parent

    count = 0
    for entry in entries:
        pointers = _links_into(entry, page_href, all_pages)
        if pointers and not any(_resolve(page_href, a.get("href")) in kept for a in pointers):
            _remove_keep_tail(entry)
            count += 1
    if count:
        chapter.modified = True
    return count


def plan_names(source: Path, analysis: OmnibusAnalysis) -> list:
    """(number, title, file name) for each book found. A book with no
    usable title is called "<file name> Book N"."""
    plan = []
    seen = set()
    for i, ob in enumerate(analysis.books, start=1):
        title = nice_title(ob.title) or f"{source.stem} Book {i}"
        if title.casefold() in seen:
            title = f"{title} ({i})"
        seen.add(title.casefold())
        plan.append((i, title, output_name(source, i, title)))
    return plan


def split_omnibus(source, out_dir=None, allow_medium: bool = False, dry_run: bool = False, log=None,
                  series: str = "", series_start: float = 1) -> SplitResult:
    """Detects the books in `source` and writes one EPUB per book into
    `out_dir` (the source's own folder by default).

    Only a high-confidence detection is split automatically. A medium
    one (see omnibus.py) is reported as "needs_review" unless
    `allow_medium` is True. `dry_run` reports the plan and writes
    nothing. `log` is an optional function that receives progress
    lines. `series` (optional) names the series every book belongs to;
    the books are then numbered in order starting at `series_start`."""
    say = log or (lambda _line: None)
    source = Path(source)
    out_dir = Path(out_dir) if out_dir else source.parent
    if (series or "").strip():
        from ebook_fix.series_names import tidy_for_book
        series = tidy_for_book(series, source)

    book = EPUBParser().load(source)
    analysis = detect(book)
    result = SplitResult(analysis=analysis)

    if not analysis.is_omnibus:
        result.status = "not_omnibus"
        result.message = analysis.reason
        return result

    plan = plan_names(source, analysis)
    nav_href = book.nav_item.href if book.nav_item is not None else None
    hrefs = [
        [c.href for c in book.chapters[ob.start_index:ob.end_index] if c.href != nav_href]
        for ob in analysis.books
    ]
    hrefs, result.moved_pages, result.shared_pages = reassign_shared_pages(book, hrefs)
    result.words_in = sum(_words(c) for c in book.chapters if c.href != nav_href)

    # Notes pages are handled by trimming/moving, not by copying.
    excluded = set(result.shared_pages) | {page for page, _from, _to, _n in result.moved_pages}
    result.front_pages, result.back_pages = plan_shared_matter(book, analysis, hrefs, excluded)
    first_title = analysis.books[0].title_index
    omnibus_front = {c.href for c in book.chapters[:first_title]} if first_title > 0 else set()
    titles = [t for t in (_norm(nice_title(b.title)) for b in analysis.books) if len(t) >= 6]
    isbns = find_book_isbns(book, hrefs, omnibus_front | set(result.back_pages) | excluded, titles)
    total = len(plan)

    def describe(number: int, title: str, name: str, **extra) -> SplitOutput:
        return SplitOutput(
            number, title, out_dir / name,
            isbn=isbns.get(number, ""),
            front_copies=len(result.front_pages) if number > 1 else 0,
            back_copies=len(result.back_pages) if number < total else 0,
            **extra,
        )

    if analysis.confidence != "high" and not allow_medium:
        result.status = "needs_review"
        result.message = (
            f"Found {len(plan)} books, but only with {analysis.confidence} confidence "
            f"({analysis.reason}) so nothing was split. Check the list, then run again with --allow-medium."
        )
        result.outputs = [describe(n, t, name, pages=len(hrefs[n - 1])) for n, t, name in plan]
        return result

    if dry_run:
        result.status = "dry_run"
        result.message = f"Would split into {len(plan)} books ({analysis.method}, {analysis.confidence} confidence)."
        result.outputs = [describe(n, t, name, pages=len(hrefs[n - 1])) for n, t, name in plan]
        return result

    out_dir.mkdir(parents=True, exist_ok=True)
    for (number, title, name) in plan:
        say(f"Writing book {number} of {len(plan)}: {title}")
        out_path = out_dir / name
        uid = _unique_id(source, number)
        notes_pages = [page for page, books in result.shared_pages.items() if number in books]
        notes_pages += [page for page, _from, to, _n in result.moved_pages if to == number]
        output = _build_one(
            source, analysis, number - 1, hrefs, out_path, title, uid, result.shared_pages,
            isbn=isbns.get(number, ""), front=result.front_pages, back=result.back_pages,
            notes_pages=notes_pages,
            series_name=(series or "").strip(),
            series_number=(series_start + number - 1) if (series or "").strip() else None,
        )
        output.problems = verify_output(out_path)
        result.outputs.append(output)

    result.words_out = sum(o.words for o in result.outputs)
    result.status = "split"
    result.message = f"Split into {len(result.outputs)} books ({analysis.method}, {analysis.confidence} confidence)."
    return result
