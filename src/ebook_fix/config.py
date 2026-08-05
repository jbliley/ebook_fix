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
class Config:
    epub3_upgrade: EPUB3UpgradeConfig = field(default_factory=EPUB3UpgradeConfig)
    paragraph_repair: ParagraphRepairConfig = field(default_factory=ParagraphRepairConfig)
    image_repair: ImageRepairConfig = field(default_factory=ImageRepairConfig)


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
    _apply_module_toggle(config.image_repair, modules, "image_repair")

    _apply_section(config.epub3_upgrade, "epub3_upgrade", data.get("epub3_upgrade", {}))
    _apply_section(config.paragraph_repair, "paragraph_repair", data.get("paragraph_repair", {}))
    _apply_section(config.image_repair, "image_repair", data.get("image_repair", {}))

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
        setattr(section_config, key, bool(value))


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
image_repair = true

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
# Image Repair
# ---------------------------------------------------------------------
[image_repair]

# Remove <img> tags in chapters whose file doesn't exist in the EPUB.
fix_broken_images = true

# Report (not fixed yet) manifest entries pointing at missing images.
report_missing_manifest_images = true
"""


def write_default_config(path: str | Path) -> Path:
    """Write the default, fully-commented config file to `path`."""
    path = Path(path)
    path.write_text(DEFAULT_CONFIG_TEXT)
    return path
