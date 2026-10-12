"""Entry point: python -m chatreader, or the packaged ChatReader.exe."""
from __future__ import annotations

import os
import sys
import traceback


def no_wmi() -> None:
    """Keep Python's platform module away from its WMI helper (_wmi.pyd).

    The installed app crashed once at start inside _wmi.pyd (access violation, 2026-10-11 23:23:42, a second or two
    after the sound library asked platform.system() on a background thread). Nothing here needs WMI: the Windows
    version comes from sys.getwindowsversion() and the processor type from the environment, which is what platform
    falls back to. The "ver" command it would also try is switched off too, so no console program is started.
    """
    try:
        import platform

        def refuse(*_a, **_k):
            raise OSError("not supported")

        platform._wmi_query = refuse
        platform._syscmd_ver = lambda system="", release="", version="", **_k: (system, release, version)
        platform.uname()            # worked out once, here on the main thread; later callers get the stored answer
    except Exception:
        pass


def main() -> int:
    no_wmi()
    from .config import APP_NAME, AppConfig, data_dir

    try:
        # Sharp text at 125% and 150%. Must happen before any window exists, including the error box below.
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    try:
        from .ui import App, mark

        mark("python_started")
        App(AppConfig.load()).run()
        mark("exited")
    except Exception:
        # A windowed program has no console: keep the reason where the user can find and send it.
        detail = traceback.format_exc()
        try:
            (data_dir() / "error.log").write_text(detail, "utf-8")
            import tkinter.messagebox as mb

            mb.showerror(APP_NAME, f"{APP_NAME} could not start.\n\nDetails were saved to:\n{data_dir() / 'error.log'}")
        except Exception:
            pass
        return 1
    return 0


def run_and_exit() -> None:
    code = main()
    try:
        # os._exit skips exit handlers, and one of them removes the temporary copy of espeak-ng.dll
        # that the phonemizer library makes on every start. Run them, then leave.
        import atexit

        atexit._run_exitfuncs()
    except Exception:
        pass
    os._exit(code)  # the tray thread must not keep the process alive


if __name__ == "__main__":
    run_and_exit()
