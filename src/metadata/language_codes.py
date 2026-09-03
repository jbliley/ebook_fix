"""
metadata.language_codes

Calibre stores language as a three-letter ISO 639-2 "bibliographic"
code (e.g. "eng"), while EPUB's dc:language is conventionally a
two-letter ISO 639-1 code (e.g. "en"). Both are correct for their own
format -- this isn't a real disagreement, just two standards' native
shapes for the same language -- so metadata.merge needs a way to
recognize "eng" and "en" (and every other such pair) as equivalent
instead of flagging every Calibre-managed book's language as a
MISMATCH. See docs/metadata_plan.md, "Open questions".

This table only needs to cover the two-letter <-> three-letter
mapping; it isn't a language-detection or validation tool, so an
unrecognized code on either side just falls back to a plain string
comparison (still correctly flagged as a mismatch if the two actually
differ).
"""
from __future__ import annotations

# ISO 639-1 (two-letter) -> ISO 639-2/B (three-letter, "bibliographic")
# code, which is what Calibre's own language list uses (e.g. "ger" and
# "fre" rather than the terminological "deu"/"fra"). Source: the
# published ISO 639 code tables.
ALPHA2_TO_ALPHA3: dict[str, str] = {
    "aa": "aar", "ab": "abk", "ae": "ave", "af": "afr", "ak": "aka",
    "am": "amh", "an": "arg", "ar": "ara", "as": "asm", "av": "ava",
    "ay": "aym", "az": "aze", "ba": "bak", "be": "bel", "bg": "bul",
    "bh": "bih", "bi": "bis", "bm": "bam", "bn": "ben", "bo": "tib",
    "br": "bre", "bs": "bos", "ca": "cat", "ce": "che", "ch": "cha",
    "co": "cos", "cr": "cre", "cs": "cze", "cu": "chu", "cv": "chv",
    "cy": "wel", "da": "dan", "de": "ger", "dv": "div", "dz": "dzo",
    "ee": "ewe", "el": "gre", "en": "eng", "eo": "epo", "es": "spa",
    "et": "est", "eu": "baq", "fa": "per", "ff": "ful", "fi": "fin",
    "fj": "fij", "fo": "fao", "fr": "fre", "fy": "fry", "ga": "gle",
    "gd": "gla", "gl": "glg", "gn": "grn", "gu": "guj", "gv": "glv",
    "ha": "hau", "he": "heb", "hi": "hin", "ho": "hmo", "hr": "hrv",
    "ht": "hat", "hu": "hun", "hy": "arm", "hz": "her", "ia": "ina",
    "id": "ind", "ie": "ile", "ig": "ibo", "ii": "iii", "ik": "ipk",
    "io": "ido", "is": "ice", "it": "ita", "iu": "iku", "ja": "jpn",
    "jv": "jav", "ka": "geo", "kg": "kon", "ki": "kik", "kj": "kua",
    "kk": "kaz", "kl": "kal", "km": "khm", "kn": "kan", "ko": "kor",
    "kr": "kau", "ks": "kas", "ku": "kur", "kv": "kom", "kw": "cor",
    "ky": "kir", "la": "lat", "lb": "ltz", "lg": "lug", "li": "lim",
    "ln": "lin", "lo": "lao", "lt": "lit", "lu": "lub", "lv": "lav",
    "mg": "mlg", "mh": "mah", "mi": "mao", "mk": "mac", "ml": "mal",
    "mn": "mon", "mr": "mar", "ms": "may", "mt": "mlt", "my": "bur",
    "na": "nau", "nb": "nob", "nd": "nde", "ne": "nep", "ng": "ndo",
    "nl": "dut", "nn": "nno", "no": "nor", "nr": "nbl", "nv": "nav",
    "ny": "nya", "oc": "oci", "oj": "oji", "om": "orm", "or": "ori",
    "os": "oss", "pa": "pan", "pi": "pli", "pl": "pol", "ps": "pus",
    "pt": "por", "qu": "que", "rm": "roh", "rn": "run", "ro": "rum",
    "ru": "rus", "rw": "kin", "sa": "san", "sc": "srd", "sd": "snd",
    "se": "sme", "sg": "sag", "si": "sin", "sk": "slo", "sl": "slv",
    "sm": "smo", "sn": "sna", "so": "som", "sq": "alb", "sr": "srp",
    "ss": "ssw", "st": "sot", "su": "sun", "sv": "swe", "sw": "swa",
    "ta": "tam", "te": "tel", "tg": "tgk", "th": "tha", "ti": "tir",
    "tk": "tuk", "tl": "tgl", "tn": "tsn", "to": "ton", "tr": "tur",
    "ts": "tso", "tt": "tat", "tw": "twi", "ty": "tah", "ug": "uig",
    "uk": "ukr", "ur": "urd", "uz": "uzb", "ve": "ven", "vi": "vie",
    "vo": "vol", "wa": "wln", "wo": "wol", "xh": "xho", "yi": "yid",
    "yo": "yor", "za": "zha", "zh": "chi", "zu": "zul",
}

# A handful of languages have a different three-letter code depending
# on whether it's "bibliographic" (B, the older/traditional form) or
# "terminological" (T, derived from the native name). Calibre uses B
# codes, but some EPUBs (or other tools) may have written the T code
# instead, so both are accepted as equivalent to the same alpha-2 code.
ALPHA3_T_TO_B: dict[str, str] = {
    "sqi": "alb", "hye": "arm", "eus": "baq", "mya": "bur", "zho": "chi",
    "ces": "cze", "nld": "dut", "fra": "fre", "kat": "geo", "deu": "ger",
    "ell": "gre", "isl": "ice", "mkd": "mac", "mri": "mao", "msa": "may",
    "fas": "per", "ron": "rum", "slk": "slo", "bod": "tib", "cym": "wel",
}


def _normalize(code: str) -> str:
    """Lowercases and strips a region/script subtag (e.g. 'en-US',
    'zh-Hans') down to the bare language code, for comparison only."""
    return code.strip().lower().split("-")[0]


def codes_equivalent(code_a: str, code_b: str) -> bool:
    """True if code_a and code_b are the same language, allowing for
    ISO 639-1 vs ISO 639-2 (B or T) and region-subtag differences.
    Two identical strings are always equivalent; two empty/blank
    strings are not (there's nothing to compare)."""
    if not code_a or not code_b:
        return False

    a = _normalize(code_a)
    b = _normalize(code_b)
    if a == b:
        return True

    # Fold any terminological three-letter code down to its
    # bibliographic form before comparing further.
    a = ALPHA3_T_TO_B.get(a, a)
    b = ALPHA3_T_TO_B.get(b, b)
    if a == b:
        return True

    return ALPHA2_TO_ALPHA3.get(a) == b or ALPHA2_TO_ALPHA3.get(b) == a
