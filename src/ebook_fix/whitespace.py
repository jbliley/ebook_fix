"""
ebook_fix.whitespace

DOM-aware whitespace analysis, replacing the old flat regex pass in
ebook_fix.modules.whitespace. Analyzes the whole book in the same
single pass the rest of the analyzer runs, so ebook_fix.modules.whitespace
doesn't have to scan the book itself -- same pattern as images.py and
paragraphs.py (see docs/analysis_first_migration_plan.md, Phase 5).

Why this needed a real rewrite instead of a quick swap-in
-----------------------------------------------------------
The old module ran `for element in root.iter(): if skip(element): continue`
against every element's own .text/.tail. That has two real problems:

1. It only skips a protected element's OWN text -- lxml's `.iter()`
   still yields every element NESTED inside a <pre>/<code>/etc, and
   those weren't skipped at all. Text inside a <span> sitting inside a
   <pre> block got whitespace-collapsed anyway.
2. The "missing space after punctuation" regex, `([,.;:!?])([A-Za-z])`,
   fires on anything that looks like punctuation-then-letter with no
   regard for what it's actually punctuating -- so "3.14", "U.S.A.",
   "example.com", and "Mr.Smith" all got mangled.

This module fixes both: a real recursive walker tracks whether it's
currently inside a protected subtree at ANY depth (not just the
current element), and the punctuation-spacing rule is narrowed to
require the shape of an actual missed sentence break, with an explicit
abbreviation guard -- see MISSING_SPACE_AFTER_SENTENCE_RE and
ABBREVIATIONS below. When a rule is ambiguous, this module leaves the
text alone rather than risk corrupting something that was fine.

What gets analyzed, per text/tail node
----------------------------------------
- Leading indentation (spaces/tabs at the start of the node)
- Trailing indentation (spaces/tabs at the end of the node)
- Repeated internal whitespace (collapses to one space)
- Tabs (converted to spaces as part of the above)
- Space before punctuation (" ,text" -> ",text"; also applies to a
  stray space directly before an ellipsis character, "…")
- Missing space after sentence-ending punctuation (narrow rule, see above)
- Whitespace-only nodes (pure formatting whitespace standing alone
  between elements -- always collapses to a single space, never
  deleted outright; see "Standalone whitespace-only nodes" below)
- Protected nodes skipped (inside pre/code/style/script/svg/math --
  counted for visibility, never touched)

Inline-element spacing
------------------------
A whitespace-only tail sitting between two inline elements ("<b>Hello</b>
<i>world</i>") is NOT the same as one sitting between two block elements
("<p>...</p>\\n  <p>...</p>") -- collapsing the first to nothing would
glue "Hello" and "world" together with no space at all. See
`_leading_glue_sensitive`/`_trailing_glue_sensitive` below: leading/
trailing padding INSIDE a node that also has real text collapses to a
single space, rather than being stripped to nothing, on whichever side
touches an inline neighbor -- each side is judged independently, since
a node can easily have a block boundary on one side and an inline
neighbor on the other.

Standalone whitespace-only nodes -- a node with nothing BUT whitespace
in it -- are a different case, and deliberately handled more
conservatively: they always collapse to a single space, never
disappear entirely, even between two block-level tags. It's tempting
to assume a block/block boundary is always safe to fully delete, since
the block tags themselves already force a visual line break either
way. That's true for how a reading system RENDERS the page -- but it
isn't true for anything that reads the book's underlying text content
instead of its rendering (accessibility tools, search indexing, or
even this project's own analysis code doing `"".join(tree.itertext())`
to count words). Some real-world conversions (see BrokenSentences.epub
in examples/, a PDF-to-EPUB conversion that puts one printed line per
`<p>` tag) rely on exactly that inter-tag whitespace as the only thing
separating two words that are actually mid-sentence, not two real
paragraphs. Deleting it outright glues them into one word with zero
warning. Collapsing to a single space instead costs nothing -- it's
invisible in any rendering that already breaks the line at that block
boundary -- and it's the only choice that's safe regardless of which
case it turns out to be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

# ---------------------------------------------------------------------
# Tag classification
# ---------------------------------------------------------------------

# Whitespace inside these (at any nesting depth) is left completely
# untouched -- it's either meaningful verbatim content (pre/code),
# governed by a different syntax entirely (svg/math), or not prose at
# all (style/script).
PROTECTED_TAGS = {"pre", "code", "style", "script", "svg", "math"}

# Elements that render inline, next to their neighbors with no
# implied line break. Whitespace touching one of these can't just be
# deleted -- see module docstring, "Inline-element spacing".
INLINE_TAGS = {
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "data", "dfn", "em",
    "i", "img", "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp",
    "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
}


def _local_tag(element) -> str:
    """Namespace-safe lowercase tag name. Returns "" for comments,
    processing instructions, and anything else lxml hands back whose
    .tag isn't a plain string."""
    if not isinstance(element.tag, str):
        return ""
    return etree.QName(element).localname.lower()


# ---------------------------------------------------------------------
# Text normalization rules
# ---------------------------------------------------------------------

# Don't strip space if it's a period followed by a letter or digit (e.g., " .44", " .com", " .py")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+(?!\.[A-Za-z0-9])([,.;:!?\u2026])")

# Common abbreviations/titles/initials that legitimately end in a
# period with no following space intended, or that would otherwise
# false-positive the sentence-boundary rule below. Checked against the
# word immediately before the period, case-insensitively.
ABBREVIATIONS = {
    "mr", "mrs", "ms", "mx", "dr", "prof", "st", "jr", "sr", "rev",
    "gen", "col", "capt", "cpt", "lt", "sgt", "hon", "esq",
    "vs", "etc", "inc", "ltd", "co", "corp", "no", "vol", "ch", "pp",
    "ed", "eds", "al", "cf", "fig", "approx", "dept", "govt", "assn",
    "univ", "et",
}

# The actual "missed a space" shape this rule targets: a word of two
# or more letters ending in . / ! / ?, immediately followed by a
# capitalized word starting the next sentence, with no space at all in
# between. The {2,} minimum is doing real work here, not just
# tightening the match: it means a single-letter run right before the
# period ("J.Smith", every letter of "U.S.A.") can never match this
# rule at all, since there's no 2+-letter word there to capture -- an
# initial or acronym segment is excluded by construction, without
# needing a special-case list of them.
MISSING_SPACE_AFTER_SENTENCE_RE = re.compile(
    r"([A-Za-z]{2,})([.!?])([A-Z][a-z])"
)

# Comma/semicolon/colon glued directly to the next word ("hello,world")
# with no space at all -- almost always a straightforward missed space
# rather than an abbreviation or a number (thousands-separator commas
# and time/ratio colons are followed by a digit, not a letter, so
# requiring a letter on both sides already excludes those).
MISSING_SPACE_AFTER_COMMA_RE = re.compile(r"([A-Za-z]),([A-Za-z])")
MISSING_SPACE_AFTER_SEMICOLON_RE = re.compile(r"([A-Za-z]);([A-Za-z])")
MISSING_SPACE_AFTER_COLON_RE = re.compile(r"([A-Za-z]):([A-Za-z])")


def _is_abbreviation(word: str) -> bool:
    return word.strip().lower() in ABBREVIATIONS


def _fix_missing_space_after_sentence(text: str) -> tuple[str, int]:
    """Insert a space after . / ! / ? where a new capitalized sentence
    clearly starts with no space at all -- skipping anything that
    looks like an abbreviation/initial. Returns (new_text, count)."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        word, punct, next_start = m.group(1), m.group(2), m.group(3)
        if punct == "." and _is_abbreviation(word):
            return m.group(0)
        count += 1
        return f"{word}{punct} {next_start}"

    return MISSING_SPACE_AFTER_SENTENCE_RE.sub(repl, text), count


def _fix_missing_space_after_mid_punct(text: str) -> tuple[str, int]:
    # Run each punctuation mark's regex separately so overlapping
    # matches (e.g. "a,b;c") are each still counted individually.
    text, n1 = _sub_with_count(MISSING_SPACE_AFTER_COMMA_RE, r"\1, \2", text)
    text, n2 = _sub_with_count(MISSING_SPACE_AFTER_SEMICOLON_RE, r"\1; \2", text)
    text, n3 = _sub_with_count(MISSING_SPACE_AFTER_COLON_RE, r"\1: \2", text)
    return text, n1 + n2 + n3


def _sub_with_count(pattern: re.Pattern, repl: str, text: str) -> tuple[str, int]:
    result, count = pattern.subn(repl, text)
    return result, count


@dataclass(frozen=True)
class NormalizationRules:
    """Which categories of fix are active. Passed through to
    normalize_fragment so ebook_fix.modules.whitespace can recompute
    the exact text a config with some categories turned off would
    produce, from the same `before` string the analyzer already
    captured -- no need to re-walk the DOM to honor a config toggle."""
    fix_leading_indent: bool = True
    fix_trailing_indent: bool = True
    fix_repeated_whitespace: bool = True
    fix_tabs: bool = True
    fix_space_before_punct: bool = True
    fix_missing_sentence_space: bool = True


ALL_RULES = NormalizationRules()


@dataclass
class NormalizeResult:
    text: str
    leading_indent: bool = False
    trailing_indent: bool = False
    repeated_whitespace: bool = False
    tabs_converted: bool = False
    space_before_punct: bool = False
    missing_sentence_space: bool = False
    changed: bool = False


def normalize_fragment(
    text: str,
    leading_glue: bool,
    trailing_glue: bool,
    rules: NormalizationRules = ALL_RULES,
) -> NormalizeResult:
    """
    Pure, unit-testable normalization of one piece of text (an
    element's .text or .tail). Does not know or care where in the DOM
    this text came from -- leading_glue/trailing_glue tell it,
    independently for each end, whether whitespace there has to
    collapse to a single space (true, when an inline element sits on
    that side) rather than being stripped away entirely (false, safe
    when that side borders a block-level boundary). The two ends need
    independent answers: text can perfectly well have a block
    boundary on one side and an inline neighbor on the other (e.g. a
    paragraph's own text running right up against an inline <b> at
    its end).

    `rules` gates each category of fix independently (see
    NormalizationRules) -- the analyzer always calls this with every
    rule on, to find everything there is to find; the repair module
    calls it again per-issue with whatever the config currently has
    enabled, to decide what to actually write back.

    fix_tabs governs whether '\\t' counts as whitespace at all here --
    off means tab characters are invisible to every other rule below
    (never stripped, never collapsed, never counted as "repeated"),
    not just left unconverted to a space.
    """
    result = NormalizeResult(text=text)
    if not text:
        return result

    ws_chars = " \t\r\n" if rules.fix_tabs else " \r\n"
    leading_re = re.compile(f"^[{ws_chars}]+")
    trailing_re = re.compile(f"[{ws_chars}]+$")
    internal_re = re.compile(f"[{ws_chars}]{{2,}}")

    working = text

    if rules.fix_tabs and "\t" in working:
        result.tabs_converted = True
        # Fold every tab to a plain space up front so the
        # leading/trailing/internal steps below can treat it exactly
        # like any other whitespace character from here on.
        working = working.replace("\t", " ")

    if rules.fix_leading_indent:
        m = leading_re.match(working)
        if m:
            result.leading_indent = True
            working = (" " if leading_glue else "") + working[m.end():]

    if rules.fix_trailing_indent:
        m = trailing_re.search(working)
        if m:
            result.trailing_indent = True
            working = working[: m.start()] + (" " if trailing_glue else "")

    if rules.fix_repeated_whitespace:
        collapsed = internal_re.sub(" ", working)
        if collapsed != working:
            result.repeated_whitespace = True
        working = collapsed

    if rules.fix_space_before_punct:
        before = working
        working = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", working)
        if working != before:
            result.space_before_punct = True

    if rules.fix_missing_sentence_space:
        before = working
        working, _n1 = _fix_missing_space_after_sentence(working)
        working, _n2 = _fix_missing_space_after_mid_punct(working)
        if working != before:
            result.missing_sentence_space = True

    result.text = working
    result.changed = working != text
    return result


def is_whitespace_only(text: str) -> bool:
    return bool(text) and text.strip() == ""


# ---------------------------------------------------------------------
# Recursive tree walker
# ---------------------------------------------------------------------

def iter_text_slots(element, protected: bool = False):
    """
    Depth-first walk yielding one entry per text/tail slot in document
    order: (host_element, attr, text, protected). `attr` is "text" or
    "tail"; `text` is the current string value (never None -- empty
    slots are skipped); `protected` is True if this slot sits inside a
    pre/code/style/script/svg/math subtree at any depth.

    This is the "safe recursive tree walker" the old flat `root.iter()`
    pass didn't have: protection is tracked going *down* the tree, so
    an element nested inside a protected ancestor is protected too,
    not just the ancestor's own text.
    """
    tag = _local_tag(element)
    protected_here = protected or tag in PROTECTED_TAGS

    if element.text:
        yield element, "text", element.text, protected_here

    for child in element:
        if not isinstance(child.tag, str):
            # Comment / processing instruction: no .text slot of ours
            # to normalize, but its .tail still belongs to this level.
            if child.tail:
                yield child, "tail", child.tail, protected_here
            continue
        yield from iter_text_slots(child, protected_here)
        if child.tail:
            yield child, "tail", child.tail, protected_here


def _leading_glue_sensitive(host, attr: str) -> bool:
    """
    True if whatever sits immediately BEFORE this text/tail slot is
    inline content, with no block-level line break in between --
    meaning leading whitespace here has to collapse to a single space
    rather than disappear. See module docstring, "Inline-element
    spacing".
    """
    if attr == "text":
        # Before host.text is host's own opening tag. A block-level
        # host is already a line break there, regardless of what's
        # further back; an inline host isn't, so treat it as
        # sensitive (a safe over-approximation -- worst case is one
        # harmless extra leading space, never a glued word).
        return _local_tag(host) in INLINE_TAGS
    # attr == "tail": before host.tail is host itself, closing. If
    # host is inline, its own content butts right up against this
    # tail with nothing to break the line.
    return _local_tag(host) in INLINE_TAGS


def _trailing_glue_sensitive(host, attr: str) -> bool:
    """
    True if whatever sits immediately AFTER this text/tail slot is
    inline content, with no block-level line break in between.
    """
    if attr == "text":
        children = [c for c in host if isinstance(c.tag, str)]
        if children:
            return _local_tag(children[0]) in INLINE_TAGS
        # No children: nothing follows within host itself. What comes
        # after host closes is host's own tail slot's concern (its
        # leading edge), not this one's -- treating it as sensitive
        # here too would just duplicate the same space from both sides.
        return False
    # attr == "tail": after host.tail is host's next sibling, if any.
    nxt = host.getnext()
    if nxt is not None and isinstance(nxt.tag, str):
        return _local_tag(nxt) in INLINE_TAGS
    # No next sibling: after this tail is the parent closing -- the
    # parent's own tail slot governs whatever comes after that.
    return False


# ---------------------------------------------------------------------
# Report data model
# ---------------------------------------------------------------------

@dataclass
class WhitespaceIssue:
    href: str = ""
    element: object = None    # live host element; not saved to the JSON cache
    attr: str = ""             # "text" or "tail"
    category: str = ""
    before: str = ""
    after: str = ""
    leading_glue: bool = False
    trailing_glue: bool = False
    is_whitespace_only: bool = False   # collapsed to a single space, not a mix of real text + padding


@dataclass
class ChapterWhitespaceSummary:
    href: str = ""
    leading_indent_count: int = 0
    trailing_indent_count: int = 0
    repeated_whitespace_count: int = 0
    tabs_converted_count: int = 0
    space_before_punct_count: int = 0
    missing_sentence_space_count: int = 0
    whitespace_only_node_count: int = 0
    protected_nodes_skipped_count: int = 0
    issues: list = field(default_factory=list)   # [WhitespaceIssue], live refs -- see serialize.py


@dataclass
class BookWhitespaceSummary:
    chapters: list = field(default_factory=list)   # [ChapterWhitespaceSummary]

    def _total(self, field_name: str) -> int:
        return sum(getattr(c, field_name) for c in self.chapters)

    @property
    def leading_indent_count(self) -> int:
        return self._total("leading_indent_count")

    @property
    def trailing_indent_count(self) -> int:
        return self._total("trailing_indent_count")

    @property
    def repeated_whitespace_count(self) -> int:
        return self._total("repeated_whitespace_count")

    @property
    def tabs_converted_count(self) -> int:
        return self._total("tabs_converted_count")

    @property
    def space_before_punct_count(self) -> int:
        return self._total("space_before_punct_count")

    @property
    def missing_sentence_space_count(self) -> int:
        return self._total("missing_sentence_space_count")

    @property
    def whitespace_only_node_count(self) -> int:
        return self._total("whitespace_only_node_count")

    @property
    def protected_nodes_skipped_count(self) -> int:
        return self._total("protected_nodes_skipped_count")

    @property
    def total_issue_count(self) -> int:
        return sum(len(c.issues) for c in self.chapters)

    @property
    def chapters_with_issues(self) -> list:
        return [c.href for c in self.chapters if c.issues]


# ---------------------------------------------------------------------
# Book-level entry point
# ---------------------------------------------------------------------

def analyze_chapter_whitespace(href: str, tree) -> ChapterWhitespaceSummary:
    summary = ChapterWhitespaceSummary(href=href)
    if tree is None:
        return summary

    for host, attr, text, protected in iter_text_slots(tree):
        if protected:
            # Only worth counting if there was actually something in
            # here that normalization would have touched -- otherwise
            # every protected node in the book (most of which are
            # perfectly fine) would inflate this count for no reason.
            probe = normalize_fragment(text, leading_glue=True, trailing_glue=True)
            if probe.changed or is_whitespace_only(text):
                summary.protected_nodes_skipped_count += 1
            continue

        leading_glue = _leading_glue_sensitive(host, attr)
        trailing_glue = _trailing_glue_sensitive(host, attr)

        if is_whitespace_only(text):
            summary.whitespace_only_node_count += 1
            # Always collapse to a single space, never delete outright
            # -- see module docstring, "Standalone whitespace-only
            # nodes". Safe in every case; deleting is only safe in
            # some, and this module doesn't take that risk.
            if text != " ":
                summary.issues.append(
                    WhitespaceIssue(
                        href=href, element=host, attr=attr,
                        category="Whitespace-only node",
                        before=text, after=" ",
                        is_whitespace_only=True,
                    )
                )
            continue

        result = normalize_fragment(text, leading_glue=leading_glue, trailing_glue=trailing_glue)
        if not result.changed:
            continue

        if result.leading_indent:
            summary.leading_indent_count += 1
        if result.trailing_indent:
            summary.trailing_indent_count += 1
        if result.repeated_whitespace:
            summary.repeated_whitespace_count += 1
        if result.tabs_converted:
            summary.tabs_converted_count += 1
        if result.space_before_punct:
            summary.space_before_punct_count += 1
        if result.missing_sentence_space:
            summary.missing_sentence_space_count += 1

        summary.issues.append(
            WhitespaceIssue(
                href=href, element=host, attr=attr,
                category=_primary_category(result),
                before=text, after=result.text,
                leading_glue=leading_glue, trailing_glue=trailing_glue,
            )
        )

    return summary


def _primary_category(result: NormalizeResult) -> str:
    """One representative label per issue for the line-by-line detail
    view -- a single node can trip more than one counter above, but
    the detail list shows the most notable reason it changed."""
    if result.missing_sentence_space:
        return "Missing space after punctuation"
    if result.space_before_punct:
        return "Space before punctuation"
    if result.tabs_converted:
        return "Tab converted to space"
    if result.repeated_whitespace:
        return "Repeated whitespace"
    if result.leading_indent:
        return "Leading indentation"
    if result.trailing_indent:
        return "Trailing indentation"
    return "Whitespace normalized"


def analyze_book_whitespace(book) -> BookWhitespaceSummary:
    summary = BookWhitespaceSummary()
    for chapter in book.chapters:
        summary.chapters.append(
            analyze_chapter_whitespace(getattr(chapter, "href", ""), getattr(chapter, "document", None))
        )
    return summary
