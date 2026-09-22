"""
ebook_fix.fb2

Reads FB2 (FictionBook) files -- a single well-formed XML document,
not a zip archive and not a binary format like MOBI. Much closer to
reading an EPUB's OPF <metadata> than anything in ebook_fix.mobi had
to deal with, since it's already namespaced XML that `lxml` parses
directly with no container/compression layer in front of it.

A single module rather than a subpackage (unlike ebook_fix.mobi):
FB2 doesn't have MOBI's separable container/header/metadata concerns,
it's one XML tree throughout, so splitting it up would just add
indirection without a real seam to split along.

Confirmed against a real sample (examples/FB2-Example.fb2, Harper
Lee's "To Kill a Mockingbird") rather than written from spec alone.
See docs/format_support_plan.md for what's confirmed and what's
still open (only FictionBook 2.0's namespace has been tested; a
2.1-namespaced or zipped .fb2.zip file hasn't been).
"""
from __future__ import annotations
import base64
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ebook_fix.cover import sniff_image_media_type
from ebook_fix.report import console, print_header

FB2_EXTENSIONS = (".fb2",)

# The only namespace URI confirmed against a real sample so far. FB2
# 2.1 files are documented to use a different URI
# ("...fictionbook/2.1") -- unconfirmed, since no 2.1 sample exists
# yet. Detected dynamically from the root element rather than
# hardcoded, so a 2.1 file at least parses even though it's untested.
FB2_2_0_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"


@dataclass
class Fb2Metadata:
    title: str = ""
    authors: list = field(default_factory=list)
    language: str = ""
    publisher: str = ""
    date: str = ""
    description: str = ""
    genres: list = field(default_factory=list)


@dataclass
class Fb2AnalysisReport:
    path: object = None
    file_size: int = 0
    namespace: str = ""
    namespace_confirmed: bool = True  # False for anything other than the 2.0 URI tested so far
    metadata: object = None
    top_level_section_count: int = 0
    total_section_count: int = 0
    has_notes_body: bool = False      # a second <body name="notes"> for footnotes/endnotes
    cover_binary_id: str = ""
    cover_media_type: str = ""
    error: str = ""


def _text(el) -> str:
    """Flattens an element's text, including nested inline tags like
    <emphasis>, into one plain string -- annotation/title paragraphs
    often carry inline markup that would otherwise get dropped."""
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _author_name(author_el, ns: dict) -> str:
    first = author_el.find("fb:first-name", ns)
    last = author_el.find("fb:last-name", ns)
    parts = [p.text for p in (first, last) if p is not None and p.text]
    name = " ".join(parts).strip()
    if name:
        return name
    # Some FB2 files give only a <nickname> instead of first/last name.
    nickname = author_el.find("fb:nickname", ns)
    return nickname.text.strip() if nickname is not None and nickname.text else ""


def extract_metadata(tree, ns: dict) -> Fb2Metadata:
    title_info = tree.find(".//fb:description/fb:title-info", ns)
    if title_info is None:
        return Fb2Metadata()

    title_el = title_info.find("fb:book-title", ns)
    lang_el = title_info.find("fb:lang", ns)
    date_el = title_info.find("fb:date", ns)
    annotation_el = title_info.find("fb:annotation", ns)
    publisher_el = tree.find(".//fb:description/fb:publish-info/fb:publisher", ns)

    authors = [
        name
        for author_el in title_info.findall("fb:author", ns)
        if (name := _author_name(author_el, ns))
    ]
    genres = [g.text.strip() for g in title_info.findall("fb:genre", ns) if g.text]

    date = ""
    if date_el is not None:
        date = date_el.get("value") or (date_el.text or "").strip()

    return Fb2Metadata(
        title=(title_el.text or "").strip() if title_el is not None else "",
        authors=authors,
        language=(lang_el.text or "").strip() if lang_el is not None else "",
        publisher=(publisher_el.text or "").strip() if publisher_el is not None else "",
        date=date,
        description=_text(annotation_el),
        genres=genres,
    )


def analyze_fb2(path: Path) -> Fb2AnalysisReport:
    """Reads and analyzes one FB2 file. Never raises for a malformed
    or unsupported file -- any problem is captured in the report's
    `error` field instead, matching ebook_fix.mobi.analyzer."""
    report = Fb2AnalysisReport(path=path)
    report.file_size = path.stat().st_size

    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError as exc:
        report.error = f"Not a well-formed XML file: {exc}"
        return report

    root = tree.getroot()
    namespace = root.nsmap.get(None, "")
    if not namespace.rstrip("/").lower().endswith("fictionbook/2.0"):
        # Still attempt the parse with whatever namespace the file
        # actually declares, rather than refusing outright -- but
        # flag it, since only the 2.0 URI has been tested.
        report.namespace_confirmed = False
    report.namespace = namespace or "(none declared)"

    expected_root_tag = f"{{{namespace}}}FictionBook" if namespace else "FictionBook"
    if root.tag != expected_root_tag:
        report.error = "Root element isn't <FictionBook> -- not an FB2 file."
        return report

    ns = {"fb": namespace} if namespace else {}
    # Non-namespaced findall calls below assume `ns` always has key
    # "fb"; fall back to the confirmed 2.0 URI so lookups still work
    # against a file with no declared default namespace at all.
    if not namespace:
        ns = {"fb": FB2_2_0_NS}

    report.metadata = extract_metadata(tree, ns)

    bodies = tree.findall(".//fb:body", ns)
    main_body = next((b for b in bodies if b.get("name") is None), bodies[0] if bodies else None)
    report.has_notes_body = any(b.get("name") == "notes" for b in bodies)

    if main_body is not None:
        report.top_level_section_count = len(main_body.findall("fb:section", ns))
        report.total_section_count = len(main_body.findall(".//fb:section", ns))

    coverpage = tree.find(".//fb:description/fb:title-info/fb:coverpage/fb:image", ns)
    if coverpage is not None:
        href = coverpage.get("{http://www.w3.org/1999/xlink}href", "").lstrip("#")
        binary_el = next(
            (b for b in tree.findall(".//fb:binary", ns) if b.get("id") == href),
            None,
        )
        if binary_el is not None:
            report.cover_binary_id = href
            try:
                image_bytes = base64.b64decode(binary_el.text or "", validate=False)
                report.cover_media_type = sniff_image_media_type(image_bytes) or (
                    binary_el.get("content-type", "") + " (unconfirmed by magic bytes)"
                )
            except (ValueError, TypeError):
                report.cover_media_type = binary_el.get("content-type", "unreadable")

    return report


def print_fb2_report(report: Fb2AnalysisReport) -> None:
    """Prints in the same plain-text, underlined-header style
    `analyze` already uses for EPUB and MOBI (see ebook_fix.report),
    clearly labeled wherever FB2 doesn't have a directly comparable
    concept."""
    print_header("[File]")
    console.print(f"Path: {report.path}")
    console.print(f"File size: {report.file_size:,} bytes")

    if report.error:
        console.print(f"\nERROR: {report.error}")
        return

    console.print("")
    print_header("[Book Metadata]")
    m = report.metadata
    console.print(f"Title: {m.title or '(none found)'}")
    console.print(f"Author: {', '.join(m.authors) if m.authors else '(none found)'}")
    console.print(f"Language: {m.language or '(none found)'}")
    console.print(f"Publisher: {m.publisher or '(none found)'}")
    console.print(f"Date: {m.date or '(none found)'}")
    if m.genres:
        console.print(f"Genre: {', '.join(m.genres)}")
    if m.description:
        console.print(f"Description: {m.description}")

    console.print("")
    print_header("[File Contents]")
    namespace_line = report.namespace
    if not report.namespace_confirmed:
        namespace_line += " (untested namespace -- only FictionBook 2.0 confirmed)"
    console.print(f"FictionBook namespace: {namespace_line}")
    console.print(f"Top-level sections (parts/chapters): {report.top_level_section_count}")
    console.print(f"Total sections (incl. nested): {report.total_section_count}")
    console.print(f"Notes/footnotes body present: {'Yes' if report.has_notes_body else 'No'}")
    if report.cover_binary_id:
        console.print(f"Cover image: <binary id=\"{report.cover_binary_id}\"> ({report.cover_media_type})")
    else:
        console.print("Cover image: not found")
