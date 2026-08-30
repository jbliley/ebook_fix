"""
ebook_fix.config

Loads and validates the fixer configuration, which controls which
repair modules -- and which individual fix inside each module -- are
enabled. The config file is TOML; see DEFAULT_CONFIG_TEXT below (also
written out by `cli.py init-config`) for a fully-commented example.

If no config file is found or specified, every fix defaults to ON,
matching the project's "all on by default" behavior.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG_FILENAME = "ebook_fix.toml"


# ---------------------------------------------------------------------
# Per-module config
# ---------------------------------------------------------------------

@dataclass(slots=True)
class ParagraphRepairConfig:
    enabled: bool = True
    fix_empty_paragraphs: bool = True
    fix_watermark_junk: bool = True
    fix_mid_sentence_splits: bool = True


@dataclass(slots=True)
class ImageRepairConfig:
    enabled: bool = True
    fix_broken_images: bool = True
    report_missing_manifest_images: bool = True


@dataclass(slots=True)
class EPUB3UpgradeConfig:
    enabled: bool = True


@dataclass(slots=True)
class ChapterMarkupConfig:
    enabled: bool = True


@dataclass(slots=True)
class TocGenerationConfig:
    enabled: bool = True


@dataclass(slots=True)
class WhitespaceRepairConfig:
    enabled: bool = True
    fix_leading_indent: bool = True
    fix_trailing_indent: bool = True
    fix_repeated_whitespace: bool = True
    fix_tabs: bool = True
    fix_space_before_punct: bool = True
    fix_missing_sentence_space: bool = True
    collapse_whitespace_only_nodes: bool = True


@dataclass(slots=True)
class GutenbergRepairConfig:
    enabled: bool = True
    fix_front_matter: bool = True
    fix_back_matter: bool = True


@dataclass(slots=True)
class EllipsisRepairConfig:
    enabled: bool = True
    # "unicode" replaces every ASCII ("...") and spaced (". . .")
    # ellipsis with the single Unicode ellipsis character (…) -- the
    # recommended default, since it's plain UTF-8 text (what an
    # EPUB's HTML content already is) and displays correctly on every
    # reading system. "ascii" instead cleans both shapes down to a
    # tidy three-dot "..." with no internal spaces, for anyone who
    # specifically wants to avoid the single character.
    target_style: str = "unicode"


@dataclass(slots=True)
class ApostropheRepairConfig:
    enabled: bool = True
    # "auto" (recommended) follows whichever apostrophe style the
    # book's own prose already favors, per typography.py's per-book
    # straight-vs-curly apostrophe counts, so a repaired contraction
    # matches the book instead of standing out. "straight" or "curly"
    # force that character regardless of what the book already has.
    target_style: str = "auto"


@dataclass(slots=True)
class SceneBreakRepairConfig:
    enabled: bool = True
    # Replace a genuine mid-chapter <hr> (real content on both sides
    # before hitting the next chapter boundary or file edge) with a
    # centered "* * *" -- the conventional ebook rendering of a scene
    # divider, since a literal horizontal rule reads as a leftover
    # web-page artifact to most readers.
    replace_mid_chapter: bool = True
    # Remove an <hr> that sits at a chapter edge outright -- it's
    # redundant with a boundary that's already marked some other way
    # (a heading, a page break), not a real scene break.
    remove_chapter_edge: bool = True


@dataclass(slots=True)
class Config:
    epub3_upgrade: EPUB3UpgradeConfig = field(default_factory=EPUB3UpgradeConfig)
    paragraph_repair: ParagraphRepairConfig = field(default_factory=ParagraphRepairConfig)
    chapter_markup: ChapterMarkupConfig = field(default_factory=ChapterMarkupConfig)
    toc_generation: TocGenerationConfig = field(default_factory=TocGenerationConfig)
    image_repair: ImageRepairConfig = field(default_factory=ImageRepairConfig)
    whitespace_repair: WhitespaceRepairConfig = field(default_factory=WhitespaceRepairConfig)
    gutenberg_repair: GutenbergRepairConfig = field(default_factory=GutenbergRepairConfig)
    ellipsis_repair: EllipsisRepairConfig = field(default_factory=EllipsisRepairConfig)
    apostrophe_repair: ApostropheRepairConfig = field(default_factory=ApostropheRepairConfig)
    scene_break_repair: SceneBreakRepairConfig = field(default_factory=SceneBreakRepairConfig)


# ---------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------

def default_config_path() -> Path:
    return Path.cwd() / DEFAULT_CONFIG_FILENAME


def load_config(path: str | Path | None = None) -> Config:
    """
    Build a Config, optionally overridden by a TOML file.

    - If `path` is given, it must exist (raises FileNotFoundError otherwise).
    - If `path` is None, looks for ./ebook_fix.toml in the current
      directory. If that isn't there either, every fix stays enabled
      (the built-in defaults).
    """
    config = Config()

    if path is None:
        candidate = default_config_path()
        if not candidate.exists():
            return config
        path = candidate
    else:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    modules = data.get("modules", {})
    _apply_module_toggle(config.epub3_upgrade, modules, "epub3_upgrade")
    _apply_module_toggle(config.paragraph_repair, modules, "paragraph_repair")
    _apply_module_toggle(config.chapter_markup, modules, "chapter_markup")
    _apply_module_toggle(config.toc_generation, modules, "toc_generation")
    _apply_module_toggle(config.image_repair, modules, "image_repair")
    _apply_module_toggle(config.whitespace_repair, modules, "whitespace_repair")
    _apply_module_toggle(config.gutenberg_repair, modules, "gutenberg_repair")
    _apply_module_toggle(config.ellipsis_repair, modules, "ellipsis_repair")
    _apply_module_toggle(config.apostrophe_repair, modules, "apostrophe_repair")
    _apply_module_toggle(config.scene_break_repair, modules, "scene_break_repair")

    _apply_section(config.epub3_upgrade, "epub3_upgrade", data.get("epub3_upgrade", {}))
    _apply_section(config.paragraph_repair, "paragraph_repair", data.get("paragraph_repair", {}))
    _apply_section(config.chapter_markup, "chapter_markup", data.get("chapter_markup", {}))
    _apply_section(config.toc_generation, "toc_generation", data.get("toc_generation", {}))
    _apply_section(config.image_repair, "image_repair", data.get("image_repair", {}))
    _apply_section(config.whitespace_repair, "whitespace_repair", data.get("whitespace_repair", {}))
    _apply_section(config.gutenberg_repair, "gutenberg_repair", data.get("gutenberg_repair", {}))
    _apply_section(config.ellipsis_repair, "ellipsis_repair", data.get("ellipsis_repair", {}))
    _apply_section(config.apostrophe_repair, "apostrophe_repair", data.get("apostrophe_repair", {}))
    _apply_section(config.scene_break_repair, "scene_break_repair", data.get("scene_break_repair", {}))

    if config.ellipsis_repair.target_style not in ("unicode", "ascii"):
        raise ValueError(
            f"Invalid ellipsis_repair.target_style: {config.ellipsis_repair.target_style!r} "
            '(must be "unicode" or "ascii")'
        )

    if config.apostrophe_repair.target_style not in ("auto", "straight", "curly"):
        raise ValueError(
            f"Invalid apostrophe_repair.target_style: {config.apostrophe_repair.target_style!r} "
            '(must be "auto", "straight", or "curly")'
        )

    return config


def _apply_module_toggle(section_config, modules_table, key) -> None:
    if key in modules_table:
        section_config.enabled = bool(modules_table[key])


def _apply_section(section_config, section_name, section_table) -> None:
    valid_keys = {f.name for f in fields(section_config)}
    for key, value in section_table.items():
        if key not in valid_keys:
            raise ValueError(
                f"Unknown config option '{key}' under [{section_name}]."
            )
        # Every option so far has been on/off, so this coerced
        # everything to bool. ellipsis_repair.target_style is the
        # first string-valued option -- coerce based on the field's
        # own default type instead of assuming bool, so existing
        # true/false options keep behaving exactly as before.
        current = getattr(section_config, key)
        if isinstance(current, bool):
            setattr(section_config, key, bool(value))
        else:
            setattr(section_config, key, value)


# ---------------------------------------------------------------------
# Generating a default config file
# ---------------------------------------------------------------------

DEFAULT_CONFIG_TEXT = """\
# ebook_fix configuration
#
# Controls which repair modules -- and which individual fix inside
# each module -- are turned on. Everything defaults to `true`
# (enabled). Set anything to `false` to skip it.
#
# Delete this file (or pass no --config flag) to run with every fix
# enabled.

[modules]
epub3_upgrade = true
paragraph_repair = true
chapter_markup = true
toc_generation = true
image_repair = true
whitespace_repair = true
gutenberg_repair = true
ellipsis_repair = true
apostrophe_repair = true
scene_break_repair = true

# ---------------------------------------------------------------------
# EPUB 3 Upgrade
# ---------------------------------------------------------------------
[epub3_upgrade]

# Upgrade EPUB 2.x (or older Open Packaging Format) books to EPUB 3:
# bumps the package version, adds the required dcterms:modified
# metadata entry, and generates an EPUB 3 Navigation Document. Runs
# first, before every other repair module. The existing NCX is left
# in place for backwards compatibility with EPUB2-only readers.
enabled = true

# ---------------------------------------------------------------------
# Paragraph Repair
# ---------------------------------------------------------------------
[paragraph_repair]

# Remove leftover empty <p></p> tags (conversion artifacts).
fix_empty_paragraphs = true

# Remove conversion-tool watermark/junk paragraphs
# (e.g. "Generated by ABC Amber LIT Converter").
fix_watermark_junk = true

# Merge paragraphs that were incorrectly split mid-sentence.
fix_mid_sentence_splits = true

# ---------------------------------------------------------------------
# Chapter Markup
# ---------------------------------------------------------------------
[chapter_markup]

# Wrap each confirmed chapter (see the chapter-detection scan in the
# analysis output) in its own <section epub:type="chapter">, with a
# page break before it. Only chapters the detector is confident about
# get split; weak/ambiguous candidates are left alone.
enabled = true

# ---------------------------------------------------------------------
# TOC Generation
# ---------------------------------------------------------------------
[toc_generation]

# For a book with no NCX and no EPUB3 nav document with any TOC
# entries at all, generate one from the book's detected chapter
# structure: a toc.ncx always, plus a nav.xhtml too if the book is
# EPUB3 and doesn't have one yet. A book with even a sparse or broken
# existing TOC is left alone -- this only ever fires when there's
# genuinely nothing there to begin with.
enabled = true

# ---------------------------------------------------------------------
# Image Repair
# ---------------------------------------------------------------------
[image_repair]

# Remove <img> tags in chapters whose file doesn't exist in the EPUB.
fix_broken_images = true

# Report (not fixed yet) manifest entries pointing at missing images.
report_missing_manifest_images = true

# ---------------------------------------------------------------------
# Whitespace Normalizer
# ---------------------------------------------------------------------
[whitespace_repair]

# Cleans up leading/trailing indentation, repeated whitespace, tabs,
# space before punctuation, and clearly missing spaces after sentence
# punctuation -- all DOM-aware, so pre/code/script/style/svg/math
# content is never touched. See the analysis output for a category
# breakdown before turning this off.
enabled = true

# Remove leading whitespace/indentation at the start of a text node.
fix_leading_indent = true

# Remove trailing whitespace/indentation at the end of a text node.
fix_trailing_indent = true

# Collapse runs of 2+ whitespace characters (anywhere, not just at
# the edges) down to a single space.
fix_repeated_whitespace = true

# Treat tab characters as whitespace at all. Turning this off leaves
# every tab untouched -- not just unconverted, but invisible to every
# other option above too, so a tab is never stripped or collapsed.
fix_tabs = true

# Remove a stray space/tab sitting directly before a punctuation mark
# (" ,text" -> ",text").
fix_space_before_punct = true

# Insert a missing space after . / ! / ? / , / ; / : where a new word
# or sentence clearly starts with no space at all. Narrow and
# conservative -- see the module docstring in ebook_fix/whitespace.py
# for exactly what it will and won't touch (things like "3.14",
# "U.S.A.", and "Mr.Smith" are deliberately left alone).
fix_missing_sentence_space = true

# Collapse a text/tail node that's nothing but whitespace down to a
# single space. Never deletes one outright, even between two
# block-level tags -- see the module docstring, "Standalone
# whitespace-only nodes", for why that's the safe default.
collapse_whitespace_only_nodes = true

# ---------------------------------------------------------------------
# Gutenberg Boilerplate Removal
# ---------------------------------------------------------------------
[gutenberg_repair]

# Remove the standard Project Gutenberg front disclaimer, wherever
# ebook_fix.gutenberg found it. Always a subtree cut (the disclaimer
# shares its file with real content in every conversion style seen so
# far) -- see the module docstring in
# ebook_fix/modules/gutenberg_repair.py for exactly what does and
# doesn't get swept.
fix_front_matter = true

# Remove the standard Project Gutenberg back license, wherever
# ebook_fix.gutenberg found it, plus any further spine files that are
# nothing but leftover license text with no marker of their own. Drops
# the whole file when the marker's file is nothing but boilerplate,
# otherwise a subtree cut like the front matter above.
fix_back_matter = true

# ---------------------------------------------------------------------
# Ellipsis Normalizer
# ---------------------------------------------------------------------
[ellipsis_repair]

# Normalize both a plain three-dot ASCII ellipsis ("...") and a
# spaced-dot ellipsis (". . .") to the same target style below.
enabled = true

# "unicode" (recommended) replaces both shapes with the single
# Unicode ellipsis character (…). It's plain UTF-8 text, which is
# what an EPUB's HTML content already is, so it displays correctly on
# every reading system. "ascii" instead cleans both shapes down to a
# tidy three-dot "..." with no internal spaces, for anyone who
# specifically wants to avoid the single character.
target_style = "unicode"

# ---------------------------------------------------------------------
# Apostrophe Repair
# ---------------------------------------------------------------------
[apostrophe_repair]

# Fixes a dropped-apostrophe conversion artifact: a contraction that
# lost its apostrophe and got left with a plain space instead, e.g.
# "don t" -> "don't", "it s" -> "it's". Matched against a closed
# whitelist of known contractions only, so this stays safe -- it will
# never touch a genuine two-word phrase that happens to share the
# same shape. Possessives ("the dog s bone") are a separate, much
# higher-false-positive-risk problem and are NOT handled by this
# module -- see docs/apostrophe_repair_plan.md.
enabled = true

# "auto" (recommended) follows whichever apostrophe style the book's
# own prose already uses -- straight (') or curly ('), per the
# Typography Overview in the analysis output -- so a repaired
# contraction matches the rest of the book. "straight" or "curly"
# force that character regardless of what the book already has.
target_style = "auto"

# ---------------------------------------------------------------------
# Scene Break Normalizer
# ---------------------------------------------------------------------
[scene_break_repair]

# Replace a genuine mid-chapter <hr> (real content on both sides
# before hitting the next chapter boundary) with a centered "* * *" --
# the conventional ebook rendering of a scene divider. A literal
# horizontal rule reads as a leftover web-page artifact to most
# readers.
enabled = true
replace_mid_chapter = true

# Remove an <hr> that sits at a chapter edge outright -- redundant
# with a boundary that's already marked some other way (a heading, a
# page break), not a real scene break.
remove_chapter_edge = true
"""


def write_default_config(path: str | Path) -> Path:
    """Write the default, fully-commented config file to `path`."""
    path = Path(path)
    path.write_text(DEFAULT_CONFIG_TEXT)
    return path
