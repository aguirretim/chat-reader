"""Entry point: python -m chatreader, or the packaged ChatReader.exe."""
from __future__ import annotations

import os
import sys
import traceback


def main() -> int:
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
