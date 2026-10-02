"""
ebook_fix.split_fragments

Cleans up one specific by-product of splitting: the orphaned
continuation fragment.

When a book was originally cut into files by size rather than by
chapter (several chapters per file, with a seam falling in the middle of
a chapter), splitting a file at its chapter headings leaves the text
before that file's first heading sitting on its own, with no heading
and no table of contents entry. That text is the tail of whatever
chapter started in the previous file, so it belongs there.

merge_leading_fragments() moves each such fragment onto the end of the
chapter file right before it and drops the now-empty file from the
book. It only acts when it is safe to:

- The file before it must be a chapter file produced by this same
  split run. A leading chunk after a title page or copyright page
  (genuine front matter) is left alone.
- The fragment must not contain a heading, and must not open with
  something that looks like a chapter/part marker (a "Part Two"
  divider page that happens to sit before the first chapter).
- The fragment must not contain internal links (their targets could
  now be in a different file).
- Nothing may link to the fragment's file as a whole (a table of
  contents entry, a guide reference, a plain link with no #fragment),
  since that file is about to stop existing. Links to specific ids
  inside it are fine; they're re-pointed at the file that now holds
  them.
- The total word count across both files must be identical before and
  after, or the move is undone.

Anything skipped is reported with the reason, never silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from lxml import etree

OPF_NS = "http://www.idpf.org/2007/opf"
NCX_NS_URI = "http://www.daisy.org/z3986/2005/ncx/"
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")
HEADING_TAGS = frozenset(("h1", "h2", "h3", "h4", "h5", "h6"))
MAX_MARKER_LOOKALIKE_CHARS = 60


@dataclass
class FragmentMergeResult:
    merged: list = field(default_factory=list)   # (fragment_href, target_href)
    skipped: list = field(default_factory=list)  # (fragment_href, reason)


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------


def _local(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return etree.QName(el).localname.lower()


def _find_body(document):
    if document is None:
        return None
    return document.find(".//{*}body")


def _word_count(element) -> int:
    if element is None:
        return 0
    return len("".join(element.itertext()).split())


def _chain(body) -> list:
    """The single-child, no-text wrapper elements under <body> before
    real content starts (same idea as splitter._effective_container).
    Returns the list of wrapper elements, outermost first."""
    chain = []
    container = body
    while not (container.text or "").strip():
        children = list(container)
        if len(children) != 1:
            break
        only_child = children[0]
        if not isinstance(only_child.tag, str):
            break
        # Never treat a paragraph/heading as a wrapper.
        if _local(only_child) not in ("div", "section", "article", "main"):
            break
        chain.append(only_child)
        container = only_child
    return chain


def _descend(body, tags: list):
    """Walks down `body` through single-child wrappers whose tag names
    match `tags`; returns the innermost container, or None if the
    structure doesn't match."""
    container = body
    for tag in tags:
        children = list(container)
        if (container.text or "").strip() or len(children) != 1:
            return None
        if _local(children[0]) != tag:
            return None
        container = children[0]
    return container


def _fragment_container(body, target_tags: list):
    """The element holding a fragment's content. Normally it has the
    same wrapper chain as the chapter it's joining. A fragment with no
    wrapper at all (bare paragraphs straight under <body>) is also fine:
    moving them into the other file's wrapper loses nothing. A fragment
    with a *different* wrapper is not -- its wrapper's own styling would
    be dropped -- so that returns None."""
    own_tags = [_local(el) for el in _chain(body)]
    if not own_tags:
        return body
    if own_tags == target_tags:
        return _descend(body, own_tags)
    return None


def _first_text(container) -> str:
    for child in container:
        if not isinstance(child.tag, str):
            continue
        text = " ".join("".join(child.itertext()).split())
        if text:
            return text
    return ""


def _path_of(href: str) -> str:
    return (href or "").partition("#")[0]


# ---------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------


def _whole_file_reference_exists(book, href: str) -> bool:
    """True if anything links to `href` without a #fragment (so it
    can't be re-pointed at a specific id), in the body of any chapter
    (the nav document included), the NCX, or the OPF's own guide."""
    for chapter in getattr(book, "chapters", []) or []:
        doc = getattr(chapter, "document", None)
        if doc is None or chapter.href == href:
            continue
        for el in doc.iter():
            if _local(el) != "a":
                continue
            target = el.get("href") or ""
            if target.startswith(EXTERNAL_PREFIXES):
                continue
            path, _, fragment = target.partition("#")
            if path == href and not fragment:
                return True

    ncx = getattr(book, "ncx_document", None)
    if ncx is not None:
        for el in ncx.iter():
            if _local(el) != "content":
                continue
            path, _, fragment = (el.get("src") or "").partition("#")
            if path == href and not fragment:
                return True

    opf = getattr(book, "opf_document", None)
    if opf is not None:
        for el in opf.iter():
            if not isinstance(el.tag, str) or _local(el) == "item":
                continue
            if _path_of(el.get("href") or "") == href:
                return True

    return False


def _skip_reason(book, fragment, target, anchored_hrefs: set, tags: list):
    if target is None:
        return "no file before it"
    if target.href not in anchored_hrefs:
        return "the file before it isn't a chapter from this split"
    if fragment.document is None or target.document is None:
        return "a file has no parsed document"

    body_f = _find_body(fragment.document)
    body_t = _find_body(target.document)
    if body_f is None or body_t is None:
        return "a file has no <body>"

    container_f = _fragment_container(body_f, tags)
    if container_f is None:
        return "its wrapper structure differs from the chapter before it"

    for el in container_f.iter():
        name = _local(el)
        if name in HEADING_TAGS:
            return "it contains a heading"
        if name == "a":
            href = el.get("href") or ""
            if href and not href.startswith(EXTERNAL_PREFIXES):
                return "it contains internal links"

    opener = _first_text(container_f)
    if opener and len(opener) <= MAX_MARKER_LOOKALIKE_CHARS:
        from ebook_fix.chapters import _classify
        try:
            looks_like_marker = _classify(opener) is not None
        except Exception:
            looks_like_marker = False
        if looks_like_marker:
            return f"it opens with what looks like a chapter or part marker ({opener!r})"

    if _whole_file_reference_exists(book, fragment.href):
        return "the table of contents or a link points at the whole file"

    return None


# ---------------------------------------------------------------------
# Rewiring
# ---------------------------------------------------------------------


def _repoint_links(book, old_href: str, new_href: str) -> None:
    for chapter in getattr(book, "chapters", []) or []:
        doc = getattr(chapter, "document", None)
        if doc is None or chapter.href == old_href:
            continue
        changed = False
        for el in doc.iter():
            if _local(el) != "a":
                continue
            target = el.get("href") or ""
            if target.startswith(EXTERNAL_PREFIXES):
                continue
            path, _, fragment = target.partition("#")
            if path == old_href and fragment:
                el.set("href", f"{new_href}#{fragment}")
                changed = True
        if changed:
            chapter.modified = True

    ncx = getattr(book, "ncx_document", None)
    if ncx is not None:
        changed = False
        for el in ncx.iter():
            if _local(el) != "content":
                continue
            path, _, fragment = (el.get("src") or "").partition("#")
            if path == old_href and fragment:
                el.set("src", f"{new_href}#{fragment}")
                changed = True
        if changed:
            book.ncx_modified = True


def _drop_file(book, href: str) -> None:
    """Removes a spine file from the book: book.chapters, the in-memory
    manifest/spine, the live OPF elements, and book.removed_files so the
    writer drops it from the saved archive."""
    opf = book.opf_document
    manifest_item = next((m for m in book.manifest if m.href == href), None)
    item_id = manifest_item.id if manifest_item is not None else None

    if opf is not None and item_id is not None:
        manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
        spine_el = opf.find(f"{{{OPF_NS}}}spine")
        if manifest_el is not None:
            for item in manifest_el.findall(f"{{{OPF_NS}}}item"):
                if item.get("id") == item_id:
                    manifest_el.remove(item)
                    break
        if spine_el is not None:
            for itemref in spine_el.findall(f"{{{OPF_NS}}}itemref"):
                if itemref.get("idref") == item_id:
                    spine_el.remove(itemref)
                    break

    book.manifest = [m for m in book.manifest if m.href != href]
    if item_id is not None:
        book.spine = [idref for idref in book.spine if idref != item_id]
    book.chapters = [c for c in book.chapters if c.href != href]

    base = PurePosixPath(book.package_path).parent
    book.removed_files.add(str(base / href))
    book.opf_modified = True
    book.mark_modified()


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------


def merge_leading_fragments(book, fragment_hrefs, anchored_hrefs) -> FragmentMergeResult:
    """`fragment_hrefs`: files that, after a split, hold only an untitled
    chunk from before their original first chapter marker.
    `anchored_hrefs`: every chapter file this split run produced (its
    original hrefs and new ones) -- the only files a fragment may be
    merged into.

    Handled in book (spine) order. Each fragment is merged into the
    chapter immediately before it, if every guard in the module
    docstring passes."""
    result = FragmentMergeResult()
    wanted = set(fragment_hrefs)
    anchored = set(anchored_hrefs)

    for fragment in [c for c in list(book.chapters) if c.href in wanted]:
        index = next((i for i, c in enumerate(book.chapters) if c is fragment), None)
        if index is None:
            continue
        target = book.chapters[index - 1] if index > 0 else None

        tags = []
        if target is not None and target.document is not None:
            body_t = _find_body(target.document)
            if body_t is not None:
                tags = [_local(el) for el in _chain(body_t)]

        reason = _skip_reason(book, fragment, target, anchored, tags)
        if reason is not None:
            result.skipped.append((fragment.href, reason))
            continue

        body_f = _find_body(fragment.document)
        body_t = _find_body(target.document)
        container_f = _fragment_container(body_f, tags)
        container_t = _descend(body_t, tags)
        if container_f is None or container_t is None:
            result.skipped.append((fragment.href, "its wrapper structure differs from the chapter before it"))
            continue

        words_before = _word_count(body_t) + _word_count(body_f)
        moving = list(container_f)
        # Make sure the last element of the chapter ends in whitespace
        # so the first word of the fragment can't fuse with its last.
        if len(container_t):
            last = container_t[-1]
            if not (last.tail or "").strip() and not (last.tail or ""):
                last.tail = "\n"
        for child in moving:
            container_t.append(child)

        if _word_count(body_t) != words_before or _word_count(body_f) != 0:
            for child in moving:
                container_f.append(child)
            result.skipped.append((fragment.href, "word count check failed, move undone"))
            continue

        target.modified = True
        _repoint_links(book, fragment.href, target.href)
        _drop_file(book, fragment.href)
        result.merged.append((fragment.href, target.href))

    return result
