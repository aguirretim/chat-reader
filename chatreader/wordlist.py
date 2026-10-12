"""The "Blocked words" window: shows the safety list and lets the streamer switch groups and single words.

The words are hidden until "Show the words" is ticked, so opening this window while live does not
put the list on stream. The tick is never remembered. Look and words: notes\\ui_design_spec.md section 8.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Callable

from . import theme
from . import viewmodel as vm
from .config import AppConfig

GROUPS = {
    1: ("block_hate", "Hate, threats and harassment"),
    2: ("block_strong", "Strong swearing and sexual words"),
    3: ("block_mild_swearing", "Mild swearing and insults"),
}
KINDS = {
    "hate": "hate", "harass": "harassment", "threat": "threats", "selfharm": "self-harm", "minors": "about minors",
    "offensive": "offensive", "sexual": "sexual", "strong": "strong swearing", "mild": "mild",
}
OWN = "Your own words"
MAX_ROWS = 600   # more rows than this are not listed; the search box narrows the list
DEFAULT_SIZE = (640, 520)
MIN_SIZE = (560, 420)


def masked(word: str) -> str:
    return word[:1] + "*" * (len(word) - 1)


def visible_rows(entries, own_words, search: str, group: int):
    """Rows for the table: (word, group number or 0 for own words, kind). `group` 0 = all, -1 = own words."""
    needle = search.strip().lower()
    rows = [(w, 0, "added by you") for w in own_words] if group in (0, -1) else []
    if group != -1:
        rows += [(w, t, KINDS.get(cat, cat)) for w, t, cat in entries if group in (0, t)]
    if needle:
        rows = [r for r in rows if needle in r[0].lower()]
    return rows


class WordListWindow:
    def __init__(self, parent: tk.Misc, cfg: AppConfig, entries: list[tuple[str, int, str]], on_change: Callable[[], None],
                 mode: str = "dark"):
        from .widgets import Kit

        self.cfg = cfg
        self.entries = sorted(entries, key=lambda e: (e[1], e[0]))
        self.on_change = on_change
        self.win = tk.Toplevel(parent)
        self.win.withdraw()                         # coloured and given its caption before it is shown
        self.win.title("Blocked words")
        self.kit = k = Kit(self.win, theme.palette(mode))
        self.win.configure(bg=k.p["window"])
        want = theme.fitted_size(k.px(DEFAULT_SIZE[0]), k.px(DEFAULT_SIZE[1]), theme.work_area())
        self.win.geometry(f"{want[0]}x{want[1]}")
        self.win.minsize(k.px(MIN_SIZE[0]), k.px(MIN_SIZE[1]))
        self.win.transient(parent)
        self.win.bind("<Escape>", lambda e: self.win.destroy())
        self.shown: list[tuple[str, int, str]] = []
        self._build()
        self._refresh()
        theme.set_titlebar(self.win, mode == "dark")
        self.win.deiconify()
        self.find.focus_set()

    def _build(self) -> None:
        import ttkbootstrap as tb

        from .widgets import Button, Check, ListView

        k, p = self.kit, self.kit.p
        outer = tk.Frame(self.win, bg=p["window"], padx=k.px(12), pady=k.px(10))
        outer.pack(fill="both", expand=True)
        tk.Label(outer, justify="left", anchor="w", font=k.body, bg=p["window"], fg=p["text"],
                 text="A chat message with a blocked word is not read aloud.\n"
                      "Turn a whole group off, or find one word and allow just it.").pack(fill="x")
        card = tk.Frame(outer, bg=p["card"], padx=k.px(10), pady=k.px(6))
        card.pack(fill="x", pady=(k.px(8), k.px(8)))
        counts = {t: sum(1 for e in self.entries if e[1] == t) for t in GROUPS}
        self.group_checks: dict[int, Check] = {}
        for tier, (attr, label) in GROUPS.items():
            row = tk.Frame(card, bg=p["card"])
            row.pack(fill="x")
            c = Check(row, k, label, getattr(self.cfg, attr), lambda v, t=tier: self._group_toggled(t, v))
            c.pack(side="left")
            tk.Label(row, text=f"{counts[tier]:,} words", font=k.small, bg=p["card"], fg=p["muted"]).pack(side="right")
            self.group_checks[tier] = c
        row = tk.Frame(card, bg=p["card"])
        row.pack(fill="x")
        self.personal = Check(row, k, "Phone numbers, e-mail, street and IP addresses", self.cfg.block_personal, self._personal_toggled)
        self.personal.pack(side="left")

        # bottom-up, so the table takes whatever height is left
        own = tk.Frame(outer, bg=p["window"])
        own.pack(side="bottom", fill="x", pady=(k.px(6), 0))
        tk.Label(own, text="Add your own word", font=k.body, bg=p["window"], fg=p["text"]).pack(side="left")
        holder = tk.Frame(own, bg=p["window"], width=k.px(200), height=k.px(32))
        holder.pack(side="left", padx=k.px(8))
        holder.pack_propagate(False)
        self.new_word = tb.Entry(holder, font=k.body)
        self.new_word.pack(fill="both", expand=True)
        self.new_word.bind("<Return>", lambda e: self._add_own())
        Button(own, k, "Add", self._add_own).pack(side="left")
        actions = tk.Frame(outer, bg=p["window"])
        actions.pack(side="bottom", fill="x", pady=(k.px(4), 0))
        self.toggle_btn = Button(actions, k, "Allow or block selected", self._toggle_selected, min_width=190)
        self.toggle_btn.pack(side="left")
        Button(actions, k, "Block every word again", self._reset_words).pack(side="left", padx=(k.px(4), 0))
        self.allowed_lbl = tk.Label(outer, text="", font=k.small, bg=p["window"], fg=p["muted"], anchor="w")
        self.allowed_lbl.pack(side="bottom", fill="x")
        self.count_lbl = tk.Label(outer, text="", font=k.small, bg=p["window"], fg=p["muted"], anchor="w")
        self.count_lbl.pack(side="bottom", fill="x", pady=(k.px(4), 0))

        bar = tk.Frame(outer, bg=p["window"])
        bar.pack(fill="x", pady=(0, k.px(6)))
        tk.Label(bar, text="Find", font=k.body, bg=p["window"], fg=p["text"]).pack(side="left")
        holder = tk.Frame(bar, bg=p["window"], width=k.px(120), height=k.px(32))
        holder.pack(side="left", padx=k.px(8))
        holder.pack_propagate(False)
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *a: self._refresh())
        self.find = tb.Entry(holder, textvariable=self.search, font=k.body)
        self.find.pack(fill="both", expand=True)
        self.group_pick = tb.Combobox(bar, state="readonly", width=24, font=k.body,
                                      values=["All groups"] + [GROUPS[t][1] for t in GROUPS] + [OWN])
        self.group_pick.current(0)
        self.group_pick.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        self.group_pick.pack(side="left")
        self.show = Check(bar, k, "Show the words", False, lambda v: self._refresh(),
                          tooltip="The words are hidden so they do not appear on your stream.")
        self.show.pack(side="right")

        box = tk.Frame(outer, bg=p["card"])
        box.pack(fill="both", expand=True)
        self.table = ListView(box, k, [
            {"key": "state", "title": "Status", "width": 150},
            {"key": "word", "title": "Word", "width": None, "min": 110},
            {"key": "group", "title": "Group", "width": 210},
            {"key": "kind", "title": "Kind", "width": 110},
        ], multi=True, row_height=28)
        self.table.pack(fill="both", expand=True)
        self.table.on_activate = lambda i: self._toggle_selected()
        self.table.canvas.bind("<ButtonRelease-1>", lambda e: self._selection_changed(), add="+")
        self.table.canvas.bind("<KeyRelease>", lambda e: self._selection_changed(), add="+")

    # -- table --------------------------------------------------------------------
    def _picked_group(self) -> int:
        i = self.group_pick.current()
        return -1 if i == len(GROUPS) + 1 else i

    def _status(self, word: str, tier: int) -> tuple[str, bool]:
        group_on = True if tier == 0 else self.group_checks[tier].get()
        return vm.word_status(tier, group_on, word in self.cfg.words_off)

    def _refresh(self) -> None:
        rows = visible_rows(self.entries, self.cfg.words_added, self.search.get(), self._picked_group())
        show = self.show.get()
        self.shown = rows[:MAX_ROWS]
        out = []
        for word, tier, kind in self.shown:
            status, blocked = self._status(word, tier)
            out.append({"tone": "text" if blocked else "muted",
                        "cells": {"state": status, "word": word if show else masked(word),
                                  "group": GROUPS[tier][1] if tier else OWN, "kind": kind}})
        self.table.set_rows(out, keep_offset=True)
        self.count_lbl.configure(text=vm.word_count_text(MAX_ROWS, len(rows)))
        self.allowed_lbl.configure(text=vm.allowed_text(len(self.cfg.words_off)))
        self._selection_changed()

    def _selection_changed(self) -> None:
        picked = [self.shown[i] for i in self.table.selection() if i < len(self.shown)]
        only_own = bool(picked) and all(tier == 0 for _, tier, _ in picked)
        self.toggle_btn.set(text="Remove selected" if only_own else "Allow or block selected")

    # -- changes ------------------------------------------------------------------
    def _changed(self) -> None:
        self.on_change()
        self._refresh()

    def _group_toggled(self, tier: int, value: bool) -> None:
        if tier == 1 and not value and not messagebox.askyesno(
                "Blocked words", "With this group off, slurs, threats and similar messages can be read aloud on your "
                                 "stream. You are responsible for what your stream says.\n\nSwitch it off?", parent=self.win):
            self.group_checks[tier].set(True)
            return
        setattr(self.cfg, GROUPS[tier][0], value)
        self._changed()

    def _personal_toggled(self, value: bool) -> None:
        self.cfg.block_personal = value
        self._changed()

    def _toggle_selected(self) -> None:
        picked = [self.shown[i] for i in self.table.selection() if i < len(self.shown)]
        if not picked:
            return
        off = set(self.cfg.words_off)
        own = list(self.cfg.words_added)
        for word, tier, _ in picked:
            if tier == 0:
                own = [w for w in own if w != word]     # removing one of your own words
            elif word in off:
                off.discard(word)
            else:
                off.add(word)
        self.cfg.words_off = sorted(off)
        self.cfg.words_added = own
        self._changed()

    def _reset_words(self) -> None:
        self.cfg.words_off = []
        self._changed()

    def _add_own(self) -> None:
        word = " ".join(self.new_word.get().split()).lower()
        if not word or word in self.cfg.words_added:
            return
        self.cfg.words_added = self.cfg.words_added + [word]
        self.new_word.delete(0, "end")
        self._changed()
