# ebook_fix

An automated tool that can detect and fix both common and uncommon issues with the structure, text, metadata, and other elements of eBook files.

The ultimate goal of this project is to allow eBook files to be analyzed and fixed without needing to run them through AI, as they are often too large of a job for free usage. This idea stems from the many free eBook files available online that were poorly converted from early PDF files or printed directly from HTML files 20+ years ago.

The project is still under development and currently supports analysis and repair of **EPUB** files.

## Table of Contents

* [Installation](#installation)

  * [Option 1: Install as a Command](#option-1-install-as-a-command-recommended)
  * [Option 2: Run Without Installing](#option-2-run-without-installing)
* [Web GUI](#web-gui)
* [Analysis](#analysis)
* [Repair](#repair)
* [Auto-Fix](#auto-fix)
* [Replace Cover](#replace-cover)
* [Series Metadata](#series-metadata)
* [File Integrity](#file-integrity)
* [Config File](#config-file)
* [Command Cheat Sheet](#command-cheat-sheet)

  * [`analyze`](#analyze)
  * [`repair`](#repair)
  * [`auto-fix`](#auto-fix)
  * [`series`](#series)
  * [`replace-cover`](#replace-cover)
  * [`validate`](#validate)
  * [`map-css`](#map-css)
  * [`map-structure`](#map-structure)
  * [`split-structure`](#split-structure)
  * [`init-config`](#init-config)

---

## Installation

There are several ways to run `ebook_fix`. Use whichever is easiest for your setup.

### Option 1: Install as a Command (Recommended)

From the project's root folder, run:

```bash
pip install -e .
```

After that, you can use the `ebook-fix` command from any folder:

```bash
ebook-fix analyze "C:/path/to/file/ebook title.epub"
```

If Windows can't find the `ebook-fix` command immediately after installing, pip will print a warning containing a folder path similar to:

```text
...Python\Scripts
```

Add that folder to your PATH through **Windows Settings → Environment Variables**, then reopen your terminal.

### Option 2: Run Without Installing

Every CLI command can also be run directly from the `src` folder without installing the package or changing your PATH:

```bash
python cli.py analyze "C:/path/to/file/ebook title.epub"
```

Every command shown in this README works either way. Wherever you see:

```bash
python cli.py
```

you can substitute:

```bash
ebook-fix
```

if you installed the command, or continue using `python cli.py` from the `src` folder.

---

## Web GUI

`ebook_fix` now includes a browser-based GUI for users who would rather not work from the command line.

### Starting the GUI on Windows

From the project's root folder, simply **double-click**:

```text
run_gui.bat
```

The launcher starts the local web server and automatically opens the GUI in your default browser.

The GUI is available at:

```text
http://127.0.0.1:5000
```

You do **not** need to install `ebook_fix` as a command to use the GUI.

> **Important:** Leave the command window opened while using the GUI. Closing that window stops the GUI server.

The GUI runs locally on your computer. Nothing is sent over the internet; it is simply a local application that uses your web browser as its interface.

### Starting the GUI manually

If you prefer to start it from a terminal, run this from the project root:

```bash
python run_gui.py
```

The browser should open automatically.

---

## Analysis

Before a repair runs, `ebook_fix` analyzes the structure and elements of the book, including:

* Images
* Metadata and EPUB version
* HTML pages
* CSS styles
* Chapter, paragraph, and word counts
* Common text issues
* Hyperlinks
* Table of Contents
* Typography
* Whitespace
* Ellipsis
* Apostrophes

### Book Metadata

Metadata analysis includes every identifier the book carries, including:

* ISBN
* ASIN
* Calibre UUID
* Other recognized identifiers

Identifiers are classified by type where possible and displayed together rather than selecting a single headline identifier.

The analysis also determines whether the book is being read from inside a Calibre library or as a standalone EPUB.

For Calibre-managed books, the EPUB's internal metadata is compared against Calibre's `metadata.opf` sidecar. Genuine disagreements in fields such as title, ISBN, series, etc. are flagged rather than silently allowing one source to override the other.

Several known non-issues are automatically recognized, including:

* Calibre's three-letter language code versus EPUB's two-letter code (`eng` vs `en`)
* Author names written in opposite order (`Smith, John` vs `John Smith`)
* Titles differing only by whitespace
* Straight versus curly punctuation
* Titles differing only because one is in ALL CAPS

UUIDs are treated differently because it is completely normal for an EPUB's UUID and Calibre's UUID to be different. UUIDs are therefore listed together rather than treated as a possible mismatch.

Likely subtitles or series annotations appearing on only one side are flagged with an explanation when appropriate, since keeping or removing them can require an actual judgment call.

For Calibre-managed books, repair and auto-fix can write confidently resolved metadata values back to both the EPUB and the `metadata.opf` sidecar.

Standalone EPUBs without a `metadata.opf` to compare against are left alone and simply have the issue reported.

Identifiers are also cleaned up regardless of Calibre status. Garbled or missing scheme labels can be corrected when the value itself clearly identifies the type, identifier values are normalized, and exact duplicates are removed.

Author initials are standardized as well. For example:

```text
AA Milne
A.A. Milne
```

become:

```text
A. A. Milne
```

When `analyze` finds something that should be reviewed, it reports it without modifying the book. Running `repair` or `auto-fix` on a book with genuinely flagged issues also appends the information to `identifier_review.csv` in the current folder, making it possible to review patterns across larger collections.

### Run Analysis

Analysis is also performed automatically before a repair.

To run analysis by itself:

```bash
python cli.py analyze "C:/path/to/file/ebook title.epub"
```

---

## Repair

After analysis, `repair` automatically fixes issues that it finds.

Most repairs are enabled by default and can be controlled through the configuration file described in [Config File](#config-file).

```bash
python cli.py repair "C:/path/to/file/ebook title.epub"
```

Once the repair finishes, a **Repair Report** is printed showing only the fixes that were actually applied. The report is grouped by module with a count for each issue type.

Use `--details` to see the complete before/after information for every individual change:

```bash
python cli.py repair "C:/path/to/file/ebook title.epub" --details
```

### Cover Handling During Repair

Repair also checks the book's cover.

If there is exactly one clearly identifiable cover image, `ebook_fix`:

1. Renames the image to the standard `cover.jpg`, `cover.png`, etc.
2. Keeps the image's existing format.
3. Does not convert the image.
4. Makes sure the older and newer EPUB cover declarations agree.

If there is no declared cover or the cover information genuinely conflicts, the problem is reported rather than guessed at.

Use [`replace-cover`](#replace-cover) when you want to explicitly provide a cover image.

---

## Auto-Fix

`auto-fix` provides a single hands-off command.

It runs everything that normal `repair` does, and also:

* Standardizes chapter-heading and body-text CSS classes using only high-confidence guesses.
* Applies those class changes immediately without creating a review file.
* Removes hardcoded text colors throughout the book so the reader's own theme/night-mode settings can control text color.

```bash
python cli.py auto-fix "C:/path/to/file/ebook title.epub"
```

This trades some safety for convenience. Unlike the reviewed class-mapping workflow, there is no manual review step.

The original EPUB is not modified unless you explicitly use `--overwrite`, so if the result does not look right, the original file remains available and the normal `repair` command can be used instead.

---

## Replace Cover

To replace or add a cover image, use `replace-cover` with either a local image file or a URL.

### Local Image

```bash
python cli.py replace-cover "C:/path/to/file/ebook title.epub" "C:/path/to/new-cover.jpg"
```

### Image URL

```bash
python cli.py replace-cover "C:/path/to/file/ebook title.epub" "https://example.com/new-cover.jpg"
```

The image's actual format is determined by inspecting the image itself rather than trusting its filename or extension.

The original format is preserved and the image is renamed to the standard:

```text
cover.<ext>
```

If the book already has a usable cover, it is replaced. If it does not have one, a new cover is added.

Unlike normal repair, `replace-cover` never needs to guess which image you intended because you explicitly provide it.

---

## Series Metadata

A book's own content cannot reliably determine which series it belongs to, so series information is something you set manually.

Once set, the series information is written using both Calibre's convention and EPUB3's official metadata, allowing it to display correctly in Calibre, e-readers, and other EPUB software.

### Set a Series

```bash
python cli.py series "C:/path/to/file/ebook title.epub" --name "Mountain Man" --index 4
```

The series position can be a decimal:

```text
3.5
```

This can be useful for bonus books or novellas that fall between numbered entries.

If the book only needs a series name, `--index` can be omitted.

### Interactive Mode

If `--name` and/or `--index` are omitted, the command prompts for the missing information:

```bash
python cli.py series "C:/path/to/file/ebook title.epub"
```

Running the command again on a book that already has series information updates the existing values rather than creating duplicates.

When `analyze` encounters existing series metadata, it displays the series name and position under Book Metadata for reference.

---

## File Integrity

Before `analyze` or `repair` works on an EPUB, the file is checked to make sure it is a valid EPUB.

The integrity check verifies that the file:

* Is readable
* Starts with the ZIP signature
* Can be opened as a ZIP archive
* Has an intact central directory
* Contains `META-INF/container.xml`
* Can locate its OPF package document

If any of these checks fail, the command reports the problem rather than blindly attempting to work with a broken file.

### Automatic Container Repair

When an integrity check fails, `analyze` and `repair` automatically attempt a conservative, best-effort repair of the EPUB's ZIP/container structure.

Issues that can be repaired with confidence include:

* Junk bytes before the start of the ZIP archive
* A missing or damaged central directory/end-of-file index, when the entries contain enough information to rebuild it
* A missing or broken `META-INF/container.xml`, when there is exactly one unambiguous `.opf` file

Issues that cannot be safely repaired are reported instead, including:

* A file with no ZIP signature anywhere in it
* Corrupted bytes inside an entry, such as a CRC mismatch
* Streamed ZIP entries when the index is also missing
* An ambiguous or missing OPF file

If container repair succeeds, the normal analysis and repair process continues on the repaired copy.

Use `--no-container-repair` to skip the automatic container-repair attempt and only report the integrity problem.

### Run File Integrity Check

```bash
python cli.py validate "C:/path/to/file/ebook title.epub"
```

---

## Config File

Every repair is enabled by default.

To disable specific repairs, generate a configuration file:

```bash
python cli.py init-config
```

This creates:

```text
ebook_fix.toml
```

in the current folder.

Open the file in a text editor and change `true` to `false` for any repair you want to disable.

### Using a Config File

```bash
python cli.py analyze "C:/Books/ebook title.epub" --config ebook_fix.toml
```

If a file named `ebook_fix.toml` exists in the folder from which you run the command, it is automatically detected. You do not need to pass `--config`.

Delete the config file, or don't create one, to run with every repair enabled.

---

# Command Cheat Sheet

Quick reference for the commands and flags currently available.

Run these from the `src` folder as:

```bash
python cli.py <command> [options]
```

If you installed `ebook_fix` as a command, you can use:

```bash
ebook-fix <command> [options]
```

## `analyze`

Analyze an EPUB without modifying it.

```bash
python cli.py analyze "C:/path/to/file/ebook title.epub"
```

Options:

* `input` — Path to the EPUB file.
* `--details` — Show the full line-by-line issue list instead of the category summary.
* `--config FILE` — Path to a TOML config file controlling which fixes are enabled. Defaults to `ebook_fix.toml` if present.
* `--no-container-repair` — Don't attempt to automatically repair a corrupted ZIP/EPUB container.

---

## `repair`

Repair an EPUB.

```bash
python cli.py repair "C:/path/to/file/ebook title.epub"
```

Options:

* `input` — Path to the EPUB file.
* `-o, --output FILE` — Output EPUB. Defaults to `<input>_fixed.epub`.
* `--dry-run` — Analyze repairs without writing a file.
* `--details` — Show the full before/after list of every change.
* `--class-mapping FILE` — Apply a confirmed CSS class-standardization mapping.
* `--case3-boundaries FILE` — Review and apply Case 3 chapter boundaries.
* `--verbose` — Verbose output.
* `--config FILE` — Configuration file.
* `--no-container-repair` — Disable automatic container repair.
* `--overwrite` — Replace the output file if it already exists. Without `-o`, this replaces the original file after confirmation.

---

## `auto-fix`

Run a hands-off repair with high-confidence CSS class standardization and hardcoded text-color removal.

```bash
python cli.py auto-fix "C:/path/to/file/ebook title.epub"
```

Options:

* `input` — Path to the EPUB file.
* `-o, --output FILE` — Output EPUB. Defaults to `<input>_autofixed.epub`.
* `--details` — Show the full before/after list of every change.
* `--verbose` — Verbose output.
* `--config FILE` — Configuration file.
* `--no-container-repair` — Disable automatic container repair.
* `--overwrite` — Replace the output file if it already exists. Without `-o`, this replaces the original file after confirmation.

---

## `series`

Set or update a book's series name and position.

```bash
python cli.py series "C:/path/to/file/ebook title.epub" --name "Mountain Man" --index 4
```

Options:

* `input` — Path to the EPUB file.
* `--name NAME` — Series name. If omitted, you're prompted for it.
* `--index NUMBER` — Series position. Supports decimals such as `3.5`.
* `-o, --output FILE` — Output EPUB. Defaults to `<input>_series.epub`.
* `--no-container-repair` — Disable automatic container repair.
* `--overwrite` — Replace the output file if it already exists. Without `-o`, this replaces the original file after confirmation.

---

## `replace-cover`

Install a new cover image from a local file or URL.

```bash
python cli.py replace-cover "C:/path/to/file/ebook title.epub" "C:/path/to/new-cover.jpg"
```

Options:

* `input` — Path to the EPUB file.
* `source` — Local image path or HTTP/HTTPS URL.
* `-o, --output FILE` — Output EPUB. Defaults to `<input>_cover.epub`.
* `--no-container-repair` — Disable automatic container repair.
* `--overwrite` — Replace the output file if it already exists. Without `-o`, this replaces the original file after confirmation.

---

## `validate`

Run the file-integrity check without analyzing or repairing the EPUB.

```bash
python cli.py validate "C:/path/to/file/ebook title.epub"
```

Options:

* `input` — Path to the EPUB file.
* `--repair` — If validation fails, also attempt to repair the ZIP/EPUB container.

---

## `map-css`

Show a best-guess semantic role for every CSS class used in the book.

For example, a class such as `calibre3` may be identified as likely body text.

```bash
python cli.py map-css "C:/path/to/file/ebook title.epub"
```

Options:

* `input` — Path to the EPUB file.
* `--no-container-repair` — Disable automatic container repair.
* `--write-mapping FILE` — Write an editable TOML mapping of high/medium-confidence chapter-heading and body-text classes for review.

---

## `map-structure`

Show the detected chapter structure and each boundary's split confidence.

```bash
python cli.py map-structure "C:/path/to/file/ebook title.epub"
```

Options:

* `input` — Path to the EPUB file.
* `--no-container-repair` — Disable automatic container repair.

---

## `split-structure`

Proof-of-concept command that physically splits files with two or more detected chapter boundaries into standalone chapter files.

It also:

* Rewrites affected in-body cross-reference links
* Updates the NCX
* Rewrites existing NCX entries that pointed into the old file
* Adds new entries for pieces that did not have one

This is currently a mechanics test rather than a finished conversion workflow.

```bash
python cli.py split-structure "C:/path/to/file/ebook title.epub"
```

Options:

* `input` — Path to the EPUB file.
* `-o, --output FILE` — Output EPUB. Defaults to `<input>_split.epub`.
* `--details` — Show the full before/after list of every cross-reference link rewritten.
* `--no-container-repair` — Disable automatic container repair.
* `--overwrite` — Replace the output file if it already exists. Without `-o`, this replaces the original file after confirmation.

---

## `init-config`

Create a default configuration file that can be edited to enable or disable repairs.

```bash
python cli.py init-config
```

Options:

* `-o, --output FILE` — Where to write the config file. Defaults to `ebook_fix.toml`.
