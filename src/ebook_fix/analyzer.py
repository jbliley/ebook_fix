"""
ebook_fix.analyzer

Analyzes the entire ebook and reports back to all other modules with structure information

Goals
-----
- Analyze and categorize book contents, metadata, images, and everything else
- Pass this information to other modules so multiple file scans are not needed
"""
from collections import Counter
from dataclasses import dataclass, field
from lxml import etree

from ebook_fix.typography import TypographyReport, BookTypographySummary, analyze_text, summarize_book
from ebook_fix.css import BookCSSSummary, analyze_book_css
from ebook_fix.chapters import BookChapterSummary, analyze_book_chapters
from ebook_fix.images import BookImageSummary, analyze_book_images
from ebook_fix.paragraphs import BookParagraphSummary, analyze_book_paragraphs
from ebook_fix.whitespace import BookWhitespaceSummary, analyze_book_whitespace
from ebook_fix.ellipsis import BookEllipsisSummary, analyze_book_ellipsis
from ebook_fix.apostrophes import (
    BookApostropheSummary, analyze_book_apostrophes,
    BookPossessiveSummary, analyze_book_possessives,
)
from ebook_fix.frontmatter import (
    BookFrontMatterSummary, analyze_book_frontmatter, FRONT_ZONE, BACK_ZONE,
)
from ebook_fix.toc import BookTocSummary, analyze_book_toc
from ebook_fix.gutenberg import BookGutenbergSummary, analyze_book_gutenberg
from ebook_fix.cover import BookCoverSummary, analyze_book_cover
from ebook_fix.span_soup import BookSpanSoupSummary, analyze_book_span_soup
from ebook_fix import epub_version

# A chapter is flagged as "thin" if it has no paragraphs at all, or if its
# total word count falls below this threshold. Front/back-matter pages
# (title page, copyright page, etc.) will often trip this, which is
# expected -- see ebook_fix.frontmatter and unexplained_thin_chapters
# below for how those get told apart from an actually broken chapter.
THIN_CHAPTER_WORD_THRESHOLD = 50

@dataclass
class ChapterAnalysis:
    href:str=""
    title:str=""
    paragraphs:int=0
    images:int=0
    links:int=0
    tables:int=0
    lists:int=0
    word_count:int=0
    is_thin:bool=False
    headings:dict=field(default_factory=dict)
    heading_issues:list=field(default_factory=list)
    tag_counts:Counter=field(default_factory=Counter)
    css_classes:Counter=field(default_factory=Counter)
    ids:list=field(default_factory=list)
    inline_style_count:int=0
    typography:TypographyReport=field(default_factory=TypographyReport)
    matter_zone:str="unknown"       # "front", "back", "main", or "unknown" -- see ebook_fix.frontmatter
    matter_label:str=""             # best-guess label, e.g. "copyright page", "dedication"
    matter_confidence:str="low"     # "high", "medium", "low"

@dataclass
class BookSummary:
    title:str=""
    author:str=""
    language:str=""
    publisher:str=""
    identifier:str=""
    date:str=""
    rights:str=""
    description:str=""
    subjects:list=field(default_factory=list)
    epub_version:str=""
    epub_needs_upgrade:bool=False
    epub_target_version:str=""
    html_page_count:int=0
    css_file_count:int=0
    image_file_count:int=0
    font_file_count:int=0
    audio_file_count:int=0
    video_file_count:int=0
    other_file_count:int=0
    spine_entry_count:int=0
    toc_entry_count:int=0
    toc_source:str=""    # "ncx", "nav", or "" if the book has neither
    total_word_count:int=0

@dataclass
class AnalysisReport:
    chapter_reports:list=field(default_factory=list)
    total_paragraphs:int=0
    total_images:int=0
    total_links:int=0
    tag_counts:Counter=field(default_factory=Counter)
    css_classes:Counter=field(default_factory=Counter)
    thin_chapters:list=field(default_factory=list)
    # Thin chapters that AREN'T explained by being classified front/back
    # matter -- this is the list worth actually flagging as a possible
    # problem. thin_chapters above stays complete/descriptive; this is
    # the filtered version engine.py's issue count should use.
    unexplained_thin_chapters:list=field(default_factory=list)
    chapters_with_heading_issues:list=field(default_factory=list)
    summary:BookSummary=field(default_factory=BookSummary)
    typography:BookTypographySummary=field(default_factory=BookTypographySummary)
    css:BookCSSSummary=field(default_factory=BookCSSSummary)
    chapters:BookChapterSummary=field(default_factory=BookChapterSummary)
    images:BookImageSummary=field(default_factory=BookImageSummary)
    paragraphs:BookParagraphSummary=field(default_factory=BookParagraphSummary)
    whitespace:BookWhitespaceSummary=field(default_factory=BookWhitespaceSummary)
    ellipsis:BookEllipsisSummary=field(default_factory=BookEllipsisSummary)
    apostrophes:BookApostropheSummary=field(default_factory=BookApostropheSummary)
    # Flag-only, manual-review possessive candidates -- see
    # ebook_fix.apostrophes module docstring for why these are kept
    # completely separate from `apostrophes` above and never touched
    # by any repair module.
    possessives:BookPossessiveSummary=field(default_factory=BookPossessiveSummary)
    frontmatter:BookFrontMatterSummary=field(default_factory=BookFrontMatterSummary)
    toc:BookTocSummary=field(default_factory=BookTocSummary)
    gutenberg:BookGutenbergSummary=field(default_factory=BookGutenbergSummary)
    cover:BookCoverSummary=field(default_factory=BookCoverSummary)
    span_soup:BookSpanSoupSummary=field(default_factory=BookSpanSoupSummary)

class EPUBAnalyzer:
    def analyze(self,book):
        r=AnalysisReport()

        # Computed ahead of the per-chapter loop below (rather than at
        # the end, alongside css/images/paragraphs/whitespace) because
        # frontmatter classification needs the confirmed chapter
        # sequence to anchor its zones on, and the loop below needs
        # frontmatter's answer to decide whether a thin chapter is an
        # actual problem or an expected short front/back-matter page.
        r.chapters=analyze_book_chapters(book)
        r.frontmatter=analyze_book_frontmatter(book,chapter_summary=r.chapters)
        matter_by_href={m.href:m for m in r.frontmatter.chapters}

        for ch in book.chapters:
            c=ChapterAnalysis(href=getattr(ch,"href",""),title=getattr(ch,"title",""))
            tree=getattr(ch,"document",None)
            last_level=0
            h1_count=0
            if tree is not None:
                for e in tree.iter():
                    if not isinstance(e.tag, str):
                        # Skip comments, processing instructions, and other
                        # special lxml node types that aren't real elements.
                        continue
                    tag=etree.QName(e).localname.lower()
                    c.tag_counts[tag]+=1
                    if tag=="p": c.paragraphs+=1
                    elif tag.startswith("h") and len(tag)==2 and tag[1].isdigit():
                        c.headings[tag]=c.headings.get(tag,0)+1
                        level=int(tag[1])
                        text=(e.text or "").strip()
                        if level==1:
                            h1_count+=1
                            if h1_count>1:
                                c.heading_issues.append(
                                    f"Multiple h1 headings in one chapter "
                                    f"(h1 #{h1_count}: {text!r})"
                                )
                        if last_level!=0 and level>last_level+1:
                            c.heading_issues.append(
                                f"Skipped heading level: h{level} follows h{last_level} "
                                f"(no h{last_level+1}) at {text!r}"
                            )
                        last_level=level
                    elif tag=="img": c.images+=1
                    elif tag=="a": c.links+=1
                    elif tag=="table": c.tables+=1
                    elif tag in ("ul","ol"): c.lists+=1
                    cls=e.get("class")
                    if cls:
                        for n in cls.split(): c.css_classes[n]+=1
                    i=e.get("id")
                    if i: c.ids.append(i)
                    if e.get("style"): c.inline_style_count+=1
                full_text="".join(tree.itertext())
                c.word_count=len(full_text.split())
                c.typography=analyze_text(full_text)
            c.is_thin = c.paragraphs==0 or c.word_count<THIN_CHAPTER_WORD_THRESHOLD
            matter=matter_by_href.get(c.href)
            if matter is not None:
                c.matter_zone=matter.zone
                c.matter_label=matter.label
                c.matter_confidence=matter.confidence
            r.chapter_reports.append(c)
            r.total_paragraphs+=c.paragraphs
            r.total_images+=c.images
            r.total_links+=c.links
            r.tag_counts.update(c.tag_counts)
            r.css_classes.update(c.css_classes)
            if c.is_thin:
                r.thin_chapters.append(c)
                if c.matter_zone not in (FRONT_ZONE,BACK_ZONE):
                    r.unexplained_thin_chapters.append(c)
            if c.heading_issues:
                r.chapters_with_heading_issues.append(c)
            r.summary.total_word_count+=c.word_count

        meta=getattr(book,"metadata",None)
        r.summary.title=getattr(meta,"title","") if meta else ""
        r.summary.author=getattr(meta,"creator","") if meta else ""
        r.summary.language=getattr(meta,"language","") if meta else ""
        r.summary.publisher=getattr(meta,"publisher","") if meta else ""
        r.summary.identifier=getattr(meta,"identifier","") if meta else ""
        r.summary.date=getattr(meta,"date","") if meta else ""
        r.summary.rights=getattr(meta,"rights","") if meta else ""
        r.summary.description=getattr(meta,"description","") if meta else ""
        r.summary.subjects=list(getattr(meta,"subject",[]) or []) if meta else []
        version_info=epub_version.detect(book)
        r.summary.epub_version=version_info.detected_version
        r.summary.epub_needs_upgrade=version_info.needs_upgrade
        r.summary.epub_target_version=version_info.target_version
        r.summary.html_page_count=len(book.chapters)
        r.summary.css_file_count=len(getattr(book,"css",[]) or [])
        r.summary.image_file_count=len(getattr(book,"images",[]) or [])
        r.summary.font_file_count=len(getattr(book,"fonts",[]) or [])
        r.summary.audio_file_count=len(getattr(book,"audio",[]) or [])
        r.summary.video_file_count=len(getattr(book,"video",[]) or [])
        r.summary.other_file_count=len(getattr(book,"other",[]) or [])
        r.summary.spine_entry_count=len(getattr(book,"spine",[]) or [])

        r.typography=summarize_book([(c.href,c.typography) for c in r.chapter_reports])
        r.css=analyze_book_css(book,r.chapter_reports)
        r.images=analyze_book_images(book)
        r.paragraphs=analyze_book_paragraphs(book)
        r.whitespace=analyze_book_whitespace(book)
        r.ellipsis=analyze_book_ellipsis(book)
        r.apostrophes=analyze_book_apostrophes(book)
        r.possessives=analyze_book_possessives(book)
        r.toc=analyze_book_toc(book,chapter_reports=r.chapter_reports)
        r.summary.toc_entry_count=r.toc.entry_count
        r.summary.toc_source=r.toc.source
        r.gutenberg=analyze_book_gutenberg(book)
        r.cover=analyze_book_cover(book)
        r.span_soup=analyze_book_span_soup(book)
        return r
