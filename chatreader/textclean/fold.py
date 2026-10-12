"""Character-trick normalisation.

Turns what a troll typed into the plain letters a voice would read, so the
blocklist can be checked against one canonical form:

    NFKC  ->  strip invisible / combining characters  ->  fold look-alike
    letters to ASCII (Unicode confusables table)  ->  lower-case  ->  symbol
    and digit swaps  ->  "atoms" (runs of letters/digits)

Nothing here is used for the text that is spoken; it only builds the form
that is matched.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "data"

# Built by tools/build_data.py from Unicode confusables.txt: {char: ascii letter or digit}
_CONFUSABLE: dict[str, str] = json.loads((_DATA / "confusables_ascii.json").read_text("utf-8"))

# Letters drawn as symbols that have no compatibility mapping and are not in confusables.txt.
for _base, _start in (("a", 0x1F150), ("a", 0x1F170), ("a", 0x1F1E6), ("a", 0x1F130)):
    for _i in range(26):
        _CONFUSABLE.setdefault(chr(_start + _i), chr(ord(_base) + _i))

# Symbols typed in place of a letter. Only applied next to a letter (see _symbol_swaps).
_SYMBOL = {"@": "a", "$": "s", "!": "i", "|": "l", "+": "t", "€": "e", "£": "l", "¢": "c", "¥": "y", "§": "s"}

# Digit swaps. Three readings are tried: look-alike with 1=i, look-alike with 1=l,
# and the way the digit sounds ("gr8" -> "grate").
_LEET_I = str.maketrans("0123456789", "oizeasgtbg")
_LEET_L = str.maketrans("0123456789", "olzeasgtbg")
_SOUND = {"0": "o", "1": "one", "2": "to", "3": "e", "4": "for", "5": "s", "6": "six", "7": "t", "8": "ate", "9": "nine"}

_INVISIBLE_CATEGORIES = {"Cf", "Mn", "Me", "Cc", "Cs", "Co"}
_ATOM = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=4096)
def _fold_char(ch: str) -> str:
    """One character -> ASCII lower-case letter/digit, '' (invisible) or itself."""
    if ch.isascii():
        return ch.lower()
    cat = unicodedata.category(ch)
    if cat in _INVISIBLE_CATEGORIES:
        return ""
    hit = _CONFUSABLE.get(ch)
    if hit:
        return hit
    # Accented letters and anything else with a decomposition: keep the base letters.
    base = "".join(c for c in unicodedata.normalize("NFKD", ch) if unicodedata.category(c) not in _INVISIBLE_CATEGORIES)
    if base and base != ch:
        return "".join(_CONFUSABLE.get(c, c) for c in base).lower()
    low = ch.lower()
    return _CONFUSABLE.get(low, low)


def fold_text(text: str) -> str:
    """Canonical lower-case string. Separators (spaces, punctuation) are kept."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(_fold_char(ch) for ch in text)


def _symbol_swaps(folded: str) -> str:
    """Turn '@', '$', '!' ... into letters, but only where they sit inside a word."""
    out = []
    n = len(folded)
    for i, ch in enumerate(folded):
        sub = _SYMBOL.get(ch)
        if sub:
            prev_alpha = i > 0 and folded[i - 1].isalnum()
            next_alpha = i + 1 < n and folded[i + 1].isalnum()
            if ch in "!|+":
                ok = next_alpha and (prev_alpha or ch == "|")  # "sh!t", not "wow! ok"
            elif ch == "@":
                ok = prev_alpha  # "@name" is a mention, "b@d" is a swap
            else:
                ok = prev_alpha or next_alpha
            out.append(sub if ok else ch)
        else:
            out.append(ch)
    return "".join(out)


_WORD = re.compile(r"[^\W_]+")


def atoms(text: str) -> list[str]:
    """Runs of letters/digits in the canonical form, in order.

    A word that still holds non-Latin letters after folding is a real foreign word
    (Russian, Greek ...), not a disguise, and is left out: matching the few of its
    letters that happen to look Latin only produces false alarms.
    """
    return [w for w in _WORD.findall(_symbol_swaps(fold_text(text))) if w.isascii()]


def join_spelled(atom_list: list[str], min_run: int = 3) -> list[str]:
    """'b a n a n a' or 'b.a.n.a.n.a' -> 'banana'. Runs of single characters are glued."""
    out: list[str] = []
    run: list[str] = []
    for a in atom_list:
        if len(a) == 1:
            run.append(a)
            continue
        if run:
            out.append("".join(run)) if len(run) >= min_run else out.extend(run)
            run = []
        out.append(a)
    if run:
        out.append("".join(run)) if len(run) >= min_run else out.extend(run)
    return out


def digit_readings(atom: str) -> tuple[str, ...]:
    """All letter-only readings of an atom that mixes letters and digits."""
    if atom.isalpha():
        return (atom,)
    if atom.isdigit():
        return ()
    a = atom.translate(_LEET_I)
    b = atom.translate(_LEET_L)
    c = "".join(_SOUND.get(ch, ch) for ch in atom)
    seen = []
    for v in (a, b, c):
        if v not in seen:
            seen.append(v)
    return tuple(seen)


def rle(word: str) -> tuple[str, tuple[int, ...]]:
    """'baaanana' -> ('banana', (1, 3, 1, 1, 1, 1)). Used so stretched letters still match."""
    letters = []
    counts = []
    prev = ""
    for ch in word:
        if ch == prev:
            counts[-1] += 1
        else:
            letters.append(ch)
            counts.append(1)
            prev = ch
    return "".join(letters), tuple(counts)
