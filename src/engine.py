from ebook_fix.parser import EPUBParser
from ebook_fix.writer import EPUBWriter
from ebook_fix.modules.paragraph import ParagraphRepair
from ebook_fix.modules.images import ImageRepair

class Engine:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.modules = [
            ParagraphRepair(),
            ImageRepair(),
        ]

    def log(self, message):
        print(message)

    def analyze(self, epub):
        self.log("Opening EPUB...")
        parser = EPUBParser()
        book = parser.load(epub)
        self.log("")
        self.log("Running analysis...\n")
        for module in self.modules:
            self.log(f"[{module.name}]")
            report = module.analyze(book)
            report.print()
            self.log("")
        self.log("Finished.")

    def repair(self, epub, output, dry_run=False):
        self.log("Opening EPUB...")
        parser = EPUBParser()
        book = parser.load(epub)
        self.log("")
        for module in self.modules:
            self.log(f"Repairing: {module.name}")
            module.repair(book)
        if dry_run:
            self.log("\nDry run complete.")
            return
        writer = EPUBWriter()
        writer.save(book, output)
        self.log(f"\nSaved: {output}")
