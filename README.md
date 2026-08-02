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
<<<<<<< HEAD

<h3>Configuring which fixes run</h3>
By default every fix is on. To turn specific fixes off, generate a config file and edit it:<br><br>
<p>&emsp;<b>Create a config:</b> python src/cli.py init-config</p>
This writes <code>ebook_fix.toml</code> in the current folder. Open it in any text editor and change <code>true</code> to <code>false</code> next to any fix you want to skip, then save.
<p>&emsp;<b>Use it:</b> python src/cli.py analyze "C:/Books/ebook title.epub" --config ebook_fix.toml</p>
If a file named <code>ebook_fix.toml</code> is sitting in the folder you run the command from, it's picked up automatically — you don't need to pass <code>--config</code> at all. Delete the file (or don't create one) to run with every fix enabled.
=======
>>>>>>> 29b5332b22007c86acb825a7c04917372d88aaf7
