import tempfile
from pathlib import Path

from ebook_fix.parser import EPUBParser
from ebook_fix.writer import EPUBWriter
from ebook_fix.config import Config
from ebook_fix.validation import validate_epub
from ebook_fix.container_repair import attempt_repair
from ebook_fix.modules.paragraph import ParagraphRepair
from ebook_fix.modules.images import ImageRepair

class Engine:
    def __init__(self, verbose=False, config=None, auto_repair_container=True):
        self.verbose = verbose
        self.config = config or Config()
        self.auto_repair_container = auto_repair_container
        self.modules = self._build_modules()

    def _build_modules(self):
        modules = []
        if self.config.paragraph_repair.enabled:
            modules.append(ParagraphRepair(self.config.paragraph_repair))
        if self.config.image_repair.enabled:
            modules.append(ImageRepair(self.config.image_repair))
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
            if not self.modules:
                self.log("No repair modules are enabled in the config. Nothing to do.")
                return
            self.log("Running analysis...\n")
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
