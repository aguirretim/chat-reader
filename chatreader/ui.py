"""The window and the tray icon. All logic lives in controller.py."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import audio
from .config import APP_NAME, VERSION, AppConfig
from .controller import Controller
from .hotkeys import DEFAULT_KEYS, Hotkeys
from .tts import VOICES
from .wordlist import WordListWindow

MAX_LOG_ROWS = 200
DEFAULT_SPEAKER = "Windows default"


def mark(name: str, **extra) -> None:
    """Start-up and activity marks for the measurement scripts (CR_TIMING_LOG). Never contains chat text."""
    path = os.environ.get("CR_TIMING_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"mark": name, "t": time.time(), **extra}) + "\n")


class App:
    def __init__(self, cfg: AppConfig, controller: Controller | None = None):
        self.cfg = cfg
        self.ctl = controller or Controller(cfg)
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("860x560")
        self.root.minsize(620, 440)
        if os.environ.get("CR_START_MINIMIZED") == "1":
            self.root.iconify()
        self.voice_ok = False
        self.audio_ok = False
        self.usable_marked = False
        self.spoken = 0
        self.skipped = 0
        self.tray = None
        self._built = False
        self._build()
        self._built = True
        self.hotkeys = None
        if cfg.hotkeys:
            self.hotkeys = Hotkeys(lambda name: self.root.after(0, self._on_hotkey, name))
            self.hotkeys.start()
        self._start_tray()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.ctl.start()
        audio.start(lambda names, err: self.root.after(0, self._audio_ready, names, err))
        self.root.after(100, self._poll)
        self.root.after_idle(lambda: mark("window_ready"))
        auto = os.environ.get("CR_AUTOCONNECT")
        if auto:
            self.channel.delete(0, "end")
            self.channel.insert(0, auto)

    # -- layout -----------------------------------------------------------------
    def _build(self) -> None:
        pad = {"padx": 10, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Twitch channel").pack(side="left")
        self.channel = ttk.Entry(top, width=28)
        self.channel.insert(0, self.cfg.channel)
        self.channel.pack(side="left", padx=6)
        self.channel.bind("<Return>", lambda e: self.toggle_connect())
        self.connect_btn = ttk.Button(top, text="Start reading", command=self.toggle_connect, state="disabled")
        self.connect_btn.pack(side="left")
        self.status = tk.StringVar(value="Loading the voice...")
        ttk.Label(self.root, textvariable=self.status).pack(fill="x", padx=10)

        voice = ttk.LabelFrame(self.root, text="Voice")
        voice.pack(fill="x", **pad)
        ttk.Label(voice, text="Voice").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        labels = [label for _, label in VOICES]
        if self.cfg.voice not in dict(VOICES):
            self.cfg.voice = VOICES[0][0]
        self.voice = ttk.Combobox(voice, values=labels, state="readonly", width=26)
        self.voice.set(dict(VOICES).get(self.cfg.voice, labels[0]))
        self.voice.grid(row=0, column=1, sticky="w")
        self.voice.bind("<<ComboboxSelected>>", lambda e: self._save())
        self.test_btn = ttk.Button(voice, text="Test voice", command=self.ctl.test_voice, state="disabled")
        self.test_btn.grid(row=0, column=2, padx=8)
        ttk.Label(voice, text="Speaker").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.device = ttk.Combobox(voice, values=[DEFAULT_SPEAKER], state="readonly", width=52)
        self.device.set(self.cfg.device_name or DEFAULT_SPEAKER)
        self.device.grid(row=1, column=1, columnspan=2, sticky="w")
        self.device.bind("<<ComboboxSelected>>", lambda e: self._save())
        ttk.Label(voice, text="Volume").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.volume = ttk.Scale(voice, from_=0, to=100, length=220, command=lambda v: self._save(write=False))
        self.volume.set(self.cfg.volume)
        self.volume.grid(row=2, column=1, sticky="w")
        ttk.Label(voice, text="Speed").grid(row=3, column=0, sticky="w", padx=6, pady=3)
        self.speed = ttk.Scale(voice, from_=0.7, to=1.5, length=220, command=lambda v: self._save(write=False))
        self.speed.set(self.cfg.speed)
        self.speed.grid(row=3, column=1, sticky="w")
        for slider in (self.volume, self.speed):
            slider.bind("<ButtonRelease-1>", lambda e: self._save())

        opts = ttk.Frame(self.root)
        opts.pack(fill="x", **pad)
        self.say_names = tk.BooleanVar(value=self.cfg.say_names)
        self.skip_bots = tk.BooleanVar(value=self.cfg.skip_bots)
        ttk.Checkbutton(opts, text="Say who wrote it", variable=self.say_names, command=self._save).pack(side="left")
        ttk.Button(opts, text="Blocked words...", command=self.open_blocked_words).pack(side="left", padx=12)
        ttk.Checkbutton(opts, text="Skip bots", variable=self.skip_bots, command=self._save).pack(side="left")
        self.low_memory = tk.BooleanVar(value=self.cfg.low_memory)
        ttk.Checkbutton(opts, text="Use less memory (slower, from next start)", variable=self.low_memory, command=self._save).pack(side="left", padx=12)

        keys = {name: label for name, (_, label) in DEFAULT_KEYS.items()}
        ctl = ttk.Frame(self.root)
        ctl.pack(fill="x", **pad)
        self.pause_btn = ttk.Button(ctl, text=f"Mute ({keys['pause']})", command=self.ctl.toggle_pause, width=22)
        self.pause_btn.pack(side="left")
        ttk.Button(ctl, text=f"Skip this one ({keys['skip']})", command=self.ctl.skip).pack(side="left", padx=6)
        ttk.Button(ctl, text=f"Clear all waiting ({keys['clear']})", command=self.ctl.clear).pack(side="left")
        ttk.Button(ctl, text=f"Stop everything ({keys['panic']})", command=self.ctl.panic).pack(side="left", padx=6)
        self.counts = tk.StringVar(value="")
        ttk.Label(ctl, textvariable=self.counts).pack(side="right")

        box = ttk.Frame(self.root)
        box.pack(fill="both", expand=True, **pad)
        cols = ("time", "who", "message", "result")
        self.log = ttk.Treeview(box, columns=cols, show="headings", height=10)
        for col, title, width in (("time", "Time", 62), ("who", "Who", 120), ("message", "Message", 330), ("result", "What happened", 190)):
            self.log.heading(col, text=title)
            self.log.column(col, width=width, stretch=col in ("message", "result"))
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.log.tag_configure("skipped", foreground="#777777")
        ttk.Label(self.root, text=f"{APP_NAME} {VERSION}. Chat is read without signing in. Messages are shown here only and are never saved or sent anywhere.",
                  foreground="#555555").pack(fill="x", padx=10, pady=(0, 6))

    # -- settings ---------------------------------------------------------------
    def _save(self, write: bool = True) -> None:
        if not self._built:
            return  # the sliders fire their command while the window is still being laid out
        by_label = {label: vid for vid, label in VOICES}
        self.cfg.voice = by_label.get(self.voice.get(), self.cfg.voice)
        chosen = self.device.get()
        self.cfg.device_name = "" if chosen == DEFAULT_SPEAKER else chosen
        self.cfg.volume = int(float(self.volume.get()))
        self.cfg.speed = round(float(self.speed.get()), 2)
        self.cfg.say_names = self.say_names.get()
        self.cfg.skip_bots = self.skip_bots.get()
        self.cfg.low_memory = self.low_memory.get()
        self.ctl.apply_config()
        if not write:
            return
        try:
            self.cfg.save()
        except OSError:
            pass  # a read-only profile must not stop the app; settings just will not persist

    def open_blocked_words(self) -> None:
        if getattr(self, "_words_win", None) is not None and self._words_win.win.winfo_exists():
            self._words_win.win.lift()
            return
        entries = self.ctl.blocked_words()
        if entries is None:
            self.status.set("The word list is still loading. Try again in a few seconds.")
            return
        self._words_win = WordListWindow(self.root, self.cfg, entries, self._save)

    # -- events from the controller ------------------------------------------------
    def _audio_ready(self, names: list[str], error: str) -> None:
        self.audio_ok = True
        mark("audio_ready", devices=len(names), error=bool(error))
        values = [DEFAULT_SPEAKER] + names
        self.device.config(values=values)
        if self.device.get() not in values:
            self.device.set(DEFAULT_SPEAKER)
        if error:
            self.status.set(error)
        self._maybe_usable()

    def _maybe_usable(self) -> None:
        if not (self.voice_ok and self.audio_ok):
            return
        self.connect_btn.config(state="normal")
        self.test_btn.config(state="normal")
        if not self.usable_marked:
            self.usable_marked = True
            self.status.set("Ready. Type a channel name and press Start reading.")
            mark("usable")
            if self.hotkeys is not None and self.hotkeys.registered.wait(1.0):
                mark("hotkeys", failed=len(self.hotkeys.failed))
                if self.hotkeys.failed:
                    self.status.set("Ready. These shortcuts are used by another program: " + ", ".join(self.hotkeys.failed))
            if os.environ.get("CR_AUTOCONNECT"):
                self.toggle_connect()
            if os.environ.get("CR_AUTOTEST") == "1":
                self.ctl.test_voice()

    def _poll(self) -> None:
        try:
            while True:
                kind, data = self.ctl.events.get_nowait()
                if kind == "voice":
                    if data == "ready":
                        self.voice_ok = True
                        mark("voice_ready")
                        self._maybe_usable()
                    elif data != "loading":
                        self.status.set(f"The voice could not be loaded. {data}")
                        mark("voice_failed")
                elif kind == "status":
                    self.status.set(data)
                    if isinstance(data, str) and data.startswith("Reading chat"):
                        mark("reading_chat")
                elif kind == "paused":
                    label = DEFAULT_KEYS["pause"][1]
                    self.pause_btn.config(text=f"Resume ({label})" if data else f"Mute ({label})")
                elif kind == "log":
                    self._add_log(data)
        except queue.Empty:
            pass
        except Exception:
            pass  # one bad event must not freeze the window for good
        finally:
            self.root.after(100, self._poll)

    def _add_log(self, entry: dict) -> None:
        spoken = entry["status"] == "spoken"
        if spoken:
            self.spoken += 1
            mark("spoken", n=self.spoken)
        else:
            self.skipped += 1
            mark("not_read", n=self.skipped)
        result = "read aloud" if spoken else f"not read: {entry['detail']}"
        row = (time.strftime("%H:%M:%S", time.localtime(entry["t"])), entry["name"], entry["text"], result)
        self.log.insert("", "end", values=row, tags=("spoken" if spoken else "skipped",))
        rows = self.log.get_children()
        if len(rows) > MAX_LOG_ROWS:
            self.log.delete(*rows[: len(rows) - MAX_LOG_ROWS])
        self.log.yview_moveto(1.0)
        self.counts.set(f"read {self.spoken}   not read {self.skipped}")

    # -- actions ----------------------------------------------------------------
    def toggle_connect(self) -> None:
        if self.ctl.connected_channel:
            self.ctl.disconnect()
            self.connect_btn.config(text="Start reading")
            self.status.set("Stopped.")
            return
        if not (self.voice_ok and self.audio_ok):
            return
        if self.ctl.connect(self.channel.get()):
            self.cfg.channel = self.ctl.connected_channel
            self._save()
            self.connect_btn.config(text="Stop reading")

    def _on_hotkey(self, name: str) -> None:
        mark("hotkey", key=name)
        {"pause": self.ctl.toggle_pause, "skip": self.ctl.skip, "clear": self.ctl.clear, "panic": self.ctl.panic}[name]()

    def show(self) -> None:
        self.root.after(0, lambda: (self.root.deiconify(), self.root.lift()))

    def _start_tray(self) -> None:
        if os.environ.get("CR_NO_TRAY") == "1":
            return
        try:
            import pystray
            from PIL import Image, ImageDraw

            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse((4, 4, 60, 60), fill=(100, 65, 165, 255))
            draw.polygon([(20, 26), (30, 26), (42, 16), (42, 48), (30, 38), (20, 38)], fill="white")
            menu = pystray.Menu(
                pystray.MenuItem("Show window", lambda: self.show(), default=True),
                pystray.MenuItem("Pause or resume", lambda: self.root.after(0, self.ctl.toggle_pause)),
                pystray.MenuItem("Skip this one", lambda: self.ctl.skip()),
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
