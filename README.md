# ebook_fix

An automated tool that can detect and fix both common and uncommon issues with the structure, text, metadata, and other elements of eBook files.

The ultimate goal of this project is to allow eBook files to be analyzed and fixed without needing to run them through AI, as they are often too large of a job for free usage. This idea stems from the many free eBook files available online that were poorly converted from early PDF files or printed directly from HTML files 20+ years ago.

The project is still under development and currently supports analysis and repair of **EPUB** files.

## Table of Contents

* [Installation](#installation)

  * [Option 1: Install as a Command](#option-1-install-as-a-command-recommended)
  * [Option 2: Run Without Installing](#option-2-run-without-installing)
* [Web GUI](#web-gui)
* [Command-Line Interface](#command-line-interface)

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

Every command covered in the [CLI documentation](https://github.com/jbliley/ebook_fix/wiki/CLI-Usage) works either way. Wherever you see:

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

## Command-Line Interface

`ebook_fix` also has a full command-line interface covering analysis, repair, auto-fix, cover replacement, series metadata, file-integrity checks, and configuration, useful for scripting or working through many books at once without the GUI.

Full CLI documentation, including every command and its available options, now lives on the project's [GitHub Wiki](https://github.com/jbliley/ebook_fix/wiki/CLI-Usage) rather than in this README, to keep this page focused on getting the GUI running. The CLI itself hasn't gone anywhere and isn't being removed, it's simply documented elsewhere now.
