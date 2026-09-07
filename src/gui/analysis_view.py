"""
gui.analysis_view

Builds the structured data the rebuilt Analysis tab renders (Phase 6,
docs/gui_plan.md) -- reads `analysis_report` directly, the same object
_load_analysis() already hands every other tab, rather than capturing
Engine.analyze()'s printed CLI text the way Phase 1's version of this
tab did.

Every section below is sorted into one of three buckets, following the
Phase 6 audit and Jacob's decisions on it (see gui_plan.md):

- Overview: FYI, context, not a problem.
- Issues: worth a person's attention.
- Manual review: flagged, but deliberately never auto-repaired (just
  Possessive Candidates today).

This intentionally does not reproduce the CLI's `--details` per-item
drill-down (e.g. every individual broken TOC link, every chapter's own
whitespace issues) -- just the same headline counts `analyze()` shows
without `--details`. A drill-down view is a reasonable future addition
but wasn't part of what this phase's audit scoped.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)


def _clean_field_line(label: str, mf) -> str:
    """One line for a core field that ISN'T flagged MISMATCH -- a
    mismatched field is handled separately, see
    _metadata_mismatch_lines() below, since Jacob wants those shown
    the way the Metadata tab already shows them, not interleaved here
    with the fields that are just fine."""
    return f"{label}: {mf.display_value or '(none found)'}"


def _metadata_mismatch_lines(merged, merged_ids) -> list[str]:
    """Every core-field/identifier/subjects/series MISMATCH, displayed
    the same way the Metadata tab's mismatch pickers show them (both
    values, side by side) -- not a one-line rollup like "3 field(s)
    need review". Jacob's call: since the Metadata tab is the only
    place these actually get resolved, this list is informational
    (what disagrees and what the two sides say), pointing over there
    to fix it, rather than a second, separate editing surface."""
    lines = []
    for label, mf in (
        ("Title", merged.title),
        ("Author", merged.author),
        ("Publisher", merged.publisher),
        ("Date", merged.date),
        ("Rights", merged.rights),
        ("Description", merged.description),
    ):
        if mf.mismatch:
            lines.append(f"{label}: {mf.epub_value!r} (EPUB) vs. {mf.calibre_value!r} (metadata.opf)")

    if merged.subjects_mismatch:
        lines.append(
            f"Subjects/Genre: {', '.join(merged.subjects_epub) or '(none)'} (EPUB) vs. "
            f"{', '.join(merged.subjects_calibre) or '(none)'} (metadata.opf)"
        )

    if merged.series.mismatch or merged.series_index.mismatch:
        epub_disp = merged.series.epub_value + (f" (Book {merged.series_index.epub_value})" if merged.series_index.epub_value else "")
        cal_disp = merged.series.calibre_value + (f" (Book {merged.series_index.calibre_value})" if merged.series_index.calibre_value else "")
        lines.append(f"Series: {epub_disp or '(none)'} (EPUB) vs. {cal_disp or '(none)'} (metadata.opf)")

    for group in merged_ids.conflicts():
        label = group[0].matched_scheme or "unrecognized"
        values = " / ".join(f"{i.normalized_value} ({'+'.join(i.sources)})" for i in group)
        lines.append(f"Identifier ({label}): {values}")

    return lines


def build_overview(analysis_report) -> list[Section]:
    """FYI sections -- context, not a problem. Mirrors the CLI's own
    "1. ANALYSIS & OVERVIEW" block, minus anything the Phase 6 audit
    moved elsewhere: metadata MISMATCHes (see Issues, "Metadata"),
    the apostrophe count that used to be duplicated here (now only
    shown once, as an issue), and the three CSS observational counts
    that moved to their own Overview section here instead of sitting
    in Issues."""
    s = analysis_report.summary
    merged = analysis_report.merged_core_fields
    merged_ids = analysis_report.merged_identifiers
    t = analysis_report.typography
    css = analysis_report.css
    ch_summary = analysis_report.chapters
    fm = analysis_report.frontmatter
    calibre_ctx = analysis_report.calibre_context
    gb = analysis_report.gutenberg

    sections = []

    metadata_lines = []
    if calibre_ctx.is_calibre_managed:
        id_part = f"id {calibre_ctx.book_id}, " if calibre_ctx.book_id is not None else ""
        metadata_lines.append(f"Library: Calibre-managed ({id_part}root: {calibre_ctx.library_root})")
    else:
        metadata_lines.append("Library: standalone EPUB (no Calibre library detected)")

    for label, mf in (
        ("Title", merged.title), ("Author", merged.author), ("Language", merged.language),
        ("Publisher", merged.publisher), ("Date", merged.date), ("Rights", merged.rights),
    ):
        if not mf.mismatch:
            metadata_lines.append(_clean_field_line(label, mf))

    metadata_lines.append(
        f"EPUB Version: {s.epub_version or 'unknown'}"
        + (f" (will be upgraded to EPUB {s.epub_target_version})" if s.epub_needs_upgrade else "")
    )

    if merged_ids.identifiers:
        conflict_ids = {id(i) for g in merged_ids.conflicts() for i in g}
        clean_ids = [i for i in merged_ids.identifiers if id(i) not in conflict_ids]
        if clean_ids:
            parts = []
            for ident in clean_ids:
                source_tag = "+".join(ident.sources)
                if ident.matched_scheme == "uuid":
                    parts.append(f"{ident.normalized_value} ({source_tag})")
                else:
                    parts.append(f"{ident.matched_scheme or 'unrecognized'}: {ident.normalized_value}")
            metadata_lines.append("Identifiers: " + "; ".join(parts))
    else:
        metadata_lines.append("Identifiers: (none found)")

    if not merged.subjects_mismatch:
        subjects = merged.subjects_calibre or merged.subjects_epub
        if subjects:
            metadata_lines.append(f"Subjects/Genre: {', '.join(subjects)}")

    if not merged.description.mismatch and merged.description.display_value:
        metadata_lines.append(f"Description: {merged.description.display_value}")

    if not (merged.series.mismatch or merged.series_index.mismatch) and merged.series.display_value:
        idx_display = merged.series_index.display_value
        metadata_lines.append(f"Series: {merged.series.display_value}" + (f" (Book {idx_display})" if idx_display else ""))

    sections.append(Section("Book Metadata", metadata_lines))

    sections.append(Section("File Contents", [
        f"HTML/XHTML pages: {s.html_page_count}",
        f"Spine entries: {s.spine_entry_count}",
        f"TOC entries: {s.toc_entry_count}" + (f" (from {s.toc_source})" if s.toc_source else " (no NCX or nav document found)"),
        f"CSS files: {s.css_file_count}",
        f"Image files: {s.image_file_count}",
        f"Font files: {s.font_file_count}",
        f"Audio files: {s.audio_file_count}",
        f"Video files: {s.video_file_count}",
        f"Other files: {s.other_file_count}",
        f"Total word count: {s.total_word_count:,}",
    ]))

    structure_lines = [
        f"Total Paragraphs: {analysis_report.total_paragraphs}",
        f"Total Images: {analysis_report.total_images}",
        f"Total Links: {analysis_report.total_links}",
    ]
    if ch_summary.parts:
        structure_lines.append(f"Divisions/Parts: {len(ch_summary.parts)}")
    if ch_summary.best_sequence:
        seq = ch_summary.best_sequence
        files_spanned = len({c.href for c in seq.candidates})
        structure_lines.append(f"Chapters Detected: {seq.length} ({seq.style.value}) across {files_spanned} file(s)")
    else:
        structure_lines.append("Chapters Detected: None (run `map-structure` to check for weaker, unlabeled candidates)")
    if fm.boundaries_confirmed:
        structure_lines.append(
            f"Front Matter: {fm.front_matter_count} page(s) | "
            f"Back Matter: {fm.back_matter_count} page(s) | "
            f"Main Content: {fm.main_content_count} page(s)"
        )
    else:
        structure_lines.append("Front/Back Matter: not classified (no confirmed chapter sequence to anchor on)")
    sections.append(Section("Book Structure", structure_lines))

    sections.append(Section("Typography", [
        f"Quotes: {t.total_straight_double_quotes} straight double, {t.total_curly_double_quotes} curly double",
        f"Apostrophes: {t.total_straight_apostrophes} straight apostrophe, {t.total_curly_apostrophes} curly apostrophe",
        f"Dashes: {t.total_hyphen} hyphen, {t.total_en_dash} en dash, {t.total_em_dash} em dash, {t.total_double_hyphen} double-hyphen (--)",
        f"Ellipsis: {t.total_unicode_ellipsis} unicode (\u2026), {t.total_ascii_ellipsis} ascii (...)",
        f"Sentence spacing: {t.total_single_space_after_sentence} single-space, {t.total_double_space_after_sentence} double-space",
        # Missing-apostrophe count deliberately NOT repeated here --
        # see the [Apostrophes] issue section, which is the only place
        # it's shown now (Phase 6 audit item #2).
    ]))

    css_lines = [
        f"Stylesheets: {css.css_file_count} | Rules: {css.total_rules} | !important uses: {css.total_important}",
        f"Declared classes: {css.declared_class_count} | Declared ids: {css.declared_id_count}",
        f"Inline style attributes used in HTML: {css.inline_style_element_count}",
    ]
    # These three (and their embedded-<style>/inline-attribute
    # counterparts) are observations about existing CSS, not problems
    # -- a book can have deliberate page-break or forced-height rules,
    # and ebook_fix's own repairs are what add embedded <style> blocks
    # in the first place. Moved here from Issues per the Phase 6 audit
    # (item #4); grouped as one "page-break/forced-height" family
    # regardless of whether the rule lives in an external stylesheet,
    # an embedded <style> block, or a style= attribute, since it's the
    # same kind of observation either way.
    page_break_total = css.page_break_rule_count + css.embedded_page_break_rule_count + css.inline_style_attr_page_break_count
    forced_height_total = css.forced_height_count + css.embedded_forced_height_count + css.inline_style_attr_forced_height_count
    if page_break_total:
        css_lines.append(f"Page-break rules declared: {page_break_total}")
    if forced_height_total:
        css_lines.append(f"Forced height/max-height rules: {forced_height_total}")
    if css.embedded_style_block_count:
        injected_note = f" ({css.injected_style_block_count} added by ebook_fix)" if css.injected_style_block_count else ""
        css_lines.append(f"Inline <style> blocks in chapter HTML: {css.embedded_style_block_count}{injected_note}")
    sections.append(Section("CSS", css_lines))

    if gb.detected and gb.front_found and gb.back_found:
        trailing_note = f", plus {len(gb.trailing_back_matter_hrefs)} trailing file(s)" if gb.trailing_back_matter_hrefs else ""
        sections.append(Section("Project Gutenberg Boilerplate", [
            f"Front disclaimer found in {gb.front.href} (via {gb.front.method})",
            f"Back license found in {gb.back.href} (via {gb.back.method}){trailing_note}",
            "Both halves found -- repair will strip this cleanly.",
        ]))

    return sections


def build_issues(analysis_report) -> list[Section]:
    """Sections worth a person's attention. Mirrors the CLI's own "2.
    ISSUES & FINDINGS" block, plus the Metadata mismatch rollup (which
    the CLI shows inline with the clean fields, but Jacob wants
    separated out here -- see _metadata_mismatch_lines()), minus
    Possessive Candidates (its own "manual review" bucket, see
    build_manual_review() below) and minus the three CSS
    observational counts moved to Overview (audit item #4)."""
    merged = analysis_report.merged_core_fields
    merged_ids = analysis_report.merged_identifiers
    t = analysis_report.typography
    css = analysis_report.css
    gb = analysis_report.gutenberg

    sections = []

    mismatch_lines = _metadata_mismatch_lines(merged, merged_ids)
    if mismatch_lines:
        mismatch_lines.append("Resolve these on the Metadata tab.")
        sections.append(Section("Metadata Mismatches", mismatch_lines))

    structural_issues = []
    if analysis_report.unexplained_thin_chapters:
        explained_count = len(analysis_report.thin_chapters) - len(analysis_report.unexplained_thin_chapters)
        explained_note = f" ({explained_count} more thin page(s) explained by front/back matter, not counted here)" if explained_count else ""
        structural_issues.append(f"Thin/Empty Chapters: {len(analysis_report.unexplained_thin_chapters)}{explained_note}")
    elif analysis_report.thin_chapters:
        structural_issues.append(f"Thin/Empty Chapters: 0 unexplained ({len(analysis_report.thin_chapters)} explained by front/back matter)")
    heading_issue_count = sum(len(ch.heading_issues) for ch in analysis_report.chapters_with_heading_issues)
    if heading_issue_count:
        structural_issues.append(f"Heading Hierarchy Issues: {heading_issue_count} across {len(analysis_report.chapters_with_heading_issues)} chapter(s)")
    if structural_issues:
        sections.append(Section("Structure", structural_issues))

    toc = analysis_report.toc
    toc_issues = []
    if not toc.source:
        toc_issues.append("No table of contents (NCX or nav document) found in this book")
    if toc.broken_link_count:
        toc_issues.append(f"Broken TOC links: {toc.broken_link_count}")
    if toc.chapters_missing_from_toc:
        toc_issues.append(f"Main-content chapters not referenced in TOC: {len(toc.chapters_missing_from_toc)}")
    if toc_issues:
        sections.append(Section("Table of Contents", toc_issues))

    if gb.detected and not (gb.front_found and gb.back_found):
        gb_issues = []
        if not gb.front_found:
            gb_issues.append("Front disclaimer: not found")
        if not gb.back_found:
            gb_issues.append("Back license: not found")
        gb_issues.append("Only partially detected -- repair may not fully strip this book's Gutenberg boilerplate.")
        sections.append(Section("Project Gutenberg Boilerplate", gb_issues))

    cover = analysis_report.cover
    cover_issues = []
    if not cover.declared:
        cover_issues.append('No cover image declared (no <meta name="cover"> and no properties="cover-image")')
    if cover.meta_id_dangling:
        cover_issues.append(f"<meta name=\"cover\"> points at id {cover.meta_content_id!r}, which isn't in the manifest")
    if cover.declared and not cover.exists_in_archive:
        cover_issues.append(f"Declared cover file is missing from the EPUB: {cover.resolved_href}")
    if cover.declared and not cover.is_image_media_type:
        cover_issues.append(f"Declared cover's media-type isn't an image: {cover.cover_item.media_type!r}")
    if cover.mismatched_declarations:
        cover_issues.append(f'<meta name="cover"> and properties="cover-image" disagree ({cover.meta_item.href!r} vs {cover.properties_item.href!r})')
    if cover_issues:
        sections.append(Section("Cover Image", cover_issues))

    span_soup = analysis_report.span_soup
    if span_soup.chain_count or span_soup.empty_span_count:
        span_lines = []
        if span_soup.chain_count:
            span_lines.append(
                f"Nested span wrapper chains: {span_soup.chain_count} "
                f"({span_soup.fully_purposeless_chain_count} fully purposeless, deepest {span_soup.max_depth} levels)"
            )
        if span_soup.empty_span_count:
            span_lines.append(f"Empty spans with no content at all: {span_soup.empty_span_count}")
        if span_soup.no_op_classes:
            sample = ", ".join(sorted(span_soup.no_op_classes)[:8])
            more = len(span_soup.no_op_classes) - 8
            span_lines.append(f"CSS classes confirmed to have no visual effect: {sample}" + (f" (+{more} more)" if more > 0 else ""))
        span_lines.append(f"Chapters affected: {len(span_soup.chapters_affected)}")
        sections.append(Section("Span Soup", span_lines))

    typo_issues = []
    if t.quote_style_inconsistent:
        typo_issues.append("Inconsistent dialogue quote styles across chapters")
    if t.mixed_quote_chapters:
        typo_issues.append(f"Mixed quote styles within same file ({len(t.mixed_quote_chapters)} chapters)")
    if t.apostrophe_style_inconsistent:
        typo_issues.append("Inconsistent apostrophe styles across chapters")
    if t.mixed_apostrophe_chapters:
        typo_issues.append(f"Mixed apostrophe styles within same file ({len(t.mixed_apostrophe_chapters)} chapters)")
    if t.chapters_with_mojibake:
        typo_issues.append(f"Possible Encoding Corruption (mojibake): {t.total_mojibake} instance(s) in {len(t.chapters_with_mojibake)} chapter(s)")
    if t.chapters_with_bom:
        typo_issues.append(f"Stray BOM characters in {len(t.chapters_with_bom)} chapter(s)")
    if t.total_zero_width_space:
        typo_issues.append(f"Zero-width spaces: {t.total_zero_width_space}")
    if t.total_soft_hyphen:
        typo_issues.append(f"Soft hyphens: {t.total_soft_hyphen}")
    if t.total_control_chars:
        typo_issues.append(f"Stray control characters: {t.total_control_chars}")
    if t.chapters_with_all_caps_runs:
        typo_issues.append(f"ALL-CAPS text runs: {t.total_all_caps_runs} across {len(t.chapters_with_all_caps_runs)} chapter(s)")
    if t.chapters_with_repeated_punctuation:
        typo_issues.append(f"Repeated punctuation runs: {t.total_repeated_punctuation} across {len(t.chapters_with_repeated_punctuation)} chapter(s)")
    if typo_issues:
        sections.append(Section("Typography", typo_issues))

    css_issues = []
    if css.unused_class_total:
        css_issues.append(f"Unused CSS classes declared: {css.unused_class_total}")
    if css.undeclared_class_total:
        css_issues.append(f"Undeclared classes used in HTML: {css.undeclared_class_total}")
    if css.duplicate_selectors_by_file:
        total_dupes = sum(len(v) for v in css.duplicate_selectors_by_file.values())
        css_issues.append(f"Duplicate selectors: {total_dupes} across {len(css.duplicate_selectors_by_file)} file(s)")
    if css.unbalanced_brace_files:
        css_issues.append(f"Stylesheets with unbalanced braces: {len(css.unbalanced_brace_files)}")
    if css.unreadable_files:
        css_issues.append(f"Unreadable stylesheets: {len(css.unreadable_files)}")
    if css.missing_embedded_fonts:
        css_issues.append(f"Missing font file references (@font-face): {len(css.missing_embedded_fonts)}")
    if css.unused_embedded_fonts:
        css_issues.append(f"Embedded fonts never referenced by any stylesheet: {len(css.unused_embedded_fonts)}")
    if css.calibre_class_count:
        css_issues.append(f"Leftover Calibre-conversion classes: {css.calibre_class_count}")
    # Page-break/forced-height/embedded-<style> counts deliberately
    # excluded here -- see the Overview's CSS section (audit item #4).
    if css_issues:
        sections.append(Section("CSS", css_issues))

    para = analysis_report.paragraphs
    paragraph_issues = []
    if para.junk_element_count:
        paragraph_issues.append(f"Watermark / junk paragraphs: {para.junk_element_count}")
    if para.empty_paragraph_count:
        paragraph_issues.append(f"Empty paragraphs: {para.empty_paragraph_count}")
    if para.mid_sentence_split_count:
        paragraph_issues.append(f"Mid-sentence paragraph splits: {para.mid_sentence_split_count}")
    if paragraph_issues:
        sections.append(Section("Paragraphs", paragraph_issues))

    img = analysis_report.images
    image_issues = []
    if img.broken_image_count:
        image_issues.append(f"Broken image references: {img.broken_image_count} across {len(img.chapters_with_broken_images)} chapter(s)")
    if img.missing_manifest_image_count:
        image_issues.append(f"Manifest entries pointing at missing images: {img.missing_manifest_image_count}")
    if image_issues:
        sections.append(Section("Images", image_issues))

    pkg = analysis_report.packaging
    if pkg.orphaned_file_count:
        kb_total = pkg.orphaned_bytes_total / 1024
        sections.append(Section("Files & Packaging", [
            f"Files in the archive with no manifest entry: {pkg.orphaned_file_count} ({kb_total:.1f} KB)",
        ]))

    ws = analysis_report.whitespace
    whitespace_issues = []
    if ws.leading_indent_count:
        whitespace_issues.append(f"Leading indentation: {ws.leading_indent_count}")
    if ws.trailing_indent_count:
        whitespace_issues.append(f"Trailing indentation: {ws.trailing_indent_count}")
    if ws.repeated_whitespace_count:
        whitespace_issues.append(f"Repeated whitespace: {ws.repeated_whitespace_count}")
    if ws.tabs_converted_count:
        whitespace_issues.append(f"Tabs found (should be spaces): {ws.tabs_converted_count}")
    if ws.space_before_punct_count:
        whitespace_issues.append(f"Space before punctuation: {ws.space_before_punct_count}")
    if ws.missing_sentence_space_count:
        whitespace_issues.append(f"Missing space after punctuation: {ws.missing_sentence_space_count}")
    if ws.whitespace_only_node_count:
        whitespace_issues.append(f"Whitespace-only text nodes: {ws.whitespace_only_node_count}")
    # ws.protected_nodes_skipped_count deliberately excluded -- moved
    # to Overview (audit item #5): "we didn't touch these" is a
    # reassurance, not a problem.
    if whitespace_issues:
        sections.append(Section("Whitespace", whitespace_issues))

    ell = analysis_report.ellipsis
    ellipsis_issues = []
    if ell.total_ascii_count:
        ellipsis_issues.append(f"ASCII ellipsis (...) found: {ell.total_ascii_count}")
    if ell.total_spaced_count:
        ellipsis_issues.append(f"Spaced-dot ellipsis found: {ell.total_spaced_count}")
    if ellipsis_issues:
        sections.append(Section("Ellipsis", ellipsis_issues))

    # Apostrophes: shown here ONLY (no longer duplicated in the
    # Overview's Typography counts -- audit item #2).
    apo = analysis_report.apostrophes
    if apo.total_match_count:
        sections.append(Section("Apostrophes", [f"Missing apostrophe (contraction) found: {apo.total_match_count}"]))

    sb = analysis_report.scene_breaks
    if sb.total_issue_count:
        sb_lines = []
        if sb.total_mid_chapter_count:
            sb_lines.append(f'Mid-chapter <hr> found (would become "* * *"): {sb.total_mid_chapter_count}')
        if sb.total_chapter_edge_count:
            sb_lines.append(f"Chapter-edge <hr> found (redundant, would be removed): {sb.total_chapter_edge_count}")
        sections.append(Section("Scene Breaks", sb_lines))

    return sections


def build_manual_review(analysis_report) -> list[Section]:
    """Flagged, but deliberately never auto-repaired -- see
    ebook_fix.apostrophes' module docstring for why a bare "word s"
    split needs a person's judgment call, not a repair module's."""
    poss = analysis_report.possessives
    if not poss.total_candidate_count:
        return []
    return [Section("Possessive Candidates", [
        f"Possible missing apostrophe (possessive or plural, ambiguous): "
        f"{poss.total_candidate_count} -- not auto-repaired, review and fix by hand",
    ])]
