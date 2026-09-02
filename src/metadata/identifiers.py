"""
metadata.identifiers

Reads every <dc:identifier> found on a book and classifies each one
against the scheme definitions in schemes/identifier_schemes.json --
see docs/metadata_plan.md for the overall design this implements.

This module only reads and classifies for now. It does not rewrite
anything on the book -- that's future work, once the Calibre/EPUB
backends described in docs/metadata_plan.md exist to actually write
the normalized results back out. Recording the classification here,
as part of the analysis pass, follows the project's analysis-first
architecture: a repair module built later reads these results rather
than re-scanning the book itself.

Bypasses book.metadata.identifier (a single flattened string set by
ebook_fix.parser, which only ever keeps the first <dc:identifier> it
finds) and reads book.opf_document directly instead -- the same way
ebook_fix.series reads series metadata straight from the OPF tree
rather than through the Metadata dataclass. A book can carry more than
one identifier (an ISBN, an ASIN, a Calibre UUID, etc.) and all of
them are worth recording.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"

SCHEMES_PATH = Path(__file__).parent / "schemes" / "identifier_schemes.json"

_NORMALIZERS = {
    "strip_dashes_spaces": lambda v: re.sub(r"[\s-]", "", v),
    "digits_only": lambda v: re.sub(r"\D", "", v),
    "uppercase": lambda v: v.upper(),
    "lowercase": lambda v: v.lower(),
    "trim_only": lambda v: v.strip(),
}


@dataclass(slots=True)
class SchemeRule:
    name: str
    enabled: bool
    aliases: list[str]
    match_prefix_regex: str | None
    value_regex: str
    normalize: str
    output_scheme: str


@dataclass(slots=True)
class IdentifierMatch:
    raw_value: str = ""
    raw_scheme: str = ""          # opf:scheme attribute as found, "" if none
    matched_scheme: str = ""      # canonical output_scheme, "" if fallback
    normalized_value: str = ""
    match_method: str = "none"    # "attribute", "prefix", or "none"
    is_fallback: bool = False


@dataclass(slots=True)
class BookIdentifierSummary:
    identifiers: list[IdentifierMatch] = field(default_factory=list)

    @property
    def primary(self) -> IdentifierMatch | None:
        """The single best identifier to show wherever only one fits
        (e.g. the existing single-line summary display) -- prefers an
        ISBN, then any other confidently matched scheme, then falls
        back to whatever was found even if unmatched, in book order."""
        if not self.identifiers:
            return None
        for ident in self.identifiers:
            if ident.matched_scheme == "ISBN":
                return ident
        for ident in self.identifiers:
            if not ident.is_fallback:
                return ident
        return self.identifiers[0]

    @property
    def fallback_count(self) -> int:
        return sum(1 for i in self.identifiers if i.is_fallback)


_schemes_cache: list[SchemeRule] | None = None


def _load_schemes() -> list[SchemeRule]:
    global _schemes_cache
    if _schemes_cache is not None:
        return _schemes_cache
    with open(SCHEMES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    rules = []
    for name, entry in raw.get("schemes", {}).items():
        rules.append(SchemeRule(
            name=name,
            enabled=entry.get("enabled", True),
            aliases=[a.lower() for a in entry.get("aliases", [])],
            match_prefix_regex=entry.get("match_prefix_regex"),
            value_regex=entry.get("value_regex", ".*"),
            normalize=entry.get("normalize", "trim_only"),
            output_scheme=entry.get("output_scheme", name),
        ))
    _schemes_cache = rules
    return rules


def _normalize(value: str, normalize_key: str) -> str:
    fn = _NORMALIZERS.get(normalize_key, _NORMALIZERS["trim_only"])
    return fn(value.strip())


def _match_by_attribute(raw_scheme: str, rules: list[SchemeRule]) -> SchemeRule | None:
    if not raw_scheme:
        return None
    lowered = raw_scheme.strip().lower()
    for rule in rules:
        if rule.enabled and lowered in rule.aliases:
            return rule
    return None


def _match_by_prefix(raw_value: str, rules: list[SchemeRule]):
    """Tries each enabled rule's match_prefix_regex against raw_value.
    Returns (rule, remaining text after the prefix is stripped), or
    None if nothing matches."""
    stripped = raw_value.strip()
    for rule in rules:
        if not rule.enabled or not rule.match_prefix_regex:
            continue
        m = re.match(rule.match_prefix_regex, stripped, re.IGNORECASE)
        if m:
            return rule, stripped[m.end():].strip()
    return None


def _classify(raw_value: str, raw_scheme: str, rules: list[SchemeRule]) -> IdentifierMatch:
    result = IdentifierMatch(raw_value=raw_value, raw_scheme=raw_scheme)

    rule = _match_by_attribute(raw_scheme, rules)
    if rule is not None:
        normalized = _normalize(raw_value, rule.normalize)
        if re.match(rule.value_regex, normalized):
            result.matched_scheme = rule.output_scheme
            result.normalized_value = normalized
            result.match_method = "attribute"
            return result

    prefix_match = _match_by_prefix(raw_value, rules)
    if prefix_match is not None:
        rule, remainder = prefix_match
        normalized = _normalize(remainder, rule.normalize)
        if re.match(rule.value_regex, normalized):
            result.matched_scheme = rule.output_scheme
            result.normalized_value = normalized
            result.match_method = "prefix"
            return result

    # Schemes with no match_prefix_regex at all (e.g. URI) are meant to
    # be recognized by their value's own shape -- a urn:/http(s): value
    # already carries its own "label" as part of the syntax, nothing to
    # strip first. Tried last since it's the lowest-confidence check.
    for rule in rules:
        if not rule.enabled or rule.match_prefix_regex:
            continue
        normalized = _normalize(raw_value, rule.normalize)
        if re.match(rule.value_regex, normalized):
            result.matched_scheme = rule.output_scheme
            result.normalized_value = normalized
            result.match_method = "value_shape"
            return result

    # No confident match -- bare-DC fallback (see docs/metadata_plan.md,
    # "Processing logic (identifiers)" step 4). Value is still cleaned
    # up, but no scheme is claimed.
    result.normalized_value = raw_value.strip()
    result.match_method = "none"
    result.is_fallback = True
    return result


def analyze_book_identifiers(book) -> BookIdentifierSummary:
    """Reads every <dc:identifier> on `book` and classifies it against
    the scheme rules in identifier_schemes.json. Read-only -- records
    what each identifier is (or isn't); doesn't rewrite anything on
    the book itself."""
    summary = BookIdentifierSummary()

    opf = getattr(book, "opf_document", None)
    if opf is None:
        return summary

    metadata_el = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata_el is None:
        return summary

    rules = _load_schemes()
    seen = set()

    for el in metadata_el.findall(f"{{{DC_NS}}}identifier"):
        raw_value = (el.text or "").strip()
        if not raw_value:
            continue
        raw_scheme = el.get(f"{{{OPF_NS}}}scheme") or ""
        match = _classify(raw_value, raw_scheme, rules)

        dedupe_key = (match.matched_scheme, match.normalized_value)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        summary.identifiers.append(match)

    return summary
