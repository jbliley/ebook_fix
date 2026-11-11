"""
ebook_fix.modules.bold_text_repair

Removes bold that has been put on (nearly) every word of the book.

Found in "Die Trying" (Lee Child, a conversion from MOBI): every paragraph of
the story is wrapped in `<span class="bold">`, and the class only says
`font-weight: bold`, so the whole book shows in bold. 99.9% of its text is
inside one of those wrappers. A book that is bold from cover to cover has no
emphasis left to protect: the bold is a converter mistake, not the author's.

What this does: when the bold wrappers cover at least `min_share` (85% by
default) of the book's body text, and the book has at least `min_chars`
characters of it, every bold wrapper in the body text is unwrapped. Its text
and anything inside it stay exactly where they were.

A wrapper is one of these, and only when it carries nothing else:
- a `<b>` or `<strong>` tag with no attribute except (perhaps) a class that
  only makes text bold,
- a `<span>` whose only attribute is a class (or classes) that only make text
  bold. A class counts as "only bold" when every rule that names it is a
  plain `.class` rule holding nothing but `font-weight: bold` (or bolder, or
  600 or more), and no other rule reaches it (such as `.bold em`).

Left alone:
- Anything inside a heading (h1 to h6) and the paragraph Chapter Markup
  marked as a chapter title.
- A short block (80 characters or fewer) whose class differs from the class
  of the book's main text, so a title line, a part name or a byline keeps the
  bold its author gave it. These blocks are not counted when the share is
  worked out.
- A wrapper with an id, a style, a language or any other attribute.
- Books that are not mostly bold (real emphasis is never touched), and books
  where the bold comes from a style on the paragraph itself rather than from
  a wrapper (nothing is changed unless the wrappers alone cover the text).
- Fixed-layout books.

The visible text of each page is compared before and after; a page whose text
would change is left as it was. Re-running finds no wrappers and does nothing.
The `.bold` rule stays in the stylesheet while a heading still uses it; Unused
CSS removes it later when nothing does.
"""
from __future__ import annotations

import re
from collections import Counter

from ebook_fix.config import BoldTextConfig
from ebook_fix.css import COMMENT_RE, RULE_RE
from ebook_fix.modules.paragraph_indent_repair import _book_rules
from ebook_fix.report import Report

HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
BLOCK_TAGS = frozenset({"p", "div", "li", "blockquote", "dd", "dt", "td", "th", "body"})
SKIP_TAGS = frozenset({"head", "title", "script", "style", "pre", "code", "svg", "math"})
SHORT_BLOCK_CHARS = 80

_CLASS_ONLY = re.compile(r"^(?:[a-zA-Z][\w-]*)?\.([\w-]+)$")
_AT_STATEMENT = re.compile(r"@[a-zA-Z-]+[^{};]*;")
_PROPERTY = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;]+)")
_CLASS_ANY = re.compile(r"\.([A-Za-z_][\w-]*)")


def _local(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def _is_bold_value(value: str) -> bool:
    value = (value or "").strip().lower()
    if value in ("bold", "bolder"):
        return True
    return value.isdigit() and int(value) >= 600


def rules_from_sources(sources):
    """[(selector, {property: (value, important)})] read from the stylesheet
    texts. A one-line statement such as `@namespace ...;` (Calibre writes one
    at the top of its stylesheets) is dropped first, because the generic rule
    reader would take it as part of the first rule's selector and skip that
    rule: in Die Trying that first rule is `.bold`."""
    rules = []
    for source in sources:
        text = _AT_STATEMENT.sub("", COMMENT_RE.sub("", source[-1] or ""))
        for m in RULE_RE.finditer(text):
            selector_group = m.group(1).strip()
            if not selector_group or selector_group.startswith("@"):
                continue
            props = {}
            for pm in _PROPERTY.finditer(m.group(2)):
                value = re.sub(r"\s*!important\s*", "", pm.group(2), flags=re.IGNORECASE).strip()
                props[pm.group(1).lower()] = (value, "!important" in pm.group(2).lower())
            for selector in selector_group.split(","):
                if selector.strip():
                    rules.append((selector.strip(), props))
    return rules


def bold_only_classes(rules) -> set:
    """Classes whose every rule is a plain class rule holding nothing but a
    bold font-weight, and that no other kind of selector reaches."""
    good, bad = set(), set()
    for selector, props in rules:
        selector = selector.strip()
        m = _CLASS_ONLY.match(selector)
        if m:
            cls = m.group(1)
            only_bold = (
                set(props) == {"font-weight"} and _is_bold_value(props["font-weight"][0])
            )
            if only_bold and cls not in bad:
                good.add(cls)
            else:
                bad.add(cls)
                good.discard(cls)
            continue
        for cls in _CLASS_ANY.findall(selector):
            bad.add(cls)
            good.discard(cls)
    return good


def is_bold_wrapper(el, bold_classes: set) -> bool:
    """True for a <b>, <strong> or <span> that does nothing but make its
    contents bold."""
    tag = _local(el)
    if tag not in ("b", "strong", "span"):
        return False
    attrs = set(el.attrib)
    if attrs - {"class"}:
        return False
    classes = (el.get("class") or "").split()
    if classes and not all(c in bold_classes for c in classes):
        return False
    if tag == "span":
        return bool(classes)
    return True


def _is_chapter_title(el) -> bool:
    return any(str(k).endswith("data-ebookfix-chapter") for k in el.attrib)


def _block_of(el):
    node = el
    while node is not None:
        if isinstance(node.tag, str) and _local(node) in BLOCK_TAGS:
            return node
        node = node.getparent()
    return None


def _skipped(el) -> bool:
    """True inside a heading, a marked chapter title, or a tag whose text is
    not body text."""
    node = el
    while node is not None:
        if isinstance(node.tag, str):
            tag = _local(node)
            if tag in HEADING_TAGS or tag in SKIP_TAGS or _is_chapter_title(node):
                return True
        node = node.getparent()
    return False


def _text_pieces(doc):
    """[(element that owns the text, text)] for every piece of non-blank
    text in the page: an element's own text and the tail after it."""
    pieces = []
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        if el.text and el.text.strip():
            pieces.append((el, el.text))
        parent = el.getparent()
        if el.tail and el.tail.strip() and parent is not None:
            pieces.append((parent, el.tail))
    return pieces


def _inside_wrapper(el, bold_classes: set) -> bool:
    node = el
    while node is not None:
        if isinstance(node.tag, str) and is_bold_wrapper(node, bold_classes):
            return True
        node = node.getparent()
    return False


def _page_measure(doc, bold_classes: set):
    """(chars_by_block_class, bold_chars_by_block_class, short_blocks) for
    one page: text characters (blank pieces and skipped text left out) per
    class of the block they sit in, those of them inside a bold wrapper, and
    the blocks that are short enough to be a title line, with their class
    and character count."""
    total, bold = Counter(), Counter()
    block_chars = {}
    for owner, text in _text_pieces(doc):
        if _skipped(owner):
            continue
        block = _block_of(owner)
        key = (block.get("class") or "") if block is not None else ""
        n = len(text.strip())
        total[key] += n
        block_chars[block] = block_chars.get(block, 0) + n
        if _inside_wrapper(owner, bold_classes):
            bold[key] += n
    return total, bold, block_chars


def find_bold_problem(book, min_share: float, min_chars: int, usage=None):
    """None, or {share, chars, bold_chars, wrappers, bold_classes, body_class}
    when the bold wrappers cover most of the book's body text."""
    _rules, sources = _book_rules(book, usage)
    bold_classes = bold_only_classes(rules_from_sources(sources))
    pages = []
    for chapter in book.chapters:
        doc = getattr(chapter, "document", None)
        if doc is None:
            continue
        pages.append((chapter, doc))
    # pass 1: which block class carries the book's main text
    class_chars = Counter()
    measured = []
    for chapter, doc in pages:
        total, bold, block_chars = _page_measure(doc, bold_classes)
        measured.append((chapter, doc, total, bold, block_chars))
        class_chars.update(total)
    if not class_chars:
        return None
    body_class = class_chars.most_common(1)[0][0]

    chars = bold_chars = wrappers = 0
    for chapter, doc, total, bold, block_chars in measured:
        short_other = _short_other_blocks(doc, body_class, block_chars)
        chars += sum(total.values())
        bold_chars += sum(bold.values())
        for block in short_other:
            n = block_chars[block]
            # a short title-like block is not part of the count either way
            chars -= n
            bold_chars -= _bold_chars_in(block, bold_classes)
        kept = set(short_other)
        wrappers += sum(
            1 for el in doc.iter()
            if isinstance(el.tag, str) and is_bold_wrapper(el, bold_classes) and not _skipped(el)
            and _block_of(el) not in kept
        )
    if chars < min_chars or not wrappers:
        return None
    share = bold_chars / chars
    if share < min_share:
        return None
    return {
        "share": share,
        "chars": chars,
        "bold_chars": bold_chars,
        "wrappers": wrappers,
        "bold_classes": bold_classes,
        "body_class": body_class,
    }


def _short_other_blocks(doc, body_class: str, block_chars: dict):
    """Blocks of 80 characters or fewer whose class is not the main text's
    class (a title line, a part name, a byline)."""
    found = []
    for block, n in block_chars.items():
        if block is None or n > SHORT_BLOCK_CHARS:
            continue
        if (block.get("class") or "") != body_class:
            found.append(block)
    return found


def _bold_chars_in(block, bold_classes: set) -> int:
    n = 0
    for owner, text in _text_pieces(block):
        if not _skipped(owner) and _block_of(owner) is block and _inside_wrapper(owner, bold_classes):
            n += len(text.strip())
    return n


def _unwrap(el) -> None:
    """Replace el with its text and children, keeping every character."""
    parent = el.getparent()
    idx = parent.index(el)
    children = list(el)
    lead = el.text or ""
    tail = el.tail or ""
    if lead:
        if idx > 0:
            prev = parent[idx - 1]
            prev.tail = (prev.tail or "") + lead
        else:
            parent.text = (parent.text or "") + lead
    for offset, child in enumerate(children):
        parent.insert(idx + offset, child)
    if tail:
        if children:
            children[-1].tail = (children[-1].tail or "") + tail
        elif idx > 0:
            prev = parent[idx - 1]
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    el.tail = None
    parent.remove(el)


def _root(doc):
    return doc.getroot() if hasattr(doc, "getroot") else doc


def _page_text(doc) -> str:
    return "".join(_root(doc).itertext())


def unwrap_page(doc, problem) -> int:
    """Unwrap the bold wrappers in one page that the problem allows. Returns
    how many were unwrapped; the page is untouched (0) if its text would
    change."""
    bold_classes = problem["bold_classes"]
    body_class = problem["body_class"]
    _total, _bold, block_chars = _page_measure(doc, bold_classes)
    keep_blocks = set(_short_other_blocks(doc, body_class, block_chars))
    targets = []
    for el in doc.iter():
        if not isinstance(el.tag, str) or not is_bold_wrapper(el, bold_classes):
            continue
        if _skipped(el):
            continue
        block = _block_of(el)
        if block is not None and block in keep_blocks:
            continue
        targets.append(el)
    if not targets:
        return 0
    before = _page_text(doc)
    for el in reversed(targets):
        if el.getparent() is not None:
            _unwrap(el)
    if _page_text(doc) != before:
        raise RuntimeError("Bold Text changed the text of a page")
    return len(targets)


class BoldTextRepair:
    name = "Bold Text"

    def __init__(self, config: BoldTextConfig | None = None):
        self.config = config or BoldTextConfig()

    def _problem(self, book, analysis=None):
        return find_bold_problem(
            book,
            float(getattr(self.config, "min_share", 0.85)),
            int(getattr(self.config, "min_chars", 3000)),
        )

    @staticmethod
    def _describe(problem, will):
        return (
            f"{round(problem['share'] * 100)}% of the book's text ({problem['bold_chars']} of "
            f"{problem['chars']} characters) is bold, so the bold is not emphasis; "
            f"{problem['wrappers']} bold wrapper(s) {'will be' if will else 'were'} removed "
            f"(chapter titles and other short title lines keep theirs), and the text is unchanged"
        )

    def analyze(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        problem = self._problem(book, analysis)
        if problem:
            report.add("stylesheet", "Bold throughout the text", self._describe(problem, will=True))
        return report

    def repair(self, book, analysis=None) -> Report:
        report = Report(self.name)
        if not self.config.enabled:
            return report
        problem = self._problem(book, analysis)
        if not problem:
            return report
        done = 0
        for chapter in book.chapters:
            doc = getattr(chapter, "document", None)
            if doc is None:
                continue
            n = unwrap_page(doc, problem)
            if n:
                chapter.modified = True
                done += n
        if done:
            if hasattr(book, "mark_modified"):
                book.mark_modified()
            problem = dict(problem, wrappers=done)
            report.add("stylesheet", "Bold removed from the body text", self._describe(problem, will=False))
        return report
