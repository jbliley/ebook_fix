"""
ebook_fix.modules.css_consolidator

Uses CSS analyzer findings to suggest safe class consolidations.

Generates TOML mapping suggestions for identical and carefully-evaluated
near-identical classes, helping the class-standardize repair know which
classes can be safely merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ConsolidationSuggestion:
    """A suggestion to consolidate multiple classes into one."""
    canonical_name: str
    classes_to_merge: list[str]
    confidence: float  # 0.0 to 1.0
    reason: str
    preserves_design: bool = True


class CSSConsolidator:
    """Use CSS analysis to suggest safe consolidations."""
    
    def __init__(self, analysis_report):
        """Initialize with a CSSAnalysisReport."""
        self.report = analysis_report
        self.suggestions: list[ConsolidationSuggestion] = []
    
    def generate_suggestions(self) -> list[ConsolidationSuggestion]:
        """Generate consolidation suggestions from analysis."""
        self.suggestions = []
        
        # 1. High-confidence: Identical classes
        self._suggest_identical()
        
        # 2. Medium-confidence: Near-identical with semantic sense
        self._suggest_near_identical()
        
        # 3. Low-confidence: Safe unused class cleanup
        self._suggest_unused_cleanup()
        
        return self.suggestions
    
    def _suggest_identical(self):
        """Suggest merging identical classes (high confidence)."""
        for sig, classes in self.report.identical_groups.items():
            if len(classes) < 2:
                continue
            
            # Use the shortest class name as canonical
            canonical = min(classes, key=len)
            others = [c for c in classes if c != canonical]
            
            self.suggestions.append(ConsolidationSuggestion(
                canonical_name=canonical,
                classes_to_merge=others,
                confidence=0.95,
                reason=f"100% identical rules: {', '.join(others)} -> {canonical}",
                preserves_design=True
            ))
    
    def _suggest_near_identical(self):
        """Suggest near-identical consolidations that make semantic sense."""
        # These are heuristics that are often safe but need user confirmation
        
        for group_key, classes in self.report.near_identical_groups.items():
            if len(classes) < 2:
                continue
            
            # Skip groups with color differences - those are intentional
            colors_in_group = set()
            for cls in classes:
                rule = self.report.analyzer.rules[cls]
                if 'color' in rule.properties:
                    colors_in_group.add(rule.properties['color'])
            
            if len(colors_in_group) > 1:
                # Different colors means intentional design, skip
                continue
            
            # Skip groups with font-size differences > 20% - those are intentional
            sizes_in_group = []
            for cls in classes:
                rule = self.report.analyzer.rules[cls]
                if 'font-size' in rule.properties:
                    # Parse size (e.g., "1.5em" -> 1.5)
                    try:
                        size_str = rule.properties['font-size'].replace('em', '').strip()
                        sizes_in_group.append(float(size_str))
                    except:
                        pass
            
            if sizes_in_group:
                min_size = min(sizes_in_group)
                max_size = max(sizes_in_group)
                if max_size > min_size * 1.2:  # More than 20% difference
                    # Different typographic levels, skip
                    continue
            
            # If we get here, it might be safe to suggest
            canonical = min(classes, key=len)
            others = [c for c in classes if c != canonical]
            
            self.suggestions.append(ConsolidationSuggestion(
                canonical_name=canonical,
                classes_to_merge=others,
                confidence=0.5,  # Lower confidence - needs review
                reason=f"Near-identical with no color/size differences: {', '.join(others)} -> {canonical}",
                preserves_design=True
            ))
    
    def _suggest_unused_cleanup(self):
        """Suggest removing completely unused classes."""
        for cls in self.report.unused_classes:
            if cls in self.report.colored_classes:
                # Skip colored classes - they might be used in edge cases
                continue
            
            self.suggestions.append(ConsolidationSuggestion(
                canonical_name=None,
                classes_to_merge=[cls],
                confidence=0.8,
                reason=f"Unused class (never applied in XHTML): can be removed",
                preserves_design=True
            ))
    
    def to_toml(self) -> str:
        """Generate TOML mapping file from suggestions."""
        output = []
        output.append("# CSS Consolidation Mapping")
        output.append("# Generated from CSS analyzer")
        output.append("# Review carefully before applying!")
        output.append("")
        output.append("# High confidence (identical classes)")
        output.append("[high_confidence]")
        
        high_conf = [s for s in self.suggestions if s.confidence > 0.8 and s.canonical_name]
        if high_conf:
            for s in high_conf:
                for merge_cls in s.classes_to_merge:
                    output.append(f"{merge_cls} = \"{s.canonical_name}\"")
        else:
            output.append("# No identical classes found\n")
        
        output.append("")
        output.append("# Medium confidence (near-identical with semantic review)")
        output.append("[medium_confidence]")
        
        med_conf = [s for s in self.suggestions if 0.4 < s.confidence <= 0.8 and s.canonical_name]
        if med_conf:
            for s in med_conf:
                output.append(f"# {s.reason}")
                for merge_cls in s.classes_to_merge:
                    output.append(f"# {merge_cls} = \"{s.canonical_name}\"")
        else:
            output.append("# No near-identical classes with safe consolidation")
        
        output.append("")
        output.append("# Unused classes (safe to remove)")
        output.append("[unused]")
        
        unused = [s for s in self.suggestions if s.canonical_name is None]
        if unused:
            for s in unused:
                output.append(f"remove = [\n")
                for cls in s.classes_to_merge:
                    output.append(f"  \"{cls}\",\n")
                output.append("]\n")
        else:
            output.append("# No unused classes found")
        
        return "\n".join(output)
    
    def print_summary(self):
        """Print a summary of suggestions."""
        print("\nConsolidation Suggestions Summary")
        print("=" * 80)
        
        high = [s for s in self.suggestions if s.confidence > 0.8 and s.canonical_name]
        med = [s for s in self.suggestions if 0.4 < s.confidence <= 0.8 and s.canonical_name]
        unused = [s for s in self.suggestions if s.canonical_name is None]
        
        print(f"High confidence (safe): {len(high)} suggestions")
        for s in high:
            print(f"  {', '.join(s.classes_to_merge)} -> {s.canonical_name}")
        
        print(f"\nMedium confidence (review): {len(med)} suggestions")
        for s in med:
            print(f"  {', '.join(s.classes_to_merge)} -> {s.canonical_name}")
            print(f"    Reason: {s.reason}")
        
        print(f"\nUnused classes (removable): {len(unused)} classes")
        if unused:
            all_unused = []
            for s in unused:
                all_unused.extend(s.classes_to_merge)
            print(f"  {', '.join(all_unused[:10])}")
            if len(all_unused) > 10:
                print(f"  ... and {len(all_unused) - 10} more")
