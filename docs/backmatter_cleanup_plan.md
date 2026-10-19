# Extra Content Removal: Plan

**Status:** Step 1 (detection) and Step 2 (the removal module) built 2026-10-18; see "Step 1 results" and "Step 2 results" at the end. Out of the box only plain advertisements and the Project Gutenberg boilerplate are removed. Steps 3 and 4 are not started.

Jacob wants a way to remove pages that are not part of the main book: advertisements, newsletter sign-ups, teaser chapters, Project Gutenberg boilerplate and similar. He is torn about deleting original content, so the design is a set of switches, almost all of them off.

## Jacob's decisions (2026-10-18)

- The long-term goal for the whole tool is that anything that can be labeled with a confidence is handled automatically. Until that confidence is earned, things are reviewed by a person first.
- Removal is a setting in the config for "things that are not part of the main book".
- Default is off for everything except plain advertisements and Project Gutenberg boilerplate.
- Project Gutenberg boilerplate, ads, newsletter pages, teaser chapters and anything else that does not need to be there all belong in this one group.
- "Also by this author" lists are the author's or publisher's own content and are a different case from ads. They are off by default.

## What exists today

- `gutenberg.py` finds the Project Gutenberg header and license, and `modules/gutenberg_repair.py` removes them. It is on by default, with separate front and back switches (`[gutenberg_repair]` in the config).
- `frontmatter.py` labels every page with a zone (front, back, main), a label and a confidence (high, medium, low). The labels include cover, title page, copyright, publisher info, dedication, epigraph, table of contents, acknowledgments, afterword, about the author, colophon, fiction disclaimer and teaser chapter.
- The Review tab lets a person correct a label. A person's choice always wins over the automatic label (the `overrides` argument).
- There is no label for an advertisement, a newsletter sign-up or an "Also by" page, and no module that removes a whole page based on its label.

## The categories

| Category | How it is found | Default |
|---|---|---|
| Project Gutenberg boilerplate | Already built (marker text and tags) | On |
| Advertisements | New label: store and retailer ads, "Buy now" and "Discover your next great read" pages, promotions for other books with buy links, publisher marketing pages | On, high confidence only |
| Newsletter and social sign-ups | New label: "Sign up for our newsletter", mailing list, "Follow the author" link pages | Off |
| Teaser chapters and previews | Label already exists ("Turn the page for a preview of ...") | Off |
| "Also by" and "Books by" lists | New label: a short page that is mostly a list of other titles | Off |
| About the author | Label already exists | Off |
| Publisher pages | Label already exists (publisher info, colophon) | Off |

Never offered for removal, because they are part of the book: cover, title page, copyright page, dedication, epigraph, table of contents, fiction disclaimer, acknowledgments, afterword, author's note, prologue, epilogue and every chapter.

## Confidence

Each page gets a confidence for its category, the same three levels used everywhere else.

- **High:** a strong fixed phrase or pattern, the page is in the right zone, and the page is short (an ad or sign-up is a few hundred words at most). Removed automatically when its category is switched on.
- **Medium:** looks like the category but one signal is missing. Shown in the Review tab as a suggestion, kept unless the person says remove.
- **Low:** never touched and not suggested.

One setting controls how far automatic goes: `automatic_confidence`, set to `high` to start. When Jacob has seen enough correct medium suggestions, he can change it to `medium` and the Review step for those disappears. This is the path to "everything automatic" without having to rebuild anything later.

## Safety rules

These apply to every category and are not switches.

- Only whole pages (spine files) are removed in the first version. A page that mixes story text with an ad is left alone.
- A page the chapter detector counted as a chapter, or labeled prologue or epilogue, is never removed.
- Cover, title, copyright, dedication and the other "never offered" labels are never removed.
- A page in the main zone is never removed. Ads and sign-ups must be in the front or back zone, and a teaser chapter must come after the last real chapter.
- Length limits: ads and sign-ups must be short. A teaser chapter may be long, but it needs an explicit preview phrase, not just a chapter heading.
- The last remaining page of a book is never removed.
- Removing a page also removes its contents entries (NCX and nav), its guide reference, its manifest entry and any picture that nothing else uses. Links that pointed at it are handled by the existing Dead Link Cleanup, which runs afterward.
- Every removal is listed on the Fix result page with the page's first words and the reason, so Jacob can check what was taken out. The original file is still protected by the existing backup and Replace original flow.

## Settings (config sketch)

```toml
[extra_content_removal]
enabled = true
automatic_confidence = "high"      # "high" or "medium"
remove_gutenberg = true
remove_advertisements = true
remove_newsletter_signups = false
remove_teaser_chapters = false
remove_books_by_author = false
remove_about_author = false
remove_publisher_pages = false
```

The existing `[gutenberg_repair]` section keeps working. `remove_gutenberg` is the same switch shown with the others, so there is one place to look.

## In the program

- **Overview tab:** a collapsed "Extra content" box listing each category found, how many pages, and the confidence. Each category has a checkbox that matches the config and is saved as made, like the other repair checkboxes.
- **Review tab:** the Front/Back Matter dropdown gains Advertisement, Newsletter sign-up and Books by the author. Choosing a different label for a page that was going to be removed keeps it, since a person's choice always wins. Medium-confidence pages appear here as suggestions.
- **Fix result page:** a short "Removed" list as described above.
- **Command line:** the same report, and the same settings from the config file.

## Omnibus splitter

The splitter copies shared back matter (books by, newsletter, about the author) into every split book. If removal is on for those categories, the cleanup runs on each split book afterward and takes them out again. If the switches are off, the copies stay, as decided on 2026-10-16.

## Build order

1. **Detection only.** Add the new labels and their confidence rules to `frontmatter.py`, show them on the Overview and Review tabs, and test on the books in `examples/` and any real books Jacob uploads. No removal yet, so nothing can be lost.
2. **Removal module (built 2026-10-18).** A page-removal repair with the settings above, the safety rules, the Removed list, and Gutenberg's switch brought under the same group. Ads and Gutenberg on, everything else off.
3. **GUI and command line wiring.** The Overview box, the Review suggestions and the report.
4. **Later:** removing an ad block at the bottom of the last chapter or on a copyright page (partial page removal), and removing ad pictures. These need more care and come only after pages are proven.

## Open questions

- Real samples matter more than rules here. Jacob, if you have a few books that carry ads, newsletter pages or teasers, upload them. Detection will be checked against `examples/` first, but those may not have many.
- Is "About the author" a category you want on the list at all, or should it stay with the pages that are never offered? It is on the list, switched off, until you say otherwise.

## Step 1 results (2026-10-18)

Detection only. No page is removed, changed or moved by anything in this step.

**What was added**
- Three new page labels in `frontmatter.py`: `advertisement`, `newsletter sign-up` and `books by the author`. Each page gets one of them with a confidence (high, medium or low), the same as every other label.
- They are looked for only on pages in the front or back zone, never in the main story, and only after the older labels have had their turn. A page that already reads as About the Author, Copyright or a teaser chapter keeps that label even if it also mentions a newsletter.
- Filename hints were added too: `newsletter`, `adcard` (the name one big publisher uses for its "Also by" page), `alsoby`, `booksby` and `advert`.
- **Books by the author:** an "Also by", "Books by" or "Other novels by" heading near the top of a short page is high. A publisher-style opening ("Also available from", "Be sure to read") is medium.
- **Newsletter sign-ups:** a short page that names a newsletter or mailing list and asks the reader to sign up is medium, and high when it is under 150 words and also mentions an email address, a free gift or new releases. A short page of social media links asking the reader to follow the author is medium.
- **Advertisements:** a page of 300 words or fewer is high when it has at least one strong selling phrase (buy now, available wherever books are sold, order your copy, a publisher promoting its own website or catalog, coming soon) and at least one more signal (a retailer name, a web address). One strong phrase on a page of 100 words or fewer is medium. A review request or a web address on its own is not enough.

**Where it shows**
- Overview tab: an "Extra pages" line in the facts at the top, shown only when a book has some (for example "1 advertisement" or "2 "Also by" pages"). It is not red, since nothing is wrong. Teaser chapters are counted there too.
- Overview, Book Structure details: one "Extra Page" line per page with its file and confidence.
- Review tab: the Front/Back Matter dropdown has a new group, "Extra pages (not part of the book)", so a person can correct a label. A person's choice still wins over the automatic label. Pages labeled high no longer show up for review as generic "front matter"; medium ones still do.

**Tested on the 28 books in `examples/` (1,955 pages)**
- Exactly 6 pages changed label, all from generic front or back matter, and all correct: the Pinnacle website promotion in the two Law of the Mountain Man copies (advertisement, high), the two "Books By" pages in Ravaged Land (books by, high), and the two "Be sure to read" lists in the Renshai book (books by, one high and one medium, the medium one being a list mixed with an excerpt). No other page changed label.
- Made-up pages checked as well: a newsletter and free-ebook page, a mailing-list-only page, a social media page, a retail ad, a "coming soon" page and an "Also by" page are each labeled; a review request, an author's note that only mentions a newsletter, a short story paragraph with "I'll buy it" and a dedication are not.
- The Overview and Review tabs were loaded through the real GUI code on two of the books and show the new line and the new dropdown group.

**Limits**
- The examples have few real ads, so the advertisement rules are the least tested. Real books with ads, newsletter pages and teasers are the best next test; upload a few if you have them.
- A page with no confirmed chapter sequence around it (zone unknown) is never given these labels.
- Pictures that are ads (an image with no text) are not detected. That is the later step 4.

## Step 2 results (2026-10-18)

The removal module is built and runs as part of Fix This Book and the command line `repair`. Out of the box it removes plain advertisements and the Project Gutenberg boilerplate and nothing else.

**What was added**
- `modules/extra_content_removal.py`: "Extra Content Removal", placed right after Gutenberg Boilerplate Removal in the repair order. It reads the labels from `frontmatter.py` and decides what to do with them. It never looks at page text for its own purposes except to measure length and read the first words.
- `[extra_content_removal]` in the config, with the switches from the plan: `enabled`, `automatic_confidence` ("high" or "medium"), `remove_gutenberg`, `remove_advertisements` (on), and `remove_newsletter_signups`, `remove_teaser_chapters`, `remove_books_by_author`, `remove_about_author`, `remove_publisher_pages` (all off). The same section is in the generated default config file.
- `remove_gutenberg` is the same switch as Gutenberg Boilerplate Removal being on or off, shown in this group so every removal choice is in one place. Setting it here changes that module. The old `[gutenberg_repair]` settings still work.
- Fix result page: a "Removed from the book" list with the page's file, its first words, its length, the confidence and the reason. It includes Gutenberg removals.
- Repair tab and Overview: "Extra Content Removal" appears with the other repairs, under Text, with its own count and checkbox.
- `omnibus_split.py`: two small changes so the omnibus splitter's page-removal helpers can be reused (the contents-list cleanup no longer needs a title and id, and the picture cleanup can be limited to the pictures of the removed pages). Omnibus behavior is unchanged.

**How a page is judged**
- The label's confidence must reach `automatic_confidence`. A page labeled on the Review tab counts as high. A page you set to anything else (main content, front matter and so on) is kept.
- The page must be before the first chapter or after the last chapter, and the book's chapters must have been confirmed. A teaser chapter must be after the last chapter.
- A page the chapter detector counted as a chapter is never removed, even if it was labeled as an extra page by hand. Neither is the contents page or the last remaining page.
- A file named like a title page, contents, cover, copyright, dedication, epigraph, prologue or epilogue is never removed, whatever its text says. This was added after the "everything on" test showed the older labels sometimes call a title page "publisher info".
- Length limits: ads and sign-ups 400 words, books-by lists and publisher pages 500, about the author 700. A teaser chapter has no limit. An About the Author page must say so near its top, and a publisher page must be at least 40 words.
- The module acts on the first pass only. Later passes analyze the book again without your Review tab choices, so a page you chose to keep could otherwise be judged again.

**What is cleaned up with a page**
Its reading order and manifest entries, its guide reference, its entries in the NCX and nav contents lists (an entry's kept children move up a level), the in-memory contents copy a later nav document is built from, and any picture only that page used. Links that pointed at it are left to Dead Link Cleanup, which runs afterward.

**Tested**
- Defaults across all 28 example books: only the Pinnacle website ad in the two Law of the Mountain Man copies is removed.
- Everything switched on with `automatic_confidence = "medium"`: the two Books By pages and the About the Author page in Ravaged Land and the two lists in the Renshai book are planned. Title pages and contents pages are not, after the name guard above.
- Full repair of Law of the Mountain Man (defaults) and Ravaged Land (everything on): the removed pages' files, pictures and contents entries are gone, nothing refers to a missing file, and epubcheck shows no new kinds of problems compared with the same book repaired with this module off.
- Safety checks: a page set to main content or front matter on the Review tab is kept, a real chapter set to advertisement is never planned, a back page set to advertisement is removed, each switch off keeps its pages, bad `automatic_confidence` values are rejected, and the `[modules]` and `remove_gutenberg` switches agree.
- Through the real GUI code: upload, Overview and Fix This Book on Law of the Mountain Man, with the Removed list shown on the result page.

**Limits and open points**
- The Overview "Extra content" box with checkboxes, Review suggestions for medium pages, and the command line report are step 3. Until then the switches are in the config file only.
- The omnibus splitter still copies shared back matter into each split book, whatever these switches say. Running the cleanup on each split book is part of step 3.
- Pre-existing label mistakes (a title page called "publisher info", a contents page called "about the author") are why those categories stay off by default and carry extra guards.
- A book repaired with this module and with it off stops after the same 5 repair passes in Ravaged Land. That is existing behavior and unrelated to this step.
