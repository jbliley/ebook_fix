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

@dataclass
class ChapterAnalysis:
    href:str=""
    title:str=""
    paragraphs:int=0
    images:int=0
    links:int=0
    tables:int=0
    lists:int=0
    headings:dict=field(default_factory=dict)
    tag_counts:Counter=field(default_factory=Counter)
    css_classes:Counter=field(default_factory=Counter)
    ids:list=field(default_factory=list)

@dataclass
class AnalysisReport:
    chapter_reports:list=field(default_factory=list)
    total_paragraphs:int=0
    total_images:int=0
    total_links:int=0
    tag_counts:Counter=field(default_factory=Counter)
    css_classes:Counter=field(default_factory=Counter)

class EPUBAnalyzer:
    def analyze(self,book):
        r=AnalysisReport()
        for ch in book.chapters:
            c=ChapterAnalysis(href=getattr(ch,"href",""),title=getattr(ch,"title",""))
            tree=getattr(ch,"document",None)
            if tree is not None:
                for e in tree.iter():
                    tag=etree.QName(e).localname.lower()
                    c.tag_counts[tag]+=1
                    if tag=="p": c.paragraphs+=1
                    elif tag.startswith("h") and len(tag)==2: c.headings[tag]=c.headings.get(tag,0)+1
                    elif tag=="img": c.images+=1
                    elif tag=="a": c.links+=1
                    elif tag=="table": c.tables+=1
                    elif tag in ("ul","ol"): c.lists+=1
                    cls=e.get("class")
                    if cls:
                        for n in cls.split(): c.css_classes[n]+=1
                    i=e.get("id")
                    if i: c.ids.append(i)
            r.chapter_reports.append(c)
            r.total_paragraphs+=c.paragraphs
            r.total_images+=c.images
            r.total_links+=c.links
            r.tag_counts.update(c.tag_counts)
            r.css_classes.update(c.css_classes)
        return r
