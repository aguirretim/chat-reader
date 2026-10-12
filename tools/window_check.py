"""Build the real window with a stand-in controller, drive every state, check what it shows, and save screenshots.

No voice, no sound, no network, no tray, no hotkeys. Settings go to a scratch folder. All names and messages are
made up. The window has to be on the screen to be drawn (Tk does not paint a window that is off-screen: tried), so it
shows for about 20 seconds. The keyboard focus is handed straight back to whatever had it.

Usage: python tools\\window_check.py [--shots <folder>]     exit code 0 = every check passed
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import queue
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CR_DATA_DIR"] = tempfile.mkdtemp(prefix="cr-window-check-")
os.environ["CR_NULL_AUDIO"] = "1"
for name in ("CR_AUTOCONNECT", "CR_AUTOTEST", "CR_START_MINIMIZED", "CR_TIMING_LOG"):
    os.environ.pop(name, None)

from chatreader import theme, ui  # noqa: E402
from chatreader import viewmodel as vm  # noqa: E402
from chatreader.config import AppConfig  # noqa: E402
from chatreader.controller import bot_login  # noqa: E402
from chatreader.irc import normalise_channel  # noqa: E402

SHOTS = Path(sys.argv[sys.argv.index("--shots") + 1]) if "--shots" in sys.argv else None
user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
user32.GetParent.restype = wt.HWND
user32.GetParent.argtypes = [wt.HWND]
FOREGROUND = user32.GetForegroundWindow()
OFFSCREEN = "+120+80"      # on the screen; the name is kept from the first attempt
results: list[tuple[bool, str, str]] = []


def check(name: str, ok, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


class FakeController:
    """What the window is allowed to use of Controller (spec section 11), with nothing behind it."""

    def __init__(self, cfg):
        self.cfg, self.events, self.paused, self.connected_channel, self.queue = cfg, queue.Queue(), False, "", []
        self.calls: list[str] = []

    def start(self): self.calls.append("start")
    def shutdown(self): self.calls.append("shutdown")
    def apply_config(self): self.calls.append("apply_config")
    def skip(self): self.calls.append("skip")
    def clear(self): self.calls.append("clear"); self.queue = []
    def test_voice(self): self.calls.append("test_voice")

    def connect(self, text):
        channel = normalise_channel(text)
        if not channel:
            self.events.put(("status", "That does not look like a Twitch channel name."))
            return False
        self.connected_channel = channel
        self.calls.append("connect")
        return True

    def disconnect(self):
        self.connected_channel = ""
        self.calls.append("disconnect")

    def set_paused(self, value):
        self.paused = value
        self.events.put(("paused", value))

    def toggle_pause(self):
        self.calls.append("toggle_pause")
        self.set_paused(not self.paused)

    def panic(self):
        self.calls.append("panic")
        self.queue = []
        self.set_paused(True)

    def blocked_words(self):
        fruit = ["apple", "apricot", "banana", "cherry", "damson", "elderberry", "fig", "grape", "kiwi", "lemon", "mango", "nectarine"]
        return [(f"{fruit[i % 12]}{i}", 1 + i % 3, ["hate", "strong", "mild"][i % 3]) for i in range(900)]


def capture(window, path: Path) -> None:
    """Draw the window (caption included) into a PNG, even though it is off-screen."""
    from PIL import Image

    window.update_idletasks()
    window.update()
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    rect = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(wt.HWND(hwnd))
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(wt.HWND(hwnd), mem, 2)          # 2 = PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(wt.HWND(hwnd), hdc)
    path.parent.mkdir(parents=True, exist_ok=True)
    # trim the invisible resize border Windows 10 adds left, right and below
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    img.crop((7, 0, w - 7, h - 7)).save(path)


def shot(app_or_win, name: str) -> None:
    if SHOTS is not None:
        capture(app_or_win, SHOTS / f"{name}.png")


def bound(window, sequence: str) -> bool:
    """True when the window has a handler for this key. A generated key press only arrives while the window has the
    keyboard, and this check hands the keyboard straight back to whatever had it, so the handler is called directly."""
    return bool(window.bind(sequence))


def pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        while True:
            try:
                kind, data = app.ctl.events.get_nowait()
            except queue.Empty:
                break
            app.handle_event(kind, data)
        app._refresh()
        app.root.update()
        time.sleep(0.01)


def size(app, w: int, h: int) -> None:
    app.root.geometry(f"{app.kit.px(w)}x{app.kit.px(h)}{OFFSCREEN}")
    pump(app, 0.15)


def clipped(widget) -> list[str]:
    """Texts that do not fit: a label asking for more width than it got, or lying outside its window."""
    import tkinter as tk

    out = []
    top = widget.winfo_toplevel()
    right, bottom = top.winfo_rootx() + top.winfo_width(), top.winfo_rooty() + top.winfo_height()
    stack = [widget]
    while stack:
        w = stack.pop()
        if not w.winfo_ismapped():
            continue
        stack.extend(w.winfo_children())
        if isinstance(w, tk.Label) and w.cget("text"):
            too_narrow = not w.cget("wraplength") and w.winfo_reqwidth() > w.winfo_width() + 1
            too_short = w.winfo_reqheight() > w.winfo_height() + 1          # a wrapped hint squeezed by what is below it
            outside = w.winfo_rootx() + w.winfo_width() > right + 1 or w.winfo_rooty() + w.winfo_height() > bottom + 1
            if too_narrow or too_short or outside:
                out.append(str(w.cget("text"))[:40])
    return out


def tab_order(first, count: int) -> list:
    """The controls Tab visits, starting at `first`."""
    out, w = [first], first
    for _ in range(count - 1):
        w = w.tk_focusNext()
        out.append(w)
    return out


T0 = time.mktime((2026, 10, 11, 21, 20, 26, 0, 0, -1))
ROWS = [   # (seconds after T0, name, login, text, status, detail), all made up
    (0, "Nightbot", "nightbot", "Follow the channel for more!", "skipped", "a bot"),
    (0, "LanternFox", "lanternfox", "!drops", "skipped", "a ! command"),
    (0, "PixelMoth", "pixelmoth", "hello hello, just got here", "spoken", ""),
    (1, "QuietHarbor", "quietharbor", "that jump was so close", "spoken", ""),
    (1, "OakAndIron", "oakandiron", "https://example.com/some/long/link", "skipped", "only a link"),
    (1, "MarbleSky", "marblesky", "", "skipped", "blocked by the safety filter"),
    (1, "QuietHarbor", "quietharbor", "how long have you been playing this game? it looks really hard and I want to know if it gets easier later on", "spoken", ""),
    (2, "TinCanTom", "tincantom", "gg", "spoken", ""),
    (5, "Viewer01", "viewer01", "burst message number 1 to fill the line", "skipped", "chat was too fast"),
    (5, "Viewer05", "viewer05", "burst message number 5 to fill the line", "spoken", ""),
    (9, "MarbleSky", "marblesky", "this one hits a sound problem", "skipped", "could not be spoken: Error opening OutputStream: Invalid device"),
]


def feed(app, rows=ROWS, base=T0) -> None:
    for dt, name, login, text, status, detail in rows:
        app.ctl.events.put(("log", {"t": base + dt, "name": name, "login": login, "text": text, "status": status, "detail": detail}))
    pump(app)


def main() -> int:
    cfg = AppConfig(channel="", fetch_emote_lists=False)
    ctl = FakeController(cfg)
    app = ui.App(cfg, controller=ctl, start=False)
    root = app.root
    root.geometry(f"{app.kit.px(920)}x{app.kit.px(600)}{OFFSCREEN}")
    if FOREGROUND:
        user32.SetForegroundWindow(FOREGROUND)      # hand the keyboard back to whatever had it
    pump(app, 0.2)
    k = app.kit

    # -- getting ready --------------------------------------------------------------
    s = app.current_state()
    check("start: getting ready", s.kind == "getting_ready" and app.state_word.cget("text") == "Getting the voice ready...")
    check("speech buttons disabled until the voice is ready", not any(b.enabled for b in app.speech_buttons))
    check("Start reading can be pressed while getting ready", app.start_btn.enabled)
    check("empty list says Nothing here yet", app.log.empty[0] == "Nothing here yet")
    shot(root, "dark_01_getting_ready")

    # -- ready, first run -----------------------------------------------------------
    app.handle_event("voice", "ready")
    app._audio_ready(["Speakers (Example Audio)", "Headphones (Example Headset)"], "")
    pump(app)
    check("ready: Not reading", app.state_word.cget("text") == "Not reading" and "Type your Twitch channel name" in app.state_detail.cget("text"))
    check("first run shows Three steps", app.log.empty[0] == "Three steps")
    check("Start reading is the primary button", app.start_btn.variant == "primary" and app.start_btn.text() == "Start reading")
    check("speech buttons enabled", all(b.enabled for b in app.speech_buttons))
    check("Silence now is plain while not reading", app.panic_btn.variant == "plain")
    check("placeholder shown in the empty channel box", app.hint.winfo_ismapped())
    shot(root, "dark_02_not_reading_first_run")

    # -- wrong channel name -----------------------------------------------------------
    app._set_channel_text("not a channel")
    app.toggle_connect()
    pump(app)
    check("bad channel name: problem state", app.current_state().kind == "problem" and app.state_word.cget("text") == "Check the channel name")
    shot(root, "dark_03_problem_channel_name")

    # -- connecting, reading ----------------------------------------------------------
    app._set_channel_text("demo_channel")
    app.toggle_connect()
    pump(app)
    check("connecting state", app.state_word.cget("text") == "Connecting to demo_channel...", app.state_word.cget("text"))
    check("button now says Stop reading, plain", app.start_btn.text() == "Stop reading" and app.start_btn.variant == "plain")
    check("channel box is read-only while connected", str(app.channel.cget("state")) == "readonly")
    check("focus moved to the list after Start", root.focus_get() is app.log.canvas or not root.focus_displayof())
    shot(root, "dark_04_connecting")
    ctl.events.put(("status", "Reading chat in demo_channel"))
    pump(app)
    check("reading state", app.state_word.cget("text") == "Reading chat" and app.state_detail.cget("text") == "demo_channel · nothing waiting")
    check("empty list says Waiting for chat", app.log.empty[0] == "Waiting for chat")
    check("Silence now is the danger button while reading", app.panic_btn.variant == "danger")
    shot(root, "dark_05_reading_no_messages")
    ctl.queue = [1, 2]
    feed(app)
    check("waiting count shown", app.state_detail.cget("text") == "demo_channel · 2 waiting to be read", app.state_detail.cget("text"))
    check("counters", app.counts.cget("text") == "5 read aloud · 6 not read", app.counts.cget("text"))
    rows = app.log.rows
    check("11 rows listed", len(rows) == 11)
    check("time column header", app.log.columns[0]["title"] == "Your PC time")
    check("time is the Windows format with seconds", rows[0]["cells"]["time"] == vm.windows_time(T0) and rows[0]["cells"]["time"].count(":") == 2)
    check("blocked row shows (hidden) and the reworded reason", rows[5]["cells"]["message"] == "(hidden)"
          and rows[5]["cells"]["result"] == "Not read: blocked word or personal detail")
    check("sound fault row", rows[10]["cells"]["result"] == "Not read: sound problem" and rows[10]["icon"][1] == "bad")
    check("read row: text colour, green tick", rows[2]["tone"] == "text" and rows[2]["icon"] == (vm.ICON["tick"], "ok"))
    check("not-read row: muted", rows[0]["tone"] == "muted" and rows[0]["icon"][1] == "muted")
    check("Clear list enabled", app.clear_btn.enabled)
    app.log._select_only(6)
    app.log.render()
    shot(root, "dark_06_reading")
    check("nothing clipped at 920x600", not clipped(root), ", ".join(clipped(root)))
    app.channel.configure(state="normal")       # read-only while connected; Tab skips nothing either way, this keeps the walk simple
    want = [app.channel, app.start_btn, app.settings_btn, app.pause_btn, app.skip_btn, app.skipall_btn, app.panic_btn,
            app.volume, app.clear_btn, app.log.canvas]
    got = tab_order(app.channel, len(want))
    check("Tab order on the main view follows the spec", got == want, " > ".join(str(w).rsplit(".", 1)[-1] for w in got))
    app.channel.configure(state="readonly")

    # -- paused, silenced -------------------------------------------------------------
    ctl.queue = [1, 2, 3, 4]
    app.toggle_pause()
    pump(app)
    check("paused state with count", app.state_word.cget("text") == "Paused"
          and app.state_detail.cget("text") == "demo_channel · 4 waiting. Press Resume or Ctrl+Alt+F10 to carry on.", app.state_detail.cget("text"))
    check("Pause button reads Resume", app.pause_btn.text() == "Resume")
    check("Silence now plain while paused", app.panic_btn.variant == "plain")
    shot(root, "dark_07_paused")
    app.toggle_pause()
    pump(app)
    check("resume returns to Reading chat", app.state_word.cget("text") == "Reading chat" and app.pause_btn.text() == "Pause")
    app._on_hotkey("panic")
    pump(app)
    check("Ctrl+Alt+F9 shows Silenced", app.state_word.cget("text") == "Silenced" and "panic" in ctl.calls and app.pause_btn.text() == "Resume")
    shot(root, "dark_08_silenced")
    app._on_hotkey("pause")
    pump(app)
    check("resume after Silence now returns to Reading chat", app.state_word.cget("text") == "Reading chat")

    # -- problems ---------------------------------------------------------------------
    ctl.events.put(("status", "Sound problem: Error opening OutputStream: Invalid device"))
    pump(app)
    check("sound problem state", app.state_word.cget("text") == "Sound problem" and "Play the voice on" in app.state_detail.cget("text"))
    check("raw error only in the tooltip", "OutputStream" in app._state_tooltip and "OutputStream" not in app.state_detail.cget("text"))
    shot(root, "dark_09_problem_sound")
    app.test_voice()
    pump(app)
    check("Test voice clears the problem", app.state_word.cget("text") == "Reading chat")
    ctl.events.put(("status", "Chat connection lost (connection closed by Twitch). Trying again in 4 s."))
    pump(app)
    check("chat lost state", app.state_word.cget("text") == "Chat connection lost" and app.state_detail.cget("text") == "Trying again in 4 seconds.")
    shot(root, "dark_10_chat_lost")
    ctl.events.put(("status", "Reading chat in demo_channel"))
    pump(app)

    # -- bots you added ----------------------------------------------------------------
    saved_calls = len(ctl.calls)
    app.toggle_bot("lanternfox")
    pump(app)
    check("right-click add: name is on the bot list", any(bot_login(n) == "lanternfox" for n in cfg.extra_bots))
    check("confirmation in the state area", app.state_detail.cget("text") == "lanternfox added to bots. Undo in Settings.")
    check("controller was told to apply the change", "apply_config" in ctl.calls[saved_calls:])
    check("menu label flips", vm.bot_menu_label("LanternFox", "lanternfox", cfg.extra_bots) == "Read LanternFox again (remove from bots)")
    shot(root, "dark_11_bot_added_note")
    app.note = ""
    pump(app)
    check("the confirmation goes away", app.state_detail.cget("text").startswith("demo_channel"))

    # -- follow-newest and jump ----------------------------------------------------------
    feed(app, [(20 + i, f"Viewer{i:02d}", f"viewer{i:02d}", f"made-up message number {i}", "spoken", "") for i in range(40)])
    check("follows the newest row", app.log.at_bottom())
    app.log._scroll_to(0)
    feed(app, [(70, "PixelMoth", "pixelmoth", "one more while scrolled up", "spoken", "")])
    check("scrolled up: list does not move, Jump to newest appears", not app.log.at_bottom() and app.jump_btn.winfo_ismapped()
          and app.jump_btn.text() == "Jump to newest (1 new)", app.jump_btn.text())
    shot(root, "dark_12_jump_to_newest")
    app.jump_to_newest()
    pump(app)
    check("Jump to newest goes to the bottom and hides", app.log.at_bottom() and not app.jump_btn.winfo_ismapped())

    # -- 200-row limit, midnight ---------------------------------------------------------
    feed(app, [(100 + i, "Viewer01", "viewer01", f"filler {i}", "spoken", "") for i in range(260)])
    check("list keeps the newest 200 rows", sum(1 for r in app.log.rows if r.get("kind") != "date") == 200)
    check("counters keep counting past 200", app.spoken + app.skipped == 312, str(app.spoken + app.skipped))

    # -- clear list -----------------------------------------------------------------------
    before = len(ctl.calls)
    check("Ctrl+L is bound on the main window", bound(root, "<Control-l>"))
    app.clear_list()
    pump(app)
    check("Clear list clears rows and counters", not app.log.rows and app.counts.cget("text") == "" and app.log.empty[0] == "List cleared")
    check("Clear list does not call the controller", len(ctl.calls) == before and ctl.connected_channel == "demo_channel")
    check("Clear list disabled when empty", not app.clear_btn.enabled)
    shot(root, "dark_13_list_cleared")
    midnight = time.mktime((2026, 10, 12, 0, 0, 0, 0, 0, -1))
    feed(app, [(-3, "PixelMoth", "pixelmoth", "last one before midnight", "spoken", ""),
               (5, "QuietHarbor", "quietharbor", "first one after midnight", "spoken", ""),
               (9, "TinCanTom", "tincantom", "and another", "skipped", "only emotes")], base=midnight)
    kinds = [r.get("kind") for r in app.log.rows]
    check("date line before the first row after midnight", kinds == ["row", "date", "row", "row"], str(kinds))
    check("date line is the long Windows date", app.log.rows[1]["cells"]["date"] == vm.windows_date(midnight + 5))
    check("date line not counted", app.counts.cget("text") == "2 read aloud · 1 not read")
    shot(root, "dark_14_midnight")

    # -- settings page ----------------------------------------------------------------------
    app.clear_list()
    feed(app)
    app.toggle_settings()
    pump(app)
    check("Settings page shown, state area still visible", app.settings_view.winfo_ismapped() and not app.list_view.winfo_ismapped()
          and app.state_word.winfo_ismapped())
    check("groups line", app.groups_lbl.cget("text") == "3 of 4 groups on")
    check("added bot is listed", [r["cells"]["name"] for r in app.bots.rows] == ["lanternfox"])
    check("speakers listed", list(app.device.cget("values")) == ["Windows default", "Speakers (Example Audio)", "Headphones (Example Headset)"])
    check("nothing clipped on Settings at 920x600", not clipped(root), ", ".join(clipped(root)))
    got = tab_order(app.settings_btn, 6)
    check("Tab order on Settings starts Back, Voice, Test voice, Play the voice on, Speed",
          got[1:] == [app.back_btn, app.voice, app.test_btn, app.device, app.speed], " > ".join(str(w).rsplit(".", 1)[-1] for w in got))
    shot(root, "dark_15_settings")
    app.bot_entry.insert(0, "@Helper_Bot9 ")
    app._add_bot()
    app._remove_bot(0)
    pump(app)
    check("add and remove a bot name in Settings", cfg.extra_bots == ["Helper_Bot9"], str(cfg.extra_bots))
    app._remove_bot(0)
    pump(app)
    check("empty bots hint", app.bots_empty.winfo_ismapped() and not cfg.extra_bots)
    app.failed_keys = ["Ctrl+Alt+F9"]
    app._show_failed_keys()
    check("failed shortcut line", app.keys_bad.cget("text") == "Not working because another program uses them: Ctrl+Alt+F9")
    shot(root, "dark_16_settings_no_bots_shortcut_taken")
    app.failed_keys = []
    app._show_failed_keys()
    check("Esc is bound on the main window", bound(root, "<Escape>"))
    app.show_view("list")
    pump(app)
    check("Back leaves Settings", app.list_view.winfo_ismapped())

    # -- sizes -----------------------------------------------------------------------------
    size(app, 720, 480)
    check("minimum size: nothing clipped on the main view", not clipped(root), ", ".join(clipped(root)))
    check("narrow: the word Volume is hidden", not app.vol_word.winfo_ismapped() and app.log.columns[3]["width"] == 190)
    visible_rows = (app.log.canvas.winfo_height() - app.log.hh) // app.log.rh
    check("minimum size shows at least 6 rows", visible_rows >= 6, str(visible_rows))
    shot(root, "dark_17_min_720x480")
    app.toggle_settings()
    pump(app, 0.15)
    check("minimum size: nothing clipped on Settings", not clipped(root), ", ".join(clipped(root)))
    low = app.low_hint.winfo_rooty() + app.low_hint.winfo_height()
    check("minimum size: Settings content ends above the shortcuts line", low <= app.keys_line.winfo_rooty() + 1, f"{low} vs {app.keys_line.winfo_rooty()}")
    shot(root, "dark_18_min_settings")
    app.toggle_settings()
    fit = theme.fitted_size(k.px(920), k.px(600), (1024, 560))
    check("opens inside a 1024x600 screen (560 work area)", fit[0] + 16 <= 1024 and fit[1] + 39 <= 560, str(fit))
    root.geometry(f"{fit[0]}x{fit[1]}{OFFSCREEN}")
    pump(app, 0.15)
    check("1024x600 fit: nothing clipped", not clipped(root), ", ".join(clipped(root)))
    shot(root, "dark_19_fit_1024x600")
    size(app, 1500, 700)
    widths = app.log.widths()
    check("wide: only the Message column grows", widths[0] == k.px(104) and widths[1] == k.px(136) and widths[3] == k.px(250) and widths[2] > k.px(900))
    shot(root, "dark_20_wide_1500x700")
    size(app, 920, 600)

    # -- blocked words window ---------------------------------------------------------------
    app.open_blocked_words()
    pump(app, 0.2)
    words = app._words_win
    words.win.geometry(f"{k.px(640)}x{k.px(520)}{OFFSCREEN}")
    pump(app, 0.2)
    cells = [r["cells"] for r in words.table.rows[:5]]
    check("blocked words: masked by default", all(c["word"].endswith("*") for c in cells) and not words.show.get(), str(cells[0]))
    check("blocked words: status words", {r["cells"]["state"] for r in words.table.rows} == {"Blocked"})
    words.group_pick.current(3)
    words._refresh()
    check("blocked words: a group that is off reads Allowed (group is off), in muted",
          {(r["cells"]["state"], r["tone"]) for r in words.table.rows} == {("Allowed (group is off)", "muted")})
    words.group_pick.current(0)
    words._refresh()
    check("blocked words: count line", words.count_lbl.cget("text") == "Showing the first 600 of 900. Type in Find to see fewer.")
    check("blocked words: nothing clipped", not clipped(words.win), ", ".join(clipped(words.win)))
    shot(words.win, "dark_21_blocked_words")
    words.table.selected = {0}
    words._toggle_selected()
    pump(app)
    check("blocked words: allow one word", len(cfg.words_off) == 1 and words.table.rows[0]["cells"]["state"] == "Allowed"
          and words.allowed_lbl.cget("text") == "1 word allowed one by one.")
    words.new_word.insert(0, "Pineapple")
    words._add_own()
    words.table.selected = {0}
    words._selection_changed()
    check("blocked words: own word added, button reads Remove selected", cfg.words_added == ["pineapple"] and words.toggle_btn.text() == "Remove selected")
    words.win.geometry(f"{k.px(560)}x{k.px(420)}{OFFSCREEN}")
    pump(app, 0.2)
    check("blocked words minimum size: nothing clipped", not clipped(words.win), ", ".join(clipped(words.win)))
    shot(words.win, "dark_22_blocked_words_min")
    check("Esc is bound on the Blocked words window", bound(words.win, "<Escape>"))
    words.win.destroy()
    pump(app)
    app.open_blocked_words()
    pump(app, 0.2)
    check("reopened: masked again", not app._words_win.show.get() and app._words_win.table.rows[0]["cells"]["word"].endswith("*"))
    app._words_win.win.destroy()
    cfg.words_off, cfg.words_added = [], []

    # -- light theme: the whole window is rebuilt, the list is kept ---------------------------
    rows_before = len(app.log.rows)
    app.colours.choose("light")
    pump(app, 0.3)
    root.geometry(f"{k.px(920)}x{k.px(600)}{OFFSCREEN}")
    if FOREGROUND:
        user32.SetForegroundWindow(FOREGROUND)
    pump(app, 0.2)
    check("light theme applied at once", app.mode == "light" and app.kit.p["card"] == "#FFFFFF" and cfg.theme == "light")
    check("list and state survive the colour change", len(app.log.rows) == rows_before and app.state_word.cget("text") == "Reading chat")
    app.log._select_only(6)
    app.log.render()
    shot(root, "light_06_reading")
    app.toggle_pause()
    pump(app)
    shot(root, "light_07_paused")
    app.toggle_pause()
    app.toggle_settings()
    pump(app, 0.15)
    check("light Settings: nothing clipped", not clipped(root), ", ".join(clipped(root)))
    shot(root, "light_15_settings")
    size(app, 720, 480)
    check("light minimum Settings: nothing clipped", not clipped(root), ", ".join(clipped(root)))
    shot(root, "light_18_min_settings")
    app.toggle_settings()
    pump(app, 0.15)
    check("light minimum main: nothing clipped", not clipped(root), ", ".join(clipped(root)))
    shot(root, "light_17_min_720x480")
    size(app, 920, 600)
    app.open_blocked_words()
    pump(app, 0.2)
    app._words_win.win.geometry(f"{k.px(640)}x{k.px(520)}{OFFSCREEN}")
    pump(app, 0.2)
    shot(app._words_win.win, "light_21_blocked_words")
    app._words_win.win.destroy()
    app.colours = app.colours
    app._theme_chosen("system")
    pump(app, 0.3)
    check("Same as Windows follows the Windows app mode", app.mode == ("light" if theme.windows_uses_light_apps() else "dark"))

    # -- stop, quit ---------------------------------------------------------------------------
    app.toggle_connect()
    pump(app)
    check("Stop reading: Not reading / Stopped", app.state_word.cget("text") == "Not reading" and app.state_detail.cget("text").startswith("Stopped."))
    app.voice_error, app.voice_ok = "A voice file is missing: kokoro-v1.0.int8.onnx.", False
    pump(app)
    check("voice failure state", app.state_word.cget("text") == "The voice could not start" and "missing" in app.state_detail.cget("text"))
    app.quit()
    check("quit shuts the controller down", "shutdown" in ctl.calls)

    failed = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed" + (f"; screenshots in {SHOTS}" if SHOTS else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
