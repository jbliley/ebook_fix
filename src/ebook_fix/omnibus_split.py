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
  dropped, since the omnibus's own number is not any one book's.
- A page that one book's chapters link to but that sits inside another
  book's span (a shared notes page at the end of the omnibus) moves to
  the book that links to it. A page that several books link to (one
  notes page for the whole omnibus) is kept by each of them, trimmed to
  the notes that book uses. Note numbers are not renumbered, so a later
  book's notes may start above 1.
- The contents (NCX and nav document) keep only that book's entries.
- Pictures that no kept page or stylesheet uses are removed.
- A link from a kept page to a page now in another book can no longer
  work. On a contents-style page (the omnibus's own table of contents
  page) the other book's entries are removed; elsewhere the link is
  turned into plain text (the link text stays).

Not built, on purpose: renumbering notes, per-book copies of shared front/back matter, and reading a
per-book ISBN out of a copyright page.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from lxml import etree

from ebook_fix.models import TocEntry
from ebook_fix.omnibus import OmnibusAnalysis, detect
from ebook_fix.parser import EPUBParser
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


def _build_one(source: Path, analysis: OmnibusAnalysis, index: int, hrefs: list, out_path: Path, title: str, uid: str, shared: dict | None = None) -> SplitOutput:
    """Loads a fresh copy of the omnibus and cuts it down to one book."""
    book = EPUBParser().load(source)
    keep = set(hrefs[index])
    nav_href = book.nav_item.href if book.nav_item is not None else None
    removed = {c.href for c in book.chapters if c.href not in keep and c.href != nav_href}

    notes_removed = 0
    for page in (shared or {}):
        if index + 1 in shared[page]:
            notes_removed += _trim_shared_page(book, page, keep - {page} | {page}, removed)

    _drop_chapters(book, removed)
    _prune_ncx(book, removed, title, uid)
    _prune_nav(book, removed)
    unlinked, entries_removed = _handle_dead_links(book, removed)
    images_removed = _prune_unused_media(book)
    _set_identity(book, title, uid)
    toc_entries = _rebuild_toc_from_contents_page(book, nav_href)
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
    )


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


def split_omnibus(source, out_dir=None, allow_medium: bool = False, dry_run: bool = False, log=None) -> SplitResult:
    """Detects the books in `source` and writes one EPUB per book into
    `out_dir` (the source's own folder by default).

    Only a high-confidence detection is split automatically. A medium
    one (see omnibus.py) is reported as "needs_review" unless
    `allow_medium` is True. `dry_run` reports the plan and writes
    nothing. `log` is an optional function that receives progress
    lines."""
    say = log or (lambda _line: None)
    source = Path(source)
    out_dir = Path(out_dir) if out_dir else source.parent

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

    if analysis.confidence != "high" and not allow_medium:
        result.status = "needs_review"
        result.message = (
            f"Found {len(plan)} books, but only with {analysis.confidence} confidence "
            f"({analysis.reason}) so nothing was split. Check the list, then run again with --allow-medium."
        )
        result.outputs = [SplitOutput(n, t, out_dir / name, pages=len(hrefs[n - 1])) for n, t, name in plan]
        return result

    if dry_run:
        result.status = "dry_run"
        result.message = f"Would split into {len(plan)} books ({analysis.method}, {analysis.confidence} confidence)."
        result.outputs = [SplitOutput(n, t, out_dir / name, pages=len(hrefs[n - 1])) for n, t, name in plan]
        return result

    out_dir.mkdir(parents=True, exist_ok=True)
    for (number, title, name) in plan:
        say(f"Writing book {number} of {len(plan)}: {title}")
        out_path = out_dir / name
        uid = _unique_id(source, number)
        output = _build_one(source, analysis, number - 1, hrefs, out_path, title, uid, result.shared_pages)
        output.problems = verify_output(out_path)
        result.outputs.append(output)

    result.words_out = sum(o.words for o in result.outputs)
    result.status = "split"
    result.message = f"Split into {len(result.outputs)} books ({analysis.method}, {analysis.confidence} confidence)."
    return result
