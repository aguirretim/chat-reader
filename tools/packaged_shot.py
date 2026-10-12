"""Start the packaged exe muted with scratch settings, save a picture of its window, close it.
Shows that the packaged build finds its colours, fonts and icon. Usage: packaged_shot.py <ChatReader.exe> <out.png> [seconds]
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

exe, out = sys.argv[1], Path(sys.argv[2])
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
foreground = user32.GetForegroundWindow()
env = dict(os.environ, CR_NULL_AUDIO="1", CR_NO_TRAY="1", CR_DATA_DIR=tempfile.mkdtemp(prefix="cr-packaged-shot-"))
for name in ("CR_START_MINIMIZED", "CR_AUTOCONNECT", "CR_AUTOTEST", "CR_TIMING_LOG"):
    env.pop(name, None)
proc = subprocess.Popen([exe], env=env)
found = []
EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def visit(hwnd, _):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    title = ctypes.create_unicode_buffer(200)
    user32.GetWindowTextW(hwnd, title, 200)
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    big = rect.right - rect.left > 300 and rect.bottom - rect.top > 200      # not the small helper windows Tk also titles
    if pid.value == proc.pid and user32.IsWindowVisible(hwnd) and title.value == "Chat Reader" and big:
        found.append(hwnd)
    return True


deadline = time.time() + 20
while not found and time.time() < deadline:
    time.sleep(0.3)
    user32.EnumWindows(EnumProc(visit), 0)
if foreground:
    user32.SetForegroundWindow(foreground)
time.sleep(wait)
ok = False
if found:
    from PIL import Image

    hwnd = found[0]
    rect = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(wt.HWND(hwnd))
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(wt.HWND(hwnd), mem, 2)

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD), ("a", wt.LONG), ("b", wt.LONG), ("c", wt.DWORD), ("d", wt.DWORD)]

    info = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(wt.HWND(hwnd), hdc)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").crop((7, 0, w - 7, h - 7)).save(out)
    user32.PostMessageW(wt.HWND(hwnd), 0x0010, 0, 0)      # WM_CLOSE: the app's own quit path
    ok = True
try:
    proc.wait(8)
except subprocess.TimeoutExpired:
    proc.kill()
print("saved" if ok else "window not found", out)
sys.exit(0 if ok else 1)
