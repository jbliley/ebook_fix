# ebook_fix
An automated tool that will be able to detect and fix both common and uncommon issues with the structure and text of eBook files.

The ultimate goal of this project is to allow any type of eBook file to be analyzed and fixed without the need of running it through AI, as they are often too large of a job for free usage. This idea is stemming from the many free eBook files online that are poorly converted from early PDF files, or those that were printed directly from HTML files from 20+ years ago. As this project is in its infancy, it can only analyze and repair .epub files so far.

<h3>Planned Software Capabilities</h3>
<ul>
<li>Analyze complete text and structure of eBook file</li>
<li>Display analysis and detected issues in a detailed uniform summary</li>
<li>Fix detected issues based on the analysis</li>
<li>Base repairs to be done on config file (all on by default)</li>
<li>Analyze/repair different types of eBooks besides EPUB</li>
<li>Offer both GUI and CLI interfaces with bulk capabilities</li>
</ul>

<h3>Analysis</h3>
Before the repair runs, it runs an analysis to map the structure and elements of the book, including:
<ul>
<li>Images</li>
<li>Metadata/Version</li>
<li>HTML Pages</li>
<li>CSS Styles</li>
<li>Chapter/Paragraph/Word Counts</li>
<li>Common Text Issues</li>
<li>Hyperlinks</li>
<li>Table of Contents</li>
<li>Typography</li>
<li>Whitespace</li>
<li>Ellipsis</li>
<li>Apostrophes</li>
</ul>
<br>This analysis runs automatically before running a repair. To run only the analysis without the repair (from the src folder):
<p>&emsp;<b>Run analysis: </b><code>python cli.py analyze "C:/path/to/file/ebook title.epub"</code></p>

<h3>Repair</h3>
After the analysis, the repair will automatically fix issues it finds. These fixes can be changed in the config file (see below). Besides the options in the config file, all repairs are done automatically without input. Nothing repaired should break the book, but if you find something does break it please pop it onto the Issues page.
<p>&emsp;<b>Run repair: </b><code>python cli.py repair "C:/path/to/file/ebook title.epub"</code></p>
Once the repair finishes, a Repair Report is printed listing only the fixes that were actually applied, grouped by module with a count for each issue type. Pass <code>--details</code> to see the full before/after list for every single change instead of just the counts.
<p>&emsp;<b>Run repair with full detail: </b><code>python cli.py repair "C:/path/to/file/ebook title.epub" --details</code></p>

<h3>Auto-Fix</h3>
<p>For a single hands-off command, <code>auto-fix</code> runs everything the normal repair does, then also standardizes chapter-heading and body-text CSS classes (using only its highest-confidence guesses, applied immediately with no file left behind to review) and strips every hardcoded text color from the book so your ebook reader's own theme/night mode controls it instead.</p>
<p>&emsp;<b>Run auto-fix: </b><code>python cli.py auto-fix "C:/path/to/file/ebook title.epub"</code></p>
This is a trade of a little safety for convenience: unlike the reviewed class-mapping workflow below, nothing here is checked by a person first. If a book comes out of <code>auto-fix</code> looking wrong, the original file was never touched, so the normal <code>repair</code> command is still there to use instead.

<h3>File Integrity</h3>
<p>Before <code>analyze</code> or <code>repair</code> touch a file, they first confirm it's actually a well-formed EPUB: readable, starts with the ZIP signature, opens as a ZIP, has an intact central directory, contains a META-INF/container.xml, and can locate its OPF package document. If any of these fail, the command stops there and reports which check failed instead of trying to work with a broken file.</p>
<p>When the integrity check fails, <code>analyze</code> and <code>repair</code> automatically try a conservative, best-effort repair of the file's ZIP/EPUB structure before giving up. What it can fix with confidence:</p>
<ul>
<li>Junk bytes sitting before the start of the ZIP archive (e.g. from a bad copy or download).</li>
<li>A missing or damaged central directory / end-of-file index, rebuilt from the individual entries — as long as none of them were written in "streaming" mode (size stored after the data instead of in the header), which can't be resolved safely.</li>
<li>A missing or broken <code>META-INF/container.xml</code>, regenerated to point at the archive's OPF file — but only when there's exactly one unambiguous <code>.opf</code> file to point it at.</li>
</ul>
What it won't fix, and will instead report clearly:
<ul>
<li>A file with no ZIP signature anywhere in it — the structure itself is gone, not just damaged, so the data is most likely unrecoverable.</li>
<li>Corrupted bytes inside an entry (CRC mismatch) — there's nothing to safely reconstruct them from.</li>
<li>Streamed ZIP entries when the index is also missing.</li>
<li>An ambiguous or missing OPF (zero or multiple <code>.opf</code> candidates).</li>
</ul>
If the container repair succeeds, the run continues normally on the repaired copy — content fixes are applied on top, and <code>repair</code> writes out one clean, fully-fixed file. Pass <code>--no-container-repair</code> to skip this and just report the problem instead.
<p>&emsp;<b>Run file integrity: </b><code>python cli.py validate "C:/path/to/file/ebook title.epub"</code></p>

<h3>Config File</h3>
Every fix is on by default. To turn specific fixes off, generate a config file (if needed) and edit it:<br><br>
<p>&emsp;<b>Create a config:</b><code>python cli.py init-config</code></p>
This writes <code>ebook_fix.toml</code> in the current folder. Open it in any text editor and change <code>true</code> to <code>false</code> next to any fix you want to skip, then save.
<p>&emsp;<b>Use it:</b> python cli.py analyze "C:/Books/ebook title.epub" --config ebook_fix.toml</p>
If a file named <code>ebook_fix.toml</code> is sitting in the folder you run the command from, it's picked up automatically — you don't need to pass <code>--config</code> at all. Delete the file (or don't create one) to run with every fix enabled.

<h3>Command Cheat Sheet</h3>
Quick reference for every command and flag currently available. Run these from the <code>src</code> folder as <code>python cli.py &lt;command&gt; [options]</code>.

<p><b>analyze</b> - Analyze an EPUB without modifying it.</p>
<ul>
<li><code>input</code> - Path to the EPUB file.</li>
<li><code>--details</code> - Show the full line-by-line issue list instead of the category summary.</li>
<li><code>--config FILE</code> - Path to a TOML config file controlling which fixes are enabled. Defaults to <code>ebook_fix.toml</code> if present, otherwise every fix runs.</li>
<li><code>--no-container-repair</code> - Don't attempt to automatically repair a corrupted ZIP/EPUB container; just report the problem.</li>
</ul>

<p><b>repair</b> - Repair an EPUB.</p>
<ul>
<li><code>input</code> - Path to the EPUB file.</li>
<li><code>-o, --output FILE</code> - Output EPUB. Defaults to <code>&lt;input&gt;_fixed.epub</code>.</li>
<li><code>--dry-run</code> - Analyze repairs without writing a file.</li>
<li><code>--details</code> - Show the full before/after list of every change instead of the category summary.</li>
<li><code>--class-mapping FILE</code> - Apply a confirmed class-standardization mapping (from <code>map-css --write-mapping</code>, reviewed by hand) as part of this repair. Renames chapter-heading/body-text classes and standardizes their CSS.</li>
<li><code>--verbose</code> - Verbose output.</li>
<li><code>--config FILE</code> - Same as <code>analyze</code>.</li>
<li><code>--no-container-repair</code> - Same as <code>analyze</code>.</li>
<li><code>--overwrite</code> - Replace the output file if it already exists. Without <code>-o/--output</code>, this replaces the original file itself instead of writing <code>&lt;input&gt;_fixed.epub</code>. You'll be asked to confirm before that happens.</li>
</ul>

<p><b>auto-fix</b> - One-command hands-off repair. Runs everything the normal repair does, plus high-confidence-only class standardization and book-wide text color removal, with no review step and no mapping file left behind.</p>
<ul>
<li><code>input</code> - Path to the EPUB file.</li>
<li><code>-o, --output FILE</code> - Output EPUB. Defaults to <code>&lt;input&gt;_autofixed.epub</code>.</li>
<li><code>--details</code> - Show the full before/after list of every change instead of the category summary.</li>
<li><code>--verbose</code> - Verbose output.</li>
<li><code>--config FILE</code> - Same as <code>analyze</code>.</li>
<li><code>--no-container-repair</code> - Same as <code>analyze</code>.</li>
<li><code>--overwrite</code> - Replace the output file if it already exists. Without <code>-o/--output</code>, this replaces the original file itself instead of writing <code>&lt;input&gt;_autofixed.epub</code>. You'll be asked to confirm before that happens.</li>
</ul>

<p><b>validate</b> - Run the file-integrity check only, without analyzing or repairing.</p>
<ul>
<li><code>input</code> - Path to the EPUB file.</li>
<li><code>--repair</code> - If the file fails validation, also try to repair its ZIP/EPUB container and report the result.</li>
</ul>

<p><b>map-css</b> - Show a best-guess semantic role for every CSS class used in the book (for example, "calibre3" likely means body-text), for review before renaming.</p>
<ul>
<li><code>input</code> - Path to the EPUB file.</li>
<li><code>--no-container-repair</code> - Same as <code>analyze</code>.</li>
<li><code>--write-mapping FILE</code> - Also write an editable TOML mapping of high/medium-confidence chapter-heading and body-text classes to FILE, for review ahead of a future standardize/rename repair pass.</li>
</ul>

<p><b>map-structure</b> - Show the detected chapter structure and each boundary's split-confidence, for review before any physical splitting.</p>
<ul>
<li><code>input</code> - Path to the EPUB file.</li>
<li><code>--no-container-repair</code> - Same as <code>analyze</code>.</li>
</ul>

<p><b>split-structure</b> - Proof of concept: physically splits any file with 2+ detected chapter boundaries into standalone chapter files, rewrites any affected in-body cross-reference links, and updates the NCX (rewriting existing entries that pointed into the old file, adding new ones for pieces that had none). A mechanics test, not a finished conversion.</p>
<ul>
<li><code>input</code> - Path to the EPUB file.</li>
<li><code>-o, --output FILE</code> - Output EPUB. Defaults to <code>&lt;input&gt;_split.epub</code>.</li>
<li><code>--details</code> - Show the full before/after list of every cross-reference link rewritten, instead of the category summary.</li>
<li><code>--no-container-repair</code> - Same as <code>analyze</code>.</li>
<li><code>--overwrite</code> - Replace the output file if it already exists. Without <code>-o/--output</code>, this replaces the original file itself instead of writing <code>&lt;input&gt;_split.epub</code>. You'll be asked to confirm before that happens.</li>
</ul>

<p><b>init-config</b> - Write a default config file you can edit to turn fixes on/off.</p>
<ul>
<li><code>-o, --output FILE</code> - Where to write the config file. Defaults to <code>ebook_fix.toml</code>.</li>
</ul>
