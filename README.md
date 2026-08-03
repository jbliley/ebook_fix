# ebook_fix
An automated tool that will be able to detect and fix both common and uncommon issues with the structure and text of eBook files.

The ultimate goal of this project is to allow any type of eBook file to be analyzed and fixed without the need of running it through AI, as they are often too large of a job for free usage. This idea is stemming from the many free eBook files online that are poorly converted from early PDF files, or those that were printed directly from HTML files from 20+ years ago. As this project is in its infancy, it can only analyze and repair .epub files so far.

<h3>Planned Software Capabilities</h3>
<ul>• Read text and structure of eBook file</ul>
<ul>• Display detected issues in a detailed summary</ul>
<ul>• Fix text and structure issues based on the detected issues</ul>
<ul>• Repairs will be done based on a configuration file (all on by default)</ul>
<ul>• Convert to different types of eBooks</ul>
<ul>• GUI and CLI with bulk capabilities</ul>

<h3>Running the Program</h3>
The current CLI can run a basic analyze and repair. Run the CLI module through the src folder:<br><br>
<p>&emsp;<b>Analysis:</b> python src/cli.py analyze "C:/Books/ebook title.epub"</p>
<p>&emsp;<b>Repair:</b> python src/cli.py repair "C:/Books/ebook title.epub"</p>

<h3>Configuring which fixes run</h3>
By default every fix is on. To turn specific fixes off, generate a config file and edit it:<br><br>
<p>&emsp;<b>Create a config:</b> python src/cli.py init-config</p>
This writes <code>ebook_fix.toml</code> in the current folder. Open it in any text editor and change <code>true</code> to <code>false</code> next to any fix you want to skip, then save.
<p>&emsp;<b>Use it:</b> python src/cli.py analyze "C:/Books/ebook title.epub" --config ebook_fix.toml</p>
If a file named <code>ebook_fix.toml</code> is sitting in the folder you run the command from, it's picked up automatically — you don't need to pass <code>--config</code> at all. Delete the file (or don't create one) to run with every fix enabled.

<h3>File integrity check</h3>
Before <code>analyze</code> or <code>repair</code> touch a file, they first confirm it's actually a well-formed EPUB: readable, starts with the ZIP signature, opens as a ZIP, has an intact central directory, contains <code>META-INF/container.xml</code>, and can locate its OPF package document. If any of these fail, the command stops there and reports which check failed instead of trying to work with a broken file.
<p>&emsp;<b>Check a file on its own:</b> python src/cli.py validate "C:/Books/ebook title.epub"</p>

<h3>Automatic container repair</h3>
When the integrity check fails, <code>analyze</code> and <code>repair</code> automatically try a conservative, best-effort repair of the file's ZIP/EPUB structure before giving up. What it can fix with confidence:
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
<p>&emsp;<b>Test a repair without writing anything:</b> python src/cli.py validate "C:/Books/ebook title.epub" --repair</p>
