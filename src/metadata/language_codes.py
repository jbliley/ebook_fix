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


# A short, well-known subset of ISO 639-1 for the GUI's Language
# dropdown (Phase 5, docs/gui_plan.md) -- not meant to cover every
# code in ALPHA2_TO_ALPHA3 above, just what's actually likely to turn
# up in a personal library. Sorted by name at the point of use, not
# here, so this list can stay in whatever order is easiest to skim.
COMMON_LANGUAGES: list[tuple[str, str]] = [
    ("en", "English"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("it", "Italian"), ("pt", "Portuguese"), ("nl", "Dutch"), ("ru", "Russian"),
    ("ja", "Japanese"), ("zh", "Chinese"), ("ko", "Korean"), ("ar", "Arabic"),
    ("hi", "Hindi"), ("pl", "Polish"), ("sv", "Swedish"), ("no", "Norwegian"),
    ("da", "Danish"), ("fi", "Finnish"), ("cs", "Czech"), ("el", "Greek"),
    ("tr", "Turkish"), ("he", "Hebrew"), ("hu", "Hungarian"), ("ro", "Romanian"),
    ("uk", "Ukrainian"), ("id", "Indonesian"), ("vi", "Vietnamese"), ("th", "Thai"),
    ("la", "Latin"), ("ga", "Irish"), ("cy", "Welsh"), ("is", "Icelandic"),
    ("bg", "Bulgarian"), ("hr", "Croatian"), ("sr", "Serbian"), ("sk", "Slovak"),
    ("sl", "Slovenian"), ("et", "Estonian"), ("lv", "Latvian"), ("lt", "Lithuanian"),
    ("ca", "Catalan"), ("eu", "Basque"), ("af", "Afrikaans"), ("sw", "Swahili"),
    ("fa", "Persian"), ("ur", "Urdu"), ("bn", "Bengali"), ("ta", "Tamil"),
    ("eo", "Esperanto"),
]


def language_options(current_value: str) -> list[tuple[str, str]]:
    """COMMON_LANGUAGES as (code, display label) pairs, sorted by
    name, for a select dropdown. If current_value (the book's actual
    current dc:language, as opened) isn't one of the common codes, it
    gets appended as its own entry labeled by its raw code rather than
    silently dropped from the list -- an unusual existing value should
    stay visible and selected until a person actively picks something
    else, not disappear the moment this dropdown renders."""
    options = list(COMMON_LANGUAGES)
    current = (current_value or "").strip()
    if current and not any(code.lower() == current.lower() for code, _name in options):
        options.append((current, current))

    labeled = [(code, f"{name} ({code})") for code, name in options]
    return sorted(labeled, key=lambda pair: pair[1].lower())


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
