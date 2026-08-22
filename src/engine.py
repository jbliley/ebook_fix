import tempfile
from pathlib import Path

from rich.console import Console

from ebook_fix.parser import EPUBParser
from ebook_fix.writer import EPUBWriter
from ebook_fix.config import Config
from ebook_fix.report import print_header
from ebook_fix.validation import validate_epub
from ebook_fix.container_repair import attempt_repair
from ebook_fix.analyzer import EPUBAnalyzer
from ebook_fix.serialize import save_report
from ebook_fix.class_map import build_class_profiles, format_class_map, write_mapping_file
from ebook_fix.structure import analyze_structure, format_structure_report, iter_chapter_nodes, SplitConfidence
from ebook_fix.splitter import apply_split, SplitMarker, SplitError
from ebook_fix.modules.epub3_upgrade import EPUB3UpgradeRepair
from ebook_fix.modules.paragraph import ParagraphRepair
from ebook_fix.modules.chapter_markup import ChapterMarkupRepair
from ebook_fix.modules.images import ImageRepair
from ebook_fix.modules.whitespace import WhitespaceRepair
from ebook_fix.modules.class_standardize import ClassStandardizeRepair, load_mapping_file, MappingError
from ebook_fix.modules.gutenberg_repair import GutenbergRepair
from ebook_fix.modules.ellipsis_repair import EllipsisRepair
from ebook_fix.ellipsis import normalize_ellipsis_text
from ebook_fix.modules.apostrophe_repair import ApostropheRepair, resolve_target_apostrophe_char
from ebook_fix.apostrophes import normalize_apostrophes_text

console = Console()


class Engine:
    def __init__(self, verbose=False, config=None, auto_repair_container=True):
        self.verbose = verbose
        self.config = config or Config()
        self.auto_repair_container = auto_repair_container
        self.modules = self._build_modules()

    def _build_modules(self):
        modules = []
        # Runs first: every other module should see the upgraded
        # (EPUB3, nav-document-having) structure, not the original.
        if getattr(self.config, "epub3_upgrade", None) and getattr(self.config.epub3_upgrade, "enabled", True):
            modules.append(EPUB3UpgradeRepair(self.config.epub3_upgrade))
        # Runs early, right after the EPUB3 upgrade (so any nav
        # document it cleans up already exists) and before every
        # content-editing module below, so those don't waste effort
        # processing text that's about to be deleted anyway.
        if getattr(self.config, "gutenberg_repair", None) and getattr(self.config.gutenberg_repair, "enabled", True):
            modules.append(GutenbergRepair(self.config.gutenberg_repair))
        if getattr(self.config, "paragraph_repair", None) and getattr(self.config.paragraph_repair, "enabled", True):
            modules.append(ParagraphRepair(self.config.paragraph_repair))
        if getattr(self.config, "chapter_markup", None) and getattr(self.config.chapter_markup, "enabled", True):
            modules.append(ChapterMarkupRepair(self.config.chapter_markup))
        if getattr(self.config, "image_repair", None) and getattr(self.config.image_repair, "enabled", True):
            modules.append(ImageRepair(self.config.image_repair))
        # Runs before Whitespace Normalizer, not after: both modules
        # can end up wanting to touch the very same text/tail node
        # (an ellipsis sitting in a paragraph that also has, say,
        # doubled internal whitespace). Each repair module only
        # trusts its own analysis-time snapshot of a node's text and
        # skips it if that node was already changed since -- see the
        # "current_val != issue.before" guard in both modules' repair()
        # -- so whichever of the two runs second loses that node for
        # this pass. Ellipsis wins the tie deliberately: an unwanted
        # "..." is a content issue, not just formatting, and running
        # this repair pipeline again afterward (once the book has been
        # re-analyzed) still catches any whitespace on that same node
        # that got skipped this time.
        if getattr(self.config, "ellipsis_repair", None) and getattr(self.config.ellipsis_repair, "enabled", True):
            modules.append(EllipsisRepair(self.config.ellipsis_repair))
        # Same reasoning as Ellipsis just above: this can touch a
        # text/tail node the Whitespace Normalizer would also want,
        # so it needs to run before that module for the "current_val
        # != issue.before" stale-check guard to actually protect it.
        # Runs after Ellipsis since the two rarely overlap in
        # practice (a missing apostrophe needs a bare single-space
        # gap between two whitelisted words, an ellipsis artifact
        # needs periods) -- order between them isn't load-bearing.
        if getattr(self.config, "apostrophe_repair", None) and getattr(self.config.apostrophe_repair, "enabled", True):
            modules.append(ApostropheRepair(self.config.apostrophe_repair))
        if getattr(self.config, "whitespace_repair", None) and getattr(self.config.whitespace_repair, "enabled", True):
            modules.append(WhitespaceRepair(self.config.whitespace_repair))
        return modules

    def _cover_status_line(self, cover):
        """One-line summary of ebook_fix.cover's findings for the
        [File Contents] overview -- the [Cover Image] findings section
        further down covers the detail when something's actually
        wrong."""
        if not cover.declared:
            return "not declared"
        if not cover.exists_in_archive:
            return f"declared ({cover.resolved_href}) but MISSING from the EPUB"
        if not cover.is_image_media_type:
            return f"declared ({cover.resolved_href}) but media-type isn't an image"
        if cover.mismatched_declarations:
            return f"declared, but meta/properties tags disagree ({cover.resolved_href} used)"
        method = "properties=\"cover-image\"" if cover.properties_item is not None else "<meta name=\"cover\">"
        return f"{cover.resolved_href} (via {method})"

    def log(self, message):
        console.print(message)

    def header(self, text):
        print_header(text)

    def _resolve_source(self, epub):
        """
        Run the integrity check first. If it passes, use the file as-is.
        If it fails, try a conservative automatic repair of the ZIP/EPUB
        container and, if that succeeds, continue using a repaired copy.

        Returns (source_path, temp_path). `source_path` is None if the
        file can't be used at all. `temp_path` is set (and should be
        cleaned up by the caller) only when a repaired temp copy was
        created.
        """
        result = validate_epub(epub)
        if result.valid:
            return Path(epub), None

        result.print()

        if not self.auto_repair_container:
            self.log("\nAborting: file failed the integrity check above.")
            return None, None

        self.log("\nAttempting an automatic repair of the file's structure...")
        repair = attempt_repair(epub)
        repair.print()

        if not repair.repaired:
            self.log("\nAborting: file failed the integrity check and couldn't be repaired.")
            return None, None

        tmp = tempfile.NamedTemporaryFile(suffix=".epub", delete=False)
        try:
            tmp.write(repair.data)
        finally:
            tmp.close()
        temp_path = Path(tmp.name)
        self.log("")
        return temp_path, temp_path

    def analyze(self, epub, details=False):
        source, temp_path = self._resolve_source(epub)
        if source is None:
            return
        try:
            self.log("Opening EPUB...")
            parser = EPUBParser()
            book = parser.load(source)
            self.log("")

            # Integrated EPUBAnalyzer structural scan
            self.log("Analyzing book structure...")
            analyzer = EPUBAnalyzer()
            analysis_report = analyzer.analyze(book)

            cache_file = save_report(analysis_report, epub)
            self.log(f"Saved analysis cache: {cache_file}")
            self.log("")

            s = analysis_report.summary
            ch_summary = analysis_report.chapters
            t = analysis_report.typography
            css = analysis_report.css
            fm = analysis_report.frontmatter

            # ==========================================
            # 1. ANALYSIS & OVERVIEW (Top Section)
            # ==========================================

            self.header("/n[Book Metadata]")
            self.log(
                f"Title: {s.title or '(none found)'}"
                f"\nAuthor: {s.author or '(none found)'}"
                f"\nLanguage: {s.language or '(none found)'}"
                f"\nPublisher: {s.publisher or '(none found)'}"
                f"\nIdentifier: {s.identifier or '(none found)'}"
                f"\nDate: {s.date or '(none found)'}"
                f"\nRights: {s.rights or '(none found)'}"
                f"\nEPUB Version: {s.epub_version or 'unknown'}"
                + (
                    f" (will be upgraded to EPUB {s.epub_target_version})"
                    if s.epub_needs_upgrade else ""
                )
            )
            if s.subjects:
                self.log(f"Subjects/Genre: {', '.join(s.subjects)}")
            if s.description:
                self.log(f"Description: {s.description}")

            self.log("")
            self.header("[File Contents]")
            self.log(
                f"HTML/XHTML pages: {s.html_page_count}"
                f"\nSpine entries: {s.spine_entry_count}"
                f"\nTOC entries: {s.toc_entry_count}"
                + (f" (from {s.toc_source})" if s.toc_source else " (no NCX or nav document found)")
                + f"\nCSS files: {s.css_file_count}"
                f"\nImage files: {s.image_file_count}"
                f"\nFont files: {s.font_file_count}"
                f"\nAudio files: {s.audio_file_count}"
                f"\nVideo files: {s.video_file_count}"
                f"\nOther files: {s.other_file_count}"
                f"\nTotal word count: {s.total_word_count:,}"
                f"\nCover image: {self._cover_status_line(analysis_report.cover)}"
            )

            self.log("")
            self.header("[Book Structure Overview]")
            self.log(
                f"Total Paragraphs: {analysis_report.total_paragraphs}"
                f"\nTotal Images: {analysis_report.total_images}"
                f"\nTotal Links: {analysis_report.total_links}"
            )

            if ch_summary.parts:
                self.log(f"Divisions/Parts: {len(ch_summary.parts)}")
                if details:
                    for p in ch_summary.parts:
                        self.log(f"  • {p.text!r} - {p.href}")

            if ch_summary.best_sequence:
                seq = ch_summary.best_sequence
                files_spanned = len({c.href for c in seq.candidates})
                self.log(
                    f"Chapters Detected: {seq.length} ({seq.style.value}) "
                    f"across {files_spanned} file(s)"
                )
            else:
                self.log("Chapters Detected: None")

            if fm.boundaries_confirmed:
                self.log(
                    f"Front Matter: {fm.front_matter_count} page(s) | "
                    f"Back Matter: {fm.back_matter_count} page(s) | "
                    f"Main Content: {fm.main_content_count} page(s)"
                )
                if details:
                    for entry in fm.chapters:
                        if entry.zone in ("front", "back"):
                            self.log(f"  • [{entry.zone}] {entry.href} - {entry.label} ({entry.confidence} confidence)")
            else:
                self.log("Front/Back Matter: not classified (no confirmed chapter sequence to anchor on)")

            self.log("")
            self.header("Typography Overview")
            self.log(
                f"Quotes: {t.total_straight_double_quotes} straight double, {t.total_curly_double_quotes} curly double"
                f"\nApostrophes: {t.total_straight_apostrophes} straight apostrophe, {t.total_curly_apostrophes} curly apostrophe"
                f"\nDashes: {t.total_hyphen} hyphen, {t.total_en_dash} en dash, {t.total_em_dash} em dash, {t.total_double_hyphen} double-hyphen (--)"
                f"\nEllipsis: {t.total_unicode_ellipsis} unicode (…), {t.total_ascii_ellipsis} ascii (...)"
                f"\nSentence spacing: {t.total_single_space_after_sentence} single-space, {t.total_double_space_after_sentence} double-space"
                f"\nMissing apostrophes (contraction split by a space): {analysis_report.apostrophes.total_match_count}"
            )

            self.log("")
            self.header("CSS Overview")
            self.log(
                f"Stylesheets: {css.css_file_count} | Rules: {css.total_rules} | !important uses: {css.total_important}"
                f"\nDeclared classes: {css.declared_class_count} | Declared ids: {css.declared_id_count}"
                f"\nInline style attributes used in HTML: {css.inline_style_element_count}"
            )

            # Optional detailed structural breakdown output
            if details:
                self.log("")
                self.header("Detailed Chapter Structure")
                if ch_summary.best_sequence:
                    for c in ch_summary.best_sequence.candidates:
                        repeat_note = (
                            f" [repeated on {c.occurrence_count} pages]"
                            if c.occurrence_count > 1 else ""
                        )
                        self.log(f"  • #{c.number} - {c.href} <{c.tag}> {c.text!r} (score {c.score}){repeat_note}")

                for ch in analysis_report.chapter_reports:
                    title = ch.title or "Untitled Chapter"
                    thin_marker = " [THIN/EMPTY]" if ch.is_thin else ""
                    matter_marker = f" [{ch.matter_label}]" if ch.matter_zone in ("front", "back") else ""
                    self.log(f"\n[Chapter: {ch.href} - {title}]{thin_marker}{matter_marker}")
                    self.log(f"  • Paragraphs: {ch.paragraphs} | Images: {ch.images} | Links: {ch.links} | Words: {ch.word_count}")
                    self.log(f"  • Tables: {ch.tables} | Lists: {ch.lists}")
                    if ch.headings:
                        self.log(f"  • Headings: " + ", ".join(f"{h.upper()}: {cnt}" for h, cnt in ch.headings.items()))
                    if ch.css_classes:
                        top_classes = ", ".join(f".{cls} ({cnt})" for cls, cnt in ch.css_classes.most_common(5))
                        self.log(f"  • Top Classes: {top_classes}")

            # ==========================================
            # 2. ISSUES & FINDINGS (Bottom Section)
            # ==========================================

            self.log("")
            self.header("Issues & Findings Summary")

            # Structure Issues
            structural_issues = []
            if analysis_report.unexplained_thin_chapters:
                explained_count = len(analysis_report.thin_chapters) - len(analysis_report.unexplained_thin_chapters)
                explained_note = (
                    f" ({explained_count} more thin page(s) explained by front/back matter, not counted here)"
                    if explained_count else ""
                )
                structural_issues.append(
                    f"Thin/Empty Chapters: {len(analysis_report.unexplained_thin_chapters)}{explained_note}"
                )
            elif analysis_report.thin_chapters:
                structural_issues.append(
                    f"Thin/Empty Chapters: 0 unexplained "
                    f"({len(analysis_report.thin_chapters)} explained by front/back matter)"
                )

            heading_issue_count = sum(len(ch.heading_issues) for ch in analysis_report.chapters_with_heading_issues)
            if heading_issue_count:
                structural_issues.append(
                    f"Heading Hierarchy Issues: {heading_issue_count} across {len(analysis_report.chapters_with_heading_issues)} chapter(s)"
                )

            if structural_issues:
                self.log("\n[Structure]")
                for issue in structural_issues:
                    self.log(f"  • {issue}")
                
                if details:
                    if analysis_report.unexplained_thin_chapters:
                        self.log("    Thin chapters detail (unexplained):")
                        for ch in analysis_report.unexplained_thin_chapters:
                            self.log(f"      - {ch.href} ({ch.word_count} words)")
                    unexplained_hrefs = {ch.href for ch in analysis_report.unexplained_thin_chapters}
                    explained = [
                        ch for ch in analysis_report.thin_chapters
                        if ch.href not in unexplained_hrefs
                    ]
                    if explained:
                        self.log("    Thin chapters detail (explained by front/back matter):")
                        for ch in explained:
                            self.log(f"      - {ch.href} ({ch.word_count} words) - {ch.matter_label}")
                    if analysis_report.chapters_with_heading_issues:
                        self.log("    Heading issues detail:")
                        for ch in analysis_report.chapters_with_heading_issues:
                            for issue in ch.heading_issues:
                                self.log(f"      - {ch.href}: {issue}")

            # Table of Contents Issues
            toc = analysis_report.toc
            toc_issues = []
            if not toc.source:
                toc_issues.append("No table of contents (NCX or nav document) found in this book")
            if toc.broken_link_count:
                toc_issues.append(f"Broken TOC links: {toc.broken_link_count}")
            if toc.chapters_missing_from_toc:
                toc_issues.append(
                    f"Main-content chapters not referenced in TOC: {len(toc.chapters_missing_from_toc)}"
                )

            if toc_issues:
                self.log("\n[Table of Contents]")
                for issue in toc_issues:
                    self.log(f"  • {issue}")

                if details:
                    if toc.broken_links:
                        self.log("    Broken links detail:")
                        for link in toc.broken_links:
                            self.log(f"      - {link.label!r} -> {link.href} ({link.reason})")
                    if toc.chapters_missing_from_toc:
                        self.log("    Chapters missing from TOC:")
                        for href in toc.chapters_missing_from_toc:
                            self.log(f"      - {href}")

            # Project Gutenberg Boilerplate
            gb = analysis_report.gutenberg
            if gb.detected:
                self.log("\n[Project Gutenberg Boilerplate]")
                if gb.front_found:
                    self.log(f"  • Front disclaimer found in {gb.front.href} (via {gb.front.method})")
                else:
                    self.log("  • Front disclaimer: not found")
                if gb.back_found:
                    trailing_note = (
                        f", plus {len(gb.trailing_back_matter_hrefs)} trailing file(s)"
                        if gb.trailing_back_matter_hrefs else ""
                    )
                    self.log(f"  • Back license found in {gb.back.href} (via {gb.back.method}){trailing_note}")
                else:
                    self.log("  • Back license: not found")

                if details:
                    if gb.front_found and gb.front.marker_text:
                        self.log(f"    Front marker text: {gb.front.marker_text!r}")
                    if gb.back_found and gb.back.marker_text:
                        self.log(f"    Back marker text: {gb.back.marker_text!r}")
                    if gb.trailing_back_matter_hrefs:
                        self.log("    Trailing back-matter files:")
                        for href in gb.trailing_back_matter_hrefs:
                            self.log(f"      - {href}")

            # Cover Image
            cover = analysis_report.cover
            cover_issues = []
            if not cover.declared:
                cover_issues.append("No cover image declared (no <meta name=\"cover\"> and no properties=\"cover-image\")")
            if cover.meta_id_dangling:
                cover_issues.append(
                    f"<meta name=\"cover\"> points at id {cover.meta_content_id!r}, which isn't in the manifest"
                )
            if cover.declared and not cover.exists_in_archive:
                cover_issues.append(f"Declared cover file is missing from the EPUB: {cover.resolved_href}")
            if cover.declared and not cover.is_image_media_type:
                cover_issues.append(
                    f"Declared cover's media-type isn't an image: {cover.cover_item.media_type!r}"
                )
            if cover.mismatched_declarations:
                cover_issues.append(
                    f"<meta name=\"cover\"> and properties=\"cover-image\" disagree "
                    f"({cover.meta_item.href!r} vs {cover.properties_item.href!r})"
                )

            if cover_issues:
                self.log("\n[Cover Image]")
                for issue in cover_issues:
                    self.log(f"  • {issue}")

            # Span Soup
            span_soup = analysis_report.span_soup
            if span_soup.chain_count or span_soup.empty_span_count:
                self.log("\n[Span Soup]")
                if span_soup.chain_count:
                    self.log(
                        f"  • Nested span wrapper chains: {span_soup.chain_count} "
                        f"({span_soup.fully_purposeless_chain_count} fully purposeless, "
                        f"deepest {span_soup.max_depth} levels)"
                    )
                if span_soup.empty_span_count:
                    self.log(f"  • Empty spans with no content at all: {span_soup.empty_span_count}")
                if span_soup.no_op_classes:
                    sample = ", ".join(sorted(span_soup.no_op_classes)[:8])
                    more = len(span_soup.no_op_classes) - 8
                    suffix = f" (+{more} more)" if more > 0 else ""
                    self.log(f"  • CSS classes confirmed to have no visual effect: {sample}{suffix}")
                self.log(f"  • Chapters affected: {len(span_soup.chapters_affected)}")

                if details:
                    if span_soup.chains:
                        self.log("    Nested chain detail:")
                        for c in span_soup.chains:
                            class_path = " > ".join(
                                ".".join(lv.classes) if lv.classes else "(bare)" for lv in c.levels
                            )
                            flag = "purposeless" if c.fully_purposeless else f"{c.purposeless_level_count}/{c.depth} purposeless"
                            self.log(f"      - {c.href}: {class_path} ({flag}) -> {c.text[:50]!r}")
                    if span_soup.empty_spans:
                        self.log("    Empty span detail:")
                        for e in span_soup.empty_spans:
                            cls = e.element.get("class") if e.element is not None else None
                            self.log(f"      - {e.href}: <span class={cls!r}></span>")

            # Typography Issues
            typo_issues = []
            if t.quote_style_inconsistent:
                typo_issues.append("Inconsistent dialogue quote styles across chapters")
            if t.mixed_quote_chapters:
                typo_issues.append(f"Mixed quote styles within same file ({len(t.mixed_quote_chapters)} chapters)")
            if t.apostrophe_style_inconsistent:
                typo_issues.append("Inconsistent apostrophe styles across chapters")
            if t.mixed_apostrophe_chapters:
                typo_issues.append(f"Mixed apostrophe styles within same file ({len(t.mixed_apostrophe_chapters)} chapters)")
            if t.chapters_with_mojibake:
                typo_issues.append(f"Possible Encoding Corruption (mojibake): {t.total_mojibake} instance(s) in {len(t.chapters_with_mojibake)} chapter(s)")
            if t.chapters_with_bom:
                typo_issues.append(f"Stray BOM characters in {len(t.chapters_with_bom)} chapter(s)")
            if t.total_zero_width_space:
                typo_issues.append(f"Zero-width spaces: {t.total_zero_width_space}")
            if t.total_soft_hyphen:
                typo_issues.append(f"Soft hyphens: {t.total_soft_hyphen}")
            if t.total_control_chars:
                typo_issues.append(f"Stray control characters: {t.total_control_chars}")
            if t.chapters_with_all_caps_runs:
                typo_issues.append(f"ALL-CAPS text runs: {t.total_all_caps_runs} across {len(t.chapters_with_all_caps_runs)} chapter(s)")
            if t.chapters_with_repeated_punctuation:
                typo_issues.append(f"Repeated punctuation runs: {t.total_repeated_punctuation} across {len(t.chapters_with_repeated_punctuation)} chapter(s)")

            if typo_issues:
                self.log("\n[Typography]")
                for issue in typo_issues:
                    self.log(f"  • {issue}")

            # CSS Issues
            css_issues = []
            if css.unused_class_total:
                css_issues.append(f"Unused CSS classes declared: {css.unused_class_total}")
            if css.undeclared_class_total:
                css_issues.append(f"Undeclared classes used in HTML: {css.undeclared_class_total}")
            if css.duplicate_selectors_by_file:
                total_dupes = sum(len(v) for v in css.duplicate_selectors_by_file.values())
                css_issues.append(f"Duplicate selectors: {total_dupes} across {len(css.duplicate_selectors_by_file)} file(s)")
            if css.unbalanced_brace_files:
                css_issues.append(f"Stylesheets with unbalanced braces: {len(css.unbalanced_brace_files)}")
            if css.unreadable_files:
                css_issues.append(f"Unreadable stylesheets: {len(css.unreadable_files)}")
            if css.missing_embedded_fonts:
                css_issues.append(f"Missing font file references (@font-face): {len(css.missing_embedded_fonts)}")
            if css.page_break_rule_count:
                css_issues.append(f"Page-break rules declared: {css.page_break_rule_count}")
            if css.forced_height_count:
                css_issues.append(f"Forced height/max-height rules: {css.forced_height_count}")
            if css.calibre_class_count:
                css_issues.append(f"Leftover Calibre-conversion classes: {css.calibre_class_count}")
            if css.embedded_style_block_count:
                injected_note = (
                    f" ({css.injected_style_block_count} added by ebook_fix)"
                    if css.injected_style_block_count else ""
                )
                css_issues.append(f"Inline <style> blocks in chapter HTML: {css.embedded_style_block_count}{injected_note}")
            if css.embedded_page_break_rule_count:
                css_issues.append(f"Page-break rules in embedded <style> blocks: {css.embedded_page_break_rule_count}")
            if css.embedded_forced_height_count:
                css_issues.append(f"Forced height/max-height in embedded <style> blocks: {css.embedded_forced_height_count}")
            if css.inline_style_attr_page_break_count:
                css_issues.append(f"Page-break declarations on style= attributes: {css.inline_style_attr_page_break_count}")
            if css.inline_style_attr_forced_height_count:
                css_issues.append(f"Forced height/max-height on style= attributes: {css.inline_style_attr_forced_height_count}")

            if css_issues:
                self.log("\n[CSS]")
                for issue in css_issues:
                    self.log(f"  • {issue}")
                
                if details:
                    if css.unused_classes:
                        self.log(f"    Unused classes: {', '.join(css.unused_classes)}")
                    if css.undeclared_classes:
                        shown = ", ".join(f"{cls} ({cnt}x)" for cls, cnt in css.undeclared_classes)
                        self.log(f"    Undeclared classes: {shown}")
                    if css.calibre_classes:
                        self.log(f"    Calibre classes: {', '.join(css.calibre_classes)}")
                    if css.chapters_with_page_break_styling:
                        self.log(f"    Chapters with page-break styling: {', '.join(css.chapters_with_page_break_styling)}")

            # Paragraph Issues
            para = analysis_report.paragraphs
            paragraph_issues = []
            if para.junk_element_count:
                paragraph_issues.append(f"Watermark / junk paragraphs: {para.junk_element_count}")
            if para.empty_paragraph_count:
                paragraph_issues.append(f"Empty paragraphs: {para.empty_paragraph_count}")
            if para.mid_sentence_split_count:
                paragraph_issues.append(f"Mid-sentence paragraph splits: {para.mid_sentence_split_count}")

            if paragraph_issues:
                self.log("\n[Paragraphs]")
                for issue in paragraph_issues:
                    self.log(f"  • {issue}")

                if details:
                    for chapter_summary in para.chapters:
                        if not (chapter_summary.junk_elements or chapter_summary.empty_paragraphs or chapter_summary.mid_sentence_splits):
                            continue
                        self.log(f"    {chapter_summary.href}:")
                        for el in chapter_summary.junk_elements:
                            self.log(f"      - junk: {el.preview!r}")
                        for split in chapter_summary.mid_sentence_splits:
                            self.log(f"      - split: ...{split.first_preview!r} | {split.second_preview!r}...")

            # Image Issues
            img = analysis_report.images
            image_issues = []
            if img.broken_image_count:
                image_issues.append(
                    f"Broken image references: {img.broken_image_count} across "
                    f"{len(img.chapters_with_broken_images)} chapter(s)"
                )
            if img.missing_manifest_image_count:
                image_issues.append(f"Manifest entries pointing at missing images: {img.missing_manifest_image_count}")

            if image_issues:
                self.log("\n[Images]")
                for issue in image_issues:
                    self.log(f"  • {issue}")

                if details:
                    if img.broken_image_refs:
                        self.log("    Broken image references:")
                        for ref in img.broken_image_refs:
                            self.log(f"      - {ref.href}: {ref.src}")
                    if img.missing_manifest_images:
                        self.log("    Missing manifest images:")
                        for entry in img.missing_manifest_images:
                            self.log(f"      - {entry.href}")

            # Whitespace Issues
            ws = analysis_report.whitespace
            whitespace_issues = []
            if ws.leading_indent_count:
                whitespace_issues.append(f"Leading indentation removed: {ws.leading_indent_count}")
            if ws.trailing_indent_count:
                whitespace_issues.append(f"Trailing indentation removed: {ws.trailing_indent_count}")
            if ws.repeated_whitespace_count:
                whitespace_issues.append(f"Repeated whitespace collapsed: {ws.repeated_whitespace_count}")
            if ws.tabs_converted_count:
                whitespace_issues.append(f"Tabs converted to spaces: {ws.tabs_converted_count}")
            if ws.space_before_punct_count:
                whitespace_issues.append(f"Space before punctuation removed: {ws.space_before_punct_count}")
            if ws.missing_sentence_space_count:
                whitespace_issues.append(f"Missing space after punctuation added: {ws.missing_sentence_space_count}")
            if ws.whitespace_only_node_count:
                whitespace_issues.append(f"Whitespace-only text nodes: {ws.whitespace_only_node_count}")
            if ws.protected_nodes_skipped_count:
                whitespace_issues.append(f"Protected nodes skipped (pre/code/script/style/svg/math): {ws.protected_nodes_skipped_count}")

            if whitespace_issues:
                self.log("\n[Whitespace]")
                for issue in whitespace_issues:
                    self.log(f"  • {issue}")

                if details:
                    for chapter_summary in ws.chapters:
                        if not chapter_summary.issues:
                            continue
                        self.log(f"    {chapter_summary.href}:")
                        for issue in chapter_summary.issues:
                            self.log(f"      - {issue.category}: {issue.before!r} -> {issue.after!r}")

            # Ellipsis Issues
            ell = analysis_report.ellipsis
            ellipsis_issues = []
            if ell.total_ascii_count:
                ellipsis_issues.append(f"ASCII ellipsis (...) found: {ell.total_ascii_count}")
            if ell.total_spaced_count:
                ellipsis_issues.append(f"Spaced-dot ellipsis found: {ell.total_spaced_count}")

            if ellipsis_issues:
                self.log("\n[Ellipsis]")
                for issue in ellipsis_issues:
                    self.log(f"  • {issue}")

                if details:
                    # Shown using the config's actual target style
                    # rather than the analysis-default "unicode" --
                    # see ebook_fix.modules.ellipsis_repair.
                    target_style = getattr(
                        getattr(self.config, "ellipsis_repair", None), "target_style", "unicode"
                    )
                    for chapter_summary in ell.chapters:
                        if not chapter_summary.issues:
                            continue
                        self.log(f"    {chapter_summary.href}:")
                        for issue in chapter_summary.issues:
                            result = normalize_ellipsis_text(issue.before, target_style=target_style)
                            self.log(f"      - {issue.category}: {issue.before!r} -> {result.text!r}")

            # Apostrophe Issues
            apo = analysis_report.apostrophes
            if apo.total_match_count:
                self.log("\n[Apostrophes]")
                self.log(f"  • Missing apostrophe (contraction) found: {apo.total_match_count}")

                if details:
                    # Shown using the config's actual resolved target
                    # character rather than the analysis-default
                    # straight apostrophe -- see
                    # ebook_fix.modules.apostrophe_repair.
                    target_char = resolve_target_apostrophe_char(
                        getattr(self.config, "apostrophe_repair", None), analysis_report
                    )
                    for chapter_summary in apo.chapters:
                        if not chapter_summary.issues:
                            continue
                        self.log(f"    {chapter_summary.href}:")
                        for issue in chapter_summary.issues:
                            result = normalize_apostrophes_text(issue.before, apostrophe_char=target_char)
                            self.log(f"      - {issue.category}: {issue.before!r} -> {result.text!r}")

            # Possessive Candidates -- FLAG ONLY, never auto-repaired.
            # See ebook_fix.apostrophes module docstring for why: a
            # bare "word s" split could mean a possessive (add an
            # apostrophe) or a plain plural that lost its space (just
            # join them), and only a person reading the sentence can
            # tell which. This section exists purely so a person can
            # review and fix these by hand -- no config option turns
            # this into an auto-repair, on purpose.
            poss = analysis_report.possessives
            if poss.total_candidate_count:
                self.log("\n[Possessive Candidates -- Manual Review]")
                self.log(
                    f"  • Possible missing apostrophe (possessive or plural, ambiguous): "
                    f"{poss.total_candidate_count} -- not auto-repaired, review and fix by hand"
                )

                if details:
                    for chapter_summary in poss.chapters:
                        if not chapter_summary.candidates:
                            continue
                        self.log(f"    {chapter_summary.href}:")
                        for c in chapter_summary.candidates:
                            self.log(
                                f"      - {c.context!r} "
                                f"(possessive: {c.possessive_reading!r} / plural: {c.plural_reading!r})"
                            )

            # Module Diagnostics Execution
            self.log("\n[Module Checks]")
            if not self.modules:
                self.log("  No repair modules enabled.")
            else:
                total_module_issues = 0
                for module in self.modules:
                    report = module.analyze(book, analysis_report)
                    total_module_issues += report.count
                    self.log(f"  • {module.name}: {report.count} issue(s) found")
                    #if details and report.count > 0:
                        #report.print(details=True)

            self.log("")

        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def map_css(self, epub, write_mapping=None):
        source, temp_path = self._resolve_source(epub)
        if source is None:
            return
        try:
            self.log("Opening EPUB...")
            parser = EPUBParser()
            book = parser.load(source)
            self.log("")

            self.header("[CSS Class Map]")
            self.log(
                "Best-guess role per class, for review before renaming --\n"
                "not something to apply unattended, especially on \"low\" confidence guesses.\n"
            )
            profiles = build_class_profiles(book)
            if not profiles:
                self.log("No classed elements found in this book.")
                return
            self.log(format_class_map(profiles))
            self.log("")

            if write_mapping:
                write_mapping_file(profiles, write_mapping)
                self.log(f"Wrote editable mapping to: {write_mapping}")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def map_structure(self, epub):
        """Phase 0h of the XHTML Recoder plan (see
        docs/xhtml_recoder_plan.md): a dry-run view of the detected
        chapter structure and each boundary's split-confidence, for
        review ahead of any future splitting -- nothing here writes or
        modifies the book. Same manual-review posture as map_css
        above.
        """
        source, temp_path = self._resolve_source(epub)
        if source is None:
            return
        try:
            self.log("Opening EPUB...")
            parser = EPUBParser()
            book = parser.load(source)
            self.log("")

            self.header("[Chapter Structure]")
            self.log(
                "Detected chapter boundaries and how confident the analysis is in each --\n"
                "for review, not something the eventual splitter should trust unattended\n"
                "below \"corroborated\".\n"
            )
            tree = analyze_structure(book)
            self.log(format_structure_report(tree))
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def split_chapters(self, epub, output):
        """Phase 1 of the XHTML Recoder plan (see
        docs/xhtml_recoder_plan.md): a hands-on way to try the
        splitter mechanics (ebook_fix.splitter) against a real book.

        NOT gated by the full split-safety-bar corroboration
        requirement yet (see docs/split_safety_bar.md) -- proper
        gating is Phase 5's job, once cross-reference rewriting and
        NCX handling exist too. This only requires a boundary to be at
        least SEQUENCE_ONLY confidence, so it can actually be tried
        against today's sample library even though none of them
        currently have a CORROBORATED boundary (see Phase 0e/0f).

        Treat any output from this command as a mechanics test, not a
        finished conversion -- a footnote or TOC entry pointing into a
        file that gets split here won't have been updated to match.
        """
        source, temp_path = self._resolve_source(epub)
        if source is None:
            return
        try:
            self.log("Opening EPUB...")
            parser = EPUBParser()
            book = parser.load(source)
            self.log("")

            self.header("[Chapter Splitting -- proof of concept]")
            self.log(
                "Physically splits any file that has 2+ chapter boundaries at\n"
                "sequence-only confidence or better. This tests the splitting\n"
                "mechanics themselves, not a finished conversion -- see\n"
                "docs/xhtml_recoder_plan.md for what Phases 2-5 still need to\n"
                "add (cross-reference rewriting, TOC regeneration, and the\n"
                "review gate that will eventually require a corroborated\n"
                "boundary before splitting anything).\n"
            )

            tree = analyze_structure(book)
            eligible = [
                node
                for node in iter_chapter_nodes(tree)
                if node.evidence is not None and node.evidence.confidence != SplitConfidence.NONE
            ]
            by_href = {}
            for node in eligible:
                by_href.setdefault(node.start_href, []).append(node)

            split_count = 0
            for href, nodes in by_href.items():
                if len(nodes) < 2:
                    continue
                chapter = next((c for c in book.chapters if c.href == href), None)
                if chapter is None:
                    continue
                markers = [
                    SplitMarker(
                        element=node.evidence.candidate.element,
                        title=node.title,
                        number=node.evidence.candidate.number,
                    )
                    for node in nodes
                ]
                try:
                    result = apply_split(book, chapter, markers)
                except SplitError as exc:
                    self.log(f"Skipped {href}: {exc}")
                    continue
                split_count += 1
                all_hrefs = [href] + result.new_hrefs
                self.log(
                    f"Split {href}: {len(markers)} chapters -> "
                    f"{len(all_hrefs)} files "
                    f"({result.original_word_count} words, integrity check "
                    f"{'passed' if result.word_counts_match else 'FAILED'})"
                )

            if split_count == 0:
                self.log("No file in this book has 2+ chapter boundaries to split.")
                return

            writer = EPUBWriter()
            writer.save(book, output)
            self.log(f"\nSaved: {output}")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def repair(self, epub, output, dry_run=False, class_mapping=None):
        source, temp_path = self._resolve_source(epub)
        if source is None:
            return
        try:
            self.log("Opening EPUB...")
            parser = EPUBParser()
            book = parser.load(source)
            self.log("")

            self.log("Analyzing book structure...")
            analyzer = EPUBAnalyzer()
            analysis_report = analyzer.analyze(book)
            cache_file = save_report(analysis_report, epub)
            self.log(f"Saved analysis cache: {cache_file}")
            self.log("")

            modules = list(self.modules)
            if class_mapping:
                mapping_path = Path(class_mapping)
                if not mapping_path.exists():
                    self.log(f"'{class_mapping}' doesn't exist yet -- generating it from this book's CSS...")
                    profiles = build_class_profiles(book)
                    write_mapping_file(profiles, mapping_path)
                    self.log(f"Wrote editable mapping to: {mapping_path}")
                    self.log(
                        "\nReview it before applying it -- especially anything marked \"low\" "
                        "confidence, or any class the guess looks wrong for. Once it looks "
                        "right, re-run this same repair command to apply it."
                    )
                    return

                try:
                    entries = load_mapping_file(class_mapping)
                except MappingError as exc:
                    self.log(f"ERROR: {exc}")
                    return
                if not entries:
                    self.log(f"Note: '{class_mapping}' has no class entries -- nothing to standardize.")
                else:
                    modules.append(ClassStandardizeRepair(entries))

            if not modules:
                self.log("No repair modules are enabled in the config. Nothing to do.")
                return
            for module in modules:
                self.log(f"Repairing: {module.name}")
                module.repair(book, analysis_report)

            # The cache existed to hand this run's findings to the repair
            # modules above -- once they've all had their turn, it's just
            # a leftover file sitting next to the book, so clean it up.
            cache_file.unlink(missing_ok=True)

            if dry_run:
                self.log("\nDry run complete.")
                return
            writer = EPUBWriter()
            writer.save(book, output)
            self.log(f"\nSaved: {output}")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)