# ebook_fix
An automated tool that will be able to detect and fix both common and uncommon issues with the structure and text of eBook files.

The ultimate goal of this project is to allow any type of eBook file to be analyzed and fixed without the need of running it through AI, as they are often too large of a job for free usage. This idea is stemming from the many free eBook files online that are poorly converted from early PDF files, or those that were printed directly from HTML files from 20+ years ago. As this project is in its infancy, it can only analyze and repair .epub files so far.

<h3>Installation</h3>
<p>There are two ways to run ebook_fix, use whichever is easier for your setup.</p>
<p><b>Option 1: Install it as a command (recommended)</b></p>
<p>From the project's root folder, run this once:</p>
<p>&emsp;<code>pip install -e .</code></p>
<p>After that, use the <code>ebook-fix</code> command from any folder, on any book:</p>
<p>&emsp;<code>ebook-fix analyze "C:/path/to/file/ebook title.epub"</code></p>
<p>If Windows can't find the <code>ebook-fix</code> command right after installing, pip will print a warning during install with a folder path in it (something like <code>...Python\Scripts</code>). Add that folder to your PATH (search "Environment Variables" in Windows Settings) and reopen your terminal, and the command will work.</p>
<p><b>Option 2: Run it without installing anything</b></p>
<p>Every command also works by running the file directly from the <code>src</code> folder, with no install step and no PATH setup needed:</p>
<p>&emsp;<code>python cli.py analyze "C:/path/to/file/ebook title.epub"</code></p>
<p>Every command shown in this README works either way. Wherever you see <code>python cli.py</code> below, you can substitute <code>ebook-fix</code> once it's installed, or keep using <code>python cli.py</code> from the <code>src</code> folder if you'd rather skip installing.</p>

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
<br>Book Metadata includes every identifier the book carries (ISBN, ASIN, a Calibre UUID, etc.), not just one, with each classified by type where recognizable. It also reports whether the book is being read from inside a Calibre library or as a standalone file. When it's Calibre-managed, the book's own internal metadata is compared against Calibre's metadata.opf sidecar, and any field where they genuinely disagree (title, ISBN, series, etc.) is flagged rather than one silently overriding the other. Two known non-issues are recognized automatically instead of being flagged: Calibre's three-letter language code versus EPUB's two-letter one (eng vs en), and an author name written in opposite order on each side (Smith, John vs John Smith), which is standardized to First Last. For a Calibre-managed book, repair and auto-fix go a step further and actually write these confidently-resolved values back, into both the EPUB and the metadata.opf sidecar, so the fix persists rather than just being reported every time. A standalone EPUB with no metadata.opf to confirm against is left alone, still just flagged. Anything genuinely flagged also gets appended to a running identifier_review.csv in the current folder, so patterns across many books are easy to spot later.
<br>This analysis runs automatically before running a repair. To run only the analysis without the repair (from the src folder):
<p>&emsp;<b>Run analysis: </b><code>python cli.py analyze "C:/path/to/file/ebook title.epub"</code></p>

<h3>Repair</h3>
After the analysis, the repair will automatically fix issues it finds. These fixes can be changed in the config file (see below). Besides the options in the config file, all repairs are done automatically without input. Nothing repaired should break the book, but if you find something does break it please pop it onto the Issues page.
<p>&emsp;<b>Run repair: </b><code>python cli.py repair "C:/path/to/file/ebook title.epub"</code></p>
Once the repair finishes, a Repair Report is printed listing only the fixes that were actually applied, grouped by module with a count for each issue type. Pass <code>--details</code> to see the full before/after list for every single change instead of just the counts.
<p>&emsp;<b>Run repair with full detail: </b><code>python cli.py repair "C:/path/to/file/ebook title.epub" --details</code></p>
One of the things repair checks is the book's cover: if there's exactly one clearly identifiable cover image (no ambiguity about which file it is), it renames that file to a standard <code>cover.jpg</code>/<code>cover.png</code>/etc. (matching whatever format the image already is, never converting it) and makes sure both the older and newer ways a book can declare its cover agree on it. A book with no cover declared at all, or with genuinely conflicting cover information, is reported rather than guessed at; use <code>replace-cover</code> below to fix those.

<h3>Auto-Fix</h3>
<p>For a single hands-off command, <code>auto-fix</code> runs everything the normal repair does, then also standardizes chapter-heading and body-text CSS classes (using only its highest-confidence guesses, applied immediately with no file left behind to review) and strips every hardcoded text color from the book so your ebook reader's own theme/night mode controls it instead.</p>
<p>&emsp;<b>Run auto-fix: </b><code>python cli.py auto-fix "C:/path/to/file/ebook title.epub"</code></p>
This is a trade of a little safety for convenience: unlike the reviewed class-mapping workflow below, nothing here is checked by a person first. If a book comes out of <code>auto-fix</code> looking wrong, the original file was never touched, so the normal <code>repair</code> command is still there to use instead.

<h3>Replace Cover</h3>
<p>To swap in a new cover image, whether the book already has one or not, use <code>replace-cover</code> with either a local file path or a URL:</p>
<p>&emsp;<b>From a local file: </b><code>python cli.py replace-cover "C:/path/to/file/ebook title.epub" "C:/path/to/new-cover.jpg"</code></p>
<p>&emsp;<b>From a URL: </b><code>python cli.py replace-cover "C:/path/to/file/ebook title.epub" "https://example.com/new-cover.jpg"</code></p>
<p>The image's actual format is checked by inspecting the file itself, not by trusting its name or extension, and it's kept as-is (never converted) but renamed to the same standard <code>cover.&lt;ext&gt;</code> filename repair uses. If the book already has a usable cover, this replaces it in place; if it doesn't, this adds one from scratch. Unlike repair, this never needs to guess since you're supplying the image directly, so it works even on a book repair would otherwise just report a cover problem on.</p>

<h3>Series Metadata</h3>
<p>Since a book's own content never reliably says what series it belongs to, this is the one piece of metadata you set by hand rather than something analysis detects on its own. Once set, it's written using both calibre's convention and EPUB3's official one, so it shows up correctly whether the book is opened in calibre, an e-reader, or anything else that reads either standard.</p>
<p>&emsp;<b>Set a series: </b><code>python cli.py series "C:/path/to/file/ebook title.epub" --name "Mountain Man" --index 4</code></p>
<p>Position can be a decimal like <code>3.5</code>, for a bonus or novella entry between two numbered books. Leave <code>--index</code> off entirely if the book only needs a series name and no number.</p>
<p>If you leave off <code>--name</code> and/or <code>--index</code>, the command asks for whatever's missing right there in the console instead of erroring out, so you don't need to remember the exact flags:</p>
<p>&emsp;<b>Set a series interactively: </b><code>python cli.py series "C:/path/to/file/ebook title.epub"</code></p>
<p>Running this again on a book that already has series info updates it in place rather than adding a duplicate, so it's safe to correct a typo or bump the number later. Once <code>analyze</code> sees a book with series metadata already on it, it shows the series name and position under Book Metadata, purely for reference; that display doesn't need this command to have been run first, only for the metadata to already be there.</p>

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
<li><code>--case3-boundaries FILE</code> - Review and apply Case 3 chapter boundaries (books with no chapter-heading words and no existing table of contents). If <code>FILE</code> doesn't exist yet, detects every candidate boundary and writes it there for review, without touching the book. Delete a boundary you don't trust and re-run the same command to physically split on whatever's left.</li>
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

<p><b>series</b> - Set (or update) a book's series name and position, written using both calibre's and EPUB3's conventions.</p>
<ul>
<li><code>input</code> - Path to the EPUB file.</li>
<li><code>--name NAME</code> - Series name. If omitted, you're prompted for it.</li>
<li><code>--index NUMBER</code> - Position within the series. Accepts decimals like <code>3.5</code> for a bonus/novella entry. If omitted, you're prompted for it.</li>
<li><code>-o, --output FILE</code> - Output EPUB. Defaults to <code>&lt;input&gt;_series.epub</code>.</li>
<li><code>--no-container-repair</code> - Same as <code>analyze</code>.</li>
<li><code>--overwrite</code> - Replace the output file if it already exists. Without <code>-o/--output</code>, this replaces the original file itself instead of writing <code>&lt;input&gt;_series.epub</code>. You'll be asked to confirm before that happens.</li>
</ul>

<p><b>replace-cover</b> - Install a new cover image from a local file or a URL, replacing whatever cover the book currently has (if any).</p>
<ul>
<li><code>input</code> - Path to the EPUB file.</li>
<li><code>source</code> - Path to a local image file, or an http(s) URL to download it from.</li>
<li><code>-o, --output FILE</code> - Output EPUB. Defaults to <code>&lt;input&gt;_cover.epub</code>.</li>
<li><code>--no-container-repair</code> - Same as <code>analyze</code>.</li>
<li><code>--overwrite</code> - Replace the output file if it already exists. Without <code>-o/--output</code>, this replaces the original file itself instead of writing <code>&lt;input&gt;_cover.epub</code>. You'll be asked to confirm before that happens.</li>
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
