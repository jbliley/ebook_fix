# CSS Analysis and Consolidation Workflow

Complete guide to analyzing, understanding, and safely consolidating CSS in ebook_fix.

## Overview

The ebook_fix CSS toolchain helps you understand which CSS classes in your EPUB are truly redundant (safe to consolidate) versus intentionally different (must preserve). This prevents breaking book layouts while reducing CSS bloat.

Three commands work together:

1. **analyze-css**: Understand your book's CSS structure
2. **consolidate-css**: Generate consolidation recommendations
3. **repair**: Apply consolidations using a mapping file

## Step 1: Analyze Your Book's CSS

### Command

```bash
ebook-fix analyze-css my-book.epub
ebook-fix analyze-css my-book.epub -o analysis.txt
```

### What It Does

Scans all CSS files and XHTML content, then reports:

- **Identical classes**: 100% duplicate rules (safe to consolidate)
- **Near-identical classes**: Differ in 1-2 properties (requires review)
- **Color patterns**: Which classes use which colors (design intent)
- **Font-size variations**: Typographic hierarchy indicator
- **Unused classes**: Defined but never applied in content

### Example Output

```
CSS Analysis Summary
================================================================================
Total classes defined: 139

IDENTICAL CLASSES (100% redundant)
1 group(s) found:

  calibre17, calibre2, image
    height:auto width:auto

NEAR-IDENTICAL CLASSES (differ in 1-2 properties)
31 group(s) found:

  block, block-first, block-center
    text-align: justify -> center
    margin: 18px 39px -> 18px 39px 0

COLOR PATTERNS (may be intentional design)
8 color(s) used:

  #98001f: chap-number, chap-subtitle, ... (9 total)

UNUSED CLASSES (defined but not applied)
0 class(es)
```

### Interpreting Results

**IDENTICAL CLASSES** - These are safe to consolidate immediately. All three classes have identical CSS, so they can be merged into one.

**NEAR-IDENTICAL CLASSES** - These differ in 1-2 properties. Look for semantic differences:
- `block-first` vs `block`: First paragraph after section has different margin
- `text-centered` vs `text`: Different text-align
- These differences are often INTENTIONAL, not bloat

**COLOR PATTERNS** - Classes with colors are part of your book's design. The burgundy (#98001f) in chap-number, chap-subtitle, etc. is the book's branding. Don't remove these colors!

**UNUSED CLASSES** - Safe to remove entirely, unless they're preserved for future compatibility.

## Step 2: Generate Consolidation Suggestions

### Command

```bash
ebook-fix consolidate-css my-book.epub -o consolidation.toml
```

### What It Does

Uses the analysis to generate smart consolidation suggestions, categorized by confidence:

- **High confidence (0.95)**: Identical classes, definitely safe
- **Medium confidence (0.5)**: Near-identical without color/font differences
- **Medium confidence (0.8)**: Unused classes

Filters out suggestions that would break design:
- Preserves classes with different colors
- Preserves classes with >20% font-size differences
- Provides reasoning for each suggestion

### Output Format

```toml
# CSS Consolidation Mapping
# Generated from CSS analyzer
# Review carefully before applying!

[high_confidence]
calibre2 = "image"
calibre17 = "image"

[medium_confidence]
# Near-identical with no color/size differences: calibre11, calibre16 -> calibre4
# calibre11 = "calibre4"
# calibre16 = "calibre4"

[unused]
# Classes to remove: unused-class1, unused-class2
```

## Step 3: Review and Edit the Mapping

Edit `consolidation.toml` to your comfort level:

1. **High-confidence suggestions**: Already active (uncommented)
   - Safe to apply as-is
   - Review if you want, but consolidation is safe

2. **Medium-confidence suggestions**: Commented out
   - Carefully review each one
   - Uncomment only those you understand and approve
   - Look at the reason provided
   - If in doubt, leave commented

3. **Unused classes**: Listed in `[unused]` section
   - Safe to remove if you don't need backwards compatibility
   - Optional to include in repair

Example review process:

```toml
# BEFORE review:
[medium_confidence]
# Near-identical: block-first, block-first1 -> block
# block-first = "block"
# block-first1 = "block"

# AFTER review (decided this is too risky):
[medium_confidence]
# Near-identical: block-first, block-first1 -> block
# (Keeping separate - first paragraph needs special styling)
```

## Step 4: Apply the Mapping

Once you've reviewed the TOML file:

```bash
ebook-fix repair my-book.epub --apply-mapping consolidation.toml -o my-book-fixed.epub
```

Or in combination with other repairs:

```bash
ebook-fix repair my-book.epub --standardize-css --apply-mapping consolidation.toml -o my-book-fixed.epub
```

## Real-World Example: Sherlock Holmes

Complete run-through of analysis and consolidation:

### Analysis Results

```
Total classes defined: 139

Identical classes: 1
  calibre17, calibre2, image (100% identical)
  
Near-identical classes: 31
  block family (different margins/alignment)
  block-right family (different indents/alignment)
  text family (different indents/margins)
  chap-number family (different sizing)
  
Color patterns: 8 colors
  #98001f (burgundy): chap-number, chap-subtitle, etc. [BRANDING!]
  #931210, #848484, etc. (other accents)
  
Unused classes: 0 (all classes are used)
```

### Consolidation Suggestions

```
High confidence: 1 suggestion
  consolidate calibre2, calibre17 -> image

Medium confidence: 29 suggestions
  (Many near-identical block/text variations)
  
Unused: 0 classes
```

### Safe Consolidation

Only the identical image classes are high-confidence safe. The 29 medium-confidence suggestions are mostly layout variants that should be reviewed carefully. Many block/text/chapter variations are intentional, not redundancy.

### Design Protection

The analyzer identified all color usage (#98001f, etc.) and flagged them as intentional design. These would NOT be included in consolidation suggestions, preserving the book's visual branding.

## Key Insights

### What's "Real Bloat" vs. "Intentional Variation"

**Real bloat** (safe to consolidate):
```css
.image { height: auto; width: auto; }
.image1 { height: auto; width: auto; }
.image2 { height: auto; width: auto; }
```
These three are identical - consolidate to one "image" class.

**Intentional variation** (preserve):
```css
.text-first { margin: 18px 0 0; text-indent: 0; }
.text { margin: 18px 0 0; text-indent: 27px; }
.text-flush { margin: 18px 0 0; text-indent: 0; }
```
First and flush paragraphs have no indent, regular paragraphs do. Different semantic purposes - keep separate.

### Why Colors Matter

```css
.chap-number { color: #98001f; font-size: 1.5em; }
.chap-number-book { color: #98001f; font-size: 1.375em; }
```

If you consolidated these, you'd lose the size distinction between book chapters and story chapters. Worse, stripping colors removes visual design. The #98001f is intentional branding.

The analyzer detects this and preserves these classes.

### Font-Size Hierarchy

```css
.heading { font-size: 1.5em; }
.subheading { font-size: 1.25em; }
.body { font-size: 1em; }
.footnote { font-size: 0.75em; }
```

20%+ differences in font-size indicate intentional typographic hierarchy. The analyzer won't consolidate across these boundaries.

## Workflow Tips

1. **Start conservative**: Enable only high-confidence suggestions first
2. **Test carefully**: After applying consolidations, open the book in your reader
3. **Check layout**: Verify paragraph formatting, indents, alignments look right
4. **Preserve design**: Don't strip colors or font-sizes that match your book's style
5. **Incremental**: You can always run consolidate-css again after other repairs

## Common Mistakes to Avoid

1. **Removing color attributes** - These are design features, not bugs
2. **Over-consolidating text variants** - First/regular/flush paragraphs have different indents for a reason
3. **Merging heading hierarchies** - Different font sizes serve different purposes
4. **Ignoring near-identical differences** - That 1-property change might be the whole point
5. **Not reviewing the generated TOML** - All medium-confidence suggestions are commented out for a reason

## Advanced: Understanding the Confidence Scores

- **0.95** (Identical): Same selectors = same properties. 100% safe.
- **0.5** (Near-identical): Properties differ, but no color/size diffs. Requires human judgment - might be intentional.
- **0.8** (Unused): Never applied in XHTML. Safe to remove unless you're preserving for compatibility.

## Next Steps

1. Run `analyze-css` to understand your book's structure
2. Run `consolidate-css` to get recommendations
3. Review the TOML file carefully
4. Apply with `repair --apply-mapping`
5. Test the result in your ereader

For questions about specific consolidations, check the "Reason" field in the analysis output - it explains why classes are grouped together.
