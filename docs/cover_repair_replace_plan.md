# Cover Image Repair & Replacement -- Planning Doc

**Status:** Phase 1 done (2026-09-01) -- repair and replacement both
shipped, in a narrower, more opinionated form than this doc originally
sketched. See "Done: Phase 1" near the end for exactly what was built
and why it diverged from a few of this doc's original recommendations
(written by another AI without the rest of this project's context,
per Jacob).

## The problem

The project already has a `cover` module that can analyze an EPUB's cover declarations, but it currently stops at reporting problems. We want to expand this into a complete cover-handling system with two related capabilities:

1. **Cover mapping/repair** — determine which image is actually intended to be the cover and make the EPUB's cover declarations point to it correctly.
2. **Cover replacement** — allow the user to explicitly provide a new image and make that image the book's cover.

These are related, but they should be treated as separate operations. Repair should be conservative and based on evidence already present in the EPUB. Replacement is an explicit user instruction and therefore does not need to guess which artwork the user wants.

## What already exists

### Cover analysis

The existing `ebook_fix.cover` module already understands the two major cover conventions:

- EPUB 2: `<meta name="cover" content="...">`
- EPUB 3: manifest items using `properties="cover-image"`

It can currently:

- Find EPUB 2 cover metadata.
- Resolve the referenced manifest item.
- Detect dangling cover IDs.
- Check whether the selected manifest item is an image.
- Check whether the referenced image exists in the archive.
- Find EPUB 3 `cover-image` declarations.
- Detect disagreement between EPUB 2 and EPUB 3 cover declarations.

This gives us a good starting point for the repair side of the feature.

### Existing image handling

The project already has image-analysis and image-repair functionality for ordinary EPUB images.

That functionality deals primarily with:

- Missing image files.
- Broken image references.
- Image resources declared in the manifest.
- Broken `<img>` elements in XHTML.

Cover handling should build on the existing image infrastructure where appropriate, but should remain a separate concern.

A cover is more than just another image: it is a package-level resource with metadata and declaration semantics.

### Existing EPUB writer

The existing writer already has mechanisms for:

- Replacing/additional files through `new_files`.
- Removing files through `removed_files`.
- Modifying the OPF.
- Writing the resulting EPUB back to disk.

Therefore, this feature should reuse the existing writer rather than introduce another EPUB-writing mechanism.

## Repair vs. replacement

These should be explicitly separated in the design.

### Cover mapping/repair

Repair asks:

> "Given the images and metadata already present in this EPUB, which image is supposed to be the cover?"

Examples of problems that may eventually be repairable:

- EPUB 3 declares a cover but EPUB 2 metadata is missing.
- EPUB 2 declares a cover but EPUB 3 metadata is missing.
- Both declarations exist but disagree.
- A cover declaration points to a missing image.
- A cover declaration points to an invalid/non-image resource.
- A cover page exists but its image reference is broken.
- The actual cover image exists but the package metadata does not identify it correctly.

The important principle is:

**Automatic repair should only happen when the intended cover can be determined with reasonable confidence.**

If the EPUB contains several plausible cover candidates, the system should report the problem rather than blindly choosing one.

### Cover replacement

Replacement asks:

> "Use this specific image as the new cover."

This is deterministic because the user has supplied the desired image.

The preferred design should be to **replace the existing cover image in place whenever possible**.

For example, if the existing EPUB contains:

```text
Images/cover.jpg
```

and its manifest ID is:

```text
cover-image
```

then replacing the bytes of `Images/cover.jpg` is preferable to creating:

```text
Images/new-cover.jpg
```

and changing every reference.

Keeping the existing path and manifest ID has several advantages:

- Existing XHTML cover pages continue to reference the correct file.
- Existing manifest references remain valid.
- EPUB 2 and EPUB 3 metadata can continue pointing to the same resource.
- Fewer files need to be changed.
- There is less opportunity to accidentally leave stale references behind.
- The resulting EPUB has a smaller and simpler change set.

If there is no usable existing cover resource, then the system can create a new image resource and update the relevant manifest/declarations.

## Cover candidate selection

The most difficult part of automatic repair is deciding what should be considered the cover when the EPUB is damaged.

The implementation should establish an explicit hierarchy of evidence rather than relying on arbitrary heuristics.

A possible starting point:

1. A valid EPUB 3 `properties="cover-image"` declaration.
2. A valid EPUB 2 `<meta name="cover">` declaration.
3. Agreement between both declarations.
4. A dedicated cover XHTML/page whose displayed image resolves to a valid image.
5. Strong filename/path evidence such as `cover.jpg`, `cover.png`, etc.
6. Other image heuristics, if real-world testing demonstrates that they are useful.

This hierarchy is only a starting point and should be validated against real EPUBs before implementation.

A key distinction should be maintained:

**Analysis can report uncertainty; repair should not hide uncertainty.**

For example:

```text
Analysis:
  Possible cover candidates:
    Images/cover.jpg
    Images/front-matter.jpg

Repair:
  Unable to automatically select a cover.
```

is preferable to silently selecting the wrong image.

## Proposed cover architecture

The likely architecture is a dedicated cover-repair module alongside the existing cover analysis.

Possible responsibilities:

### `cover.py`

Continue to provide cover analysis and potentially add helper functions for:

- Finding cover declarations.
- Resolving cover manifest items.
- Determining the current canonical cover.
- Identifying repairable cover states.

### `modules/cover.py`

Handle actual modifications:

- Repair cover declarations.
- Synchronize EPUB 2/EPUB 3 cover metadata.
- Replace the cover image.
- Add a new cover resource when necessary.
- Remove or correct stale competing cover declarations.

The repair module should consume the results of `analysis.cover` rather than independently rescanning the EPUB.

### Existing writer

Use the existing mechanisms:

```text
Book.new_files
Book.removed_files
Book.opf_document
Book.opf_modified
```

No new EPUB-writing system should be created unless implementation reveals a genuine limitation in the existing writer.

## Replacement workflow

The eventual replacement operation should roughly follow this process:

```text
Load EPUB
    ↓
Analyze cover
    ↓
Validate supplied replacement image
    ↓
Find current cover resource
    ↓
Does a usable cover resource exist?
    ├── Yes → replace its bytes
    │
    └── No → create a new cover resource
                 ↓
Update manifest/declarations
    ↓
Remove/correct stale cover declarations
    ↓
Write EPUB
    ↓
Reopen resulting EPUB
    ↓
Analyze again
    ↓
Verify replacement is now the cover
```

The replacement operation should not modify unrelated book content.

## What should and should not be changed

A normal cover replacement should ideally change only:

- The cover image bytes.
- OPF metadata/declarations when necessary.
- The manifest when a new image resource must be created.
- A cover XHTML page only if there is a genuine need to repair its image reference.

It should **not** modify:

- Chapter text.
- Chapter images.
- CSS.
- Unrelated metadata.
- Other images.
- Reading order.
- Navigation.
- Other package resources.

This is another reason to prefer replacing an existing cover resource in place.

## EPUB 2 and EPUB 3 compatibility

The implementation should account for both standards.

When possible, a repaired/replaced EPUB should have internally consistent declarations.

For example, if both conventions are present:

```xml
<meta name="cover" content="cover-image"/>
```

and:

```xml
<item id="cover-image"
      href="Images/cover.jpg"
      media-type="image/jpeg"
      properties="cover-image"/>
```

both should refer to the same image resource.

If the EPUB only uses one convention, we need to decide whether repair should add the other declaration or simply repair the convention already being used.

This should be resolved during the planning/testing phase rather than assumed.

## CLI design

The exact CLI syntax is not decided yet.

Possible approaches include:

```text
ebook-fixer cover book.epub
```

for cover analysis/repair, with:

```text
ebook-fixer cover book.epub --replace new-cover.jpg
```

for explicit replacement.

Another possibility is a dedicated command:

```text
ebook-fixer replace-cover book.epub new-cover.jpg
```

The underlying cover functionality should not depend on which CLI design is eventually selected. A future GUI should be able to call the same functionality directly.

CLI behavior should eventually follow the project's existing conventions for:

- Input files.
- Output files.
- `--overwrite`.
- Error handling.
- Reporting what was changed.

## Phased implementation plan

### Phase 0 -- Real-world cover survey

Before writing code, collect a representative set of EPUBs containing different cover structures.

Include examples such as:

- Clean EPUB 2.
- Clean EPUB 3.
- EPUB 2 + EPUB 3 declarations.
- Conflicting declarations.
- Missing declarations.
- Dangling cover IDs.
- Missing cover image files.
- Cover XHTML pages.
- Calibre-generated EPUBs.
- Deliberately damaged EPUBs.
- EPUBs with multiple plausible cover images.

The purpose of this phase is to determine which situations can safely be repaired automatically.

### Phase 1 -- Strengthen cover analysis

Review and improve the existing `cover.py` against the sample EPUB collection.

The analysis should be able to clearly distinguish states such as:

- Valid.
- Missing.
- Dangling.
- Invalid.
- Conflicting.
- Ambiguous.

The analysis result should contain enough information for the repair module to act without performing a second independent scan.

Add tests for the different declaration and path structures.

### Phase 2 -- Cover declaration repair

Add the cover repair module.

Initially support only high-confidence repairs.

Examples:

- Synchronizing missing EPUB 2/EPUB 3 declarations.
- Correcting a declaration when the intended existing cover is unambiguous.
- Repairing references to a known existing cover resource.

The operation should be **idempotent**:

```text
repair
repair
repair
```

should produce the same result as running repair once.

Ambiguous cases should be reported rather than guessed.

### Phase 3 -- Explicit cover replacement

Add the ability to supply a new image.

The implementation should:

1. Validate the supplied image.
2. Determine the existing cover resource.
3. Prefer replacing the existing resource in place.
4. Create a new resource when necessary.
5. Update cover declarations.
6. Ensure the resulting manifest is internally consistent.
7. Write using the existing writer.
8. Reopen the resulting EPUB.
9. Re-analyze the cover.
10. Confirm that the new image is correctly identified as the cover.

### Phase 4 -- CLI integration

Add the selected CLI interface.

Include:

- Replacement image argument.
- Output handling.
- `--overwrite`.
- Clear success/error reporting.
- Useful reporting of what was changed.

Keep the core functionality independent of the CLI.

### Phase 5 -- Regression and compatibility testing

Test repaired and replaced EPUBs with:

- The project's existing regression suite.
- The real-world sample collection.
- Multiple EPUB readers/tools where practical.

Verify that:

- The new cover is recognized.
- The cover page still works.
- The image exists at the expected path.
- Cover metadata is consistent.
- No stale cover declarations remain.
- No unrelated files were modified.
- The resulting EPUB remains valid.

## Configuration and auto-fix

Explicit cover replacement should **never** happen automatically.

There is no sensible default replacement image.

Automatic cover declaration repair may eventually be allowed through the normal repair/auto-fix process, but only for high-confidence situations.

For ambiguous cases:

```text
Analyze → report problem
```

rather than:

```text
Analyze → guess → modify book
```

Cover-specific configuration should also remain separate from ordinary image repair so that enabling generic image repair does not unexpectedly change cover metadata.

## Relationship to existing image repair

The existing image modules should remain responsible for generic image integrity.

The conceptual boundary should be:

```text
images.py
    "Is this image resource/reference intact?"

cover.py
    "Which image is the cover, and are the cover declarations correct?"

modules/images.py
    Repair ordinary broken image references.

modules/cover.py
    Repair cover declarations and replace the explicit cover image.

writer.py
    Write the resulting EPUB.
```

Some low-level helpers may eventually be shared, particularly around archive-path resolution or image validation. Those should only be extracted when there is actual duplication.

The cover feature should not turn `images.py` into a catch-all image manipulation module.

## Non-goals

This feature is **not** intended to:

- Generate new cover artwork.
- AI-enhance or restore cover artwork.
- Redesign cover pages.
- Clean up unrelated EPUB images.
- Delete old images merely because they aren't currently the cover.
- Guess between multiple plausible covers during automatic repair.
- Modify unrelated book content.

Image restoration/enhancement can remain a separate operation. This feature's responsibility is to make sure the desired image is correctly installed and identified as the EPUB's cover.

## Open questions

Before implementation, we should decide:

1. When valid EPUB 2 and EPUB 3 declarations disagree, should EPUB 3 automatically win?
2. How much weight should a dedicated `cover.xhtml` page have when determining the intended cover?
3. Should replacement always preserve the existing manifest ID and href when possible?
4. What image formats should replacement accept?
5. Should the supplied image format be preserved, or should covers be normalized to JPEG/PNG?
6. Should replacement enforce image dimensions or file-size limits?
7. If a new cover resource is necessary, what naming convention should be used?
8. What should happen when multiple manifest items have `properties="cover-image"`?
9. Should replacement automatically remove competing `cover-image` properties?
10. Should repair ever add a missing EPUB 2 or EPUB 3 declaration, or only repair declarations that already exist?
11. How should ambiguous cover candidates be presented to the user?
12. Should cover replacement be its own CLI command or a mode of the `cover` command?

These questions should be resolved using actual EPUB samples wherever possible rather than purely theoretical assumptions.

## Testing strategy

### Unit tests

At minimum:

- EPUB 2-only valid cover.
- EPUB 3-only valid cover.
- Both declarations agreeing.
- Both declarations disagreeing.
- Dangling EPUB 2 cover ID.
- Missing cover image.
- Non-image cover resource.
- Multiple `cover-image` declarations.
- Nested OPF/image paths.
- Existing cover replacement.
- Replacement requiring a new resource.
- Invalid replacement image.
- Repeated replacement.
- Repeated repair.

### Archive-level tests

After replacement, verify:

- Replacement bytes exist at the expected path.
- Old bytes are replaced when performing an in-place replacement.
- OPF references an existing image.
- No unrelated archive entries changed.
- EPUB structure remains valid.

### End-to-end tests

For each representative EPUB:

1. Analyze the original.
2. Repair it if appropriate.
3. Analyze the repaired version.
4. Replace the cover.
5. Reopen the resulting EPUB from scratch.
6. Analyze it again.
7. Verify the new image is identified as the cover.
8. Verify any cover page still displays the new image.
9. Verify unrelated content remains unchanged.
10. Run the complete regression suite.

## Definition of done

The feature should ultimately be considered complete when:

- The project can reliably identify the cover in common EPUB 2 and EPUB 3 structures.
- Cover declaration inconsistencies can be repaired when the intended cover is unambiguous.
- Ambiguous cases are reported rather than guessed.
- A user can provide a replacement image and make it the EPUB's cover.
- Existing cover resources are replaced in place whenever practical.
- Newly created cover resources are correctly added to the manifest.
- EPUB 2/EPUB 3 cover declarations are kept consistent.
- The resulting EPUB can be reopened and independently re-analyzed successfully.
- Existing image repair functionality continues to work independently.
- Existing regression tests remain passing.

## Continuity note

This document is the source of truth for the cover-image feature's design and progress.

Update it as decisions are made and implementation phases are completed. Keep implementation details here only when they represent an intentional architectural decision or something future work needs to remember.

## Done: Phase 1 -- repair and replacement (2026-09-01)

Built both halves this session, resolving this doc's open questions
per Jacob's direct calls rather than the doc's own defaults (written
by another AI without the rest of the project in front of it):

- **Filename standardization is deliberate, not just "preserve
  existing when possible."** Every verified cover gets renamed to
  ebook_fix's own standard `cover.<ext>` (extension matches the
  image's real format, e.g. `cover.jpg`/`cover.png` -- never
  converted), the opposite of this doc's original "keep existing name
  to minimize changes" preference. Jacob wants consistency across his
  whole library, not per-book preservation.
- **Two separate commands**, not one `cover` command with a
  `--replace` mode as this doc's "Option A" suggested: cover repair
  lives inside the normal `repair` pipeline as a new module
  (`modules/cover_repair.py`), and `replace-cover` is its own
  standalone command, matching how `series` works.
- **Repair only ever acts on exactly one clearly-resolved, existing,
  valid cover image** -- the doc's own "report don't guess" principle,
  kept intentionally narrow rather than building out the
  multi-candidate scoring/tiering system the doc sketched (first
  image, largest image, filename heuristics, etc.). If a book has no
  declared cover, or multiple different items both claiming
  properties="cover-image", repair leaves it alone; only the two
  existing conventions disagreeing about which valid item is the
  cover gets auto-resolved (EPUB3 wins, matching what
  `ebook_fix.cover`'s own analysis already picks). Revisit the wider
  candidate-scoring idea later if Jacob hits real books this misses.
- **Replacement accepts a local file path or a URL**, downloaded via
  the standard library (`urllib`, no new dependency), sniffed from
  its actual bytes rather than trusting a filename extension or
  server-supplied Content-Type -- this doc didn't originally cover
  URL input at all. A 25 MB download cap and clear error messages
  (unreachable URL, non-image content, missing local file) prevent it
  from hanging or crashing on a bad input.
- **Replacement never guesses**: runs even on a book with no cover,
  or with conflicting/ambiguous declarations repair would just
  report, since the image is explicit. Replaces in place (same or
  different filename/format) when a usable existing cover is found;
  creates a brand-new manifest entry, reusing whatever folder the
  book's other images already live in, when there isn't one.
- The low-level OPF-editing operations (`sync_declarations`,
  `swap_filename`, `find_manifest_item_element`,
  `rewrite_chapter_image_references`, `create_cover_item`,
  `sniff_image_media_type`) live in `ebook_fix.cover` itself, shared
  between `modules/cover_repair.py` and `Engine.replace_cover` rather
  than duplicated -- this is real cover-domain logic, not the kind of
  trivial helper this project otherwise duplicates freely per module.
- Tested: repair standardizes an EPUB2-only `.jpeg` cover to `.jpg`
  and adds the missing EPUB3 declaration, confirmed idempotent
  (second pass finds zero issues) and clean across all 10 sample
  books. Replacement tested against a real existing cover (bytes,
  media-type, and filename all update correctly, chapter/manifest
  references follow), a synthetic book with no cover declared at all
  (creates one from scratch, verified via `analyze` afterward), a
  live download over a local loopback HTTP server, and all three
  error paths (unreachable URL, non-image file, missing local file).
  Ran clean across all 10 sample books both ways, and the resulting
  file still passes full `validate` integrity checks.
- README updated with new "Cover Repair" behavior under the existing
  Repair section, plus a new "Replace Cover" section and cheat-sheet
  entries for `replace-cover`.

Not built, left for later if it turns out to matter: image
dimension/size-quality gating, automatic handling of multiple
competing `properties="cover-image"` items, and the broader
candidate-scoring/tiering system this doc originally sketched for
identifying a cover with no declaration at all.