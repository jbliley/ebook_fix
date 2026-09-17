"""
ebook_fix.modules.font_strip

Strips only the `confident` embedded-font-family declarations
ebook_fix.fonts's analysis pass identified -- body-text-role classes,
the `body` element selector itself, and inline font-family set
directly on a main-narrative <p> (or a <font face> tag directly
wrapping one). See ebook_fix.fonts's module docstring for the full
confident/review split and the reasoning behind it -- it mirrors
ebook_fix.modules.color_strip almost exactly, with one genuinely new
piece: an embedded font is a real file in the manifest, not just a CSS
property value, so actually removing one confidently also means:

- Deleting the @font-face rule(s) that declare it, wherever they
  live (an external stylesheet or a chapter's own embedded <style>
  block) -- leaving one behind pointing at a file that no longer
  exists would fail validation.
- Removing the font resource's own manifest entry and, if the book
  ships one, dropping (or trimming) META-INF/encryption.xml -- some
  embedded fonts are IDPF-obfuscated per the EPUB spec, and a leftover
  <EncryptedData> entry pointing at a deleted file is exactly the same
  kind of dangling reference. NOTE: the encryption.xml handling below
  has not been exercised against a real IDPF-obfuscated sample -- none
  of this project's current example books ships one. It's implemented
  from the spec (a CipherReference URI naming the font's own full
  in-zip path) and is inert whenever a book has no encryption.xml at
  all, but treat it as unverified until a real sample turns one up.

A font resource is only ever deleted once EVERY usage of its family
found anywhere in the book -- not just the ones this run happens to
strip -- is itself confident (BookFontSummary.fully_confident_families,
computed once per repair() call from a fresh analysis). A family also
used by a heading class or anywhere else review-bucket is left fully
alone at the resource/@font-face level even while its confident
usages still get their declarations stripped -- something else in the
book still needs that font file to keep working.

apply_review_removals() below, unlike repair(), is deliberately
narrower than its color_strip counterpart: it only ever strips the
CSS-level font-family declaration a person accepted from the GUI
Review tab, never the underlying resource/@font-face rule. Safely
deciding "is this family now unused everywhere" after an ad-hoc,
partial set of accepted review ids would need a full re-scan against
the post-removal book rather than the simple id-membership check
color_strip's own version gets away with -- left for a future pass if
this ever turns out to matter in practice; the confident, unattended
path above already handles the common case (a font embedded purely
for body text, nothing else touching it) safely on its own.

Re-scans the book's raw CSS/element text itself with
ebook_fix.fonts's own classification helpers rather than trusting the
live `element` references on analysis's `confident` findings to still
be the right things to touch -- same "recompute fresh" precedent
color_strip/ellipsis_repair/apostrophe_repair already follow.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import PurePosixPath

from lxml import etree

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE, FONT_FACE_RE, FONT_FAMILY_RE
from ebook_fix.report import Report
from ebook_fix.config import FontRepairConfig
from ebook_fix.fonts import (
    analyze_book_font_usage,
    FONT_FAMILY_DECLARATION_RE,
    FONT_FAMILY_VALUE_RE,
    first_family,
)
from ebook_fix.color import is_confident_selector_group, is_confident_paragraph_context

OPF_NS = "http://www.idpf.org/2007/opf"
XMLENC_NS = "http://www.w3.org/2001/04/xmlenc#"
ENCRYPTION_PATH = "META-INF/encryption.xml"


class FontStripRepair:
    name = "Font Strip"

    def __init__(self, config: FontRepairConfig | None = None):
        self.config = config or FontRepairConfig()

    # -----------------------------------------------------
    # Analysis
    # -----------------------------------------------------

    def analyze(self, book, analysis=None):
        report = Report(self.name)
        fonts = analysis.fonts if analysis is not None else analyze_book_font_usage(book)
        for finding in fonts.confident:
            report.add(finding.href, "Embedded body-text font found", finding.context)
        return report

    # -----------------------------------------------------
    # Repair
    # -----------------------------------------------------

    def repair(self, book, analysis=None):
        fonts = analysis.fonts if analysis is not None else analyze_book_font_usage(book)
        body_text_classes = fonts.body_text_classes
        report = self._remove_matching(
            book,
            main_hrefs=fonts.main_hrefs,
            family_to_hrefs=fonts.family_to_hrefs,
            css_predicate=lambda selector, finding_id: is_confident_selector_group(selector, body_text_classes),
            element_predicate=lambda el, finding_id, href: is_confident_paragraph_context(el),
        )

        removable_families = fonts.fully_confident_families()
        if removable_families:
            self._remove_font_faces_and_resources(book, removable_families, fonts, report)

        return report

    def apply_review_removals(self, book, accepted_ids):
        """Removes exactly the review-bucket CSS-level font-family
        declarations named in `accepted_ids` (a person's choices from
        the GUI Review tab). Never touches the @font-face rule or the
        underlying font resource -- see module docstring for why."""
        accepted_ids = set(accepted_ids)
        if not accepted_ids:
            return Report(self.name)
        fonts = analyze_book_font_usage(book)
        return self._remove_matching(
            book,
            main_hrefs=None,  # id membership alone decides -- zone doesn't matter here
            family_to_hrefs=fonts.family_to_hrefs,
            css_predicate=lambda selector, finding_id: finding_id in accepted_ids,
            element_predicate=lambda el, finding_id, href: finding_id in accepted_ids,
        )

    # -----------------------------------------------------
    # Shared removal walk -- CSS-level font-family declarations only
    # -----------------------------------------------------

    def _remove_matching(self, book, main_hrefs, family_to_hrefs, css_predicate, element_predicate):
        """Walks every CSS file, embedded <style> block, inline
        style="", and <font face> tag exactly once, in the same order
        ebook_fix.fonts.analyze_book_font_usage does, removing a
        `font-family` declaration wherever css_predicate(selector, id)
        or element_predicate(element, id, href) says to -- but only
        ever a declaration whose family resolves to one of this book's
        own embedded fonts (family_to_hrefs), exactly matching what
        analysis found in the first place so ids line up."""
        report = Report(self.name)
        changed_anything = False
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent
        counters: dict = {}

        # 1. External CSS files
        contents = read_book_css(book)
        new_files = getattr(book, "new_files", None) or {}
        for res in getattr(book, "css", []) or []:
            zpath = str(base / res.href)
            if zpath in new_files:
                text = new_files[zpath].decode("utf-8", errors="replace")
            else:
                text = contents.get(res.href)
            if not text:
                continue
            new_text, count = self._strip_css_text(text, res.href, "external_css", family_to_hrefs, css_predicate, counters)
            if count:
                book.new_files[zpath] = new_text.encode("utf-8")
                changed_anything = True
                report.add(
                    res.href,
                    "Embedded body-text font removed",
                    f"{count} font-family declaration(s) removed from {res.href}.",
                )

        # 2. Embedded <style> blocks, inline style="" attributes, and
        #    legacy <font face> tags, per chapter.
        for chapter in book.chapters:
            root = self._root(chapter.document)
            if root is None:
                continue
            href = getattr(chapter, "href", "")
            in_main_zone = main_hrefs is None or href in main_hrefs
            changed = False
            chapter_count = 0

            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                local = etree.QName(el).localname.lower()

                if local == "style":
                    new_text, count = self._strip_css_text(el.text or "", href, "embedded_style", family_to_hrefs, css_predicate, counters)
                    if count:
                        el.text = new_text
                        changed = True
                        chapter_count += count
                    continue

                style_val = el.get("style")
                if style_val:
                    fm = FONT_FAMILY_VALUE_RE.search(style_val)
                    if fm and first_family(fm.group(1)) in family_to_hrefs:
                        key = (href, "inline_style")
                        i = counters.get(key, 0)
                        counters[key] = i + 1
                        finding_id = f"inline_style:{href}:{i}"
                        if in_main_zone and element_predicate(el, finding_id, href):
                            stripped, count = self._strip_inline_style(style_val)
                            if count:
                                if stripped:
                                    el.set("style", stripped)
                                else:
                                    del el.attrib["style"]
                                changed = True
                                chapter_count += count

                if local == "font" and el.get("face"):
                    if first_family(el.get("face")) in family_to_hrefs:
                        key = (href, "font_tag")
                        i = counters.get(key, 0)
                        counters[key] = i + 1
                        finding_id = f"font_tag:{href}:{i}"
                        if in_main_zone and element_predicate(el, finding_id, href):
                            del el.attrib["face"]
                            changed = True
                            chapter_count += 1

            if changed:
                chapter.modified = True
                changed_anything = True
                report.add(
                    chapter.href,
                    "Embedded body-text font removed",
                    f"{chapter_count} font-family declaration(s) removed from {chapter.href}.",
                )

        if changed_anything:
            book.mark_modified()

        return report

    # -----------------------------------------------------
    # @font-face + resource + encryption.xml cleanup
    # -----------------------------------------------------

    def _remove_font_faces_and_resources(self, book, removable_families, fonts, report):
        """For every family where EVERY usage in the book is
        confident, strips its @font-face rule(s) wherever they live
        and deletes the font resource(s) it embeds -- but only a
        resource none of its OTHER declaring families (if any) still
        needs (see module docstring)."""
        base = PurePosixPath(getattr(book, "package_path", "") or "").parent

        # 1. Strip the @font-face rule(s) themselves, external CSS
        #    files first.
        contents = read_book_css(book)
        new_files = getattr(book, "new_files", None) or {}
        for res in getattr(book, "css", []) or []:
            zpath = str(base / res.href)
            text = new_files[zpath].decode("utf-8", errors="replace") if zpath in new_files else contents.get(res.href)
            if not text:
                continue
            new_text, count = self._strip_font_face_blocks(text, removable_families)
            if count:
                book.new_files[zpath] = new_text.encode("utf-8")
                report.add(
                    res.href,
                    "Embedded font @font-face rule removed",
                    f"{count} @font-face rule(s) removed from {res.href} -- font no longer used for body text.",
                )

        # ...then any chapter's own embedded <style> block.
        for chapter in book.chapters:
            root = self._root(chapter.document)
            if root is None:
                continue
            for el in root.iter():
                if not isinstance(el.tag, str) or etree.QName(el).localname.lower() != "style":
                    continue
                new_text, count = self._strip_font_face_blocks(el.text or "", removable_families)
                if count:
                    el.text = new_text
                    chapter.modified = True
                    book.mark_modified()
                    report.add(
                        chapter.href,
                        "Embedded font @font-face rule removed",
                        f"{count} @font-face rule(s) removed from {chapter.href} -- font no longer used for body text.",
                    )

        # 2. Delete the resource file(s) -- only ever a resource whose
        #    every declaring family is itself fully removable.
        hrefs_to_delete = set()
        for family in removable_families:
            for href in fonts.family_to_hrefs.get(family, []):
                owning_families = set(fonts.href_to_families.get(href, []))
                if owning_families and owning_families <= removable_families:
                    hrefs_to_delete.add(href)

        for href in hrefs_to_delete:
            if self._remove_font_resource(book, href):
                report.add(href, "Embedded font file removed", f"{href}: no longer referenced by any @font-face rule.")

        if hrefs_to_delete:
            self._clean_encryption_xml(book, hrefs_to_delete, base, report)

    def _remove_font_resource(self, book, href) -> bool:
        """Drops one embedded font file entirely: removes it from
        book.fonts, the in-memory manifest list, the live OPF
        <manifest> element, and book.removed_files so the writer
        actually drops the physical file -- same idiom
        modules/gutenberg_repair.py's _remove_whole_chapter already
        uses for dropping a whole spine file, minus the spine/nav
        bookkeeping a font resource never had in the first place."""
        opf = getattr(book, "opf_document", None)
        if opf is None:
            return False

        manifest_item = next((m for m in book.manifest if m.href == href), None)
        if manifest_item is None:
            return False
        item_id = manifest_item.id

        manifest_el = opf.find(f"{{{OPF_NS}}}manifest")
        if manifest_el is not None:
            for item in manifest_el.findall(f"{{{OPF_NS}}}item"):
                if item.get("id") == item_id:
                    manifest_el.remove(item)
                    break

        book.manifest = [m for m in book.manifest if m.href != href]
        book.fonts = [f for f in book.fonts if f.href != href]

        base = PurePosixPath(book.package_path).parent
        book.removed_files.add(str(base / href))
        book.opf_modified = True
        return True

    def _clean_encryption_xml(self, book, removed_hrefs, base, report) -> None:
        """Drops (or trims) META-INF/encryption.xml if the book has
        one -- some embedded fonts are IDPF-obfuscated per the EPUB
        spec, and leaving a <EncryptedData> entry pointing at a font
        file that was just deleted would fail validation. See module
        docstring: this path is implemented from the spec and hasn't
        been exercised against a real obfuscated sample yet."""
        import zipfile

        source = getattr(book, "source", None)
        if source is None:
            return
        try:
            with zipfile.ZipFile(source, "r") as archive:
                names = archive.namelist()
                if ENCRYPTION_PATH not in names:
                    return
                raw = archive.read(ENCRYPTION_PATH)
        except (OSError, zipfile.BadZipFile, KeyError):
            return

        new_files = getattr(book, "new_files", None)
        if ENCRYPTION_PATH in (new_files or {}):
            raw = new_files[ENCRYPTION_PATH]

        try:
            tree = etree.fromstring(raw)
        except etree.XMLSyntaxError:
            return

        removed_full_paths = {str(base / href) for href in removed_hrefs}
        changed = False
        for enc_data in tree.findall(f".//{{{XMLENC_NS}}}EncryptedData"):
            cipher_ref = enc_data.find(f"{{{XMLENC_NS}}}CipherData/{{{XMLENC_NS}}}CipherReference")
            if cipher_ref is None:
                continue
            uri = (cipher_ref.get("URI") or "").lstrip("/")
            if uri in removed_full_paths:
                parent = enc_data.getparent()
                if parent is not None:
                    parent.remove(enc_data)
                    changed = True

        if not changed:
            return

        remaining = tree.findall(f".//{{{XMLENC_NS}}}EncryptedData")
        if not remaining:
            book.removed_files.add(ENCRYPTION_PATH)
            if new_files is not None:
                new_files.pop(ENCRYPTION_PATH, None)
            report.add(ENCRYPTION_PATH, "encryption.xml removed", "No obfuscated resources left after font removal.")
        else:
            book.new_files[ENCRYPTION_PATH] = etree.tostring(tree, xml_declaration=True, encoding="utf-8")
            report.add(ENCRYPTION_PATH, "encryption.xml trimmed", f"Removed entr(y/ies) for: {', '.join(sorted(removed_hrefs))}")

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _root(self, tree):
        if tree is None:
            return None
        return tree if hasattr(tree, "iter") else tree.getroot()

    def _strip_css_text(self, text: str, href: str, location_kind: str, family_to_hrefs: dict, predicate, counters: dict):
        """Remove a `font-family: ...;` declaration, but only from a
        rule `predicate(selector_group, finding_id)` says yes to AND
        whose declared family resolves to one of this book's own
        embedded fonts -- leaving every other rule, every other
        declaration within a stripped rule, and any font-family naming
        a plain system font untouched. Returns (new_text,
        count_removed)."""
        if not text:
            return text, 0
        cleaned = COMMENT_RE.sub("", text)
        count = 0

        def strip_rule(m):
            nonlocal count
            selector = m.group(1).strip()
            body = m.group(2)
            if not selector or selector.startswith("@"):
                return m.group(0)
            fm = FONT_FAMILY_VALUE_RE.search(body)
            if not fm or first_family(fm.group(1)) not in family_to_hrefs:
                return m.group(0)
            key = (href, location_kind)
            i = counters.get(key, 0)
            counters[key] = i + 1
            finding_id = f"{location_kind}:{href}:{i}"
            if not predicate(selector, finding_id):
                return m.group(0)
            new_body, n = FONT_FAMILY_DECLARATION_RE.subn("", body)
            if not n:
                return m.group(0)
            count += n
            return f"{m.group(1)}{{{new_body}}}"

        new_text = RULE_RE.sub(strip_rule, cleaned)
        return new_text, count

    def _strip_font_face_blocks(self, text: str, families_to_remove: set):
        """Removes an entire @font-face rule whose declared family
        (first name only, case-insensitive) is in families_to_remove.
        Returns (new_text, count_removed)."""
        if not text:
            return text, 0
        cleaned = COMMENT_RE.sub("", text)
        count = 0

        def strip_block(m):
            nonlocal count
            fm = FONT_FAMILY_RE.search(m.group(1))
            if not fm or first_family(fm.group(1)) not in families_to_remove:
                return m.group(0)
            count += 1
            return ""

        new_text = FONT_FACE_RE.sub(strip_block, cleaned)
        return new_text, count

    def _strip_inline_style(self, style_value: str):
        """Remove any `font-family: ...;` from an inline style=""
        value, keeping everything else exactly as declared. Returns
        (new_value, count_removed)."""
        new_value, n = FONT_FAMILY_DECLARATION_RE.subn("", style_value)
        if not n:
            return style_value, 0
        cleaned = re.sub(r'\s*;\s*;+', ';', new_value).strip().strip(";").strip()
        return cleaned, n
