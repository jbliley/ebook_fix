"""
ebook_fix.language_detect

Guesses what language a book's text is written in, for books whose
dc:language is blank or "undetermined" (HTML files, many converted MOBI
and FB2 books) or whose language looks wrong.

Hand-rolled, no outside library (the project's rule): two steps.

1. Script. Cyrillic, Greek, Hebrew, Arabic, Devanagari, Thai, Hangul,
   Japanese kana, Chinese characters and so on each point at a small set
   of languages, told apart by a few letters only they use (Ukrainian
   "ї", Russian "ы", Persian "گ", ...).
2. Latin script: the share of the text made of each language's most
   common small words ("the", "und", "que", "het" ...). The words in each
   list are ones the other listed languages do not use often, since a
   share is only useful if the lists barely overlap.

The answer is only given when it is clear: enough text, a high share, and
a clear lead over the runner-up (Danish and Norwegian, say, share most of
their small words, so an unclear case gives no answer at all). A wrong
language written into a book is worse than none, so "I can't tell" is a
normal result.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass

MIN_TOKENS = 250              # less text than this: no answer
MIN_SHARE = 0.10              # the best language's small words must be at least this share of the words
MIN_LEAD = 1.35               # ...and this many times the runner-up's share
SAMPLE_TOKENS = 40000         # long books are sampled, evenly spaced, down to about this many words

_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)

_WORDS = {
    "en": "the and of to was he she it that with his her had you for they on but at not from by said there what were would have been them their which one then this could when who him will out up if about into than we my me your all are or as be an so no do did me just like over only know see now how back down way our us two".split(),
    "es": "de la que el en y los se del las un por con una su para es al lo como más pero sus le ya fue este ha sí porque esta entre cuando muy sin sobre también hasta hay donde quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos esto antes algunos qué unos yo otro otras otra él tanto esa estos mucho nada muchos cual poco ella estar estas algo nosotros mi mis tú te ti tu tus ellas era había estaba dijo".split(),
    "fr": "de la le les et des en un du une que est pour qui dans par plus pas au sur ne se ce il sont elle avec son ses mais comme ou leur nous vous je tout être fait cette aux ont sans était été où même très bien peut ils elles aussi dit moi lui toute tous quand qu j l c n d s y rien alors cela ça".split(),
    "de": "der die und den von zu das mit sich des auf für ist im dem nicht ein eine als auch es an werden aus er hat dass sie nach wird bei einer um am sind noch wie einem über einen so zum war haben nur oder aber vor zur bis mehr durch man sein wurde sei ich ihr wir was wenn mir mich ihm ihn dich du uns schon hier dann sagte wieder immer".split(),
    "it": "di il che per un del non una le si con da sono al dei della lo più come ma anche nel gli alla delle ha se io lui lei mi ci ti tutto questo quello suo sua loro cosa quando perché così molto ancora solo mai già essere fatto detto era aveva disse nella degli sul sulla negli nei".split(),
    "pt": "de que do da em um para é com não uma os no se na por mais as dos como mas foi ao ele das tem seu sua ou ser quando muito há nos já está eu também só pelo pela até isso ela entre era depois sem mesmo aos ter seus quem nas me esse eles estão você tinha foram essa num nem suas meu às minha têm numa pelos elas havia seja qual será nós tenho lhe deles essas esses pelas este fosse dele disse".split(),
    "nl": "de het een en van ik te dat die niet zijn op aan met als voor had er maar om hij dan zou of wat mijn men dit zo ze wordt over ook tot je mij uit naar heb hoe nog heeft hem haar wel geen door meer deze zij bij kan toen nu weer werd nooit altijd iets zich ons onze jij wij jullie doen".split(),
    "sv": "och att det som en på är av för med till den har de inte om ett men jag han var sig så vi från hon kan ska vid eller när man mig honom hade sin sitt också bara nu här ut efter vara ingen blev upp vad mycket".split(),
    "da": "og i at det en den til er som på de med han af for ikke der var mig sig men et har om vi min havde ham hun nu over da fra du ud sin dem os op man hans hvor eller hvad skal selv her alle vil blev kunne ind når være dog noget ville jo deres efter ned skulle denne end dette mit også under have dig anden nogen meget sådan".split(),
    "no": "og i det på som er en til å med for av at ikke den har de et om var jeg han men fra vi så seg hun kan ble eller være hadde ham meg da nå ut skal bare etter også mot hva deg noen hvor ville dette denne mye veldig".split(),
    "pl": "i w nie na z do się to że jest o jak ale po co tak za od go już czy tym przez być ja mnie tego był ten dla jego ma tylko są jej może ich żeby jeszcze bardzo gdy kiedy który mi mu nic było ona on my wy ty tu tam bo więc ani lub".split(),
    "cs": "a se na že je v s to z do o i k pro ale jako by byl byla bylo tak si jsem jsou jeho už jen když má ze od po bude nebo ještě před ho být který která které co jak tam tu ani ten ta tom ve".split(),
    "fi": "ja on oli että ei se hän mutta kun ole niin kuin joka vain sen mitä minä sinä ne tai jos mikä olla jo nyt vielä kyllä tämä ovat olen ollut hänen mukaan siitä sitten kanssa tässä".split(),
    "tr": "bir ve bu da de için ne ile çok o ben sen gibi daha kadar ama var değil mi ki ya her şey sonra olarak olan ise onu onun bana sana".split(),
    "hu": "a az és hogy nem is egy de meg el volt ez még már csak ha mint vagy van azt ő én te mi ti ők nagyon után között".split(),
    "ro": "și de în la cu un o pe că nu se este care din mai să ce sau dar au pentru fi am fost el ea noi voi ei ele".split(),
}
_SETS = {code: frozenset(words) for code, words in _WORDS.items()}
# Words several languages share count for none of them.
_SHARED = set()
_seen = {}
for _code, _set in _SETS.items():
    for _w in _set:
        _seen.setdefault(_w, set()).add(_code)
for _w, _codes in _seen.items():
    if len(_codes) > 2:
        _SHARED.add(_w)
_SETS = {code: frozenset(s - _SHARED) for code, s in _SETS.items()}

# (regex of the script, code, letters that rule it in) for scripts that point at one language
_SCRIPT_SINGLE = [
    (re.compile(r"[Ͱ-Ͽἀ-῿]"), "el"),
    (re.compile(r"[֐-׿]"), "he"),
    (re.compile(r"[ऀ-ॿ]"), "hi"),
    (re.compile(r"[฀-๿]"), "th"),
    (re.compile(r"[가-힯ᄀ-ᇿ]"), "ko"),
    (re.compile(r"[԰-֏]"), "hy"),
    (re.compile(r"[Ⴀ-ჿ]"), "ka"),
    (re.compile(r"[ঀ-৿]"), "bn"),
    (re.compile(r"[஀-௿]"), "ta"),
]
_KANA = re.compile(r"[぀-ヿ]")
_HAN = re.compile(r"[一-鿿]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")

_RU_WORDS = frozenset("и в не на что он с как это я но то все она так его по из за от бы да для же вы мы был было ты её их они".split())
_BG_WORDS = frozenset("и в не на че се от за да той с как това са по със ще има като но или беше бяха".split())


@dataclass
class Detection:
    code: str = ""          # two-letter code, or "" when it can't be told
    share: float = 0.0      # how much of the text the winning language's small words made up
    lead: float = 0.0       # how many times the runner-up's share that is


def _sample(text: str) -> str:
    """Long books are cut to evenly spaced slices, so the front matter
    and the end of the book don't decide the answer by themselves."""
    if len(text) <= SAMPLE_TOKENS * 6:
        return text
    slice_len = 20000
    count = max(2, (SAMPLE_TOKENS * 6) // slice_len)
    step = (len(text) - slice_len) // (count - 1)
    return " ".join(text[i * step:i * step + slice_len] for i in range(count))


def _count(text: str, pattern) -> int:
    return len(pattern.findall(text))


def _detect_cyrillic(text: str, tokens: list) -> str:
    lower = text.lower()
    if any(ch in lower for ch in "їєґ"):
        return "uk"
    if "ў" in lower:
        return "be"
    if any(ch in lower for ch in "јљњћђ"):
        return "sr"
    ru = sum(1 for t in tokens if t in _RU_WORDS)
    bg = sum(1 for t in tokens if t in _BG_WORDS)
    if any(ch in lower for ch in "ыэё"):
        return "ru"
    if "ъ" in lower and bg > ru:
        return "bg"
    return "ru" if ru >= bg else "bg"


def _detect_arabic(text: str) -> str:
    if any(ch in text for ch in "پچژگ"):
        return "fa"
    if any(ch in text for ch in "ٹڈڑںے"):
        return "ur"
    return "ar"


def detect_language(text: str) -> Detection:
    """What language `text` is written in. `Detection.code` is "" when
    the text is too short or the answer isn't clear."""
    text = _sample(text or "")
    letters = _count(text, re.compile(r"[^\W\d_]", re.UNICODE))
    if letters < MIN_TOKENS * 3:
        return Detection()

    # -- scripts that name a language (or a small family) by themselves
    for pattern, code in _SCRIPT_SINGLE:
        if _count(text, pattern) > letters * 0.5:
            return Detection(code=code, share=1.0, lead=99.0)
    han, kana = _count(text, _HAN), _count(text, _KANA)
    if (han + kana) > letters * 0.5:
        return Detection(code="ja" if kana > han * 0.1 else "zh", share=1.0, lead=99.0)
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    if _count(text, _CYRILLIC) > letters * 0.5:
        return Detection(code=_detect_cyrillic(text, tokens), share=1.0, lead=99.0)
    if _count(text, _ARABIC) > letters * 0.5:
        return Detection(code=_detect_arabic(text), share=1.0, lead=99.0)

    # -- Latin script: share of each language's small words
    if _count(text, _LATIN) < letters * 0.5:
        return Detection()
    text_n = text.replace("’", "'").replace("‘", "'")
    tokens = [t.lower() for t in _TOKEN.findall(text_n)]
    if len(tokens) < MIN_TOKENS:
        return Detection()
    total = len(tokens)
    scores = sorted(
        ((sum(1 for t in tokens if t in words) / total, code) for code, words in _SETS.items()),
        reverse=True,
    )
    (best, code), (second, other) = scores[0], scores[1]
    if {code, other} == {"da", "no"}:
        # Danish and Norwegian share most small words, so only the words
        # one of them owns decide between the two (and only clearly).
        only_da = _SETS["da"] - _SETS["no"]
        only_no = _SETS["no"] - _SETS["da"]
        da_hits = sum(1 for t in tokens if t in only_da)
        no_hits = sum(1 for t in tokens if t in only_no)
        winner, loser = ("da", "no") if da_hits >= no_hits else ("no", "da")
        high, low = max(da_hits, no_hits), min(da_hits, no_hits)
        if high / total < 0.01 or high < low * 2:
            return Detection(share=best, lead=1.0)
        return Detection(code=winner, share=best, lead=high / max(low, 1))
    lead = best / second if second else 99.0
    if best < MIN_SHARE or lead < MIN_LEAD:
        return Detection(share=best, lead=lead)
    return Detection(code=code, share=best, lead=lead)


# ---------------------------------------------------------------------
# Getting the text of a book
# ---------------------------------------------------------------------

_TAGS = re.compile(r"<[^>]+>")
_STYLE_BLOCKS = re.compile(r"<(?:style|script)\b.*?</(?:style|script)>", re.S | re.I)


def plain_text(markup: str) -> str:
    import html
    return html.unescape(_TAGS.sub(" ", _STYLE_BLOCKS.sub(" ", markup)))


def detect_from_pages(pages: list) -> Detection:
    """`pages` is [(title, body_html)], as the EPUB builder holds them."""
    return detect_language(" ".join(plain_text(body) for _title, body in pages))


def detect_from_epub(path) -> Detection:
    """Reads the page text out of an EPUB file and detects its language.
    Navigation documents are skipped; a book that can't be read just gives
    no answer."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
            names = [n for n in names if "nav" not in n.rsplit("/", 1)[-1].lower() and "toc" not in n.rsplit("/", 1)[-1].lower()]
            parts = []
            for n in sorted(names):
                parts.append(plain_text(zf.read(n).decode("utf-8", errors="replace")))
        return detect_language(" ".join(parts))
    except (OSError, zipfile.BadZipFile, KeyError):
        return Detection()
