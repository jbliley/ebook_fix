import tempfile
from pathlib import Path

from ebook_fix.parser import EPUBParser
from ebook_fix.writer import EPUBWriter
from ebook_fix.config import Config
from ebook_fix.validation import validate_epub
from ebook_fix.container_repair import attempt_repair
from ebook_fix.analyzer import EPUBAnalyzer
from ebook_fix.modules.paragraph import ParagraphRepair
from ebook_fix.modules.images import ImageRepair
from ebook_fix.modules.whitespace import WhitespaceRepair

class Engine:
    def __init__(self, verbose=False, config=None, auto_repair_container=True):
        self.verbose = verbose
        self.config = config or Config()
        self.auto_repair_container = auto_repair_container
        self.modules = self._build_modules()

    def _build_modules(self):
        modules = []
        if getattr(self.config, "paragraph_repair", None) and getattr(self.config.paragraph_repair, "enabled", True):
            modules.append(ParagraphRepair(self.config.paragraph_repair))
        if getattr(self.config, "image_repair", None) and getattr(self.config.image_repair, "enabled", True):
            modules.append(ImageRepair(self.config.image_repair))
        if not hasattr(self.config, "whitespace_repair") or getattr(self.config.whitespace_repair, "enabled", True):
            modules.append(WhitespaceRepair())
        return modules

    def log(self, message):
        print(message)

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

            s = analysis_report.summary
            self.log(
                f"\n--- Book Info ---"
                f"\nTitle: {s.title or '(none found)'}"
                f"\nAuthor: {s.author or '(none found)'}"
                f"\nLanguage: {s.language or '(none found)'}"
                f"\nPublisher: {s.publisher or '(none found)'}"
                f"\nIdentifier: {s.identifier or '(none found)'}"
                f"\nDate: {s.date or '(none found)'}"
                f"\nRights: {s.rights or '(none found)'}"
                f"\nEPUB Version: {s.epub_version or 'unknown'}"
            )
            if s.subjects:
                self.log(f"Subjects/Genre: {', '.join(s.subjects)}")
            if s.description:
                self.log(f"Description: {s.description}")

            self.log(
                f"\n--- File Contents ---"
                f"\nHTML/XHTML pages: {s.html_page_count}"
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

            self.log(
                f"\n--- Book Overview ---"
                f"\nChapters: {len(analysis_report.chapter_reports)}"
                f"\nTotal Paragraphs: {analysis_report.total_paragraphs}"
                f"\nTotal Images: {analysis_report.total_images}"
                f"\nTotal Links: {analysis_report.total_links}"
            )

            if analysis_report.thin_chapters:
                self.log(
                    f"\n⚠ {len(analysis_report.thin_chapters)} chapter(s) look thin/empty "
                    "(0 paragraphs or under 50 words). This can be normal for a "
                    "title/copyright page, or a sign of a bad chapter split. "
                    "Run with --details to see which."
                )
                if not details:
                    for ch in analysis_report.thin_chapters:
                        title = ch.title or "Untitled Chapter"
                        self.log(f"  • {ch.href} - {title} ({ch.word_count} words)")

            if analysis_report.chapters_with_heading_issues:
                total_issues = sum(len(ch.heading_issues) for ch in analysis_report.chapters_with_heading_issues)
                self.log(
                    f"\n⚠ {total_issues} heading hierarchy issue(s) found across "
                    f"{len(analysis_report.chapters_with_heading_issues)} chapter(s) "
                    "(skipped levels, e.g. h1 -> h3, or multiple h1s in one chapter). "
                    "Run with --details to see which."
                )
                if not details:
                    for ch in analysis_report.chapters_with_heading_issues:
                        title = ch.title or "Untitled Chapter"
                        self.log(f"  • {ch.href} - {title} ({len(ch.heading_issues)} issue(s))")

            t = analysis_report.typography
            self.log(
                f"\n--- Typography ---"
                f"\nQuotes: {t.total_straight_double_quotes} straight double, "
                f"{t.total_curly_double_quotes} curly double, "
                f"{t.total_straight_apostrophes} straight apostrophe, "
                f"{t.total_curly_apostrophes} curly apostrophe"
                f"\nDashes: {t.total_hyphen} hyphen, {t.total_en_dash} en dash, "
                f"{t.total_em_dash} em dash, {t.total_double_hyphen} double-hyphen (--)"
                f"\nEllipsis: {t.total_unicode_ellipsis} unicode (…), {t.total_ascii_ellipsis} ascii (...)"
                f"\nSentence spacing: {t.total_single_space_after_sentence} single-space, "
                f"{t.total_double_space_after_sentence} double-space"
            )

            if t.quote_style_inconsistent:
                self.log(
                    f"\n⚠ Dialogue quote style is inconsistent across the book: "
                    f"{len(t.straight_quote_chapters)} chapter(s) use straight quotes, "
                    f"{len(t.curly_quote_chapters)} chapter(s) use curly quotes. "
                    "This is a common sign of content merged from different sources."
                )
            if t.mixed_quote_chapters:
                self.log(
                    f"⚠ {len(t.mixed_quote_chapters)} chapter(s) mix straight and curly "
                    "dialogue quotes within the same file. Run with --details to see which."
                )
            if t.apostrophe_style_inconsistent:
                self.log(
                    f"\nNote: apostrophe style differs across the book "
                    f"({len(t.straight_apostrophe_chapters)} chapter(s) straight, "
                    f"{len(t.curly_apostrophe_chapters)} chapter(s) curly). "
                    "This is often intentional (many conversions only curl quotation "
                    "marks, not contractions) but worth a look if unexpected."
                )
            if t.mixed_apostrophe_chapters:
                self.log(
                    f"⚠ {len(t.mixed_apostrophe_chapters)} chapter(s) mix straight and curly "
                    "apostrophes within the same file. Run with --details to see which."
                )

            if t.chapters_with_mojibake:
                self.log(
                    f"\n⚠ Possible encoding corruption (mojibake) found in "
                    f"{len(t.chapters_with_mojibake)} chapter(s), {t.total_mojibake} instance(s) total. "
                    "Run with --details to see samples."
                )
            if t.chapters_with_bom:
                self.log(f"⚠ {len(t.chapters_with_bom)} chapter(s) contain a stray BOM character.")
            if t.total_zero_width_space:
                self.log(f"⚠ {t.total_zero_width_space} zero-width space character(s) found across the book.")
            if t.total_soft_hyphen:
                self.log(f"⚠ {t.total_soft_hyphen} soft hyphen character(s) found across the book.")
            if t.total_control_chars:
                self.log(f"⚠ {t.total_control_chars} stray control character(s) found across the book.")

            if t.chapters_with_all_caps_runs:
                self.log(
                    f"\n{len(t.chapters_with_all_caps_runs)} chapter(s) contain runs of ALL-CAPS text "
                    f"({t.total_all_caps_runs} total) -- could be intentional emphasis or an OCR artifact. "
                    "Run with --details to see samples."
                )
            if t.chapters_with_repeated_punctuation:
                self.log(
                    f"{len(t.chapters_with_repeated_punctuation)} chapter(s) contain repeated punctuation "
                    f"(e.g. '!!', '....') -- {t.total_repeated_punctuation} instance(s) total. "
                    "Run with --details to see samples."
                )

            css = analysis_report.css
            self.log(
                f"\n--- CSS ---"
                f"\nStylesheets: {css.css_file_count} | Rules: {css.total_rules} | "
                f"!important uses: {css.total_important}"
                f"\nDeclared classes: {css.declared_class_count} | Declared ids: {css.declared_id_count}"
                f"\nInline style attributes used in HTML: {css.inline_style_element_count}"
            )

            if css.unused_class_total:
                self.log(
                    f"\n{css.unused_class_total} CSS class(es) are declared but never used in any chapter. "
                    "This is common in template/converter-generated stylesheets and isn't "
                    "necessarily a problem. Run with --details to see samples."
                )
            if css.undeclared_class_total:
                self.log(
                    f"⚠ {css.undeclared_class_total} class(es) are used in chapter HTML but never "
                    "declared in any stylesheet, so they have no styling. Run with --details to see which."
                )
            if css.duplicate_selectors_by_file:
                total_dupes = sum(len(v) for v in css.duplicate_selectors_by_file.values())
                self.log(
                    f"⚠ {total_dupes} duplicate selector(s) found across "
                    f"{len(css.duplicate_selectors_by_file)} stylesheet(s) (same selector declared "
                    "more than once -- later rules silently override earlier ones). "
                    "Run with --details to see which."
                )
            if css.unbalanced_brace_files:
                self.log(f"⚠ {len(css.unbalanced_brace_files)} stylesheet(s) have unbalanced {{ }} braces: {css.unbalanced_brace_files}")
            if css.unreadable_files:
                self.log(f"⚠ {len(css.unreadable_files)} stylesheet(s) listed in the manifest couldn't be read: {css.unreadable_files}")
            if css.missing_embedded_fonts:
                self.log(
                    f"⚠ {len(css.missing_embedded_fonts)} @font-face reference(s) point to a font file "
                    f"that isn't actually in the EPUB: {css.missing_embedded_fonts}"
                )

            if details:
                if css.unused_classes:
                    more = f" (+{css.unused_class_total - len(css.unused_classes)} more)" if css.unused_class_total > len(css.unused_classes) else ""
                    self.log(f"\n  Unused CSS classes: {', '.join(css.unused_classes)}{more}")
                if css.undeclared_classes:
                    more = f" (+{css.undeclared_class_total - len(css.undeclared_classes)} more)" if css.undeclared_class_total > len(css.undeclared_classes) else ""
                    shown = ", ".join(f"{cls} ({cnt}x)" for cls, cnt in css.undeclared_classes)
                    self.log(f"  Undeclared classes used in HTML: {shown}{more}")
                for href, dupes in css.duplicate_selectors_by_file.items():
                    shown = ", ".join(f"{sel!r} ({cnt}x)" for sel, cnt in dupes)
                    self.log(f"  Duplicate selectors in {href}: {shown}")

            # Render detailed breakdown if details=True is requested
            if details:
                self.log("\n--- Detailed Chapter Analysis ---")
                for ch in analysis_report.chapter_reports:
                    title = ch.title or "Untitled Chapter"
                    thin_marker = "  [THIN/EMPTY]" if ch.is_thin else ""
                    self.log(f"\n[Chapter: {ch.href} - {title}]{thin_marker}")
                    self.log(f"  • Paragraphs: {ch.paragraphs} | Images: {ch.images} | Links: {ch.links} | Words: {ch.word_count}")
                    self.log(f"  • Tables: {ch.tables} | Lists: {ch.lists}")
                    
                    if ch.headings:
                        headings_str = ", ".join(f"{h.upper()}: {cnt}" for h, cnt in ch.headings.items())
                        self.log(f"  • Headings: {headings_str}")

                    if ch.heading_issues:
                        for issue in ch.heading_issues:
                            self.log(f"  ⚠ {issue}")
                    
                    if ch.css_classes:
                        # Top 5 most used CSS classes in this chapter
                        top_classes = ", ".join(f".{cls} ({cnt})" for cls, cnt in ch.css_classes.most_common(5))
                        self.log(f"  • Top Classes: {top_classes}")

                    typ = ch.typography
                    self.log(
                        f"  • Quote style: {typ.quote_style} "
                        f"({typ.straight_double_quotes} straight-\", {typ.curly_double_quotes} curly-\")"
                        f" | Apostrophe style: {typ.apostrophe_style} "
                        f"({typ.straight_apostrophes} straight-', {typ.curly_apostrophes} curly-')"
                    )
                    if typ.sentence_count:
                        self.log(
                            f"  • Sentences: {typ.sentence_count} "
                            f"(avg {typ.avg_sentence_words} words, "
                            f"range {typ.shortest_sentence_words}-{typ.longest_sentence_words})"
                        )
                    if typ.mojibake_count:
                        self.log(f"  ⚠ Possible mojibake ({typ.mojibake_count}): {typ.mojibake_samples}")
                    if typ.bom_found:
                        self.log(f"  ⚠ BOM character found in chapter text")
                    if typ.zero_width_space_count:
                        self.log(f"  ⚠ {typ.zero_width_space_count} zero-width space character(s)")
                    if typ.soft_hyphen_count:
                        self.log(f"  ⚠ {typ.soft_hyphen_count} soft hyphen character(s)")
                    if typ.control_char_count:
                        self.log(f"  ⚠ {typ.control_char_count} stray control character(s)")
                    if typ.all_caps_run_count:
                        self.log(f"  • ALL-CAPS runs ({typ.all_caps_run_count}): {typ.all_caps_samples}")
                    if typ.repeated_punctuation_count:
                        self.log(f"  • Repeated punctuation ({typ.repeated_punctuation_count}): {typ.repeated_punctuation_samples}")
                    if typ.double_hyphen_count:
                        self.log(f"  • Double-hyphen (--) possibly standing in for em dash: {typ.double_hyphen_count}")
                    if typ.double_space_after_sentence:
                        self.log(f"  • Double-space after sentence: {typ.double_space_after_sentence}")

            self.log("")

            if not self.modules:
                self.log("No repair modules are enabled in the config. Nothing to do.")
                return

            self.log("Running module analysis...\n")
            total_issues = 0
            for module in self.modules:
                self.log(f"[{module.name}]")
                report = module.analyze(book)
                report.print(details=details)
                total_issues += report.count
                self.log("")
            self.log(f"Finished. {total_issues} issue(s) found total.")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def repair(self, epub, output, dry_run=False):
        source, temp_path = self._resolve_source(epub)
        if source is None:
            return
        try:
            self.log("Opening EPUB...")
            parser = EPUBParser()
            book = parser.load(source)
            self.log("")
            if not self.modules:
                self.log("No repair modules are enabled in the config. Nothing to do.")
                return
            for module in self.modules:
                self.log(f"Repairing: {module.name}")
                module.repair(book)
            if dry_run:
                self.log("\nDry run complete.")
                return
            writer = EPUBWriter()
            writer.save(book, output)
            self.log(f"\nSaved: {output}")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)