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
