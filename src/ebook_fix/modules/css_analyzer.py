"""
ebook_fix.modules.css_analyzer

Analyzes CSS in an EPUB to identify redundant classes versus intentionally
different ones. This feeds into the mapping process, helping distinguish
between true bloat (identical rules) and meaningful variation (different
indents, margins, font-sizes, or semantic roles).

Exports a CSS analysis report showing:
- Identical classes (100% redundant)
- Near-identical classes (differ in 1-2 properties)
- Classes grouped by their CSS properties (to spot patterns)
- Unused classes (defined but never applied in XHTML)
- Color patterns (flags theme colors that shouldn't be stripped)
- Font-size variations (flags intentional typographic hierarchy)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from lxml import etree

from ebook_fix.css import read_book_css, COMMENT_RE, RULE_RE
from ebook_fix.class_map import SIMPLE_CLASS_SELECTOR_RE
from ebook_fix.report import Report

# Ensure we can find these even if they're in different submodules
try:
    from ebook_fix.css import read_book_css
except ImportError:
    # Fallback if module structure differs
    pass


@dataclass
class CSSRule:
    """Represents a parsed CSS rule for a single class."""
    class_name: str
    properties: dict[str, str] = field(default_factory=dict)
    selectors: list[str] = field(default_factory=list)
    source_file: str = ""
    line_number: int = 0
    
    def property_signature(self) -> tuple:
        """Return a sorted tuple of (key, value) pairs for comparison.
        Used to identify identical rules."""
        return tuple(sorted(self.properties.items()))
    
    def critical_properties(self) -> dict[str, str]:
        """Return only the properties that affect layout/appearance.
        Excludes vendor prefixes, transitions, etc."""
        critical = {}
        exclude = {'display', 'border-bottom-width', 'border-left-width',
                   'border-right-width', 'border-top-width', '-webkit-',
                   '-moz-', '-ms-', 'transform', 'transition'}
        
        for k, v in self.properties.items():
            # Skip vendor prefixes
            if any(k.startswith(ex) for ex in exclude if ex.startswith('-')):
                continue
            # Skip if key contains excluded term
            if any(ex in k for ex in exclude if not ex.startswith('-')):
                continue
            critical[k] = v
        
        return critical
    
    def description(self) -> str:
        """Human-readable description of this rule's properties."""
        if not self.properties:
            return "(no properties)"
        
        props = []
        # Show in reading priority order
        priority_keys = ['color', 'font-weight', 'font-size', 'font-style',
                        'text-align', 'text-indent', 'margin', 'padding',
                        'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
                        'padding-top', 'padding-bottom', 'padding-left', 'padding-right']
        
        for key in priority_keys:
            if key in self.properties:
                props.append(f"{key}:{self.properties[key]}")
        
        for key in sorted(self.properties.keys()):
            if key not in priority_keys:
                props.append(f"{key}:{self.properties[key]}")
        
        return " ".join(props[:5])  # Truncate for readability


@dataclass
class ClassUsage:
    """Track where a class appears in the document."""
    class_name: str
    element_count: int = 0
    tag_names: set[str] = field(default_factory=set)
    in_class_attribute: bool = False
    used: bool = False
    
    def __post_init__(self):
        self.used = self.element_count > 0


class CSSAnalyzer:
    """Analyze CSS in a book to find redundancy patterns and usage."""
    
    def __init__(self):
        self.rules: dict[str, CSSRule] = {}  # class_name -> CSSRule
        self.usage: dict[str, ClassUsage] = {}  # class_name -> ClassUsage
        self.color_patterns: dict[str, set[str]] = defaultdict(set)  # color -> set of class_names
        self.font_size_patterns: dict[str, set[str]] = defaultdict(set)  # font-size -> set of class_names
    
    def analyze_book(self, book) -> "CSSAnalysisReport":
        """Analyze a full book's CSS and usage."""
        # 1. Extract all CSS rules from stylesheets
        self._extract_css_rules(book)
        
        # 2. Scan XHTML for class usage
        self._scan_class_usage(book)
        
        # 3. Build analysis report
        return self._generate_report()
    
    def _extract_css_rules(self, book):
        """Parse all CSS files and embedded <style> blocks."""
        # External stylesheets
        css_contents = read_book_css(book)
        for href, text in css_contents.items():
            if text:
                self._parse_css_text(text, source_file=str(href))
        
        # Embedded <style> blocks
        for chapter in book.chapters:
            root = chapter.document
            if root is None:
                continue
            
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                if etree.QName(el).localname.lower() == "style":
                    style_text = el.text or ""
                    if style_text:
                        self._parse_css_text(style_text, 
                                            source_file=f"embedded in {chapter.href}")
    
    def _parse_css_text(self, text: str, source_file: str = ""):
        """Parse CSS text and extract class rules."""
        # Remove comments
        text = COMMENT_RE.sub("", text)
        
        for m in RULE_RE.finditer(text):
            selector_group = m.group(1)
            body = m.group(2)
            
            parts = [s.strip() for s in selector_group.split(",")]
            
            for part in parts:
                # Match simple class selectors like ".className" or "p.className"
                sm = SIMPLE_CLASS_SELECTOR_RE.match(part)
                if not sm:
                    continue
                
                class_name = sm.group(2)
                if not class_name:
                    continue
                
                # Parse properties
                properties = {}
                for prop_m in re.finditer(r'([a-zA-Z-]+)\s*:\s*([^;]+)', body):
                    prop_name = prop_m.group(1).strip()
                    prop_value = prop_m.group(2).strip()
                    # Normalize whitespace in values
                    prop_value = re.sub(r'\s+', ' ', prop_value)
                    properties[prop_name] = prop_value
                
                # Store or merge with existing rule
                if class_name not in self.rules:
                    self.rules[class_name] = CSSRule(
                        class_name=class_name,
                        properties=properties,
                        source_file=source_file
                    )
                    self.rules[class_name].selectors.append(part)
                    
                    # Track color and font-size patterns
                    if 'color' in properties:
                        self.color_patterns[properties['color']].add(class_name)
                    if 'font-size' in properties:
                        self.font_size_patterns[properties['font-size']].add(class_name)
    
    def _scan_class_usage(self, book):
        """Scan all XHTML files for class usage."""
        for chapter in book.chapters:
            root = chapter.document
            if root is None:
                continue
            
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                
                class_attr = el.get("class")
                if not class_attr:
                    continue
                
                classes = class_attr.split()
                tag_name = etree.QName(el).localname.lower()
                
                for cls in classes:
                    if cls not in self.usage:
                        self.usage[cls] = ClassUsage(class_name=cls)
                    
                    self.usage[cls].element_count += 1
                    self.usage[cls].tag_names.add(tag_name)
                    self.usage[cls].in_class_attribute = True
                    self.usage[cls].used = True
    
    def _generate_report(self) -> "CSSAnalysisReport":
        """Generate analysis report."""
        # Find identical rules
        identical_groups = defaultdict(list)
        for class_name, rule in self.rules.items():
            sig = rule.property_signature()
            identical_groups[sig].append(class_name)
        
        identical_groups = {k: v for k, v in identical_groups.items() if len(v) > 1}
        
        # Find near-identical rules (differ by 1-2 properties)
        near_identical_groups = self._find_near_identical()
        
        # Find unused classes
        unused = [cls for cls in self.rules.keys() 
                  if cls not in self.usage or not self.usage[cls].used]
        
        # Find classes with color (likely intentional design)
        colored_classes = {cls: rule.properties.get('color')
                          for cls, rule in self.rules.items()
                          if 'color' in rule.properties}
        
        return CSSAnalysisReport(
            analyzer=self,
            total_classes=len(self.rules),
            identical_groups=identical_groups,
            near_identical_groups=near_identical_groups,
            unused_classes=unused,
            colored_classes=colored_classes,
            color_patterns=dict(self.color_patterns),
            font_size_patterns=dict(self.font_size_patterns),
        )
    
    def _find_near_identical(self, max_diff=2) -> dict:
        """Find groups of classes that differ by max_diff properties."""
        groups = defaultdict(list)
        processed = set()
        
        for class1_name, rule1 in self.rules.items():
            if class1_name in processed:
                continue
            
            group = [class1_name]
            crit1 = rule1.critical_properties()
            
            for class2_name, rule2 in self.rules.items():
                if class2_name <= class1_name or class2_name in processed:
                    continue
                
                crit2 = rule2.critical_properties()
                
                # Count differences
                all_keys = set(crit1.keys()) | set(crit2.keys())
                diffs = sum(1 for k in all_keys if crit1.get(k) != crit2.get(k))
                
                if 0 < diffs <= max_diff:
                    group.append(class2_name)
            
            if len(group) > 1:
                groups[tuple(sorted(group))] = group
                processed.update(group)
        
        return dict(groups)


@dataclass
class CSSAnalysisReport:
    """Report from CSS analysis."""
    analyzer: CSSAnalyzer
    total_classes: int
    identical_groups: dict  # sig -> [class_names]
    near_identical_groups: dict  # sig -> [class_names]
    unused_classes: list[str]
    colored_classes: dict[str, str]  # class_name -> color
    color_patterns: dict[str, set]  # color -> set of class_names
    font_size_patterns: dict[str, set]  # font-size -> set of class_names
    
    def to_text(self) -> str:
        """Convert analysis to formatted text."""
        lines = []
        
        lines.append("CSS Analysis Summary")
        lines.append("=" * 80)
        lines.append(f"Total classes defined: {self.total_classes}")
        lines.append("")
        
        # Identical classes
        if self.identical_groups:
            lines.append("IDENTICAL CLASSES (100% redundant)")
            lines.append(f"{len(self.identical_groups)} group(s) found:")
            lines.append("")
            
            for sig, classes in sorted(self.identical_groups.items()):
                rule = self.analyzer.rules[classes[0]]
                class_list = ", ".join(sorted(classes))
                lines.append(f"  {class_list}")
                lines.append(f"    {rule.description()}")
                lines.append("")
        
        # Near-identical classes
        if self.near_identical_groups:
            lines.append("NEAR-IDENTICAL CLASSES (differ in 1-2 properties)")
            lines.append(f"{len(self.near_identical_groups)} group(s) found:")
            lines.append("")
            
            for group_key, classes in sorted(self.near_identical_groups.items()):
                diffs = self._get_differences(classes)
                class_list = ", ".join(sorted(classes))
                lines.append(f"  {class_list}")
                for k, v in diffs:
                    lines.append(f"    {k}: {v}")
                lines.append("")
        
        # Color patterns
        if self.color_patterns:
            lines.append("COLOR PATTERNS (may be intentional design)")
            lines.append(f"{len(self.color_patterns)} color(s) used:")
            lines.append("")
            
            for color, classes in sorted(self.color_patterns.items(), key=lambda x: (x[0], len(x[1])), reverse=True):
                if len(classes) > 1:
                    class_preview = ", ".join(sorted(classes)[:5])
                    if len(classes) > 5:
                        class_preview += f", ... ({len(classes)} total)"
                    lines.append(f"  {color}: {class_preview}")
            lines.append("")
        
        # Unused classes
        if self.unused_classes:
            lines.append("UNUSED CLASSES (defined but not applied)")
            lines.append(f"{len(self.unused_classes)} class(es):")
            lines.append("")
            
            for cls in sorted(self.unused_classes)[:20]:
                rule = self.analyzer.rules[cls]
                lines.append(f"  .{cls}")
                lines.append(f"    {rule.description()}")
            
            if len(self.unused_classes) > 20:
                lines.append(f"  ... and {len(self.unused_classes) - 20} more")
            lines.append("")
        
        lines.append("=" * 80)
        return "\n".join(lines)
    
    def to_report(self) -> str:
        """Alias for to_text for consistency."""
        return self.to_text()
    
    def _get_differences(self, classes: list[str]) -> list[tuple[str, str]]:
        """Get properties that differ between classes."""
        diffs = []
        if not classes:
            return diffs
        
        rule1 = self.analyzer.rules[classes[0]]
        crit1 = rule1.critical_properties()
        
        for cls in classes[1:]:
            rule2 = self.analyzer.rules[cls]
            crit2 = rule2.critical_properties()
            
            all_keys = set(crit1.keys()) | set(crit2.keys())
            for k in sorted(all_keys):
                v1 = crit1.get(k, "(none)")
                v2 = crit2.get(k, "(none)")
                if v1 != v2:
                    diffs.append((k, f"{v1} -> {v2}"))
        
        return diffs


def analyze_epub_css(book) -> CSSAnalysisReport:
    """Convenience function to analyze a book's CSS."""
    analyzer = CSSAnalyzer()
    return analyzer.analyze_book(book)
