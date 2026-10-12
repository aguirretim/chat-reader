"""The "Blocked words" window: shows the safety list and lets the streamer switch groups and single words.

The words are hidden until "Show the words" is ticked, so opening this window while live does not
put the list on stream.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

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
MAX_ROWS = 600   # more rows than this are not drawn; the search box narrows the list


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
    def __init__(self, parent: tk.Misc, cfg: AppConfig, entries: list[tuple[str, int, str]], on_change: Callable[[], None]):
        self.cfg = cfg
        self.entries = sorted(entries, key=lambda e: (e[1], e[0]))
        self.on_change = on_change
        self.win = tk.Toplevel(parent)
        self.win.title("Blocked words")
        self.win.geometry("640x560")
        self.win.minsize(520, 420)
        self.win.transient(parent)
        self.rows: dict[str, tuple[str, int, str]] = {}
        self._build()
        self._refresh()

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 3}
        ttk.Label(self.win, wraplength=600, justify="left",
                  text="A chat message that contains a blocked word is not read aloud. Switch off a whole group, "
                       "or find one word below and switch only that word off.").pack(fill="x", padx=10, pady=(8, 2))

        groups = ttk.LabelFrame(self.win, text="Groups")
        groups.pack(fill="x", **pad)
        counts = {t: sum(1 for e in self.entries if e[1] == t) for t in GROUPS}
        self.group_vars: dict[int, tk.BooleanVar] = {}
        for tier, (attr, label) in GROUPS.items():
            var = tk.BooleanVar(value=getattr(self.cfg, attr))
            self.group_vars[tier] = var
            ttk.Checkbutton(groups, text=f"Block: {label} ({counts[tier]} words)", variable=var,
                            command=lambda t=tier: self._group_toggled(t)).pack(anchor="w", padx=6)
        self.personal = tk.BooleanVar(value=self.cfg.block_personal)
        ttk.Checkbutton(groups, text="Block: phone numbers, e-mail addresses, street addresses and IP addresses",
                        variable=self.personal, command=self._personal_toggled).pack(anchor="w", padx=6)

        bar = ttk.Frame(self.win)
        bar.pack(fill="x", **pad)
        ttk.Label(bar, text="Find").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *a: self._refresh())
        ttk.Entry(bar, textvariable=self.search, width=20).pack(side="left", padx=6)
        self.group_pick = ttk.Combobox(bar, state="readonly", width=32,
                                       values=["All groups"] + [GROUPS[t][1] for t in GROUPS] + [OWN])
        self.group_pick.current(0)
        self.group_pick.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        self.group_pick.pack(side="left")
        self.show = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Show the words", variable=self.show, command=self._refresh).pack(side="left", padx=10)

        box = ttk.Frame(self.win)
        box.pack(fill="both", expand=True, **pad)
        cols = ("state", "word", "group", "kind")
        self.table = ttk.Treeview(box, columns=cols, show="headings", selectmode="extended")
        for col, title, width in (("state", "Now", 90), ("word", "Word", 190), ("group", "Group", 210), ("kind", "Kind", 110)):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, stretch=col in ("word", "group"))
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.table.tag_configure("off", foreground="#888888")
        self.table.bind("<Double-1>", lambda e: self._toggle_selected())
        self.table.bind("<space>", lambda e: self._toggle_selected())

        actions = ttk.Frame(self.win)
        actions.pack(fill="x", **pad)
        ttk.Button(actions, text="Switch selected on / off", command=self._toggle_selected).pack(side="left")
        ttk.Button(actions, text="Switch every single word back on", command=self._reset_words).pack(side="left", padx=6)
        self.summary = tk.StringVar()
        ttk.Label(actions, textvariable=self.summary).pack(side="right")

        own = ttk.Frame(self.win)
        own.pack(fill="x", padx=10, pady=(3, 10))
        ttk.Label(own, text="Block a word of your own").pack(side="left")
        self.new_word = ttk.Entry(own, width=24)
        self.new_word.pack(side="left", padx=6)
        self.new_word.bind("<Return>", lambda e: self._add_own())
        ttk.Button(own, text="Add", command=self._add_own).pack(side="left")

    # -- table --------------------------------------------------------------------
    def _picked_group(self) -> int:
        i = self.group_pick.current()
        return -1 if i == len(GROUPS) + 1 else i

    def _state(self, word: str, tier: int) -> str:
        if tier == 0:
            return "blocked"
        if not self.group_vars[tier].get():
            return "group is off"
        return "off" if word in self.cfg.words_off else "blocked"

    def _refresh(self) -> None:
        self.table.delete(*self.table.get_children())
        self.rows.clear()
        rows = visible_rows(self.entries, self.cfg.words_added, self.search.get(), self._picked_group())
        show = self.show.get()
        for n, (word, tier, kind) in enumerate(rows[:MAX_ROWS]):
            state = self._state(word, tier)
            iid = f"r{n}"
            self.rows[iid] = (word, tier, kind)
            self.table.insert("", "end", iid=iid, tags=(() if state == "blocked" else ("off",)),
                              values=(state, word if show else masked(word), GROUPS[tier][1] if tier else OWN, kind))
        more = f"showing {MAX_ROWS} of {len(rows)}, use Find to narrow" if len(rows) > MAX_ROWS else f"{len(rows)} words"
        self.summary.set(f"{more}   switched off one by one: {len(self.cfg.words_off)}")

    # -- changes ------------------------------------------------------------------
    def _changed(self) -> None:
        self.on_change()
        self._refresh()

    def _group_toggled(self, tier: int) -> None:
        var = self.group_vars[tier]
        if tier == 1 and not var.get() and not messagebox.askyesno(
                "Blocked words", "With this group off, slurs, threats and similar messages can be read aloud on your "
                                 "stream. You are responsible for what your stream says.\n\nSwitch it off?", parent=self.win):
            var.set(True)
            return
        setattr(self.cfg, GROUPS[tier][0], var.get())
        self._changed()

    def _personal_toggled(self) -> None:
        self.cfg.block_personal = self.personal.get()
        self._changed()

    def _toggle_selected(self) -> None:
        picked = [self.rows[i] for i in self.table.selection() if i in self.rows]
        if not picked:
            return
        off = set(self.cfg.words_off)
        own = list(self.cfg.words_added)
        for word, tier, _ in picked:
            if tier == 0:
                own = [w for w in own if w != word]     # switching off one of your own words removes it
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
