"""The window's own controls, drawn with plain Tk so every colour, size and state follows the design spec.

Stock ttk (through ttkbootstrap) is kept for the text box, the drop-down and the scroll bar. Everything a
streamer looks at while playing (buttons, the list, the slider, tick boxes) is drawn here.
All sizes are given at 100% and multiplied by the display scale.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from typing import Callable

from . import theme


class Kit:
    """Palette, scale and fonts shared by every control in one window."""

    def __init__(self, root: tk.Misc, palette: dict):
        self.root = root
        self.p = palette
        self.S = max(1.0, root.winfo_fpixels("1i") / 96.0)
        f = lambda px, family=theme.TEXT_FONT: tkfont.Font(root=root, family=family, size=-round(px * self.S))  # noqa: E731
        semibold = "Segoe UI Semibold"
        self.body = f(14)
        self.body_b = f(14, semibold)
        self.small = f(13)
        self.tiny = f(12)
        self.tiny_b = f(12, semibold)
        self.state = f(20, semibold)
        self.title = f(16, semibold)
        self.icon = f(16, theme.ICON_FONT)
        self.icon_small = f(13, theme.ICON_FONT)
        self.icon_big = f(22, theme.ICON_FONT)

    def px(self, n: float) -> int:
        return round(n * self.S)


# -- tooltip ----------------------------------------------------------------------
class Tooltip:
    def __init__(self, kit: Kit, widget: tk.Misc, text: str | Callable[[], str], delay_ms: int = 500):
        self.kit, self.widget, self.text, self.delay = kit, widget, text, delay_ms
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _e=None):
        self.hide()
        self._after = self.widget.after(self.delay, self.show)

    def show(self, at: tuple[int, int] | None = None, text: str | None = None):
        text = text if text is not None else (self.text() if callable(self.text) else self.text)
        if not text or self._tip is not None:
            return
        k = self.kit
        x, y = at or (self.widget.winfo_pointerx() + k.px(12), self.widget.winfo_pointery() + k.px(18))
        self._tip = tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.configure(bg=k.p["border"])
        tk.Label(tip, text=text, justify="left", bg=k.p["raised"], fg=k.p["text"], font=k.small,
                 padx=k.px(8), pady=k.px(5), wraplength=k.px(460)).pack(padx=1, pady=1)
        tip.update_idletasks()
        x = max(0, min(x, tip.winfo_screenwidth() - tip.winfo_width() - 4))
        tip.wm_geometry(f"+{x}+{y}")

    def hide(self, _e=None):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


# -- button -----------------------------------------------------------------------
class Button(tk.Frame):
    """A button with an optional icon and an optional second line. Variants: plain, primary, quiet, danger, chosen."""

    def __init__(self, parent: tk.Misc, kit: Kit, text: str, command: Callable[[], None], variant: str = "plain",
                 icon: str = "", caption: str = "", icon_tone: str = "", min_width: int = 0, height: int = 32,
                 bold: bool = False, tooltip: str = "", side_pad: int = 12):
        self.kit = kit
        self.under = parent.cget("bg")
        super().__init__(parent, bg=self.under, padx=kit.px(2), pady=kit.px(2), takefocus=1, highlightthickness=0)
        self.command, self.variant, self.icon_tone = command, variant, icon_tone
        self.enabled, self.hovering, self.pressed, self.focused = True, False, False, False
        self.face = tk.Frame(self, highlightthickness=max(1, kit.px(1)), bd=0)
        self.face.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.face, bd=0)
        self.inner.pack(expand=True, padx=kit.px(side_pad), pady=kit.px(3 if caption else 0))
        self.icon_lbl = tk.Label(self.inner, text=icon, font=kit.icon, bd=0, padx=0, pady=0)
        self.strong = bool(bold or caption)
        self.text_lbl = tk.Label(self.inner, text=text, bd=0, padx=0, pady=0, anchor="w",
                                 font=kit.body_b if (self.strong or variant in ("primary", "chosen")) else kit.body)
        self.cap_lbl = tk.Label(self.inner, text=caption, font=kit.tiny, bd=0, padx=0, pady=0, anchor="w")
        col = 0
        if icon:
            self.icon_lbl.grid(row=0, column=0, rowspan=1, padx=(0, kit.px(8)), sticky="w")
            col = 1
        self.text_lbl.grid(row=0, column=col, sticky="w")
        if caption:
            self.cap_lbl.grid(row=1, column=0, columnspan=col + 1, sticky="w")
        # fixed height; width is the content's own width or the caller's minimum, whichever is larger
        self.inner.update_idletasks()
        natural = self.inner.winfo_reqwidth() + kit.px(2 * side_pad + 2)
        self.face.configure(height=kit.px(48 if caption else height), width=max(natural, kit.px(min_width)))
        self.face.pack_propagate(False)
        for w in (self, self.face, self.inner, self.icon_lbl, self.text_lbl, self.cap_lbl):
            w.bind("<Enter>", self._enter, add="+")
            w.bind("<Leave>", self._leave, add="+")
            w.bind("<ButtonPress-1>", self._press, add="+")
            w.bind("<ButtonRelease-1>", self._release, add="+")
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        self.bind("<Return>", self._key)
        self.bind("<space>", self._key)
        if tooltip:
            self.tooltip = Tooltip(kit, self, tooltip)
        self._paint()

    # -- public ----------------------------------------------------------------
    def set(self, text: str | None = None, icon: str | None = None, variant: str | None = None,
            icon_tone: str | None = None, enabled: bool | None = None, caption: str | None = None) -> None:
        if text is not None:
            self.text_lbl.configure(text=text)
        if icon is not None:
            self.icon_lbl.configure(text=icon)
        if caption is not None:
            self.cap_lbl.configure(text=caption)
        if variant is not None:
            self.variant = variant
        if icon_tone is not None:
            self.icon_tone = icon_tone
        if enabled is not None:
            self.enabled = enabled
            self.configure(takefocus=1 if enabled else 0)
        self._paint()

    def text(self) -> str:
        return self.text_lbl.cget("text")

    def invoke(self) -> None:
        if self.enabled:
            self.command()

    # -- events ------------------------------------------------------------------
    def _enter(self, _e):
        self.hovering = True
        self._paint()

    def _leave(self, e):
        x, y = self.winfo_pointerxy()
        inside = self.winfo_containing(x, y)
        self.hovering = inside is not None and str(inside).startswith(str(self))
        if not self.hovering:
            self.pressed = False
        self._paint()

    def _press(self, _e):
        if self.enabled:
            self.pressed = True
            self.focus_set()
            self._paint()

    def _release(self, _e):
        fire = self.pressed and self.hovering and self.enabled
        self.pressed = False
        self._paint()
        if fire:
            self.command()

    def _focus(self, on: bool):
        self.focused = on
        self._paint()

    def _key(self, _e):
        self.invoke()
        return "break"

    # -- drawing -----------------------------------------------------------------
    def colours(self) -> tuple[str, str, str, str, str]:
        """(fill, border, text, caption, icon) for the current variant and state."""
        p = self.kit.p
        v = self.variant
        if not self.enabled:
            fill = self.under if v == "quiet" else p["raised"]
            return fill, (fill if v == "quiet" else p["divider"]), p["disabled"], p["disabled"], p["disabled"]
        if v in ("primary", "chosen"):
            fill = p["accent"]
            fill = theme.mix(fill, "#000000", 0.10) if self.pressed else theme.mix(fill, "#FFFFFF", 0.10) if self.hovering else fill
            return fill, fill, p["on_accent"], p["on_accent"], p["on_accent"]
        if v == "danger":
            fill = p["danger"]
            fill = theme.mix(fill, "#000000", 0.10) if self.pressed else theme.mix(fill, "#FFFFFF", 0.10) if self.hovering else fill
            return fill, fill, p["on_danger"], p["on_danger"], p["on_danger"]
        rest = self.under if v == "quiet" else p["raised"]
        fill = p["selected"] if self.pressed else p["hover"] if self.hovering else rest
        border = fill if v == "quiet" else p["border"]
        return fill, border, p["text"], p["muted"], p[self.icon_tone] if self.icon_tone else p["text"]

    def _paint(self) -> None:
        fill, border, text, caption, icon = self.colours()
        self.configure(bg=self.kit.p["accent"] if (self.focused and self.enabled) else self.under)
        self.face.configure(bg=fill, highlightbackground=border, highlightcolor=border)
        self.inner.configure(bg=fill)
        self.icon_lbl.configure(bg=fill, fg=icon)
        emphasised = self.strong or self.variant in ("primary", "chosen")
        self.text_lbl.configure(bg=fill, fg=text, font=self.kit.body_b if emphasised else self.kit.body)
        self.cap_lbl.configure(bg=fill, fg=caption)


# -- tick box ---------------------------------------------------------------------
class Check(tk.Frame):
    def __init__(self, parent: tk.Misc, kit: Kit, text: str, value: bool, command: Callable[[bool], None], tooltip: str = ""):
        self.kit, self.command, self.value = kit, command, bool(value)
        self.under = parent.cget("bg")
        super().__init__(parent, bg=self.under, takefocus=1, padx=kit.px(2), pady=kit.px(2))
        self.hovering = self.focused = False
        size = kit.px(16)
        self.box = tk.Canvas(self, width=size, height=size, bd=0, highlightthickness=0, bg=self.under)
        self.box.pack(side="left")
        self.label = tk.Label(self, text=text, font=kit.body, bg=self.under, fg=kit.p["text"], padx=kit.px(8), pady=0, bd=0)
        self.label.pack(side="left")
        for w in (self, self.box, self.label):
            w.bind("<ButtonRelease-1>", self._toggle, add="+")
            w.bind("<Enter>", lambda e: self._hover(True), add="+")
            w.bind("<Leave>", lambda e: self._hover(False), add="+")
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        if tooltip:
            Tooltip(kit, self, tooltip)
        self._paint()

    def get(self) -> bool:
        return self.value

    def set(self, value: bool) -> None:
        self.value = bool(value)
        self._paint()

    def _toggle(self, _e=None):
        self.focus_set()
        self.value = not self.value
        self._paint()
        self.command(self.value)
        return "break"

    def _hover(self, on):
        self.hovering = on
        self._paint()

    def _focus(self, on):
        self.focused = on
        self._paint()

    def _paint(self):
        p, k = self.kit.p, self.kit
        s = k.px(16)
        self.box.delete("all")
        fill = p["accent"] if self.value else (p["hover"] if self.hovering else p["raised"])
        edge = p["accent"] if (self.value or self.focused) else p["border"]
        self.box.create_rectangle(0, 0, s - 1, s - 1, fill=fill, outline=edge, width=2 if self.focused else 1)
        if self.value:
            self.box.create_text(s // 2, s // 2, text="", font=k.icon_small, fill=p["on_accent"])


# -- slider -----------------------------------------------------------------------
class Slider(tk.Canvas):
    """Horizontal slider: 4 px track, accent fill up to a round 16 px thumb. Left and Right move one step."""

    def __init__(self, parent: tk.Misc, kit: Kit, low: float, high: float, step: float, value: float, width: int,
                 on_change: Callable[[float], None], on_release: Callable[[float], None] | None = None):
        self.kit = kit
        self.low, self.high, self.step = low, high, step
        self.on_change, self.on_release = on_change, on_release or (lambda v: None)
        self.w, self.h = kit.px(width), kit.px(26)
        super().__init__(parent, width=self.w, height=self.h, bd=0, highlightthickness=0, bg=parent.cget("bg"), takefocus=1)
        self.value = min(high, max(low, value))
        self.hovering = self.focused = False
        self.bind("<ButtonPress-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda e: self.on_release(self.value))
        self.bind("<Left>", lambda e: self._nudge(-1))
        self.bind("<Right>", lambda e: self._nudge(1))
        self.bind("<Enter>", lambda e: self._flag("hovering", True))
        self.bind("<Leave>", lambda e: self._flag("hovering", False))
        self.bind("<FocusIn>", lambda e: self._flag("focused", True))
        self.bind("<FocusOut>", lambda e: self._flag("focused", False))
        self._paint()

    def set_width(self, width: int) -> None:
        self.w = self.kit.px(width)
        self.configure(width=self.w)
        self._paint()

    def _flag(self, name, on):
        setattr(self, name, on)
        self._paint()

    def _snap(self, v: float) -> float:
        v = min(self.high, max(self.low, v))
        return round(round((v - self.low) / self.step) * self.step + self.low, 4)

    def _drag(self, e):
        self.focus_set()
        r = self.kit.px(9)
        v = self._snap(self.low + (self.high - self.low) * (e.x - r) / max(1, self.w - 2 * r))
        if v != self.value:
            self.value = v
            self._paint()
            self.on_change(v)

    def _nudge(self, direction: int):
        v = self._snap(self.value + direction * self.step)
        if v != self.value:
            self.value = v
            self._paint()
            self.on_change(v)
            self.on_release(v)
        return "break"

    def _paint(self):
        p, k = self.kit.p, self.kit
        self.delete("all")
        r = k.px(9 if self.hovering else 8)
        pad = k.px(9)
        mid = self.h // 2
        x = pad + (self.w - 2 * pad) * (self.value - self.low) / (self.high - self.low)
        t = max(2, k.px(4))
        self.create_line(pad, mid, self.w - pad, mid, fill=p["border"], width=t, capstyle="round")
        self.create_line(pad, mid, x, mid, fill=p["accent"], width=t, capstyle="round")
        if self.focused:
            self.create_oval(x - r - 3, mid - r - 3, x + r + 3, mid + r + 3, outline=p["accent"], width=2)
        self.create_oval(x - r, mid - r, x + r, mid + r, fill=p["accent"], outline=p["accent"])


# -- three-way choice -------------------------------------------------------------
class Segmented(tk.Frame):
    def __init__(self, parent: tk.Misc, kit: Kit, choices: list[tuple[str, str]], value: str, command: Callable[[str], None]):
        super().__init__(parent, bg=parent.cget("bg"))
        self.command, self.value, self.buttons = command, value, {}
        for key, label in choices:
            b = Button(self, kit, label, lambda k=key: self.choose(k), variant="chosen" if key == value else "plain", side_pad=8)
            b.pack(side="left")
            b.configure(padx=0)
            self.buttons[key] = b

    def choose(self, key: str) -> None:
        if key == self.value:
            return
        self.value = key
        for k, b in self.buttons.items():
            b.set(variant="chosen" if k == key else "plain")
        self.command(key)


# -- scroll bar ---------------------------------------------------------------------
class ScrollBar(tk.Canvas):
    """A slim scroll bar in the window's own colours. Hidden (blank) when everything fits."""

    def __init__(self, parent: tk.Misc, kit: Kit, on_move: Callable[[float], None]):
        self.kit, self.on_move = kit, on_move
        super().__init__(parent, width=kit.px(12), bd=0, highlightthickness=0, bg=kit.p["card"])
        self.lo, self.hi, self.hovering, self._grab = 0.0, 1.0, False, None
        self.bind("<Configure>", lambda e: self._paint())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_grab", None))
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))

    def set(self, lo: float, hi: float) -> None:
        if (lo, hi) != (self.lo, self.hi):
            self.lo, self.hi = lo, hi
            self._paint()

    def _hover(self, on):
        self.hovering = on
        self._paint()

    def _press(self, e):
        h = max(1, self.winfo_height())
        top, bottom = self.lo * h, self.hi * h
        if not (top <= e.y <= bottom):          # a click on the track jumps there
            self.on_move(max(0.0, e.y / h - (self.hi - self.lo) / 2))
            top = self.lo * h
        self._grab = e.y - top

    def _drag(self, e):
        if self._grab is not None:
            self.on_move(max(0.0, (e.y - self._grab) / max(1, self.winfo_height())))

    def _paint(self):
        self.delete("all")
        if self.hi - self.lo >= 0.999:
            return
        p, k, h, w = self.kit.p, self.kit, self.winfo_height(), self.winfo_width()
        top, bottom = self.lo * h, max(self.hi * h, self.lo * h + k.px(24))
        inset = k.px(3)
        self.create_rectangle(inset, top + 2, w - inset, bottom - 2, outline="",
                              fill=p["border"] if (self.hovering or self._grab is not None) else p["divider"])


# -- list -------------------------------------------------------------------------
class ListView(tk.Frame):
    """A table drawn on a canvas. Only the rows on screen are drawn, so 200 rows cost the same as 20.

    columns: list of dicts {key, title, width (None = takes the rest), min}
    rows:    dicts {cells: {key: text}, tone: "text"|"muted", icon: (glyph, palette token) or None, kind: "row"|"date", data}
    """

    def __init__(self, parent: tk.Misc, kit: Kit, columns: list[dict], icon_column: str = "", multi: bool = False,
                 row_height: int = 30, header_height: int = 28):
        self.kit = kit
        p = kit.p
        super().__init__(parent, bg=p["card"])
        self.columns, self.icon_column, self.multi = columns, icon_column, multi
        self.rh, self.hh = kit.px(row_height), kit.px(header_height)
        self.rows: list[dict] = []
        self.offset = 0
        self.selected: set[int] = set()
        self.anchor = None
        self.hover = None
        self.empty = ("", "")
        self.header_tips: dict[str, str] = {}
        self.on_activate: Callable[[int], None] = lambda i: None
        self.on_context: Callable[[int, int, int], None] = lambda i, x, y: None
        self.on_scroll: Callable[[], None] = lambda: None
        self.row_tip: Callable[[int], str] = lambda i: ""
        self._fit_cache: dict = {}
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0, bg=p["card"], takefocus=1)
        self.bar = ScrollBar(self, kit, lambda share: self._scroll_to(share * len(self.rows) * self.rh))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        c = self.canvas
        c.bind("<Configure>", lambda e: self.render())
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Motion>", self._motion)
        c.bind("<Leave>", self._leave)
        c.bind("<ButtonPress-1>", self._click)
        c.bind("<Double-Button-1>", lambda e: self._activate_at(e))
        c.bind("<ButtonPress-3>", self._context_click)
        c.bind("<App>", self._context_key)
        c.bind("<Shift-F10>", self._context_key)
        c.bind("<space>", lambda e: self._activate_selected())
        c.bind("<FocusIn>", lambda e: self.render())
        c.bind("<FocusOut>", lambda e: self.render())
        for key, step in (("<Up>", -1), ("<Down>", 1)):
            c.bind(key, lambda e, s=step: self._move(s))
        c.bind("<Prior>", lambda e: self._move(-self._page()))
        c.bind("<Next>", lambda e: self._move(self._page()))
        c.bind("<Home>", lambda e: self._move(-len(self.rows)))
        c.bind("<End>", lambda e: self._move(len(self.rows)))
        self._tip = Tooltip(kit, c, "")
        c.unbind("<Enter>")
        self._tip_after = None

    # -- data --------------------------------------------------------------------
    def set_rows(self, rows: list[dict], keep_offset: bool = False) -> None:
        self.rows = rows
        self.selected = {i for i in self.selected if i < len(rows)}
        if not keep_offset:
            self.offset = 0
        self.render()

    def add_row(self, row: dict, follow: bool) -> None:
        self.rows.append(row)
        if follow:
            self.offset = self._max_offset()
        self.render()

    def drop_first(self, n: int) -> None:
        if n <= 0:
            return
        del self.rows[:n]
        self.selected = {i - n for i in self.selected if i >= n}
        self.offset = max(0, self.offset - n * self.rh)

    def clear(self) -> None:
        self.rows, self.selected, self.offset, self.hover = [], set(), 0, None
        self.render()

    def at_bottom(self) -> bool:
        return self.offset >= self._max_offset() - 1

    def to_bottom(self) -> None:
        self.offset = self._max_offset()
        self.render()

    def selection(self) -> list[int]:
        return sorted(self.selected)

    # -- geometry ----------------------------------------------------------------
    def _view_height(self) -> int:
        return max(0, self.canvas.winfo_height() - self.hh)

    def _max_offset(self) -> int:
        return max(0, len(self.rows) * self.rh - self._view_height())

    def _page(self) -> int:
        return max(1, self._view_height() // self.rh - 1)

    def widths(self) -> list[int]:
        k, total = self.kit, max(1, self.canvas.winfo_width())
        fixed = sum(k.px(c["width"]) for c in self.columns if c.get("width"))
        return [k.px(c["width"]) if c.get("width") else max(k.px(c.get("min", 80)), total - fixed) for c in self.columns]

    def _index_at(self, y: int):
        if y < self.hh:
            return None
        i = (y - self.hh + self.offset) // self.rh
        return i if 0 <= i < len(self.rows) else None

    def fit(self, text: str, font, width: int) -> str:
        """The text, cut with an ellipsis so it fits the width."""
        key = (text, id(font), width)
        got = self._fit_cache.get(key)
        if got is not None:
            return got
        out = text
        if font.measure(text) > width:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.measure(text[:mid].rstrip() + "…") <= width:
                    lo = mid
                else:
                    hi = mid - 1
            out = text[:lo].rstrip() + "…" if lo else ""
        if len(self._fit_cache) > 4000:
            self._fit_cache.clear()
        self._fit_cache[key] = out
        return out

    # -- drawing -----------------------------------------------------------------
    def render(self) -> None:
        c, k, p = self.canvas, self.kit, self.kit.p
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 4 or h < 4:
            return
        self.offset = max(0, min(self.offset, self._max_offset()))
        widths, pad = self.widths(), k.px(14)
        xs = [sum(widths[:i]) for i in range(len(widths))]
        if not self.rows and self.empty[0]:
            mid = self.hh + (h - self.hh) // 2
            c.create_text(w // 2, mid - k.px(14), text=self.empty[0], font=k.title, fill=p["text"], anchor="s")
            c.create_text(w // 2, mid - k.px(6), text=self.empty[1], font=k.body, fill=p["muted"], anchor="n",
                          justify="center", width=max(100, w - k.px(80)))
        first = self.offset // self.rh
        y = self.hh - (self.offset % self.rh)
        focused = c.focus_get() is c
        for i in range(first, len(self.rows)):
            if y >= h:
                break
            row = self.rows[i]
            if row.get("kind") == "date":
                c.create_line(pad, y, w - pad, y, fill=p["divider"])
                c.create_line(pad, y + self.rh - 1, w - pad, y + self.rh - 1, fill=p["divider"])
                c.create_text(w // 2, y + self.rh // 2, text=row["cells"].get("date", ""), font=k.tiny, fill=p["muted"])
            else:
                if i in self.selected:
                    c.create_rectangle(0, y, w, y + self.rh, fill=p["selected"], outline="")
                    if focused and i == self.anchor:
                        c.create_rectangle(1, y + 1, w - 2, y + self.rh - 2, outline=p["accent"])
                elif i == self.hover:
                    c.create_rectangle(0, y, w, y + self.rh, fill=p["hover"], outline="")
                colour = p[row.get("tone", "text")]
                for col, x, cw in zip(self.columns, xs, widths):
                    tx, avail = x + pad, cw - pad - k.px(6)
                    if col["key"] == self.icon_column and row.get("icon"):
                        glyph, tone = row["icon"]
                        c.create_text(tx, y + self.rh // 2 + 1, text=glyph, font=k.icon_small, fill=p[tone], anchor="w")
                        tx += k.px(22)
                        avail -= k.px(22)
                    text = self.fit(row["cells"].get(col["key"], ""), k.body, max(10, avail))
                    c.create_text(tx, y + self.rh // 2, text=text, font=k.body, fill=p[col["tone"]] if col.get("tone") else colour, anchor="w")
            y += self.rh
        # header on top of the rows
        c.create_rectangle(0, 0, w, self.hh, fill=p["card"], outline="")
        c.create_line(0, 0, w, 0, fill=p["divider"])
        c.create_line(0, self.hh - 1, w, self.hh - 1, fill=p["divider"])
        for col, x in zip(self.columns, xs):
            c.create_text(x + pad, self.hh // 2, text=col["title"], font=k.tiny_b, fill=p["muted"], anchor="w")
        total = len(self.rows) * self.rh
        view = self._view_height()
        if total <= view or view <= 0:
            self.bar.set(0, 1)
        else:
            self.bar.set(self.offset / total, (self.offset + view) / total)

    # -- scrolling ---------------------------------------------------------------
    def _scroll_to(self, offset: int) -> None:
        offset = max(0, min(int(offset), self._max_offset()))
        if offset != self.offset:
            self.offset = offset
            self.render()
            self.on_scroll()

    def _wheel(self, e):
        self._scroll_to(self.offset - (e.delta // 120) * 3 * self.rh)
        return "break"

    def _show(self, i: int) -> None:
        top = i * self.rh
        if top < self.offset:
            self.offset = top
        elif top + self.rh > self.offset + self._view_height():
            self.offset = top + self.rh - self._view_height()

    # -- pointer and keys ----------------------------------------------------------
    def _motion(self, e):
        i = self._index_at(e.y)
        if i is not None and self.rows[i].get("kind") == "date":
            i = None
        if i != self.hover:
            self.hover = i
            self.render()
            self._hide_tip()
            if i is not None:
                self._tip_after = self.canvas.after(500, lambda: self._show_tip(i))
        if i is None and e.y < self.hh:
            self._header_tip(e)

    def _header_tip(self, e):
        xs, x0 = self.widths(), 0
        for col, cw in zip(self.columns, xs):
            if x0 <= e.x < x0 + cw and col["key"] in self.header_tips and self._tip_after is None:
                tip = self.header_tips[col["key"]]
                self._tip_after = self.canvas.after(500, lambda: self._tip.show(text=tip))
            x0 += cw

    def _show_tip(self, i):
        if i == self.hover and i is not None and i < len(self.rows):
            self._tip.show(text=self.row_tip(i))

    def _hide_tip(self):
        if self._tip_after is not None:
            self.canvas.after_cancel(self._tip_after)
            self._tip_after = None
        self._tip.hide()

    def _leave(self, _e):
        self._hide_tip()
        if self.hover is not None:
            self.hover = None
            self.render()

    def _select_only(self, i):
        self.selected = set() if i is None else {i}
        self.anchor = i

    def _click(self, e):
        self.canvas.focus_set()
        i = self._index_at(e.y)
        if i is None or self.rows[i].get("kind") == "date":
            return
        if self.multi and (e.state & 0x0004):      # Ctrl
            self.selected ^= {i}
            self.anchor = i
        elif self.multi and (e.state & 0x0001) and self.anchor is not None:   # Shift
            lo, hi = sorted((self.anchor, i))
            self.selected = {n for n in range(lo, hi + 1) if self.rows[n].get("kind") != "date"}
        else:
            self._select_only(i)
        self.render()

    def _activate_at(self, e):
        i = self._index_at(e.y)
        if i is not None and self.rows[i].get("kind") != "date":
            self.on_activate(i)

    def _activate_selected(self):
        if self.selected:
            self.on_activate(min(self.selected))
        return "break"

    def _context_click(self, e):
        self.canvas.focus_set()
        i = self._index_at(e.y)
        if i is None or self.rows[i].get("kind") == "date":
            return
        if i not in self.selected:
            self._select_only(i)
            self.render()
        self.on_context(i, e.x_root, e.y_root)

    def _context_key(self, _e):
        if self.anchor is None or self.anchor >= len(self.rows):
            return "break"
        y = self.hh + self.anchor * self.rh - self.offset + self.rh // 2
        self.on_context(self.anchor, self.canvas.winfo_rootx() + self.kit.px(140), self.canvas.winfo_rooty() + y)
        return "break"

    def _move(self, step: int):
        if not self.rows:
            return "break"
        i = (self.anchor if self.anchor is not None else (len(self.rows) - 1 if step < 0 else 0) - step) + step
        i = max(0, min(len(self.rows) - 1, i))
        direction = 1 if step > 0 else -1
        while 0 <= i < len(self.rows) and self.rows[i].get("kind") == "date":
            i += direction
        if not (0 <= i < len(self.rows)):
            return "break"
        self._select_only(i)
        self._show(i)
        self.render()
        self.on_scroll()
        return "break"
