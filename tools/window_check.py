"""Build the real window with a stand-in controller, drive every state, check what it shows, and save screenshots.

No voice, no sound, no network, no tray, no hotkeys. Settings go to a scratch folder. All names and messages are
made up. The window has to be on the screen to be drawn (Tk does not paint a window that is off-screen: tried), so it
shows for about 25 seconds, on the small second monitor from the moment it is created (CR_CHECK_POS, default
+2580+285; that monitor is 1024x600, so the largest size used is 1000x505). It is never maximised, no key presses
are sent, the keyboard focus is handed straight back to whatever had it, and the run stops (exit code 3) if the
mouse moves into the window.

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
POS = os.environ.get("CR_CHECK_POS", "+2580+285")
BASE_H = 505               # the tallest window that fits the 1024x600 monitor with its caption and the taskbar
os.environ["CR_WINDOW_POS"] = POS


class MouseInWindow(Exception):
    pass


def mouse_inside(window) -> bool:
    point, rect = wt.POINT(), wt.RECT()
    user32.GetCursorPos(ctypes.byref(point))
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    return rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom
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
    def retry_voice(self): self.calls.append("retry_voice")

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


def caption_icon_purple(window) -> int:
    """Purple pixels in the top-left corner of the window as it is on the screen (the caption icon). A real screen
    grab, because PrintWindow does not draw the caption icon. 0 if another window covers the corner."""
    from PIL import ImageGrab

    window.update()
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    rect = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    img = ImageGrab.grab(bbox=(rect.left + 8, rect.top + 2, rect.left + 44, rect.top + 30), all_screens=True).convert("RGB")
    if SHOTS is not None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        img.resize((img.width * 6, img.height * 6)).save(SHOTS / "caption_icon_screen_grab.png")
    raw = img.tobytes()
    return sum(1 for i in range(0, len(raw), 3) if abs(raw[i] - 100) < 30 and abs(raw[i + 1] - 65) < 30 and abs(raw[i + 2] - 165) < 35)


def shot(app_or_win, name: str) -> None:
    if SHOTS is not None:
        capture(app_or_win, SHOTS / f"{name}.png")


def bound(window, sequence: str) -> bool:
    """True when the window has a handler for this key. A generated key press only arrives while the window has the
    keyboard, and this check hands the keyboard straight back to whatever had it, so the handler is called directly."""
    return bool(window.bind(sequence))


def pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    tops = [app.root] + ([app._words_win.win] if app._words_win is not None and app._words_win.win.winfo_exists() else [])
    if any(mouse_inside(w) for w in tops):
        raise MouseInWindow
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
    app.root.geometry(f"{app.kit.px(w)}x{app.kit.px(h)}{POS}")
    pump(app, 0.15)


def clipped(widget) -> list[str]:
    """What does not fit: a label asking for more room than it got, or any control lying partly outside the
    frame it sits in (that is how a control pushed off the bottom of the Settings page shows up)."""
    import tkinter as tk

    out = []
    top = widget.winfo_toplevel()
    right, bottom = top.winfo_rootx() + top.winfo_width(), top.winfo_rooty() + top.winfo_height()
    stack = [widget]
    while stack:
        w = stack.pop()
        if not w.winfo_ismapped():
            continue
        stack.extend(c for c in w.winfo_children() if not isinstance(c, tk.Toplevel))
        m = w.master
        if w is not top and m is not None and not isinstance(w, tk.Toplevel):
            over_x = w.winfo_rootx() + w.winfo_width() - (m.winfo_rootx() + m.winfo_width())
            over_y = w.winfo_rooty() + w.winfo_height() - (m.winfo_rooty() + m.winfo_height())
            if over_x > 1 or over_y > 1 or w.winfo_rootx() < m.winfo_rootx() - 1 or w.winfo_rooty() < m.winfo_rooty() - 1:
                label = w.cget("text") if isinstance(w, tk.Label) else ""
                out.append(f"{type(w).__name__} {str(label)[:30] or str(w).rsplit('.', 2)[-1]} sticks out by {max(over_x, over_y)}")
                continue
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
    root.geometry(f"{app.kit.px(920)}x{app.kit.px(BASE_H)}{POS}")
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

    # -- voice files being downloaded (a copy run from source, first start) -----------
    ctl.events.put(("voice_files", {"state": "downloading", "done": 45_000_000, "total": 120_575_669}))
    pump(app)
    check("download: progress in the state area", app.state_word.cget("text") == "Getting the voice, 43 of 115 MB"
          and app.progress_track.winfo_ismapped(), app.state_word.cget("text"))
    share = app.progress_fill.winfo_width() / max(1, app.progress_track.winfo_width())
    check("download: the bar is 37% full", abs(share - 45_000_000 / 120_575_669) < 0.02, f"{share:.3f}")
    check("download: no Try again button yet", not app.retry_btn.winfo_ismapped())
    shot(root, "dark_01b_voice_download")
    ctl.events.put(("voice_files", {"state": "failed", "kind": "offline"}))
    pump(app)
    check("download failed: plain message and Try again", app.state_word.cget("text") == "The voice could not be downloaded"
          and "internet connection" in app.state_detail.cget("text") and app.retry_btn.winfo_ismapped() and not app.progress_track.winfo_ismapped())
    shot(root, "dark_01c_voice_download_failed")
    app.retry_btn.invoke()
    pump(app)
    check("Try again asks the controller and clears the message", "retry_voice" in ctl.calls and app.state_word.cget("text").startswith("Getting the voice"))
    ctl.events.put(("voice_files", {"state": "downloading", "done": 120_575_669, "total": 120_575_669}))
    pump(app)
    check("download finished: back to plain getting ready, bar gone", app.state_word.cget("text") == "Getting the voice ready..."
          and not app.progress_track.winfo_ismapped())

    # -- ready, first run -----------------------------------------------------------
    app.handle_event("voice", "ready")
    app._audio_ready(["Speakers (Example Audio)", "Headphones (Example Headset)"], "")
    pump(app)
    check("ready: Not reading", app.state_word.cget("text") == "Not reading" and "Type your Twitch channel name" in app.state_detail.cget("text"))
    check("first run shows Three steps", app.log.empty[0] == "Three steps")
    check("Start reading is the primary button", app.start_btn.variant == "primary" and app.start_btn.text() == "Start reading")
    check("speech buttons enabled", all(b.enabled for b in app.speech_buttons))
    check("Silence now is plain while not reading", app.panic_btn.variant == "plain")
    check("placeholder shown in the empty channel box, in the readable grey", app.hint.winfo_ismapped() and app.hint.cget("fg") == k.p["muted"])
    check("M8 the icon file is in the package", bool(ui.icon_file()))
    purple = caption_icon_purple(root)
    check("M8 the caption shows the app icon (purple pixels where the icon is)", purple >= 20, f"{purple} purple pixels")
    app._set_channel_text("demo_channel")
    pump(app)
    check("channel typed: the state line says only Press Start reading", app.state_detail.cget("text") == "Press Start reading.")
    app._set_channel_text("")
    pump(app)
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
          and rows[5]["cells"]["result"] == "blocked word" and "personal detail" in app._row_tip(5))
    check("sound fault row", rows[10]["cells"]["result"] == "sound problem" and rows[10]["icon"][1] == "bad")
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
    # must-fix 1: a shorter window must keep showing the newest row, and keep following
    size(app, 920, 480)
    last_bottom = app.log.hh + len(app.log.rows) * app.log.rh - app.log.offset
    check("M1 after making the window shorter the newest row is still fully in view",
          app.log.offset == app.log._max_offset() and last_bottom <= app.log.canvas.winfo_height(), f"{app.log.offset} of {app.log._max_offset()}")
    feed(app, [(60, "PixelMoth", "pixelmoth", "one more after the window got shorter", "spoken", "")])
    check("M1 and it still follows new chat", app.log.offset == app.log._max_offset() and not app.jump_btn.winfo_ismapped())
    size(app, 920, BASE_H)
    check("M1 taller again: still at the newest row", app.log.offset == app.log._max_offset())
    # must-fix 2: a tooltip stays on the monitor its control is on
    area = theme.monitor_work_area(root.winfo_rootx() + 50, root.winfo_rooty() + 50)
    tip = app.pause_btn.tooltip
    spots = []
    for at in ((area[2] + 400, area[3] + 400), (area[0] - 900, area[1] - 300)):
        tip.show(at=at)
        root.update()
        x, y = getattr(tip, "last_position", (None, None))
        inside = x is not None and area[0] <= x and x + tip._tip.winfo_width() <= area[2] and area[1] <= y and y + tip._tip.winfo_height() <= area[3]
        spots.append(inside)
        tip.hide()
    check("M2 a tooltip asked for off the edge is kept on the window's own monitor", all(spots), f"{spots} area {area}")
    # must-fix 4: keyboard focus can be seen. The focus handlers are called directly: real focus would take Tim's keyboard.
    p = k.p
    app.start_btn.set(variant="primary")
    app.start_btn._focus(True)
    check("M4 focus on the purple Start reading button: inner ring in the text colour",
          int(app.start_btn.face.cget("highlightthickness")) >= 2 and app.start_btn.face.cget("highlightbackground") == p["on_accent"]
          and app.start_btn.cget("bg") == p["accent"])
    app.start_btn._focus(False)
    check("M4 the ring goes away again", int(app.start_btn.face.cget("highlightthickness")) == max(1, k.px(1)))
    app._state_key = None
    app._refresh()
    app.say_names.set(True)
    app.say_names._focus(True)
    check("M4 focus on a ticked tick box: ring around box and label", app.say_names.cget("bg") == p["accent"] and bound(app.say_names, "<FocusIn>"))
    app.say_names._focus(False)
    check("M4 tick box ring goes away", app.say_names.cget("bg") == app.say_names.under)
    seg = app.colours.buttons[app.colours.value]
    seg._focus(True)
    check("M4 focus on the chosen colour: ring has room and an inner line",
          int(str(seg.cget("padx"))) >= 2 and int(seg.face.cget("highlightthickness")) >= 2 and seg.face.cget("highlightbackground") == p["on_accent"])
    seg._focus(False)
    app.log.selected, app.log.anchor = set(), None
    app.log.set_focused(True)
    check("M4 focus on the list with no row chosen: ring around the list", bool(app.log.canvas.find_withtag("focusring")) and bound(app.log.canvas, "<FocusIn>"))
    shot(root, "dark_12b_list_focus_ring")
    app.log.set_focused(False)
    check("M4 list ring goes away", not app.log.canvas.find_withtag("focusring"))
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
    check("counters keep counting past 200", app.spoken + app.skipped == 313, str(app.spoken + app.skipped))

    # -- clear list -----------------------------------------------------------------------
    before = len(ctl.calls)
    check("Ctrl+L is bound on the main window, also with Caps Lock", bound(root, "<Control-l>") and bound(root, "<Control-L>"))
    was_reading = ctl.connected_channel
    app._channel_enter()
    check("Enter in the channel box does not stop reading", ctl.connected_channel == was_reading and "disconnect" not in ctl.calls)
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
    app.bot_entry.insert(0, "two words;")
    app._add_bot()
    pump(app)
    check("a bot name that cannot be a Twitch name is refused and stays in the box",
          cfg.extra_bots == ["lanternfox"] and app.bot_entry.get() == "two words;" and "Twitch name" in app.state_detail.cget("text"))
    app.note = ""
    app.bot_entry.delete(0, "end")
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
    check("failed shortcut line", app.licence_line.cget("text") == "Not working because another program uses them: Ctrl+Alt+F9")
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
    check("narrow: the word Volume is hidden", not app.vol_word.winfo_ismapped() and app.log.columns[3]["width"] == 220
          and app.log.columns[1]["width"] == 106)
    # must-fix 3: every reason a message was not read fits the "What happened" column at the smallest window
    from chatreader.controller import REASONS

    room = app.log.widths()[3] - k.px(14) - k.px(6) - k.px(22)
    reasons = list(REASONS.values()) + ["blocked by the safety filter", "could not be spoken: x", "could not be read (x)"]
    shown = [vm.row_view({"status": "skipped", "detail": d, "text": "x"}).result for d in reasons] + ["Read aloud"]
    cut = [t for t in shown if app.log.fit(t, k.body, room) != t]
    check("M3 minimum size: all %d results fit their column uncut" % len(shown), not cut, ", ".join(cut))
    visible_rows = (app.log.canvas.winfo_height() - app.log.hh) // app.log.rh
    check("minimum size shows at least 6 rows", visible_rows >= 6, str(visible_rows))
    shot(root, "dark_17_min_720x480")
    app.toggle_settings()
    pump(app, 0.15)
    check("minimum size: nothing clipped on Settings", not clipped(root), ", ".join(clipped(root)))
    # must-fix 3: the worst case for height: the shortcut warning line showing and several bots added
    cfg.extra_bots = ["helper_bot_one", "helper_bot_two", "helper_bot_three"]
    app._render_bots()
    app.failed_keys = ["Ctrl+Alt+F9", "Ctrl+Alt+F10", "Ctrl+Alt+F11", "Ctrl+Alt+F12"]
    app._show_failed_keys()
    pump(app, 0.15)
    check("the list of added bots starts at the first name and shows all three",
          app.bots.offset == 0 and not app.bots.pinned and len(app.bots.rows) == 3 and app.bots._max_offset() == 0)
    cfg.extra_bots = cfg.extra_bots + ["helper_bot_four", "helper_bot_five"]
    app._render_bots()
    pump(app, 0.15)
    check("five bots at the minimum size: the list scrolls and shows a scroll thumb", app.bots._max_offset() > 0 and bool(app.bots.bar.find_all()))
    cfg.extra_bots = cfg.extra_bots[:3]
    app._render_bots()
    pump(app, 0.15)
    chosen = app.colours.buttons["system"]
    low_edge = chosen.winfo_rooty() + chosen.winfo_height()
    check("M3 minimum size, warning line and 3 bots: nothing clipped on Settings", not clipped(root), ", ".join(clipped(root)))
    check("M3 minimum size, warning line and 3 bots: the theme choice is whole and above the footer",
          low_edge <= app.licence_line.winfo_rooty() and chosen.winfo_rootx() + chosen.winfo_width() <= root.winfo_rootx() + root.winfo_width(),
          f"{low_edge} vs {app.licence_line.winfo_rooty()}")
    check("footer lines are one line each", app.keys_line.winfo_height() <= k.px(22) and app.licence_line.winfo_height() <= k.px(22)
          and app.licence_line.cget("fg") == k.p["bad"])
    shot(root, "dark_18b_min_settings_warning_3_bots")
    cfg.extra_bots, app.failed_keys = [], []
    app._render_bots()
    app._show_failed_keys()
    pump(app)
    low = app.low_hint.winfo_rooty() + app.low_hint.winfo_height()
    check("minimum size: Settings content ends above the shortcuts line", low <= app.keys_line.winfo_rooty() + 1, f"{low} vs {app.keys_line.winfo_rooty()}")
    shot(root, "dark_18_min_settings")
    app.toggle_settings()
    fit = theme.fitted_size(k.px(920), k.px(600), (1024, 560))
    check("opens inside a 1024x600 screen (560 work area)", fit[0] + 16 <= 1024 and fit[1] + 39 <= 560, str(fit))
    root.geometry(f"{fit[0]}x{fit[1]}{POS}")
    pump(app, 0.15)
    check("1024x600 fit: nothing clipped", not clipped(root), ", ".join(clipped(root)))
    shot(root, "dark_19_fit_1024x600")
    at_920 = app.log.widths()
    size(app, 1000, BASE_H)        # the widest that fits this monitor; 1500 wide was checked before the monitor rule
    widths = app.log.widths()
    check("wider: only the Message column grows", widths[0] == k.px(104) and widths[1] == k.px(136) and widths[3] == k.px(250)
          and widths[2] - at_920[2] == k.px(80), f"{at_920} > {widths}")
    shot(root, "dark_20_wide_1000")
    size(app, 920, BASE_H)

    # -- blocked words window ---------------------------------------------------------------
    app.open_blocked_words()
    pump(app, 0.3)
    words = app._words_win
    # must-fix 5: it opens over the main window, on the same monitor
    wx, wy = words.win.winfo_rootx(), words.win.winfo_rooty()
    area = theme.monitor_work_area(root.winfo_rootx() + 50, root.winfo_rooty() + 50)
    check("M5 Blocked words opens over the main window, on its monitor",
          root.winfo_rootx() <= wx <= root.winfo_rootx() + root.winfo_width() and area[0] <= wx and wx + words.win.winfo_width() <= area[2]
          and area[1] <= wy and wy + words.win.winfo_height() <= area[3], f"at {wx},{wy}; main {root.winfo_rootx()},{root.winfo_rooty()}; area {area}")
    shot(words.win, "dark_21a_blocked_words_where_it_opens")
    words.win.geometry(f"{k.px(640)}x{k.px(500)}{POS}")
    pump(app, 0.2)
    # must-fix 7: the hate-group question has No as its default button
    from chatreader import wordlist

    asked = {}

    def fake_ask(title, message, **kw):
        asked.update(kw, message=message)
        return False

    real_ask, wordlist.messagebox.askyesno = wordlist.messagebox.askyesno, fake_ask
    words.group_checks[1].set(False)
    words._group_toggled(1, False)
    wordlist.messagebox.askyesno = real_ask
    check("M7 switching the hate group off asks, and No is the default button", asked.get("default") == "no" and "Switch it off?" in asked.get("message", ""), str(asked.get("default")))
    check("M7 answering No leaves the group on and the box ticked", cfg.block_hate and words.group_checks[1].get())
    got = tab_order(words.group_checks[1], 12)
    want = [words.group_checks[1], words.group_checks[2], words.group_checks[3], words.personal, words.find, words.group_pick,
            words.show, words.table.canvas, words.toggle_btn]
    check("Blocked words: Tab goes top to bottom", got[:9] == want and got[10] is words.new_word, " > ".join(str(w).rsplit(".", 1)[-1] for w in got))
    check("Blocked words: no \"0 words allowed\" line", not words.allowed_lbl.winfo_ismapped())
    cells = [r["cells"] for r in words.table.rows[:5]]
    check("blocked words: the table starts at its first row", words.table.offset == 0)
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
    words.table.selected = {1}
    words._selection_changed()
    first = words.toggle_btn.text()
    words.table.selected = {2}
    words._selection_changed()
    check("blocked words: the button says what it will do", (first, words.toggle_btn.text()) == ("Block selected", "Allow selected"), f"{first} / {words.toggle_btn.text()}")
    words.win.geometry(f"{k.px(560)}x{k.px(420)}{POS}")
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
    app.open_blocked_words()            # must-fix 6: left open while the colours change
    pump(app, 0.2)
    old_words = app._words_win
    app.colours.choose("light")
    pump(app, 0.4)
    now = app._words_win
    check("M6 an open Blocked words window changes colours with the main window",
          now is not None and now is not old_words and now.win.winfo_exists() and now.kit.p["window"] == theme.LIGHT["window"]
          and now.win.cget("bg") == theme.LIGHT["window"] and now.table.canvas.cget("bg") == theme.LIGHT["card"])
    if now is not None and now.win.winfo_exists():
        shot(now.win, "light_21a_blocked_words_after_colour_change")
        now.win.destroy()
    root.geometry(f"{k.px(920)}x{k.px(BASE_H)}{POS}")
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
    size(app, 920, BASE_H)
    app.open_blocked_words()
    pump(app, 0.2)
    app._words_win.win.geometry(f"{k.px(640)}x{k.px(500)}{POS}")
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
    try:
        sys.exit(main())
    except MouseInWindow:
        print("STOPPED: the mouse moved into the test window. Nothing was clicked or typed. Run again when it is clear.")
        os._exit(3)
