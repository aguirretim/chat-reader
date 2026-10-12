"""Makes chat text sound right when a voice reads it. Pure functions, standard library only."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_DATA = Path(__file__).parent / "data"
ABBREVIATIONS: dict[str, str] = json.loads((_DATA / "abbreviations.json").read_text("utf-8"))

# ---------------------------------------------------------------- numbers
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = ["", "thousand", "million", "billion", "trillion"]
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
MAX_SPOKEN_DIGITS = 12


def _under_thousand(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else ""))
    elif n or not parts:
        parts.append(_ONES[n])
    return " ".join(parts)


def number_words(n: int) -> str:
    if n < 0:
        return "minus " + number_words(-n)
    if n < 1000:
        return _under_thousand(n)
    groups = []
    scale = 0
    while n and scale < len(_SCALES):
        n, chunk = divmod(n, 1000)
        if chunk:
            groups.append(_under_thousand(chunk) + (" " + _SCALES[scale] if _SCALES[scale] else ""))
        scale += 1
    return " ".join(reversed(groups))


def ordinal_words(n: int) -> str:
    words = number_words(n)
    head, sep, last = words.rpartition(" ")
    pre, dash, tail = last.rpartition("-")
    if tail in _ORD:
        tail = _ORD[tail]
    elif tail.endswith("y"):
        tail = tail[:-1] + "ieth"
    else:
        tail += "th"
    return head + sep + pre + dash + tail


def _digits(s: str) -> str:
    return " ".join(_ONES[int(c)] for c in s)


def _decimal(whole: str, frac: str) -> str:
    return number_words(int(whole)) + " point " + _digits(frac)


_CURRENCY = {"$": ("dollar", "cent"), "£": ("pound", "penny"), "€": ("euro", "cent")}
_SUFFIX = {"k": "thousand", "m": "million", "b": "billion"}

_RE_MONEY = re.compile(r"([$£€])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?([kmb])?\b", re.I)
_RE_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\s?(am|pm)?\b", re.I)
_RE_ORD = re.compile(r"\b(\d{1,4})(st|nd|rd|th)\b", re.I)
_RE_PCT = re.compile(r"(\d+(?:\.\d+)?)%")
_RE_KMB = re.compile(r"\b(\d{1,3})(?:\.(\d))?(?:([kmb])| (k))\b", re.I)
_RE_VS = re.compile(r"\b(\d)v(\d)\b", re.I)
_RE_TIMES = re.compile(r"\b(?:x(\d{1,3})|(\d{1,3})x)\b", re.I)
_RE_SCORE = re.compile(r"\b(\d{1,3})\s?[-:]\s?(\d{1,3})\b")
_RE_FRACTION = re.compile(r"\b(\d{1,3})/(\d{1,3})\b")
_RE_NUM = re.compile(r"(?<![\w.])([-+])?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])")
_RE_LEFTOVER_DIGITS = re.compile(r"\d+")


def _plural(word: str, n: int) -> str:
    if n == 1:
        return word
    return "pence" if word == "penny" else word + "s"


def _money(m: re.Match[str]) -> str:
    unit, sub = _CURRENCY[m.group(1)]
    whole = int(m.group(2).replace(",", ""))
    frac, suf = m.group(3), (m.group(4) or "").lower()
    if suf:
        amount = number_words(whole) + (" point " + _digits(frac) if frac else "") + " " + _SUFFIX[suf]
        return f" {amount} {unit}s "
    out = f"{number_words(whole)} {_plural(unit, whole)}"
    if frac and int(frac):
        cents = int(frac.ljust(2, "0"))
        out += f" {number_words(cents)}" if whole else ""
        if not whole:
            out = f"{number_words(cents)} {_plural(sub, cents)}"
    return f" {out} "


def _time(m: re.Match[str]) -> str:
    h, mnt, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if mnt == 0:
        mid = "" if ap else " o'clock"
    elif mnt < 10:
        mid = " oh " + _ONES[mnt]
    else:
        mid = " " + number_words(mnt)
    tail = " " + " ".join(ap) if ap else ""
    return f" {number_words(h)}{mid}{tail} "


def _plain_number(m: re.Match[str]) -> str:
    neg, whole, frac = m.group(1), m.group(2).replace(",", ""), m.group(3)
    if len(whole) > MAX_SPOKEN_DIGITS or (len(whole) > 6 and int(whole) % 10 ** (len(whole) - 2)):
        return " a big number "  # nobody wants to hear "twenty-two million two hundred twenty-two thousand ..."
    if len(whole) > 1 and whole.startswith("0"):
        words = _digits(whole)
    elif len(whole) == 4 and "," not in m.group(2) and 1100 <= int(whole) <= 2099 and not frac and int(whole) % 100:
        hi, lo = divmod(int(whole), 100)  # years and similar: 1999, 2026
        words = number_words(int(whole)) if hi == 20 and lo < 10 else number_words(hi) + (" oh " + _ONES[lo] if lo < 10 else " " + number_words(lo))
    else:
        words = number_words(int(whole))
    if frac:
        words += " point " + _digits(frac)
    sign = {"-": "minus ", "+": "plus "}.get(neg or "", "")
    return f" {sign}{words} "


def speak_numbers(text: str) -> str:
    """Money, times, ordinals, percentages, 5k, 1v1, x2, scores and plain numbers -> words."""
    if not any(ch.isdigit() for ch in text):
        return text
    text = _RE_MONEY.sub(_money, text)
    text = _RE_TIME.sub(_time, text)
    text = _RE_ORD.sub(lambda m: f" {ordinal_words(int(m.group(1)))} ", text)
    text = _RE_PCT.sub(lambda m: f" {_decimal(*m.group(1).split('.')) if '.' in m.group(1) else number_words(int(m.group(1)))} percent ", text)
    text = _RE_KMB.sub(lambda m: f" {number_words(int(m.group(1)))}{' point ' + _ONES[int(m.group(2))] if m.group(2) else ''} {_SUFFIX[(m.group(3) or m.group(4)).lower()]} ", text)
    text = _RE_VS.sub(lambda m: f" {_ONES[int(m.group(1))]} v {_ONES[int(m.group(2))]} ", text)
    text = _RE_TIMES.sub(lambda m: f" times {number_words(int(m.group(1) or m.group(2)))} ", text)
    text = _RE_FRACTION.sub(lambda m: f" {number_words(int(m.group(1)))} out of {number_words(int(m.group(2)))} " if int(m.group(2)) in (5, 10, 100) else f" {number_words(int(m.group(1)))} {number_words(int(m.group(2)))} ", text)
    text = _RE_SCORE.sub(lambda m: f" {number_words(int(m.group(1)))} {number_words(int(m.group(2)))} ", text)
    text = _RE_NUM.sub(_plain_number, text)
    # digits glued to letters ("ps5", "mp4"): say short ones, drop long ones
    text = _RE_LEFTOVER_DIGITS.sub(lambda m: f" {number_words(int(m.group()))} " if len(m.group()) <= 4 else " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


# ---------------------------------------------------------------- links
_RE_URL = re.compile(
    r"(?:https?://|www\.)\S+|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|net|org|tv|gg|io|co|me|ly|be|app|dev|xyz|live|info|uk|de|fr|ru|us|ca|au)(?:/\S*)?(?=$|[\s,!?)])",
    re.I,
)


def handle_links(text: str, mode: str = "drop") -> tuple[str, int]:
    """mode: 'drop' removes links, 'say' replaces each with the word 'link'."""
    count = 0

    def repl(_m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return " link " if mode == "say" else " "

    return _RE_URL.sub(repl, text), count


# ---------------------------------------------------------------- characters
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍‎‏⁠⁡⁢⁣⁤﻿­͏؜᠎\U000e0000"))
_QUOTES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'", "“": '"', "”": '"', "…": "...", "–": "-", "—": "-"})
_RE_LETTER_RUN = re.compile(r"([^\W\d_])\1{2,}")
_RE_PUNCT_RUN = re.compile(r"([!?.,;:~*\-_=+#])\1+")
_RE_MIXED_PUNCT = re.compile(r"[!?]{2,}")
_RE_EMOTICON = re.compile(r"(?<!\w)(?:[:;=8xX]['\-^o]?[)(\]\[DPpOo3/\\|*><cC]+|[)(\]\[DC]['\-^o]?[:;=]|<3+|\^[_\-.]?\^|[oO0T>][_.][oO0T<]|-_-|:[a-z]:|uwu|owo)(?!\w)")
_BRAILLE_OR_BOX = re.compile(r"[⠀-⣿─-▟■-◿]")


def clean_characters(text: str) -> str:
    """NFKC (fullwidth and fancy letters -> plain), zero-width characters removed."""
    text = unicodedata.normalize("NFKC", text.translate(_ZERO_WIDTH))
    return text.translate(_QUOTES)


def is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF or 0x2190 <= cp <= 0x21FF
        or 0x2300 <= cp <= 0x23FF or cp in (0x200D, 0xFE0F, 0x20E3, 0x3030, 0x303D, 0x3297, 0x3299, 0xA9, 0xAE)
        or 0xE0020 <= cp <= 0xE007F
    )


def handle_emoji(text: str, mode: str = "drop", max_named: int = 2) -> tuple[str, int]:
    """mode 'drop' removes emoji; 'name' says up to max_named of them using their Unicode names."""
    out = []
    count = 0
    named: list[str] = []
    for ch in text:
        if not is_emoji(ch):
            out.append(ch)
            continue
        if ord(ch) in (0x200D, 0xFE0F, 0x20E3) or 0x1F3FB <= ord(ch) <= 0x1F3FF or 0xE0020 <= ord(ch) <= 0xE007F:
            continue  # joiners, variation selectors, skin tones
        count += 1
        if mode == "name":
            name = unicodedata.name(ch, "").lower()
            if name and name not in named and len(named) < max_named:
                named.append(name)
                out.append(f" {name} ")
            else:
                out.append(" ")
        else:
            out.append(" ")
    return "".join(out), count


def looks_like_art(text: str) -> bool:
    """ASCII/Braille art and symbol walls: nothing a voice should attempt."""
    if len(_BRAILLE_OR_BOX.findall(text)) >= 6:
        return True
    visible = [ch for ch in text if not ch.isspace()]
    if len(visible) < 12:
        return False
    wordy = sum(ch.isalnum() for ch in visible)
    return wordy / len(visible) < 0.45


def collapse_repeats(text: str) -> str:
    """'LOOOOOL' -> 'LOOL', '!!!!!' -> '!', '?!?!?!' -> '?'."""
    text = _RE_LETTER_RUN.sub(r"\1\1", text)
    text = _RE_MIXED_PUNCT.sub(lambda m: "?" if "?" in m.group() else "!", text)
    return _RE_PUNCT_RUN.sub(r"\1", text)


def drop_emoticons(text: str) -> str:
    return _RE_EMOTICON.sub(" ", text)


def dedupe_phrases(text: str, max_repeat: int = 2) -> tuple[str, bool]:
    """Copypasta and emote walls: 'gg gg gg gg gg' -> 'gg gg'. Also repeated word groups."""
    words = text.split()
    changed = False
    for size in (4, 3, 2, 1):
        out: list[str] = []
        i = 0
        while i < len(words):
            chunk = words[i : i + size]
            reps = 1
            while len(chunk) == size and [w.lower() for w in words[i + reps * size : i + (reps + 1) * size]] == [w.lower() for w in chunk]:
                reps += 1
            keep = min(reps, max_repeat if size == 1 else 1)
            if reps > keep:
                changed = True
            out.extend(chunk * keep)
            i += size * reps
        words = out
    return " ".join(words), changed


_VOWELS = set("aeiouy")


def is_keyboard_mash(word: str) -> bool:
    """'asdfghjklqwrt' and similar: long, and almost no vowels or one long consonant run."""
    w = word.lower()
    if len(w) < 9 or not w.isalpha():
        return False
    vowels = sum(ch in _VOWELS for ch in w)
    longest = max((len(m) for m in re.findall(r"[^aeiouy]+", w)), default=0)
    return vowels / len(w) < 0.15 or longest >= 7


def soften_caps(text: str) -> str:
    """ALL CAPS words -> lower case so the voice does not spell them out; short known acronyms stay."""
    def fix(m: re.Match[str]) -> str:
        w = m.group()
        if w.lower() in ABBREVIATIONS or len(w) <= 1:
            return w
        return w.lower()

    return re.sub(r"\b[A-Z]{2}[A-Z']*\b", fix, text)  # two capitals in a row, so "I'm" is left alone


_RE_WORD = re.compile(r"(?<![A-Za-z0-9'])[A-Za-z][A-Za-z0-9']*(?:/(?=\s|$))?")


def expand_abbreviations(text: str) -> str:
    """Table lookup, whole words only. A value of '' removes the word; '=' keeps it as typed."""
    def repl(m: re.Match[str]) -> str:
        sub = ABBREVIATIONS.get(m.group().lower())
        if sub is None or sub == "=":
            return m.group()
        return sub

    return _RE_WORD.sub(repl, text)


def tidy(text: str) -> str:
    text = re.sub(r"[#_]+", " ", text)  # hashtags and snake_case
    text = re.sub(r"[^\w\s.,!?'%&:;()\-/]", " ", text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    text = re.sub(r"^[\s.,!?;:\-/()]+|[\s,;:\-/(]+$", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def limit_words(text: str, max_words: int, suffix: str = "") -> tuple[str, bool]:
    words = text.split()
    if len(words) <= max_words:
        return text, False
    return " ".join(words[:max_words]).rstrip(",;:-") + (" " + suffix if suffix else ""), True


# ---------------------------------------------------------------- names
_RE_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NAME_LEET = str.maketrans("013457", "oieast")
_NAME_NOISE = {"xx", "xxx", "x", "tv", "ttv", "yt", "o", "the", "its", "im"}


_RE_MENTION = re.compile(r"(?<![\w@])@(\w{2,25})")


def speak_mentions(text: str, drop: frozenset[str] = frozenset()) -> str:
    """'@Cool_Guy_77 hi' -> 'Cool Guy hi'. Names in `drop` (the streamer) are removed."""
    def repl(m: re.Match[str]) -> str:
        if m.group(1).lower() in drop:
            return " "
        return sayable_name(m.group(1)) or " "

    return _RE_MENTION.sub(repl, text)


def sayable_name(name: str, max_words: int = 3) -> str:
    """'xX_Dark_Sl4yer_99_Xx' -> 'Dark Slayer'. Empty string if nothing sayable is left."""
    name = clean_characters(name)
    parts = re.split(r"[\W_]+", name)
    words: list[str] = []
    for part in parts:
        for piece in _RE_CAMEL.split(part):
            if not piece:
                continue
            # digits at either end are decoration ("99"); digits inside a word are letters ("sl4yer")
            if piece.isdigit():
                continue
            digits = sum(c.isdigit() for c in piece)
            letters = sum(c.isalpha() for c in piece)
            leet_like = digits and not re.search(r"\d{3,}", piece) and all(c in "013457" for c in piece if c.isdigit()) and letters <= digits + 1
            if not leet_like:
                # digits at either end are decoration ("99"); digits inside a word are letters ("sl4yer")
                piece = re.sub(r"^\d+(?=[A-Za-z])|(?<=[A-Za-z])\d+$", "", piece)
            if any(c.isdigit() for c in piece):
                piece = piece.translate(_NAME_LEET)
                piece = re.sub(r"\d+", "", piece)
            words.extend(w for w in re.split(r"(?<=[a-z])(?=[A-Z])", piece) if w)
    merged: list[str] = []
    for w in words:  # "KPazAh" -> "K", "Paz", "Ah": glue stray single letters back on
        if merged and (len(w) == 1 or len(merged[-1]) == 1):
            merged[-1] += w
        else:
            merged.append(w)
    words = merged
    if words and latin_share("".join(words)) < 0.5:
        return ""  # a name an English voice cannot read; the caller falls back to the login
    kept = [w for w in words if w.lower() not in _NAME_NOISE] or words
    kept = [collapse_repeats(w) for w in kept][:max_words]
    return " ".join(kept)


# ---------------------------------------------------------------- script
def latin_share(text: str) -> float:
    """Share of letters that are Latin. Cheap stand-in for language detection."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 1.0
    latin = sum(1 for ch in letters if ch.isascii() or "LATIN" in unicodedata.name(ch, ""))
    return latin / len(letters)


# ---------------------------------------------------------------- personal details
_RE_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)|(?<!\d)\+\d{10,14}(?!\d)")
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_RE_IP = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_RE_ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Za-z0-9.']+\s){1,4}(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|court|ct|way|place|pl|terrace|circle|cir)\b\.?(?:\s+(?:apt|unit|#)\s?\w+)?",
    re.I,
)
_RE_LONG_ID = re.compile(r"(?<![\d.])\d(?:[\s\-]?\d){8,}(?![\d%])")


def personal_details(text: str) -> str | None:
    """Returns what kind of personal detail the text seems to carry, or None."""
    if _RE_EMAIL.search(text):
        return "email"
    if _RE_PHONE.search(text):
        return "phone"
    m = _RE_IP.search(text)
    if m and all(int(p) <= 255 for p in m.group().split(".")):
        return "ip_address"
    if _RE_ADDRESS.search(text):
        return "street_address"
    if _RE_LONG_ID.search(text):
        return "long_number"
    return None
