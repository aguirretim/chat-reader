"""Tiered blocklist that is checked on the canonical form built by fold.py.

Matching rules, chosen to survive the usual tricks without the Scunthorpe problem:

  exact      an atom equals a blocked word (stretched letters allowed: "baaanana"),
             optionally with a common ending (-s, -es, -ed, -er, -ers, -ing, -z, -y)
  spelled    single characters in a row are glued first ("b a n a n a", "b.a.n.a.n.a")
  joined     2 to 4 neighbouring atoms glued together equal a blocked word ("ban ana")
  contains   ONLY for long tier-1 words that almost never occur inside ordinary
             English words, and never when the atom is on the allow list
  raw        non-Latin entries are looked for as plain substrings

Short words are never matched inside longer words. That lets some evasion through
(a short blocked word glued to other letters) and is the price of not blocking
"Scunthorpe", "assassin" or "cockpit".
"""
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from pathlib import Path

from . import fold

_DATA = Path(__file__).parent / "data"
_SUFFIXES = ("s", "es", "ed", "er", "ers", "ing", "in", "z", "y")
_MAX_JOIN = 4


@dataclass(frozen=True)
class Hit:
    tier: int
    category: str
    how: str  # exact | spelled | joined | contains | raw | custom


class Blocklist:
    def __init__(self) -> None:
        # letters-without-repeats -> list of (min run lengths, tier, category, token_start_only)
        self._words: dict[str, list[tuple[tuple[int, ...], int, str, bool]]] = {}
        self._contains: list[tuple[re.Pattern[str], int, str]] = []
        self._contains_parts: dict[int, list[str]] = {}
        self._contains_cat: dict[int, str] = {}
        self._raw: list[tuple[str, int, str]] = []
        self._allow: set[str] = set()
        self._allow_phrases: set[tuple[str, ...]] = set()
        self._entries: list[tuple[str, int, str, str]] = []   # (form, tier, category, flags), as loaded

    # ---- loading -----------------------------------------------------------
    @classmethod
    def load_default(cls) -> "Blocklist":
        bl = cls()
        for line in (_DATA / "blocklist.rot13.tsv").read_text("utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            tier, cat, flags, form = line.split("\t")[:4]
            bl._add_form(codecs.decode(form, "rot13"), int(tier), cat, flags)
        allow = _DATA / "allow_words.txt"
        if allow.exists():
            bl._allow.update(allow.read_text("utf-8").split())
        extra = _DATA / "allow_extra.txt"
        if extra.exists():
            for line in extra.read_text("utf-8").splitlines():
                line = line.split("#")[0].strip().lower()
                if " " in line:
                    bl._allow_phrases.add(tuple(line.split()))
                elif line:
                    bl._allow.add(line)
        bl._compile()
        return bl

    def add(self, word: str, tier: int = 1, category: str = "custom", contains: bool = False) -> None:
        """Add a streamer's own word or phrase. It goes through the same folding as chat text."""
        parts = fold.atoms(word)
        form = "".join(parts)
        readings = fold.digit_readings(form)
        form = readings[0] if readings else form
        if form:
            self._add_form(form, tier, category, "c" if contains else "x")
            self._compile()

    def allow(self, word: str) -> None:
        self._allow.add("".join(fold.atoms(word)))

    def entries(self) -> list[tuple[str, int, str]]:
        """Every loaded word as (word, tier, category), for a settings window."""
        return [(form, tier, cat) for form, tier, cat, _ in self._entries]

    def filtered(self, off_tiers=(), off_words=()) -> "Blocklist":
        """A copy without the switched-off groups and words. A streamer's own words ("custom") ignore the groups."""
        off_t, off_w = set(off_tiers), set(off_words)
        bl = Blocklist()
        bl._allow, bl._allow_phrases = self._allow, self._allow_phrases
        for form, tier, cat, flags in self._entries:
            if form in off_w or (tier in off_t and cat != "custom"):
                continue
            bl._add_form(form, tier, cat, flags)
        bl._compile()
        return bl

    def _add_form(self, form: str, tier: int, cat: str, flags: str) -> None:
        self._entries.append((form, tier, cat, flags))
        if "r" in flags:
            self._raw.append((form, tier, cat))
            return
        letters, counts = fold.rle(form)
        self._words.setdefault(letters, []).append((counts, tier, cat, "b" in flags))
        if "c" in flags:
            self._contains_parts.setdefault(tier, []).append(
                "".join(f"{ch}{{{n},}}" if n > 1 else f"{ch}+" for ch, n in zip(letters, counts))
            )
            self._contains_cat.setdefault(tier, cat)

    def _compile(self) -> None:
        self._contains = [
            (re.compile("|".join(sorted(parts, key=len, reverse=True))), tier, self._contains_cat.get(tier, "hate"))
            for tier, parts in sorted(self._contains_parts.items())
        ]

    # ---- matching ----------------------------------------------------------
    def _exact(self, atom: str, max_tier: int, stretch: bool = True) -> tuple[int, str] | None:
        """stretch=True lets 'baaanana' match 'banana' (extra repeats of a letter)."""
        letters, counts = fold.rle(atom)
        best = None
        for need, tier, cat, start_only in self._words.get(letters, ()):
            if tier <= max_tier and (counts == need or (stretch and all(have >= n for have, n in zip(counts, need)))):
                if best is None or tier < best[0]:
                    best = (tier, cat)
        return best

    def _exact_with_endings(self, atom: str, max_tier: int) -> tuple[int, str] | None:
        hit = self._exact(atom, max_tier)
        if hit or len(atom) < 6:
            return hit
        for suf in _SUFFIXES:
            stem = atom[: -len(suf)]
            if not atom.endswith(suf) or len(stem) < 5:
                continue
            if suf == "es" and not stem.endswith(("s", "x", "z", "ch", "sh")):
                continue
            if stem:
                hit = self._exact(stem, max_tier)
                if hit:
                    return hit
        return None

    def check_atoms(self, atom_list: list[str], max_tier: int = 2) -> Hit | None:
        """atom_list comes from fold.atoms(). Returns the most serious hit, or None."""
        best: Hit | None = None

        def consider(tier: int, cat: str, how: str) -> None:
            nonlocal best
            if best is None or tier < best.tier:
                best = Hit(tier, cat, how)

        # allowed phrases ("homo sapiens") are removed before anything is checked
        if self._allow_phrases:
            atom_list = self._drop_allowed_phrases(atom_list)

        glued = fold.join_spelled(atom_list)
        spelled = {a for a in glued if a not in atom_list}

        variants: list[tuple[str, ...]] = []
        for atom in glued:
            readings = fold.digit_readings(atom)
            variants.append(readings)
            how = "spelled" if atom in spelled else "exact"
            for r in readings:
                if r in self._allow:
                    continue
                hit = self._exact_with_endings(r, max_tier)
                if hit:
                    consider(hit[0], hit[1], how)
                    continue
                if len(r) >= 5:
                    for rx, tier, cat in self._contains:
                        if tier <= max_tier and rx.search(r):
                            consider(tier, cat, "contains")
            if best and best.tier == 1:
                return best

        # Neighbouring atoms glued together must EQUAL a blocked word, letter for letter
        # (no stretched letters: "an all" is not a word with an extra l).
        # Tier 1 may span up to four atoms ("go eat a lemon"); lower tiers only two ("da mn").
        n = len(glued)
        for i in range(n):
            if not variants[i]:
                continue
            joined_options = list(variants[i])
            for j in range(i + 1, min(n, i + _MAX_JOIN)):
                if not variants[j]:
                    break
                joined_options = [a + b for a in joined_options for b in variants[j]][:9]
                for cand in joined_options:
                    if len(cand) >= 4 and cand not in self._allow:
                        hit = self._exact(cand, max_tier if j == i + 1 else 1, stretch=False)
                        if hit:
                            consider(hit[0], hit[1], "joined")
        return best

    def _drop_allowed_phrases(self, atom_list: list[str]) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(atom_list):
            for phrase in self._allow_phrases:
                k = len(phrase)
                if tuple(atom_list[i : i + k]) == phrase:
                    i += k
                    break
            else:
                out.append(atom_list[i])
                i += 1
        return out

    def check(self, text: str, max_tier: int = 2) -> Hit | None:
        folded = fold.fold_text(text)
        for form, tier, cat in self._raw:
            if tier <= max_tier and form in folded:
                return Hit(tier, cat, "raw")
        return self.check_atoms(fold.atoms(text), max_tier)
