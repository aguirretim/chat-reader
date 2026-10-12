"""The window and the tray icon. All logic lives in controller.py; what to show is decided in viewmodel.py.

Layout and words: notes\\ui_design_spec.md. Regions, top to bottom: channel row, state area, speech row,
messages card, footer. Settings replaces the last three.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import tkinter as tk

from . import audio, theme
from . import viewmodel as vm
from .config import APP_NAME, VERSION, AppConfig
from .controller import Controller
from .hotkeys import DEFAULT_KEYS, Hotkeys
from .tts import VOICES
from .widgets import Button, Check, Kit, ListView, Segmented, Slider, Tooltip
from .wordlist import WordListWindow

MAX_LOG_ROWS = 200
DEFAULT_SPEAKER = "Windows default"
DEFAULT_SIZE = (920, 600)
MIN_SIZE = (720, 480)
NARROW_BELOW = 840
SHORT_BELOW = 560           # below this height the list of added bots shows 2 names instead of 5; a third row pushes
                            # the theme choice off the page at the minimum size (the list scrolls instead)
BOT_ROW = 23
KEYS = {name: label for name, (_, label) in DEFAULT_KEYS.items()}
TIPS = {
    "pause": "Stops speaking. New messages wait and are read when you press Resume.",
    "skip": "Stops the message being spoken and moves to the next.",
    "clear": "Stops the message being spoken and drops every message that is waiting.",
    "panic": "Stops the voice at once, drops waiting messages and stays quiet until you press Resume.",
    "clear_list": "Empties this list and sets the counters to zero. The voice carries on.",
    "time": "The clock time on this PC when Chat Reader read or skipped the message.",
}
CHAT_STATUS_STARTS = ("Connecting to", "Reading chat", "Chat connection lost", "Cannot reach Twitch", "Chat reader problem",
                      "Chat connection ended", "Not connected")


def mark(name: str, **extra) -> None:
    """Start-up and activity marks for the measurement scripts (CR_TIMING_LOG). Never contains chat text."""
    path = os.environ.get("CR_TIMING_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"mark": name, "t": time.time(), **extra}) + "\n")


def make_root():
    """The one top-level window. ttkbootstrap declares DPI awareness here, before any window exists."""
    import ttkbootstrap as tb

    root = tb.Window(title=APP_NAME, high_dpi=True)
    set_window_icon(root)
    return root


def icon_file() -> str:
    """The .ico next to this module (also inside the packaged app), or "" when it is missing."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico")
    return path if os.path.isfile(path) else ""


def set_window_icon(window) -> None:
    """The app's own icon in the caption, the taskbar and Alt+Tab. Windows takes the caption icon from an .ico file;
    a picture handed over with iconphoto was not shown there (the caption kept the generic icon)."""
    try:
        path = icon_file()
        if path:
            window.iconbitmap(default=path)
            window.iconbitmap(path)
        else:
            from PIL import ImageTk

            window._app_icons = [ImageTk.PhotoImage(app_icon(size)) for size in (64, 32, 16)]   # kept: Tk holds no reference
            window.iconphoto(True, *window._app_icons)
    except Exception:
        pass


def app_icon(size: int = 64):
    """Purple disc with a white speaker; the same drawing as the tray icon and the exe icon."""
    from PIL import Image, ImageDraw

    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4 * s, 4 * s, 60 * s, 60 * s), fill=(100, 65, 165, 255))
    draw.polygon([(20 * s, 26 * s), (30 * s, 26 * s), (42 * s, 16 * s), (42 * s, 48 * s), (30 * s, 38 * s), (20 * s, 38 * s)], fill="white")
    return img


class App:
    def __init__(self, cfg: AppConfig, controller: Controller | None = None, start: bool = True):
        self.cfg = cfg
        self.ctl = controller or Controller(cfg)
        self.root = make_root()
        self.root.withdraw()                      # stay hidden until coloured: no white flash, and the dark caption takes
        # what the state area is built from
        self.voice_ok = self.audio_ok = False
        self.voice_error = ""
        self.download: tuple[int, int] | None = None     # (bytes done, bytes total) while the voice files are fetched
        self.download_error = ""
        self.start_pending = False
        self.chat_status = ""
        self.problem = ""
        self.silenced = False
        self.stopped_by_user = False
        self.note, self.note_until = "", 0.0
        self.failed_keys: list[str] = []
        self.usable_marked = False
        # the list
        self.entries: list[dict] = []             # {"kind": "row"|"date", "entry": ..., "t": ...}
        self.spoken = self.skipped = 0
        self.cleared = False
        self.new_rows = 0
        self.view = "list"
        self.narrow = None
        self.devices: list[str] = []
        self.tray = None
        self._words_win = None
        self._state_key = None
        self._state_tooltip = ""
        self._channel_text = cfg.channel
        self._log_dirty = False
        self._short = False
        self._built = False
        self._build()
        k = self.kit
        place = os.environ.get("CR_WINDOW_POS", "")        # "+x+y", for the scripted checks: open on a chosen monitor
        at = re.fullmatch(r"([+-]\d+)([+-]\d+)", place)
        area = theme.work_area()
        if at:                                              # fit the monitor it opens on, not the primary one
            left, top, right, bottom = theme.monitor_work_area(int(at[1]) + 50, int(at[2]) + 50)
            area = (right - left, bottom - max(top, int(at[2])) + 16)
        want = theme.fitted_size(k.px(DEFAULT_SIZE[0]), k.px(DEFAULT_SIZE[1]), area)
        self.root.geometry(f"{want[0]}x{want[1]}{place if at else ''}")
        self.root.minsize(k.px(MIN_SIZE[0]), k.px(MIN_SIZE[1]))
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        for key in ("<Control-l>", "<Control-L>"):          # with Caps Lock on, Tk reports the capital letter
            self.root.bind(key, lambda ev: self.clear_list() if self.view == "list" else None)
        self.root.bind("<Escape>", lambda e: self.show_view("list") if self.view == "settings" else None)
        self.root.bind("<Configure>", self._on_resize)
        theme.set_titlebar(self.root, self.mode == "dark")
        self.root.deiconify()
        if os.environ.get("CR_START_MINIMIZED") == "1":
            self.root.iconify()
        self.hotkeys = None
        if start:
            if cfg.hotkeys:
                self.hotkeys = Hotkeys(lambda name: self.root.after(0, self._on_hotkey, name))
                self.hotkeys.start()
            self._start_tray()
            self.ctl.start()
            audio.start(lambda names, err: self.root.after(0, self._audio_ready, names, err))
            self.root.after(100, self._poll)
        self.root.after_idle(lambda: mark("window_ready"))
        auto = os.environ.get("CR_AUTOCONNECT")
        if auto:
            self._set_channel_text(auto)
        self._initial_focus()

    # -- building ---------------------------------------------------------------
    def _build(self) -> None:
        """(Re)create every control from the current settings. Called at start and when the colours change."""
        self._built = False
        for child in self.root.winfo_children():
            if child is not getattr(self._words_win, "win", None):
                child.destroy()
        self.mode = theme.resolve(self.cfg.theme)
        self.kit = k = Kit(self.root, theme.palette(self.mode))
        p = k.p
        self.root.configure(bg=p["window"])
        import ttkbootstrap as tb

        style = tb.Style()
        style.theme_use("bootstrap-dark" if self.mode == "dark" else "bootstrap-light")
        theme.style_ttk(style, p, k.body, k.S)
        for opt, val in (("background", p["raised"]), ("foreground", p["text"]), ("selectBackground", p["accent"]),
                         ("selectForeground", p["on_accent"]), ("font", k.body)):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", val)
        self.tb = tb
        outer = tk.Frame(self.root, bg=p["window"], padx=k.px(10), pady=k.px(10))
        outer.pack(fill="both", expand=True)
        self._build_channel_row(outer)
        self._build_state_area(outer)
        self.body = tk.Frame(outer, bg=p["window"])
        self.body.pack(fill="both", expand=True)
        self.list_view = tk.Frame(self.body, bg=p["window"])
        self.settings_view = tk.Frame(self.body, bg=p["window"])
        self._build_speech_row(self.list_view)
        self._build_messages(self.list_view)
        self._build_settings(self.settings_view)
        self._built = True
        self.narrow = None
        self.show_view(self.view, focus=False)
        self._layout_speech_row(False)
        self._render_rows()
        self._state_key = None
        self._refresh()

    def _build_channel_row(self, parent) -> None:
        k, p = self.kit, self.kit.p
        row = tk.Frame(parent, bg=p["window"])
        row.pack(fill="x")
        tk.Label(row, text="Twitch channel", font=k.body, bg=p["window"], fg=p["muted"]).pack(side="left", padx=(k.px(2), k.px(8)))
        holder = tk.Frame(row, bg=p["window"], width=k.px(220), height=k.px(32))
        holder.pack(side="left")
        holder.pack_propagate(False)
        self.channel_var = tk.StringVar(value=self._channel_text)
        self.channel = self.tb.Entry(holder, textvariable=self.channel_var, font=k.body)
        self.channel.pack(fill="both", expand=True)
        self.channel.bind("<Return>", lambda ev: self._channel_enter())
        self.channel_var.trace_add("write", lambda *a: self._channel_edited())
        self.hint = tk.Label(holder, text="your channel name", font=k.body, bg=p["raised"], fg=p["muted"], cursor="xterm")
        self.hint.bind("<Button-1>", lambda e: self.channel.focus_set())
        self.channel.bind("<FocusIn>", lambda e: self._place_hint(focused=True), add="+")
        self.channel.bind("<FocusOut>", lambda e: self._place_hint(focused=False), add="+")
        self.start_btn = Button(row, k, "Start reading", self.toggle_connect, variant="primary", min_width=124)
        self.start_btn.pack(side="left", padx=(k.px(6), 0))
        self.settings_btn = Button(row, k, "Settings", self.toggle_settings, variant="quiet", icon=vm.ICON["settings"])
        self.settings_btn.pack(side="right")
        self._place_hint(focused=False)

    def _build_state_area(self, parent) -> None:
        k, p = self.kit, self.kit.p
        card = tk.Frame(parent, bg=p["card"], height=k.px(60))
        card.pack(fill="x", pady=(k.px(8), k.px(6)))
        card.pack_propagate(False)
        self.state_bar = tk.Frame(card, bg=p["muted"], width=k.px(6))
        self.state_bar.pack(side="left", fill="y")
        self.state_icon = tk.Label(card, text="", font=k.icon_big, bg=p["card"], fg=p["muted"], width=2)
        self.state_icon.pack(side="left", padx=(k.px(14), k.px(8)))
        words = tk.Frame(card, bg=p["card"])
        words.pack(side="left", fill="both", expand=True, pady=k.px(5))
        self.state_word = tk.Label(words, text="", font=k.state, bg=p["card"], fg=p["text"], anchor="w", bd=0, pady=0)
        self.state_word.pack(fill="x")
        self.state_detail = tk.Label(words, text="", font=k.small, bg=p["card"], fg=p["muted"], anchor="w", bd=0, pady=0)
        self.state_detail.pack(fill="x")
        self.state_tips = [Tooltip(k, w, lambda: self._state_tooltip)        # anywhere on the state area, not just the small line
                           for w in (card, self.state_icon, words, self.state_word, self.state_detail)]
        # shown only while the voice files are downloading / when that failed
        self.progress_track = tk.Frame(card, bg=p["divider"])
        self.progress_fill = tk.Frame(self.progress_track, bg=p["info"])
        self.retry_btn = Button(card, k, "Try again", self.retry_voice, variant="primary")

    def _build_speech_row(self, parent) -> None:
        k, p = self.kit, self.kit.p
        self.speech_row = row = tk.Frame(parent, bg=p["window"])
        row.pack(fill="x")

        def make(text, command, key, icon="", **kw):
            return Button(row, k, text, command, icon=icon, caption=KEYS[key], min_width=124, tooltip=TIPS[key], **kw)

        self.pause_btn = make("Pause", self.toggle_pause, "pause", vm.ICON["pause"])
        self.skip_btn = make("Skip this one", self.ctl.skip, "skip", vm.ICON["skip"])
        self.skipall_btn = make("Skip all", self.ctl.clear, "clear")
        self.panic_btn = make("Silence now", self.silence_now, "panic", vm.ICON["mute"], icon_tone="bad")
        self.speech_buttons = [self.pause_btn, self.skip_btn, self.skipall_btn, self.panic_btn]
        self.vol_box = tk.Frame(row, bg=p["window"])
        self.vol_icon = tk.Label(self.vol_box, text=vm.ICON["volume"], font=k.icon, bg=p["window"], fg=p["muted"])
        self.vol_word = tk.Label(self.vol_box, text="Volume", font=k.body, bg=p["window"], fg=p["muted"])
        self.volume = Slider(self.vol_box, k, 0, 100, 1, self.cfg.volume, 120, self._volume_moved, lambda v: self._save())
        self.vol_num = tk.Label(self.vol_box, text=f"{self.cfg.volume}%", font=k.body, bg=p["window"], fg=p["text"], width=4, anchor="e")

    def _layout_speech_row(self, narrow: bool) -> None:
        k = self.kit
        for w in self.speech_buttons + [self.vol_box, self.vol_icon, self.vol_word, self.volume, self.vol_num]:
            w.pack_forget()
        self.vol_box.pack(side="right", padx=(k.px(8), 0))
        self.vol_icon.pack(side="left")
        if not narrow:
            self.vol_word.pack(side="left", padx=(k.px(6), 0))
        self.volume.set_width(72 if narrow else 120)
        self.volume.pack(side="left", padx=(k.px(4), 0))
        self.vol_num.pack(side="left")
        for i, b in enumerate(self.speech_buttons):
            b.pack(side="left", padx=(0 if i == 0 else k.px(4), 0), fill="x" if narrow else "none", expand=narrow)

    def _build_messages(self, parent) -> None:
        k, p = self.kit, self.kit.p
        foot = tk.Label(parent, text="No sign-in needed. Messages are shown here only. They are never saved or sent anywhere.",
                        font=k.tiny, bg=p["window"], fg=p["muted"], anchor="w")
        foot.pack(side="bottom", fill="x", pady=(k.px(4), 0))
        card = tk.Frame(parent, bg=p["card"])
        card.pack(fill="both", expand=True, pady=(k.px(6), 0))
        head = tk.Frame(card, bg=p["card"], height=k.px(40))
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="Messages", font=k.body_b, bg=p["card"], fg=p["text"]).pack(side="left", padx=(k.px(14), k.px(10)))
        self.counts = tk.Label(head, text="", font=k.small, bg=p["card"], fg=p["muted"])
        self.counts.pack(side="left")
        self.jump_btn = Button(head, k, "Jump to newest (000 new)", self.jump_to_newest, variant="quiet",
                               icon=vm.ICON["down"], icon_tone="accent", text_tone="accent")
        self.clear_btn = Button(head, k, "Clear list", self.clear_list, variant="quiet", icon=vm.ICON["bin"], tooltip=TIPS["clear_list"])
        self.clear_btn.pack(side="right", padx=(0, k.px(6)))
        self.log = ListView(card, k, [
            {"key": "time", "title": "Your PC time", "width": 104},
            {"key": "who", "title": "Who", "width": 136},
            {"key": "message", "title": "Message", "width": None, "min": 120},
            {"key": "result", "title": "What happened", "width": 250},
        ], icon_column="result")
        self.log.header_tips = {"time": TIPS["time"]}
        self.log.row_tip = self._row_tip
        self.log.on_context = self._row_menu
        self.log.on_scroll = self._list_scrolled
        self.log.pack(fill="both", expand=True)

    def _build_settings(self, parent) -> None:
        k, p = self.kit, self.kit.p
        card = tk.Frame(parent, bg=p["card"])
        card.pack(fill="both", expand=True)
        head = tk.Frame(card, bg=p["card"])
        head.pack(fill="x", padx=k.px(8), pady=(k.px(2), 0))
        self.back_btn = Button(head, k, "Back", lambda: self.show_view("list"), variant="quiet", icon=vm.ICON["back"])
        self.back_btn.pack(side="left")
        tk.Label(head, text="Settings", font=k.title, bg=p["card"], fg=p["text"]).pack(side="left", padx=k.px(8))
        # Always exactly two footer lines, each short enough for the narrowest window. A wrapped line, or a third line
        # for the shortcut warning, pushed the theme choice out of sight at the smallest size; the warning now takes
        # the place of the licence line (which is also in the README and LICENSE).
        self.keys_line = tk.Label(card, text=vm.shortcuts_text(KEYS), font=k.tiny, bg=p["card"], fg=p["muted"], anchor="w")
        tk.Frame(card, bg=p["card"], height=k.px(4)).pack(side="bottom")
        self.licence_line = tk.Label(card, text=vm.licence_text(VERSION), font=k.tiny, bg=p["card"], fg=p["muted"], anchor="w")
        self.licence_line.pack(side="bottom", fill="x", padx=k.px(16))
        self.keys_line.pack(side="bottom", fill="x", padx=k.px(16), pady=(k.px(2), 0))
        cols = tk.Frame(card, bg=p["card"])
        cols.pack(fill="both", expand=True, padx=k.px(16), pady=(k.px(2), 0))
        left = tk.Frame(cols, bg=p["card"])
        right = tk.Frame(cols, bg=p["card"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, k.px(10)))
        right.grid(row=0, column=1, sticky="nsew", padx=(k.px(10), 0))
        cols.grid_columnconfigure(0, weight=1, minsize=k.px(350))
        cols.grid_columnconfigure(1, weight=1, minsize=k.px(288))
        cols.grid_rowconfigure(0, weight=1)

        def section(parent, text):
            tk.Label(parent, text=text, font=k.tiny_b, bg=p["card"], fg=p["muted"], anchor="w").pack(fill="x", pady=(k.px(4), k.px(2)))

        def label(parent, text):
            return tk.Label(parent, text=text, font=k.body, bg=p["card"], fg=p["text"], anchor="w", width=14)

        def hint(parent, text):
            return tk.Label(parent, text=text, font=k.tiny, bg=p["card"], fg=p["muted"], anchor="w", justify="left")

        def line(parent, pady=2):
            r = tk.Frame(parent, bg=p["card"])
            r.pack(fill="x", pady=k.px(pady))
            return r

        # -- left: voice, look
        section(left, "VOICE")
        r = line(left)
        label(r, "Voice").pack(side="left")
        labels = [lbl for _, lbl in VOICES]
        if self.cfg.voice not in dict(VOICES):
            self.cfg.voice = VOICES[0][0]
        self.test_btn = Button(r, k, "Test voice", self.test_voice)
        self.test_btn.pack(side="right", padx=(k.px(4), 0))
        self.voice = self.tb.Combobox(r, values=labels, state="readonly", width=12, font=k.body)
        self.voice.set(dict(VOICES)[self.cfg.voice])
        self.voice.pack(side="left", fill="x", expand=True)
        self.voice.bind("<<ComboboxSelected>>", lambda e: self._save())
        self.test_btn.lift(self.voice)           # Tab reaches Voice first, then Test voice
        r = line(left)
        label(r, "Play the voice on").pack(side="left")
        self.device = self.tb.Combobox(r, values=[DEFAULT_SPEAKER] + self.devices, state="readonly", width=12, font=k.body)
        self.device.set(self.cfg.device_name or DEFAULT_SPEAKER)
        self.device.pack(side="left", fill="x", expand=True)
        self.device.bind("<<ComboboxSelected>>", lambda e: self._save())
        hint(left, "Pick your headphones if viewers should not hear it.").pack(anchor="w")
        r = line(left)
        label(r, "Speed").pack(side="left")
        self.speed = Slider(r, k, 0.7, 1.5, 0.05, self.cfg.speed, 140, self._speed_moved, lambda v: self._save())
        self.speed.pack(side="left")
        self.speed_num = tk.Label(r, text=vm.speed_text(self.cfg.speed), font=k.body, bg=p["card"], fg=p["text"])
        self.speed_num.pack(side="left", padx=(k.px(6), 0))
        # Advanced and Look have swapped columns against the spec: with "Bots you added" (added after the mock-up)
        # the right column would not fit the 720x480 minimum with Advanced in it.
        section(left, "ADVANCED")
        self.low_memory = Check(left, k, "Use less memory", self.cfg.low_memory, lambda v: self._save())
        self.low_memory.pack(anchor="w")
        self.low_hint = hint(left, "The voice takes longer to start speaking. Takes effect the next time you open Chat Reader.")
        self.low_hint.configure(wraplength=k.px(310))
        self.low_hint.pack(anchor="w", padx=(k.px(26), 0))
        # -- right: what gets read
        section(right, "WHAT GETS READ")
        self.say_names = Check(right, k, "Say who wrote each message", self.cfg.say_names, lambda v: self._save())
        self.say_names.pack(anchor="w")
        self.skip_bots = Check(right, k, "Skip messages from bots", self.cfg.skip_bots, lambda v: self._save())
        self.skip_bots.pack(anchor="w")
        r = line(right, 1)
        tk.Label(r, text="Bots you added", font=k.body, bg=p["card"], fg=p["text"]).pack(side="left", padx=(k.px(2), k.px(8)))
        self.bot_add = Button(r, k, "Add", self._add_bot, height=28)
        self.bot_add.pack(side="right")
        holder = tk.Frame(r, bg=p["card"], width=k.px(100), height=k.px(30))
        holder.pack(side="left", fill="x", expand=True)
        holder.pack_propagate(False)
        self.bot_entry = self.tb.Entry(holder, font=k.body)
        self.bot_entry.pack(fill="both", expand=True)
        self.bot_entry.bind("<Return>", lambda e: self._add_bot())
        self.bot_add.lift()
        self.bots = ListView(right, k, [{"key": "name", "title": "", "width": None, "min": 80},
                                        {"key": "remove", "title": "", "width": 96, "tone": "accent"}], row_height=BOT_ROW, header_height=1)
        self.bots.pack(fill="x", padx=(k.px(2), 0))
        self.bots.pack_propagate(False)
        self.bots.canvas.bind("<ButtonRelease-1>", self._bots_released, add="+")
        for key in ("<Delete>", "<Return>", "<space>"):          # the keyboard way to "Remove"
            self.bots.canvas.bind(key, lambda ev: self._remove_selected_bot())
        self.bots.on_activate = lambda i: None
        self.bots_empty = hint(right, "None yet. Add a name here, or right-click a message in the list.")
        self.bots_empty.configure(wraplength=k.px(270))
        r = line(right, 1)
        self.words_btn = Button(r, k, "Blocked words...", self.open_blocked_words)
        self.words_btn.pack(side="left")
        self.groups_lbl = tk.Label(r, text=vm.groups_on(self.cfg), font=k.small, bg=p["card"], fg=p["muted"])
        self.groups_lbl.pack(side="left", padx=k.px(8))
        section(right, "THEME")
        r = line(right)
        # no "Colours" label on this row: the section title says it, and label + three choices do not fit 720 wide
        self.colours = Segmented(r, k, [("dark", "Dark"), ("light", "Light"), ("system", "Same as Windows")],
                                 self.cfg.theme if self.cfg.theme in theme.THEMES else "dark", self._theme_chosen)
        self.colours.pack(side="left")
        self._render_bots()
        self._show_failed_keys()

    # -- views ------------------------------------------------------------------
    def show_view(self, name: str, focus: bool = True) -> None:
        self.view = name
        self.settings_view.pack_forget()
        self.list_view.pack_forget()
        (self.settings_view if name == "settings" else self.list_view).pack(fill="both", expand=True)
        if name == "list":
            self._flush_list()
        if focus:
            (self.back_btn if name == "settings" else self.settings_btn).focus_set()

    def toggle_settings(self) -> None:
        self.show_view("list" if self.view == "settings" else "settings")

    def _initial_focus(self) -> None:
        (self.start_btn if self.channel_var.get().strip() else self.channel).focus_set()

    def _on_resize(self, event) -> None:
        if event.widget is not self.root or not self._built:
            return
        narrow = event.width < self.kit.px(NARROW_BELOW)
        if narrow != self.narrow:
            self.narrow = narrow
            self._layout_speech_row(narrow)
            self.log.columns[1]["width"] = 106 if narrow else 136      # Who gives way so every reason fits "What happened"
            self.log.columns[3]["width"] = 220 if narrow else 250
            self.log.render()
        short = event.height < self.kit.px(SHORT_BELOW)
        if short != self._short:
            self._short = short
            self._render_bots()

    # -- channel box --------------------------------------------------------------
    def _set_channel_text(self, text: str) -> None:
        self.channel.configure(state="normal")
        self.channel_var.set(text)

    def _channel_enter(self) -> None:
        """Enter in the channel box starts reading. It never stops it: the box is read-only while connected."""
        if not (self.ctl.connected_channel or self.start_pending):
            self.toggle_connect()

    def _channel_edited(self) -> None:
        self._channel_text = self.channel_var.get()
        self._place_hint()
        self._refresh_empty()
        self._refresh()

    def _place_hint(self, focused: bool | None = None) -> None:
        if focused is not None:
            self._channel_focused = focused
        if not self.channel_var.get():       # also while the box has focus; the text cursor stays visible left of it
            self.hint.place(x=self.kit.px(13), rely=0.5, anchor="w")
        else:
            self.hint.place_forget()

    # -- settings ---------------------------------------------------------------
    def _volume_moved(self, v: float) -> None:
        self.vol_num.configure(text=f"{int(v)}%")
        self._save(write=False)

    def _speed_moved(self, v: float) -> None:
        self.speed_num.configure(text=vm.speed_text(v))
        self._save(write=False)

    def _save(self, write: bool = True) -> None:
        if not self._built:
            return
        by_label = {label: vid for vid, label in VOICES}
        self.cfg.voice = by_label.get(self.voice.get(), self.cfg.voice)
        chosen = self.device.get()
        self.cfg.device_name = "" if chosen == DEFAULT_SPEAKER else chosen
        self.cfg.volume = int(self.volume.value)
        self.cfg.speed = round(float(self.speed.value), 2)
        self.cfg.say_names = self.say_names.get()
        self.cfg.skip_bots = self.skip_bots.get()
        self.cfg.low_memory = self.low_memory.get()
        self.ctl.apply_config()
        self.groups_lbl.configure(text=vm.groups_on(self.cfg))
        if not write:
            return
        try:
            self.cfg.save()
        except OSError:
            pass  # a read-only profile must not stop the app; settings just will not persist

    def _theme_chosen(self, key: str) -> None:
        self.cfg.theme = key
        self._save()
        if theme.resolve(key) != self.mode:
            self.root.after(10, self._recolour)

    def _recolour(self) -> None:
        """Rebuild the window in the other colours. The caption only changes while the window is hidden."""
        was = self.root.state()
        words_at = ""
        if self._words_win is not None and self._words_win.win.winfo_exists():
            words_at = self._words_win.win.geometry()      # an open Blocked words window would keep the old colours
            self._words_win.win.destroy()
        self._words_win = None
        self.root.withdraw()
        self._build()
        theme.set_titlebar(self.root, self.mode == "dark")
        self.root.deiconify()
        if was in ("iconic", "zoomed"):
            self.root.state(was)
        if self.view == "settings":
            self.colours.buttons[self.cfg.theme].focus_set()
        if words_at:
            self.open_blocked_words()
            if self._words_win is not None:
                self._words_win.win.geometry(words_at)

    def open_blocked_words(self) -> None:
        if self._words_win is not None and self._words_win.win.winfo_exists():
            self._words_win.win.lift()
            return
        entries = self.ctl.blocked_words()
        if entries is None:
            self._set_note("The word list is still loading. Try again in a few seconds.", 5)
            return
        self._words_win = WordListWindow(self.root, self.cfg, entries, self._save, mode=self.mode)

    # -- bots the streamer added ---------------------------------------------------
    def _render_bots(self) -> None:
        names = self.cfg.extra_bots
        shown = min(len(names), 2 if self._short else 5)       # more names than this scroll
        self.bots.configure(height=self.kit.px(BOT_ROW * shown + 2))
        self.bots.set_rows([{"cells": {"name": n, "remove": "Remove"}, "tone": "text"} for n in names])
        if names:
            self.bots_empty.pack_forget()
            self.bots.pack(fill="x", padx=(self.kit.px(2), 0), after=self.bot_entry.master.master)
        else:
            self.bots.pack_forget()
            self.bots_empty.pack(anchor="w", padx=(self.kit.px(2), 0), after=self.bot_entry.master.master)

    def _set_bots(self, names: list[str]) -> None:
        self.cfg.extra_bots = names
        self._save()
        self._render_bots()

    def _add_bot(self) -> None:
        typed = self.bot_entry.get().strip()
        if not typed:
            return
        name = typed.lstrip("@")
        if not vm.valid_bot_name(typed):
            self._set_note("That does not look like a Twitch name. Use letters, numbers and _ only.", 5)
            return                                   # the text stays in the box to be corrected
        if not vm.is_added_bot(name, self.cfg.extra_bots):
            self._set_bots(self.cfg.extra_bots + [name])
        self.bot_entry.delete(0, "end")

    def _bots_released(self, event) -> None:
        i = self.bots._index_at(event.y)
        if i is not None and event.x >= self.bots.widths()[0]:
            self._remove_bot(i)

    def _remove_bot(self, index: int) -> None:
        if 0 <= index < len(self.cfg.extra_bots):
            self._set_bots([n for j, n in enumerate(self.cfg.extra_bots) if j != index])

    def _remove_selected_bot(self) -> None:
        for i in self.bots.selection()[:1]:
            self._remove_bot(i)

    def _row_menu(self, index: int, x: int, y: int) -> None:
        entry = self.entries[index].get("entry") or {}
        login = entry.get("login") or ""
        if not login:
            return                      # the test sentence has no writer
        p, k = self.kit.p, self.kit
        menu = tk.Menu(self.root, tearoff=0, bg=p["raised"], fg=p["text"], activebackground=p["accent"],
                       activeforeground=p["on_accent"], font=k.body, bd=0, relief="flat")
        menu.add_command(label=vm.bot_menu_label(entry.get("name") or login, login, self.cfg.extra_bots),
                         command=lambda: self.toggle_bot(login))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def toggle_bot(self, login: str) -> None:
        names, added = vm.toggle_bot(login, self.cfg.extra_bots)
        self._set_bots(names)
        self._set_note(f"{login} added to bots. Undo in Settings." if added else f"{login} removed from bots.", 4)

    # -- the state area -----------------------------------------------------------
    def _set_note(self, text: str, seconds: float) -> None:
        self.note, self.note_until = text, time.time() + seconds
        self._refresh()

    def current_state(self) -> vm.State:
        if self.note and time.time() > self.note_until:
            self.note = ""
        return vm.window_state(
            voice_error=self.voice_error, ready=self.voice_ok and self.audio_ok, start_pending=self.start_pending,
            status=self.problem or self.chat_status, paused=self.ctl.paused, silenced=self.silenced,
            channel=self.ctl.connected_channel, waiting=len(self.ctl.queue), stopped_by_user=self.stopped_by_user,
            note=self.note, download=self.download, download_error=self.download_error,
            channel_typed=bool(self.channel_var.get().strip()))

    def _refresh(self) -> None:
        """Bring the state area and every button in line with the controller. Cheap; called from the poll."""
        if not self._built:
            return
        self._flush_list()
        s = self.current_state()
        ready = self.voice_ok and self.audio_ok
        connected = bool(self.ctl.connected_channel) or self.start_pending
        key = (s, ready, connected, self.ctl.paused)
        if key == self._state_key:
            return
        self._state_key = key
        p = self.kit.p
        self.state_bar.configure(bg=p[s.tone])
        self.state_icon.configure(text=s.icon, fg=p[s.tone])
        self.state_word.configure(text=s.word)
        self.state_detail.configure(text=s.detail)
        self._state_tooltip = s.tooltip
        k = self.kit
        if s.progress is None:
            self.progress_track.place_forget()
        else:
            self.progress_track.place(x=k.px(6), rely=1.0, anchor="sw", relwidth=1.0, width=-k.px(6), height=k.px(4))
            self.progress_fill.place(x=0, y=0, relheight=1.0, relwidth=max(0.0, min(1.0, s.progress)))
        if s.kind == "voice_download_failed":
            self.retry_btn.place(relx=1.0, rely=0.5, anchor="e", x=-k.px(12))
        else:
            self.retry_btn.place_forget()
        self.start_btn.set(text="Stop reading" if connected else "Start reading", variant="plain" if connected else "primary")
        self.channel.configure(state="readonly" if connected else "normal")
        for b in self.speech_buttons:
            b.set(enabled=ready)
        self.test_btn.set(enabled=ready)
        self.pause_btn.set(text="Resume" if self.ctl.paused else "Pause", icon=vm.ICON["play"] if self.ctl.paused else vm.ICON["pause"])
        self.panic_btn.set(variant="danger" if s.kind == "reading" else "plain")
        self._refresh_empty()
        if self.tray is not None:
            try:
                self.tray.title = f"{APP_NAME}: {s.word}"
            except Exception:
                pass

    def _show_failed_keys(self) -> None:
        text = vm.failed_shortcuts_text(self.failed_keys)
        self.licence_line.configure(text=text or vm.licence_text(VERSION), fg=self.kit.p["bad" if text else "muted"])

    # -- events from the controller ------------------------------------------------
    def _audio_ready(self, names: list[str], error: str) -> None:
        self.audio_ok = True
        self.devices = names
        mark("audio_ready", devices=len(names), error=bool(error))
        values = [DEFAULT_SPEAKER] + names
        self.device.configure(values=values)
        if self.device.get() not in values:
            self.device.set(DEFAULT_SPEAKER)
        if error:
            self.problem = error
        self._maybe_usable()

    def _maybe_usable(self) -> None:
        if self.voice_ok and self.audio_ok and not self.usable_marked:
            self.usable_marked = True
            mark("usable")
            if self.hotkeys is not None and self.hotkeys.registered.wait(1.0):
                self.failed_keys = list(self.hotkeys.failed)
                mark("hotkeys", failed=len(self.failed_keys))
                self._show_failed_keys()
            if os.environ.get("CR_AUTOCONNECT") or self.start_pending:
                self.start_pending = False
                self._connect()
            if os.environ.get("CR_AUTOTEST") == "1":
                self.ctl.test_voice()
        self._refresh()

    def handle_event(self, kind: str, data) -> None:
        if kind == "voice_files":
            if data.get("state") == "downloading":
                if self.download is None:
                    mark("voice_download_started", total=data["total"])
                self.download, self.download_error = (data["done"], data["total"]), ""
                if data["done"] >= data["total"]:
                    mark("voice_download_done")
            elif data.get("state") == "failed":
                self.download_error = str(data.get("kind") or "offline")
                mark("voice_download_failed", kind=self.download_error)
        elif kind == "voice":
            if data == "loading":
                self.download_error = ""
            if data == "ready":
                self.voice_ok = True
                mark("voice_ready")
                self._maybe_usable()
            elif data != "loading":
                text = str(data)
                self.voice_error = text[len("error: "):] if text.startswith("error: ") else text
                mark("voice_failed")
        elif kind == "status":
            text = str(data)
            if text.startswith("Paused. Press Resume to hear the test"):
                self._set_note("Press Resume to hear the test.", 5)
            elif vm.problem_for(text):
                self.problem = text
            else:
                self.problem = ""
                if text.startswith(CHAT_STATUS_STARTS):
                    self.chat_status = "" if text == "Not connected" else text
                if text.startswith("Reading chat"):
                    mark("reading_chat")
        elif kind == "paused":
            if not data:
                self.silenced = False
        elif kind == "log":
            self._add_log(data)

    def _poll(self) -> None:
        try:
            while True:
                kind, data = self.ctl.events.get_nowait()
                self.handle_event(kind, data)
        except queue.Empty:
            pass
        except Exception:
            pass  # one bad event must not freeze the window for good
        finally:
            try:
                self._refresh()
            except Exception:
                pass
            self.root.after(100, self._poll)

    # -- the list ---------------------------------------------------------------
    def _row(self, item: dict) -> dict:
        if item["kind"] == "date":
            return {"kind": "date", "cells": {"date": vm.windows_date(item["t"])}}
        e = item["entry"]
        view = vm.row_view(e)
        name, login = e.get("name") or "", e.get("login") or ""
        who = login if (login and name and name.lower() != login.lower()) else (name or login)
        tone = {"read": "ok", "skip": "muted", "fault": "bad"}[view.kind]
        return {"kind": "row", "tone": "text" if view.kind == "read" else "muted", "icon": (view.icon, tone),
                "cells": {"time": vm.windows_time(e["t"]), "who": who, "message": view.message, "result": view.result}}

    def _render_rows(self) -> None:
        self.log.set_rows([self._row(i) for i in self.entries])
        self.log.to_bottom()
        self._refresh_counts()

    def _add_log(self, entry: dict) -> None:
        spoken = entry["status"] == "spoken"
        if spoken:
            self.spoken += 1
            mark("spoken", n=self.spoken)
        else:
            self.skipped += 1
            mark("not_read", n=self.skipped)
        self.cleared = False
        follow = self.log.at_bottom()
        last_t = next((i["t"] for i in reversed(self.entries) if i["kind"] == "row"), None)
        if vm.date_changed(last_t, entry["t"]):
            item = {"kind": "date", "t": entry["t"]}
            self.entries.append(item)
            self.log.add_row(self._row(item), follow, render=False)    # drawn once per poll, see _flush_list
        item = {"kind": "row", "t": entry["t"], "entry": entry}
        self.entries.append(item)
        self.log.add_row(self._row(item), follow, render=False)
        self._log_dirty = True
        extra = sum(1 for i in self.entries if i["kind"] == "row") - MAX_LOG_ROWS
        if extra > 0:
            drop = 0
            while extra > 0 or (drop < len(self.entries) and self.entries[drop]["kind"] == "date"):
                extra -= self.entries[drop]["kind"] == "row"
                drop += 1
            del self.entries[:drop]
            self.log.drop_first(drop)
        if not follow:
            self.new_rows += 1
        self._refresh_counts()

    def _flush_list(self) -> None:
        """Draw the rows added since the last poll: once, and not at all while Settings is showing."""
        if self._log_dirty and self.view == "list":
            self._log_dirty = False
            self.log.render()

    def _refresh_counts(self) -> None:
        self.counts.configure(text=vm.counters_text(self.spoken, self.skipped))
        self.clear_btn.set(enabled=bool(self.entries))
        if self.new_rows and not self.log.at_bottom():
            self.jump_btn.set(text=vm.jump_text(self.new_rows))
            self.jump_btn.pack(side="right", padx=(0, self.kit.px(4)))
        else:
            self.new_rows = 0
            self.jump_btn.pack_forget()
        self._refresh_empty()

    def _refresh_empty(self) -> None:
        if not self._built:
            return
        empty = vm.empty_state(cleared=self.cleared, ready=self.voice_ok and self.audio_ok,
                               channel_typed=bool(self.channel_var.get().strip()), reading=bool(self.ctl.connected_channel))
        if empty != self.log.empty:
            self.log.empty = empty
            if not self.entries:
                self.log.render()

    def _list_scrolled(self) -> None:
        if self.log.at_bottom() and self.new_rows:
            self._refresh_counts()

    def _row_tip(self, index: int) -> str:
        item = self.entries[index] if index < len(self.entries) else None
        return vm.row_view(item["entry"]).tooltip if item and item["kind"] == "row" else ""

    def jump_to_newest(self) -> None:
        self.log.to_bottom()
        self.new_rows = 0
        self._refresh_counts()
        self.log.canvas.focus_set()

    def clear_list(self) -> None:
        """Empties the list on screen and the counters. The controller is not told: the voice carries on."""
        if not self.entries:
            return
        self.entries = []
        self.spoken = self.skipped = self.new_rows = 0
        self.cleared = True
        self.log.clear()
        self._refresh_counts()
        self.log.canvas.focus_set()

    # -- actions ----------------------------------------------------------------
    def _connect(self) -> None:
        self.stopped_by_user = False
        if self.ctl.connect(self.channel_var.get()):
            self.cfg.channel = self.ctl.connected_channel
            self._set_channel_text(self.cfg.channel)
            self.chat_status = f"Connecting to {self.cfg.channel}..."
            self._save()
            self.log.canvas.focus_set()          # a stray Space or Enter now lands on the list and does nothing

    def toggle_connect(self) -> None:
        self.problem = ""
        if self.ctl.connected_channel or self.start_pending:
            self.start_pending = False
            self.ctl.disconnect()
            self.chat_status = ""
            self.stopped_by_user = True
        elif not (self.voice_ok and self.audio_ok):
            self.start_pending = bool(self.channel_var.get().strip())
        else:
            self._connect()
        self._refresh()

    def toggle_pause(self) -> None:
        if self.ctl.paused:
            self.problem = ""
        self.ctl.toggle_pause()
        self.silenced = self.silenced and self.ctl.paused
        self._refresh()

    def silence_now(self) -> None:
        self.silenced = True
        self.ctl.panic()
        self._refresh()

    def test_voice(self) -> None:
        self.problem = ""
        self.ctl.test_voice()

    def retry_voice(self) -> None:
        self.download_error = ""
        self.ctl.retry_voice()
        self._refresh()

    def _on_hotkey(self, name: str) -> None:
        mark("hotkey", key=name)
        {"pause": self.toggle_pause, "skip": self.ctl.skip, "clear": self.ctl.clear, "panic": self.silence_now}[name]()
        self._refresh()

    def show(self) -> None:
        self.root.after(0, lambda: (self.root.deiconify(), self.root.lift()))

    def _start_tray(self) -> None:
        if os.environ.get("CR_NO_TRAY") == "1":
            return
        try:
            import pystray
            img = app_icon(64)
            menu = pystray.Menu(
                pystray.MenuItem("Show window", lambda: self.show(), default=True),
                pystray.MenuItem("Pause or resume", lambda: self.root.after(0, self.toggle_pause)),
                pystray.MenuItem("Skip this one", lambda: self.ctl.skip()),
                pystray.MenuItem("Silence now", lambda: self.root.after(0, self.silence_now)),
                pystray.MenuItem("Quit", lambda: self.root.after(0, self.quit)),
            )
            self.tray = pystray.Icon("chat_reader", img, APP_NAME, menu)
            threading.Thread(target=self.tray.run, daemon=True, name="tray").start()
            mark("tray_started")
        except Exception:
            self.tray = None  # the app works without a tray icon

    def quit(self) -> None:
        mark("quitting", spoken=self.spoken, skipped=self.skipped)
        self.ctl.shutdown()
        if self.hotkeys is not None:
            self.hotkeys.stop()
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
