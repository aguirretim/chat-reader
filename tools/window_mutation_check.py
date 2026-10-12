"""Take one window fix out at a time and confirm the scripted window check (window_check.py) goes red for it.

Each run opens the test window for about 25 seconds on the second monitor, so the whole thing takes a few minutes.
Every file is restored, also when a run is stopped. A run that stops because the mouse moved into the window
(exit code 3) proves nothing and is reported as NOT RUN.

Usage: python tools\\window_mutation_check.py [name ...]     (from the app folder; names like M1 M4b)
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
W, U, L, V = "chatreader/widgets.py", "chatreader/ui.py", "chatreader/wordlist.py", "chatreader/viewmodel.py"
# name, what is taken out, [(file, text, replacement), ...], the start of the check that must fail
MUTATIONS = [
    ("M1", "the list no longer stays at the newest row when the window changes height", [
        (W, "        if self.pinned:\n            self.offset = self._max_offset()\n        self.render()", "        self.render()"),
        (W, "self.offset = self._max_offset() if self.pinned else max(0, min(self.offset, self._max_offset()))",
            "self.offset = max(0, min(self.offset, self._max_offset()))"),
        (W, "return self.pinned or self.offset >= self._max_offset() - 1", "return self.offset >= self._max_offset() - 1"),
    ], "M1 "),
    ("M2", "tooltips are no longer kept on the monitor of their control", [
        (W, "        x, y = theme.keep_inside(x, y, tip.winfo_width(), tip.winfo_height(), area)\n", ""),
    ], "M2 "),
    ("M3a", "the What happened column is 190 wide again in a narrow window", [
        (U, '= 220 if narrow else 250', '= 190 if narrow else 250'),
    ], "M3 minimum size: all"),
    ("M3b", "the reasons carry the Not read: prefix again", [
        (V, 'return RowView("skip", ICON["dash"], message, REWORD.get(detail, detail),',
            'return RowView("skip", ICON["dash"], message, "Not read: " + REWORD.get(detail, detail),'),
    ], "M3 minimum size: all"),
    ("M3c", "the shortcuts line in the Settings footer wraps onto more lines again", [
        (U, 'self.keys_line = tk.Label(card, text=vm.shortcuts_text(KEYS), font=k.tiny, bg=p["card"], fg=p["muted"], anchor="w")',
            'self.keys_line = tk.Label(card, text=vm.shortcuts_text(KEYS), font=k.tiny, bg=p["card"], fg=p["muted"], anchor="w", '
            'wraplength=k.px(330), justify="left")'),
    ], "M3 minimum size, warning line and 3 bots"),
    ("M4a", "no inner ring on a focused purple button", [
        (W, 'if ring and self.variant in ("primary", "chosen"):', "if False:"),
    ], "M4 focus on the purple"),
    ("M4b", "no ring around a focused tick box", [
        (W, 'self.configure(bg=p["accent"] if self.focused else self.under)', "self.configure(bg=self.under)"),
    ], "M4 focus on a ticked"),
    ("M4c", "no ring around the focused list", [
        (W, "        if self.focused:        # the list has the keyboard", "        if False:               # the list has the keyboard"),
    ], "M4 focus on the list"),
    ("M5", "Blocked words is not told where to open", [
        (L, 'self.win.geometry(f"{want[0]}x{want[1]}+{x}+{y}")', 'self.win.geometry(f"{want[0]}x{want[1]}")'),
    ], "M5 "),
    ("M6", "an open Blocked words window is left alone when the colours change", [
        (U, "            words_at = self._words_win.win.geometry()      # an open Blocked words window would keep the old colours\n"
            "            self._words_win.win.destroy()\n        self._words_win = None\n", "            pass\n"),
    ], "M6 "),
    ("M7", "the hate-group question has Yes as its default again", [
        (L, 'parent=self.win, default="no", icon="warning"', "parent=self.win"),
    ], "M7 switching"),
    ("M8", "the icon is handed over as a picture again, not as an .ico file", [
        (U, "        if path:\n            window.iconbitmap", "        if False:\n            window.iconbitmap"),
    ], "M8 the caption"),
]


def run() -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "window_check.py")], cwd=ROOT, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main() -> int:
    wanted = set(sys.argv[1:])
    bad = 0
    for name, what, edits, must_fail in MUTATIONS:
        if wanted and name not in wanted:
            continue
        originals = {rel: (ROOT / rel).read_text("utf-8") for rel, _, _ in edits}
        texts = dict(originals)
        missing = [old[:50] for rel, old, _ in edits if old not in texts[rel]]
        if missing:
            print(f"SETUP ERROR  {name}: text to take out not found: {missing}")
            bad += 1
            continue
        for rel, old, new in edits:
            texts[rel] = texts[rel].replace(old, new, 1)
        try:
            for rel, text in texts.items():
                (ROOT / rel).write_text(text, "utf-8", newline="\n")
            code, out = run()
        finally:
            for rel, text in originals.items():
                (ROOT / rel).write_text(text, "utf-8", newline="\n")
        failed = [line[5:].split("  [")[0] for line in out.splitlines() if line.startswith("FAIL ")]
        if code == 3:
            print(f"NOT RUN  {name}: the mouse was in the test window")
            bad += 1
        elif any(f.startswith(must_fail) for f in failed):
            others = [f for f in failed if not f.startswith(must_fail)]
            print(f"ok   {name}: {what} -> the check fails" + (f" (also red: {'; '.join(others)})" if others else ""))
        else:
            tail = out.strip().splitlines()[-1] if out.strip() else "no output"
            print(f"BAD  {name}: {what} -> the check for it STILL PASSES ({tail}; red: {failed})")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
