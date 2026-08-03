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