from ebook_fix.parser import EPUBParser
from ebook_fix.writer import EPUBWriter
from ebook_fix.config import Config
from ebook_fix.validation import validate_epub
from ebook_fix.modules.paragraph import ParagraphRepair
from ebook_fix.modules.images import ImageRepair

class Engine:
    def __init__(self, verbose=False, config=None):
        self.verbose = verbose
        self.config = config or Config()
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

    def _check_integrity(self, epub) -> bool:
        """Run pre-flight integrity checks. Returns True if the file
        is safe to parse, False (after printing the failure) if not."""
        result = validate_epub(epub)
        result.print()
        if not result.valid:
            self.log("\nAborting: file failed the integrity check above.")
            return False
        self.log("")
        return True

    def analyze(self, epub, details=False):
        if not self._check_integrity(epub):
            return
        self.log("Opening EPUB...")
        parser = EPUBParser()
        book = parser.load(epub)
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

    def repair(self, epub, output, dry_run=False):
        if not self._check_integrity(epub):
            return
        self.log("Opening EPUB...")
        parser = EPUBParser()
        book = parser.load(epub)
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
