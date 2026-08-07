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
from ebook_fix.modules.epub3_upgrade import EPUB3UpgradeRepair
from ebook_fix.modules.paragraph import ParagraphRepair
from ebook_fix.modules.chapter_markup import ChapterMarkupRepair
from ebook_fix.modules.images import ImageRepair
from ebook_fix.modules.whitespace import WhitespaceRepair
from ebook_fix.modules.class_standardize import ClassStandardizeRepair, load_mapping_file, MappingError

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
        if getattr(self.config, "paragraph_repair", None) and getattr(self.config.paragraph_repair, "enabled", True):
            modules.append(ParagraphRepair(self.config.paragraph_repair))
        if getattr(self.config, "chapter_markup", None) and getattr(self.config.chapter_markup, "enabled", True):
            modules.append(ChapterMarkupRepair(self.config.chapter_markup))
        if getattr(self.config, "image_repair", None) and getattr(self.config.image_repair, "enabled", True):
            modules.append(ImageRepair(self.config.image_repair))
        if not hasattr(self.config, "whitespace_repair") or getattr(self.config.whitespace_repair, "enabled", True):
            modules.append(WhitespaceRepair())
        return modules

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
                f"\nCSS files: {s.css_file_count}"
                f"\nImage files: {s.image_file_count}"
                f"\nFont files: {s.font_file_count}"
                f"\nAudio files: {s.audio_file_count}"
                f"\nVideo files: {s.video_file_count}"
                f"\nOther files: {s.other_file_count}"
                f"\nTotal word count: {s.total_word_count:,}"
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

            self.log("")
            self.header("Typography Overview")
            self.log(
                f"Quotes: {t.total_straight_double_quotes} straight double, {t.total_curly_double_quotes} curly double"
                f"\nApostrophes: {t.total_straight_apostrophes} straight apostrophe, {t.total_curly_apostrophes} curly apostrophe"
                f"\nDashes: {t.total_hyphen} hyphen, {t.total_en_dash} en dash, {t.total_em_dash} em dash, {t.total_double_hyphen} double-hyphen (--)"
                f"\nEllipsis: {t.total_unicode_ellipsis} unicode (…), {t.total_ascii_ellipsis} ascii (...)"
                f"\nSentence spacing: {t.total_single_space_after_sentence} single-space, {t.total_double_space_after_sentence} double-space"
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
                    self.log(f"\n[Chapter: {ch.href} - {title}]{thin_marker}")
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
            if analysis_report.thin_chapters:
                structural_issues.append(f"Thin/Empty Chapters: {len(analysis_report.thin_chapters)}")
            
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
                    if analysis_report.thin_chapters:
                        self.log("    Thin chapters detail:")
                        for ch in analysis_report.thin_chapters:
                            self.log(f"      - {ch.href} ({ch.word_count} words)")
                    if analysis_report.chapters_with_heading_issues:
                        self.log("    Heading issues detail:")
                        for ch in analysis_report.chapters_with_heading_issues:
                            for issue in ch.heading_issues:
                                self.log(f"      - {ch.href}: {issue}")

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