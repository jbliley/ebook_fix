"""
CSS Analyzer Module Documentation

The CSS analyzer is a tool within ebook_fix that helps identify which CSS
classes are redundant (safe to consolidate) versus intentionally different
(must preserve).

Usage from CLI
==============

    ebook-fix analyze-css book.epub
    ebook-fix analyze-css book.epub -o report.txt

This generates a detailed report showing:
- Identical classes (100% duplicate rules)
- Near-identical classes (differ in 1-2 properties)
- Color patterns (helps identify intentional design)
- Font-size variations (indicates typographic hierarchy)
- Unused classes (defined but never applied)

Example Output
==============

    CSS Analysis Summary
    ================================================================================
    Total classes defined: 139

    IDENTICAL CLASSES (100% redundant)
    1 group(s) found:

      calibre17, calibre2, image
        height:auto width:auto

    NEAR-IDENTICAL CLASSES (differ in 1-2 properties)
    31 group(s) found:

      block, block-center, block-first, block-last
        text-align: justify -> center
        margin: 18px 39px -> 18px 39px 0

    COLOR PATTERNS (may be intentional design)
    8 color(s) used:

      #98001f: chap-number, chap-subtitle, ... (9 total)

When to Use This
================

Before running the class-standardize repair, run analyze-css to understand
the structure of your book's CSS. This helps you:

1. Know which classes can safely be merged (identical groups)
2. Avoid accidentally breaking layout by over-consolidating near-identical
   classes (which differ in important margin/padding/text-align properties)
3. Preserve intentional design elements like color schemes (see COLOR PATTERNS)
4. Clean up truly unused classes

Key Insights
============

What looks like bloat isn't always bloat. For example, in the Sherlock Holmes
"Complete Edition":

    block-first (margin: 18px 39px)       -> First paragraph after section
    block-first1 (margin: 18px 39px 0)    -> First paragraph, no space after
    block (margin: 18px 39px)             -> Regular paragraph
    block-center (margin: 18px 39px, centered) -> Centered paragraph

These near-identical classes aren't redundant - they serve different semantic
purposes (first paragraph, centered, indented). The margins/text-align
differences ARE intentional.

However:

    calibre2, calibre17, image
      height: auto
      width: auto

These three classes are 100% identical and can safely be consolidated into a
single "image" class.

Interpreting the Report
=======================

IDENTICAL CLASSES - Safe to consolidate
    These classes have exactly the same CSS rules. You can safely merge them
    into a single class or remove the duplicates.

NEAR-IDENTICAL CLASSES - Be cautious
    These classes differ in 1-2 properties. Before consolidating, check:
    - Do the differences serve a real purpose? (e.g., first vs. regular)
    - Are they used in different semantic contexts?
    If yes, keep them separate. If they're just typographical variations,
    consolidation might be safe.

COLOR PATTERNS - Preserve intentional design
    Classes with explicit colors are likely part of your book's design.
    Don't strip color attributes thinking they're bloat. Example:
    - #98001f appears in 9 classes (chapter headers)
    This is the book's branding, not an error.

UNUSED CLASSES - Safe to remove
    Classes that are defined but never used anywhere in the EPUB.
    Safe to remove entirely.

Font sizes - Indicates intentional hierarchy
    If you see 10+ different font-size values, that's probably intentional
    (headings, body, footnotes, etc.), not bloat.

Integration with Class-Standardize
===================================

After reviewing the CSS analysis:

1. Use the identical classes list to create your class mapping
   (map identical classes to a single canonical name)
2. Skip the near-identical classes unless you're confident they're truly
   redundant in context
3. Don't remove color attributes in your standardization
4. Consider removing unused classes (safe cleanup)

Example workflow:

    $ ebook-fix analyze-css my-book.epub -o analysis.txt
    # Review analysis.txt, decide which classes to consolidate
    $ ebook-fix map-css my-book.epub --write-mapping my-mapping.toml
    # Edit my-mapping.toml to your standards
    $ ebook-fix repair my-book.epub --apply-mapping my-mapping.toml
