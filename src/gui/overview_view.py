"""
gui.overview_view

Builds the data the Overview tab renders (Phase 1 of
docs/gui_redesign_plan.md): a short summary of what the book contains
with anything bad marked red, then six categorized boxes that hold the
detail. Read-only. Nothing here changes a book.

Everything shown already exists elsewhere in the tool, so this module
only arranges it:

- Facts and the "Details" lines come from the same analysis_report the
  old Analysis tab reads, through gui.analysis_view's three builders.
- Fix counts come from each repair module's own analyze(), the same
  numbers the Repair tab shows beside its checkboxes. The caller passes
  them in as `module_reports` ({module attr: {"count", "issues"}}), so
  this file never has to run a module itself and the result can be
  cached.
- "Needs your review" counts come from the same lists the Review tab
  renders.

The result is a plain dict of strings, numbers and lists, so the whole
page can be cached as JSON in the session folder.

Red means "will cause trouble in a reader" (see the plan's red list).
Cosmetic untidiness is shown as a count without color.
"""
from __future__ import annotations

from gui import analysis_view

# Box order is display order. Each box lists the analysis sections that
# feed it, and the repair modules behind it (in the Repair tab's own
# pipeline order, so the two views read the same way).
BOXES = [
    {
        "key": "metadata",
        "title": "Metadata and Cover",
        "sections": ["Book Metadata", "Metadata Mismatches", "Cover Image"],
        "modules": ["title_cleanup", "metadata_repair", "identifier_repair", "author_initials", "cover_repair"],
    },
    {
        "key": "structure",
        "title": "Structure and Navigation",
        "sections": ["Book Structure", "Structure", "Table of Contents"],
        "modules": ["chapter_markup", "chapter_title_cleanup", "toc_cleanup", "dead_link_repair", "toc_generation"],
    },
    {
        "key": "text",
        "title": "Text and Typography",
        "sections": [
            "Typography", "Paragraphs", "Scene Breaks", "Whitespace", "Ellipsis",
            "Apostrophes", "Project Gutenberg Boilerplate",
            "Possessive Candidates", "Possible Truncation",
        ],
        "modules": [
            "gutenberg_repair", "running_title_repair", "linebreak_repair", "paragraph_repair",
            "scene_break_repair", "scene_opener_repair", "ellipsis_repair", "apostrophe_repair", "whitespace_repair",
        ],
    },
    {
        "key": "styling",
        "title": "Styling and Fonts",
        "sections": ["CSS", "Span Soup", "Possible Decorative Color", "Possible Decorative Font"],
        "modules": ["color_repair", "font_repair", "paragraph_spacing_repair"],
    },
    {
        "key": "images",
        "title": "Images",
        "sections": ["Images"],
        "modules": ["image_repair"],
    },
    {
        "key": "files",
        "title": "Files and Packaging",
        "sections": ["File Contents", "Files & Packaging"],
        "modules": ["epub3_upgrade"],
    },
]

# Plain-language names for the repairs, so the main view never shows a
# module name like "Ellipsis Normalizer". The Advanced box shows the
# real names alongside.
PLAIN_NAMES = {
    "gutenberg_repair": "Remove Project Gutenberg boilerplate",
    "running_title_repair": "Remove repeated page headers",
    "linebreak_repair": "Remove stray line breaks",
    "paragraph_repair": "Tidy paragraphs",
    "chapter_markup": "Mark up chapter headings",
    "chapter_title_cleanup": "Tidy chapter titles",
    "toc_cleanup": "Tidy the table of contents and empty pages",
    "dead_link_repair": "Fix dead links",
    "epub3_upgrade": "Upgrade to EPUB 3",
    "toc_generation": "Build a table of contents",
    "scene_break_repair": "Standardize scene breaks",
    "scene_opener_repair": "Mark scene changes that open with capitals",
    "paragraph_spacing_repair": "Remove the gap between indented paragraphs",
    "image_repair": "Fix broken images",
    "cover_repair": "Fix the cover declaration",
    "color_repair": "Remove decorative text colors",
    "font_repair": "Remove decorative fonts",
    "ellipsis_repair": "Standardize ellipses",
    "apostrophe_repair": "Restore missing apostrophes",
    "whitespace_repair": "Clean up spacing",
    "title_cleanup": "Clean up the title and author name",
    "metadata_repair": "Sync metadata with Calibre",
    "identifier_repair": "Standardize identifiers",
    "author_initials": "Fix author initials",
}

# Which box a "bad" fact belongs to, so the box header can show it.
FACT_BOX = {
    "cover": "metadata",
    "toc": "structure",
    "chapters": "structure",
    "names": "metadata",
    "calibre": "metadata",
    "dead_links": "structure",
    "empty_pages": "structure",
    "images": "images",
    "truncation": "text",
    "encoding": "text",
}


# A fix with thousands of findings (every missing apostrophe in a long
# novel) would make the page enormous, so each "What's being fixed?"
# list shows the first few and says how many more there are. The full
# list is still on the Repair tab and in the CLI's --details output.
MAX_LISTED = 25


def _listed(issues: list) -> tuple[list, int]:
    shown = [{"location": i["location"], "description": i["description"]} for i in issues[:MAX_LISTED]]
    return shown, max(0, len(issues) - MAX_LISTED)


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _issues_in(report: dict, *categories_starting_with: str) -> list:
    """Cached module findings whose category starts with any given text."""
    found = []
    for issue in (report or {}).get("issues", []):
        category = issue.get("category", "")
        if any(category.startswith(prefix) for prefix in categories_starting_with):
            found.append(issue)
    return found


def _fact(key, label, value, bad=False):
    return {"key": key, "label": label, "value": value, "bad": bad}


def _build_facts(ar, module_reports) -> list:
    s = ar.summary
    merged = ar.merged_core_fields
    facts = []

    # Cover
    cover = ar.cover
    if not cover.declared:
        facts.append(_fact("cover", "Cover", "none", True))
    elif cover.meta_id_dangling or not cover.exists_in_archive:
        facts.append(_fact("cover", "Cover", "declared, but the file is missing", True))
    elif cover.mismatched_declarations:
        facts.append(_fact("cover", "Cover", "present, but the declarations disagree", True))
    else:
        facts.append(_fact("cover", "Cover", "yes"))

    # Table of contents
    toc = ar.toc
    tidy = _issues_in(
        module_reports.get("toc_cleanup"),
        "Blank contents label", "Duplicate contents entry", "Contents entry",
    )
    if not toc.source:
        facts.append(_fact("toc", "Table of contents", "none", True))
    else:
        entries = _plural(s.toc_entry_count, "entry", "entries")
        problems = []
        if toc.broken_link_count:
            problems.append(_plural(toc.broken_link_count, "broken link"))
        if tidy:
            problems.append("needs tidying")
        facts.append(
            _fact("toc", "Table of contents", entries + (f" ({', '.join(problems)})" if problems else ""), bool(problems))
        )

    # Chapters
    seq = ar.chapters.best_sequence
    if seq:
        facts.append(_fact("chapters", "Chapters", f"{seq.length} found"))
    else:
        facts.append(_fact("chapters", "Chapters", "none found", True))

    # Title and author
    name_problems = []
    if _issues_in(module_reports.get("title_cleanup"), "Filename-style title"):
        name_problems.append("title looks like a filename")
    if _issues_in(module_reports.get("title_cleanup"), "Author name"):
        name_problems.append("author is stored as \"Last, First\"")
    if name_problems:
        facts.append(_fact("names", "Title and author", ", ".join(name_problems), True))
    else:
        facts.append(_fact("names", "Title and author", "look right"))

    # Language
    language = merged.language.display_value
    facts.append(_fact("language", "Language", language or "not set"))

    # Fixed-layout pages (spec-declared). The engine skips its risky
    # repairs on these, so say so rather than leave it a surprise.
    if getattr(getattr(ar, "layout", None), "confirmed_fixed_layout", False):
        facts.append(_fact("layout", "Layout", "fixed-layout; some repairs are skipped to protect the pages"))

    # Series (only when set)
    series = merged.series.display_value
    if series:
        index = merged.series_index.display_value
        facts.append(_fact("series", "Series", series + (f", book {index}" if index else "")))

    # Library
    facts.append(
        _fact(
            "library", "Library",
            "Calibre-managed" if ar.calibre_context.is_calibre_managed else "standalone file",
        )
    )

    # Only shown when something is wrong.
    mismatches = analysis_view._metadata_mismatch_lines(merged, ar.merged_identifiers)
    if mismatches:
        facts.append(
            _fact("calibre", "Calibre record", f"disagrees with the book on {_plural(len(mismatches), 'item')}", True)
        )

    dead = (module_reports.get("dead_link_repair") or {}).get("count", 0)
    if dead:
        facts.append(_fact("dead_links", "Dead links", f"{dead} in the text", True))

    empty = _issues_in(module_reports.get("toc_cleanup"), "Empty page")
    if empty:
        facts.append(_fact("empty_pages", "Empty pages", f"{_plural(len(empty), 'file')} in the reading order", True))

    broken_images = ar.images.broken_image_count + ar.images.missing_manifest_image_count
    if broken_images:
        facts.append(_fact("images", "Images", f"{_plural(broken_images, 'broken reference')}", True))

    if ar.paragraphs.dangling_ending_count:
        facts.append(
            _fact("truncation", "Possible truncation", f"{_plural(ar.paragraphs.dangling_ending_count, 'spot')}", True)
        )

    if ar.typography.total_mojibake:
        facts.append(_fact("encoding", "Text encoding", f"{_plural(ar.typography.total_mojibake, 'garbled character group')}", True))

    return facts


def build_overview_page(book, ar, module_reports, module_labels, module_enabled, review_counts, filename, module_skipped=frozenset()) -> dict:
    """The whole Overview page as one JSON-friendly dict.

    module_reports: {attr: {"count": int, "issues": [{"location", "category", "description"}]}}
    module_labels:  [(attr, real module label)] in pipeline order
    module_enabled: {attr: bool} from the config file
    review_counts:  {"structure": n, "text": n, "styling": n} items a person has to decide
    module_skipped: repairs the engine will skip on this book (fixed-layout guard)
    """
    s = ar.summary
    merged = ar.merged_core_fields
    facts = _build_facts(ar, module_reports)

    # ---- header ----
    page_count = s.html_page_count
    header = {
        "title": merged.title.display_value or filename,
        "author": merged.author.display_value or "",
        "line": " | ".join(
            part for part in (
                f"EPUB {s.epub_version}" if s.epub_version else "EPUB",
                f"{s.total_word_count:,} words",
                _plural(page_count, "page"),
            ) if part
        ),
    }

    # ---- findings by analysis section ----
    problems_by_section: dict = {}
    for section in analysis_view.build_issues(ar) + analysis_view.build_manual_review(ar):
        problems_by_section.setdefault(section.title, []).extend(section.lines)
    details_by_section: dict = {}
    for section in analysis_view.build_overview(ar):
        details_by_section.setdefault(section.title, []).extend(section.lines)

    labels = dict(module_labels)

    boxes = []
    ready_modules = 0
    ready_changes = 0
    for spec in BOXES:
        found = []
        for title in spec["sections"]:
            lines = problems_by_section.get(title, [])
            if lines:
                found.append({"title": title, "lines": lines})
        details = []
        for title in spec["sections"]:
            lines = details_by_section.get(title, [])
            if lines:
                details.append({"title": title, "lines": lines})

        fixes = []
        for attr in spec["modules"]:
            report = module_reports.get(attr) or {"count": 0, "issues": []}
            if not report["count"]:
                continue
            skipped = attr in module_skipped
            will_run = bool(module_enabled.get(attr, True)) and not skipped
            shown, more = _listed(report["issues"])
            fixes.append({
                "attr": attr,
                "name": PLAIN_NAMES.get(attr, labels.get(attr, attr)),
                "module_label": labels.get(attr, attr),
                "count": report["count"],
                "enabled": will_run,
                "skipped": skipped,
                "issues": shown,
                "more": more,
            })
            if will_run:
                ready_modules += 1
                ready_changes += report["count"]

        problems = sum(1 for f in facts if f["bad"] and FACT_BOX.get(f["key"]) == spec["key"])
        fix_changes = sum(f["count"] for f in fixes if f["enabled"])
        review = review_counts.get(spec["key"], 0)

        if problems:
            summary = _plural(problems, "problem")
        elif fix_changes:
            summary = _plural(fix_changes, "fix", "fixes")
        else:
            summary = "nothing found"
        if review:
            summary += f", {review} to review"

        boxes.append({
            "key": spec["key"],
            "title": spec["title"],
            "summary": summary,
            "red": problems > 0,
            "found": found,
            "details": details,
            "fixes": fixes,
            "review": review,
        })

    review_total = sum(review_counts.values())
    advanced = []
    for attr, label in module_labels:
        report = module_reports.get(attr) or {"count": 0, "issues": []}
        shown, more = _listed(report["issues"])
        advanced.append({
            "attr": attr,
            "label": label,
            "plain": PLAIN_NAMES.get(attr, label),
            "count": report["count"],
            "enabled": bool(module_enabled.get(attr, True)) and attr not in module_skipped,
            "skipped": attr in module_skipped,
            "issues": shown,
            "more": more,
        })

    return {
        "header": header,
        "facts": facts,
        "red_count": sum(1 for f in facts if f["bad"]),
        "ready_modules": ready_modules,
        "ready_changes": ready_changes,
        "review_total": review_total,
        "boxes": boxes,
        "advanced": advanced,
    }
