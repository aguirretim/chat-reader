"""Global hotkeys through Win32 RegisterHotKey: Windows delivers them whatever window has focus.

No keyboard hook is installed and no other key is ever seen by this app.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
# name -> (virtual key, label)
DEFAULT_KEYS = {
    "pause": (0x79, "Ctrl+Alt+F10"),   # pause / resume
    "skip": (0x7A, "Ctrl+Alt+F11"),    # skip the message being spoken
    "clear": (0x7B, "Ctrl+Alt+F12"),   # stop and forget everything waiting
}


class Hotkeys(threading.Thread):
    def __init__(self, on_key: Callable[[str], None]):
        super().__init__(daemon=True, name="hotkeys")
        self.on_key = on_key
        self.failed: list[str] = []          # labels Windows refused (another program owns them)
        self.registered = threading.Event()
        self._thread_id = None

    def run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        ids = {}
        for i, (name, (vk, label)) in enumerate(DEFAULT_KEYS.items(), start=1):
            if user32.RegisterHotKey(None, i, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
                ids[i] = name
            else:
                self.failed.append(label)
        self.registered.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                try:
                    self.on_key(ids[msg.wParam])
                except Exception:
                    pass  # a failing handler must not end the hotkey thread
        for i in ids:
            user32.UnregisterHotKey(None, i)

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
