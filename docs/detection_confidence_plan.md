# Detection Confidence: Plan

**Status:** Survey done (2026-10-24). Phase 1 (div paragraphs) is built, and two chapter detection fixes from Phase 3 were made along the way; see "Progress" at the end. Phase 2 (classless body paragraphs) is done in a smaller form than planned. Phase 3 (heading blocks) is done. Phase 4 (zones) is done. Phase 5 (low-confidence page labels) is done.

Jacob wants the tool to be more sure about what each part of a book is (chapters, front and back matter, body text, headings), because most of the newer repairs only act when detection is confident, and a repair that is skipped does nothing for the reader. The long-term goal from the extra content plan still stands: anything that can be labeled with a confidence is handled automatically, and a person only reviews what stays uncertain.

## How the survey was done

`survey.py` (a small script written for this plan, not in the repository yet) loads each example book, runs the analyzer and the class profile builder, and prints one line per book: pages, chapter candidates, confirmed chapter boundaries, the best chapter sequence, whether front/back/main zones were confirmed, how many pages have a low-confidence label, the book's main body-text class with its confidence and share of paragraphs, and the classes found to be chapter headings. It was run on the 23 example books plus The Numbers Killer and Hunter's Moon. A second script counted `<p>` paragraphs against `<div>` paragraphs.

## What the survey found

| Finding | Books | Why it matters |
|---|---|---|
| Paragraphs written as `<div class="p-indent">` instead of `<p>` | Hunter's Moon (2,694 div paragraphs, 36 `<p>`), OmnibusExample (4,454 div paragraphs, 4 `<p>`) | The analyzer counts 36 paragraphs in a 92,000 word book. No body-text class is found (OmnibusExample) or the wrong one is (Hunter's Moon picks `saut_fullpage` at low confidence). Every repair that looks at `<p>` (Paragraph Spacing, Body Paragraph Look, Reader Settings on body text, Scene Opener, Paragraph Repair, Paragraph Indent) either skips these books or works on the wrong thing. |
| Body paragraphs with no class at all | War and Peace (10,298 `<p>` with no class; the class profile calls `footnote`, 136 uses, the body text) | Class-based detection has nothing to count, so a minor class wins. Anything keyed to "the body class" is aimed at the wrong paragraphs. |
| Chapter headings written as a `<div>` | Hunter's Moon (`div.title-chapter`, role unknown; only the `<span class="b">` inside it is found) | The heading is found through its inner span, so the heading block's own styling (size, margin, page break) is never seen as a heading style. |
| Heading classes found at only "medium" confidence | MM5 Complete and Incomplete (`title`, `title2`, `center`, `centera`), Numbers Killer (`calibre15`, `calibre23`) | Several modules only act on high-confidence classes, so these books keep heading styling that could be cleaned up. |
| Zones not confirmed | Watermarks-SmallChapterNumbers (24 chapters found, zones unconfirmed), GutenbergText-HRule (no sequence at all), CrossReferences, FixedLayout, PossibleFixedLayout | Without confirmed zones every page is "unknown", so front matter, back matter and extra content repairs cannot tell where the story starts. |
| Many pages with a low-confidence label | Omnibus-Tarzan (154 of 659), Images-PageNumbers (26 of 470), MM5 Complete (18) | These are pages the tool is guessing about (page images, chapter pieces, omnibus joins). |

Books that look fine today: BrokenSentences, ChaptersNotAligned-New, GutenbergText-ChapterSplit, MM21, Omnibus-RavagedLand, Sidewinders, The Numbers Killer. Each has a confirmed chapter sequence, confirmed zones, and a single high-confidence body class covering about 100% of paragraphs.

## Phases

### Phase 1: Div paragraphs

Goal: a book that writes its paragraphs as `<div>` gets the same treatment as one that uses `<p>`.

Two ways to do it:

1. **Recode (recommended).** A new repair, Div Paragraphs, turns a `<div>` into a `<p>` when it is a leaf (only text and inline tags inside, no block tags), has more than a few words, and its class is used for text throughout the book (the page uses it many times, and it is not a wrapper, heading or table cell). Its class and other attributes are kept, and any stylesheet selector that said `div.p-indent` is rewritten to `.p-indent` so nothing looks different. After that every existing module works unchanged, and so does every future one. The conversion is skipped on fixed-layout books and is switched by one setting.
2. **Teach every module.** Make the analyzer and each repair treat a text `<div>` as a paragraph. This is more code, touches every module, and every new module would have to remember to do it.

Plan: build the repair, put it right after Chapter Markup (so chapter wrappers are already marked and are not mistaken for paragraphs), and add a check to the analysis that reports "Paragraphs written as div tags: N" so the problem is visible when the repair is turned off.

Checks: Hunter's Moon and OmnibusExample should report thousands of paragraphs after the repair, find a high-confidence body class (`p-indent`), and be stable on a second run. The visible text of every page must be identical with the repair on or off. The other 21 example books should not change at all.

### Phase 2: Classless body paragraphs

Goal: when most paragraphs have no class, the body is "paragraphs with no class", not whatever small class happens to be left.

Count unclassed `<p>` as a profile of their own (name shown as `(no class)`, selector `p`) and let it win the body-text role when it covers most of the main-zone paragraphs. Repairs that edit the body class's rules then edit the `p` rule in the stylesheet, which the Reader Settings repair already does for the other body classes.

Checks: War and Peace and other Project Gutenberg style books report the body at high confidence, and `footnote` and `poem` are no longer called body text.

### Phase 3: Heading blocks and medium-confidence headings

Goal: find the heading block, not just the span inside it, and raise medium to high where the evidence is there.

- Treat the nearest block (`div` or `p`) around a confirmed chapter marker as the heading block, and give its class the chapter-heading role (Hunter's Moon `title-chapter`).
- Raise a class from medium to high when it covers at least 90 percent of the confirmed chapters and is also bold, larger, or centered. The numbers are in the profile already (`chapter_marker_coverage`).

Checks: MM5 and The Numbers Killer headings reach high; no class that is used for ordinary text gets promoted (count the paragraphs each promoted class covers on all example books).

### Phase 4: Zones when chapters are not confirmed

Goal: front, back and main zones for the books where no chapter sequence is confirmed, or where it is but the zones are not.

- Watermarks-SmallChapterNumbers: chapters are found (24) but zones are not, because the chapters share files. Anchor the zones on the first and last confirmed chapter's position inside its file, not only on file order.
- GutenbergText-HRule and the synthetic books: use the Project Gutenberg start and end markers, the contents list, and the first long run of body paragraphs as secondary anchors.

Checks: the zone count line in the survey reads confirmed for every example book that has a real story in it.

### Phase 5: Low-confidence page labels

Goal: fewer pages that the tool is guessing about.

Start with Omnibus-Tarzan (154) and Images-PageNumbers (26). For each, list the labels and the reason (the `reason` field is already recorded), group them, and decide which group can be raised with evidence the tool already has (a page that is one picture, a page inside a confirmed chapter's range, a page whose text matches a known front or back matter phrase). This phase is investigation first, so its steps will be filled in once the groups are known.

## What each phase unlocks

| Phase | Repairs that start acting on books they skipped before |
|---|---|
| 1 | Paragraph Spacing, Body Paragraph Look, Reader Settings (body text), Scene Opener, Paragraph Indent, Paragraph Repair, Quote Style (paragraph context) on div-style books |
| 2 | Same list, on books with unclassed paragraphs; Body Paragraph Look no longer compares against the wrong class |
| 3 | Heading cleanup in Reader Settings and Line Height, Chapter Header Spacing on div headings |
| 4 | Extra Content Removal, Gutenberg, front and back matter handling |
| 5 | Review tab shows fewer pages for a person to confirm |

## Order and effort

Phase 1 first: it has the clearest evidence (two books, over 7,000 paragraphs the tool cannot see) and the other phases do not depend on it. Rough effort: one to two work intervals for Phase 1, one for Phase 2, one for Phase 3, one to two for Phase 4, and an investigation interval for Phase 5. Every phase ends with the 23-book stability check and a new row in `docs/analysis_roadmap.md`.

## Open questions for Jacob

- Phase 1 changes the markup of a book (div to p). It is switchable and the visible text and look do not change. Is recoding acceptable, or would he rather have the analyzer and modules learn about divs (approach 2)?
- Should the survey script be added to the repository as a tool so the numbers can be re-run after each phase?

## Progress

### 2026-10-24: Phase 1 built, plus two chapter detection fixes

- **Div Paragraphs** (`modules/div_paragraph_repair.py`, key `div_paragraph_repair`, on by default) turns paragraphs written as `<div>` into `<p>`, as described in Phase 1, using the recode approach. Hunter's Moon and OmnibusExample now find `p-indent` as the body class at high confidence on the repaired file (before: a wrong low-confidence class, and none at all). The visible text of both books is identical with the module on and off, a second run changes nothing, and the other example books are untouched.
- **Footnote numbers are no longer chapter markers** (`chapters.py`). OmnibusExample's "23 confirmed chapters" were really the footnote reference numbers 1 to 23 (`<sup><a href="#ftn1"><span>1</span></a></sup>`), which happen to form a clean sequence. That false sequence also made Chapter Markup tag ordinary body paragraphs as chapter starts. A number inside a `<sup>` or `<sub>`, or inside a link to a footnote target (an `epub:type` of noteref, or an address such as `#ftn1`, `#fn2`, `#note3`), is now skipped.
- **Heading blocks written as a `<div>`** (part of Phase 3). A short `<div>` whose class or id says chapter, title or heading, and which holds no paragraphs or other blocks, can now be a chapter marker, like a `<p>` or `<h2>` always could. OmnibusExample's `div.title-chapter` ("20") is now found: 32 real chapters in one confirmed sequence, zones confirmed, `title-chapter` recognized as the chapter heading class, and the 32 chapter titles are tagged correctly. Every other example book gives exactly the same chapter results as before.

Still open from the survey: medium-confidence heading classes (MM5, Numbers Killer), zones for books whose chapters share files (Watermarks-SmallChapterNumbers), books with no chapter sequence at all (GutenbergText-HRule, The Call of Cthulhu), and the low-confidence pages in Omnibus-Tarzan and Images-PageNumbers.

### 2026-10-24: Phase 2 done, smaller than planned

Looking at what actually depends on the body class changed the plan. For a book with plain `<p>` paragraphs (War and Peace), the repairs that matter already work: Reader Settings treats the bare `p` rule as body text whether or not any class is named, and it removes the indent and gap from War and Peace's `p { text-indent: 1em; margin-top: 0.25em; margin-bottom: 0.25em }`. So no pretend "no class" profile was added (every other module builds `.class` selectors from profile names, and a made-up name would have broken them).

What was wrong was only the label. War and Peace's 136 `footnote` paragraphs (margin and smaller text on purpose) were called the body text at medium confidence, because all of its uses are `<p>` tags, while the 10,000 plain paragraphs were not counted at all. The font and color checks accept a medium body class, so they were aimed at the wrong style. Now, when plain paragraphs are at least half of the main-content paragraphs, a class covering under 10 percent of them is labeled "unknown" instead of body text (`class_map.py`: `UNCLASSED_BODY_SHARE`, `MINOR_CLASS_SHARE`).

Result on the survey: War and Peace and The Call of Cthulhu no longer report a wrong body class, and every other example book reports exactly the same body class as before. All 23 example books are stable on a second run.

### 2026-10-24: Phase 3 done

The heading block around a confirmed chapter marker is now credited to its class (`class_map.py`: `MAX_HEADING_BLOCK_CHARS`, `MIN_BLOCK_MARKER_SHARE`). A class needs at least half of its uses to be such blocks before it is called a chapter heading, which keeps ordinary text classes from being promoted (an early version promoted `calibre9` in Images-PageNumbers from a few hits, which is why the share rule exists). Hunter's Moon and OmnibusExample `title-chapter` is recognized. The medium-confidence classes in MM5 stay medium: they are single-use title page classes, and medium is the honest label for them.

Finding: testing Phase 3 on the auto-fix command showed that it kept changing books on every run (Scene Break Markers, Class Standardize and Reader Settings undoing each other). These are fixed and described in the roadmap. Auto-fix stability is not covered by the 23-book check yet; adding it is a good idea for the next phase.

### 2026-10-25: Phase 4 done

- **Shared chapter file** (Watermarks-SmallChapterNumbers): the guard that discarded zones when every chapter marker landed in one file now keeps them when that file has at least 150 words per marker (this book: about 2,600). A one-file book is always the story. This is anchored on the file, not on the position inside the file, because zones are labeled per page; the front matter that sits at the top of a chapter file stays inside the main zone.
- **Project Gutenberg markers** (GutenbergText-HRule, The Call of Cthulhu): new `zone_anchors.py` finds the start and end lines, modern and old style, and anchors the zones on them when no chapter sequence was confirmed.
- **Skipped from the plan:** the contents-list and first-long-run-of-paragraphs anchors. The books they were meant for turned out to be the three tiny fixed-layout test files, which should stay "unknown". If a real book shows up that needs them they can be added to `zone_anchors.py`.
- Result on the survey: zones are confirmed for every example book except FixedLayout-Synthetic and PossibleFixedLayout-Synthetic. No other book changed. Auto-fix stability on the four books matches the earlier behavior; CrossReferences-Synthetic still changes on a second auto-fix run, which predates Phase 4 and is worth its own look.

### 2026-10-25: Phase 5 done

The investigation found one group, not several: story pages outside a partial confirmed chapter run were labeled generic front or back matter at low confidence (Omnibus-Tarzan 151 of its 154, MM5 Complete 17 of 18, Images-PageNumbers 24 of 26, all generic and over 300 words; the only other long generic pages were Project Gutenberg license text). A long unlabeled page is now main content at medium confidence, with a shorter "Chapter ..." headed page treated the same way (details in the roadmap). Low-confidence pages: Omnibus-Tarzan 154 to 2, MM5 Complete 18 to 1, Images-PageNumbers 26 to 12 (mostly its real appendix). Other books unchanged.

Not done: the two page-image and chapter-piece groups the plan guessed at did not show up as separate groups; the 12 pages left in Images-PageNumbers are appendix and glossary pages (correctly uncertain) and two story-ending pages under 450 words with no chapter heading. Open from Phase 4's note: auto-fix on CrossReferences-Synthetic and Images-PageNumbers still changes the file on a second run; worth its own look, since the survey's 23-book check does not cover auto-fix stability.

