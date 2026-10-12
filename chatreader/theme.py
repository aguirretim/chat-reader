"""Colours, fonts and Windows window chrome. Colour table: notes\\ui_design_spec.md section 3.1."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

DARK = {
    "window": "#14161B", "card": "#1C1F26", "raised": "#262A33", "hover": "#2F3440", "selected": "#33305A",
    "divider": "#3A404D", "border": "#7C8496", "text": "#ECEEF2", "muted": "#A7AEBB", "disabled": "#7A8190",
    "accent": "#A990FF", "on_accent": "#15121F", "ok": "#5FD38D", "warn": "#F2B84B", "bad": "#FF7A7A",
    "info": "#73B7FF", "danger": "#C83A3A", "on_danger": "#FFFFFF",
}
LIGHT = {
    "window": "#F2F3F6", "card": "#FFFFFF", "raised": "#E8EAEF", "hover": "#DDE0E7", "selected": "#ECE8FF",
    "divider": "#D3D7DF", "border": "#737A88", "text": "#1A1C21", "muted": "#545B69", "disabled": "#7D8492",
    "accent": "#5A3FD0", "on_accent": "#FFFFFF", "ok": "#18794A", "warn": "#8A5700", "bad": "#C02B2B",
    "info": "#1A62B8", "danger": "#C02B2B", "on_danger": "#FFFFFF",
}
THEMES = ("dark", "light", "system")
ICON_FONT = "Segoe MDL2 Assets"
TEXT_FONT = "Segoe UI"


def mix(colour: str, toward: str, share: float) -> str:
    a = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(toward[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * share):02X}" for x, y in zip(a, b))


def windows_uses_light_apps() -> bool:
    """The Windows setting "Choose your default app mode". True (light) when it cannot be read."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return True


def resolve(setting: str, system_light=None) -> str:
    """The setting ("dark", "light", "system", or anything else from an old file) -> "dark" or "light"."""
    if setting == "light":
        return "light"
    if setting == "system":
        light = windows_uses_light_apps() if system_light is None else system_light
        return "light" if light else "dark"
    return "dark"


def palette(mode: str) -> dict:
    return dict(LIGHT if mode == "light" else DARK)


def set_titlebar(window, dark: bool) -> None:
    """Dark or light caption on Windows 10/11. Only takes effect while the window is withdrawn (measured)."""
    try:
        window.update_idletasks()
        user32 = ctypes.windll.user32
        user32.GetParent.restype = wt.HWND
        user32.GetParent.argtypes = [wt.HWND]
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        val = ctypes.c_int(1 if dark else 0)
        if ctypes.windll.dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), 20, ctypes.byref(val), 4) != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), 19, ctypes.byref(val), 4)
    except Exception:
        pass  # an older Windows keeps its standard caption


def work_area() -> tuple[int, int]:
    """Width and height of the primary monitor without the taskbar."""
    rect = wt.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):   # SPI_GETWORKAREA
        return rect.right - rect.left, rect.bottom - rect.top
    return 1024, 560


def fitted_size(want_w: int, want_h: int, area: tuple[int, int], frame=(16, 39), margin: int = 16) -> tuple[int, int]:
    """The inside size to open with: the wanted size, shrunk so the whole window fits the work area minus a margin."""
    return (max(200, min(want_w, area[0] - margin - frame[0])), max(200, min(want_h, area[1] - margin - frame[1])))


def style_ttk(style, p: dict, font_body, scale: float) -> None:
    """Colour the few stock ttk widgets the window still uses: text box, drop-down, scroll bar."""
    pad = round(6 * scale)
    for name in ("TEntry", "TCombobox"):
        style.configure(name, fieldbackground=p["raised"], background=p["raised"], foreground=p["text"],
                        bordercolor=p["border"], lightcolor=p["raised"], darkcolor=p["raised"], insertcolor=p["text"],
                        selectbackground=p["accent"], selectforeground=p["on_accent"], arrowcolor=p["text"],
                        padding=(pad, round(4 * scale)), relief="flat", font=font_body)
        style.map(name,
                  fieldbackground=[("disabled", p["raised"]), ("readonly", p["raised"])],
                  foreground=[("disabled", p["disabled"])],
                  bordercolor=[("focus", p["accent"]), ("disabled", p["divider"])],
                  lightcolor=[("focus", p["accent"])], darkcolor=[("focus", p["accent"])],
                  background=[("readonly", p["raised"]), ("active", p["hover"])],
                  arrowcolor=[("disabled", p["disabled"])],
                  selectbackground=[("readonly", p["raised"])], selectforeground=[("readonly", p["text"])])
    style.configure("Vertical.TScrollbar", background=p["divider"], troughcolor=p["card"], bordercolor=p["card"],
                    lightcolor=p["divider"], darkcolor=p["divider"], arrowcolor=p["muted"], relief="flat", arrowsize=round(12 * scale))
    style.map("Vertical.TScrollbar", background=[("active", p["border"]), ("pressed", p["border"])])
