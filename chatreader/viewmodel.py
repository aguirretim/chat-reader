"""What the window shows, decided in plain functions with no window attached (so tests can call them).

Design source: notes\\ui_design_spec.md, sections 4 (state area), 5 (list) and 9 (words).
"""
from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes
from dataclasses import dataclass

# Segoe MDL2 Assets code points (spec 3.6)
ICON = {
    "settings": "", "play": "", "pause": "", "mute": "", "busy": "", "stop": "",
    "warn": "", "skip": "", "volume": "", "bin": "", "tick": "", "dash": "",
    "back": "", "down": "", "close": "", "add": "",
}
PAUSE_KEY = "Ctrl+Alt+F10"


# -- time ---------------------------------------------------------------------
class _SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wintypes.WORD) for n in ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds")]


def _systemtime(t: float) -> _SYSTEMTIME:
    lt = time.localtime(t)
    return _SYSTEMTIME(lt.tm_year, lt.tm_mon, (lt.tm_wday + 1) % 7, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec, 0)


def fallback_time(t: float) -> str:
    return time.strftime("%I:%M:%S %p", time.localtime(t)).lstrip("0")


def windows_time(t: float) -> str:
    """The row's clock time in the user's own Windows time format, with seconds ("9:20:27 PM" or "21:20:27")."""
    try:
        buf = ctypes.create_unicode_buffer(80)
        st = _systemtime(t)
        if ctypes.windll.kernel32.GetTimeFormatEx(None, 0, ctypes.byref(st), None, buf, len(buf)) and buf.value:
            return buf.value
    except Exception:
        pass
    return fallback_time(t)


def windows_date(t: float) -> str:
    """The long date in the user's Windows format ("Monday, October 12, 2026")."""
    try:
        buf = ctypes.create_unicode_buffer(120)
        st = _systemtime(t)
        if ctypes.windll.kernel32.GetDateFormatEx(None, 2, ctypes.byref(st), None, buf, len(buf), None) and buf.value:  # 2 = DATE_LONGDATE
            return buf.value
    except Exception:
        pass
    return time.strftime("%A, %B %d, %Y", time.localtime(t))


def date_changed(previous_t: float | None, t: float) -> bool:
    """True when a row falls on another local day than the row before it (a date line goes in between)."""
    if previous_t is None:
        return False
    a, b = time.localtime(previous_t), time.localtime(t)
    return (a.tm_year, a.tm_yday) != (b.tm_year, b.tm_yday)


# -- state area ---------------------------------------------------------------
@dataclass(frozen=True)
class State:
    kind: str        # voice_failed | getting_ready | problem | silenced | paused | not_reading | chat_lost | connecting | reading
    tone: str        # palette token for the bar and the icon: bad | info | warn | muted | ok
    icon: str
    word: str
    detail: str = ""
    tooltip: str = ""
    progress: float | None = None     # 0..1 while the voice files are being downloaded

    @property
    def paused_like(self) -> bool:
        return self.kind in ("silenced", "paused")


PROBLEM_STARTS = ("Sound problem", "Windows sound", "Twitch says:", "Twitch has no channel", "That does not look like")


def problem_for(status: str) -> tuple[str, str, str] | None:
    """(state word, detail line, tooltip) for a status text that is a problem, else None. Spec 4.1."""
    if status.startswith("Sound problem"):
        return "Sound problem", 'The voice could not play. Check "Play the voice on" in Settings.', status
    if status.startswith("Windows sound"):
        return "Sound problem", "Windows sound could not be started.", status
    if status.startswith("Twitch has no channel"):
        return "Channel not found", status, ""
    if status.startswith("That does not look like"):
        return "Check the channel name", "Use the name from your channel's address: twitch.tv/name.", ""
    if status.startswith("Twitch says:"):
        return "Message from Twitch", status[len("Twitch says:"):].strip(), ""
    return None


def _retry_detail(status: str) -> str:
    m = re.search(r"Trying again in (\d+) s", status)
    if not m:
        return "Trying again shortly."
    n = int(m.group(1))
    return f"Trying again in {n} second{'' if n == 1 else 's'}."


DOWNLOAD_PROBLEMS = {
    "offline": "Check your internet connection, then press Try again. What arrived so far is kept.",
    "damaged": "The download did not arrive intact. Press Try again.",
    "disk": "The voice could not be saved on this PC. Free some disk space, then press Try again.",
}


def megabytes(n: int) -> int:
    """Whole megabytes as Windows shows them (1 MB = 1,048,576 bytes)."""
    return round(n / 1048576)


def window_state(*, voice_error: str = "", ready: bool = False, start_pending: bool = False, status: str = "",
                 paused: bool = False, silenced: bool = False, channel: str = "", waiting: int = 0,
                 stopped_by_user: bool = False, note: str = "", download: tuple[int, int] | None = None,
                 download_error: str = "", channel_typed: bool = False) -> State:
    """The one state the window shows. First match wins (spec section 4). `note` replaces the detail line briefly."""
    if voice_error:
        return State("voice_failed", "bad", ICON["warn"], "The voice could not start", voice_error)
    if download_error and not ready:
        return State("voice_download_failed", "bad", ICON["warn"], "The voice could not be downloaded",
                     DOWNLOAD_PROBLEMS.get(download_error, DOWNLOAD_PROBLEMS["offline"]))
    if download and not ready and download[0] < download[1]:
        done, total = download
        return State("getting_ready", "info", ICON["busy"], f"Getting the voice, {megabytes(done)} of {megabytes(total)} MB",
                     "This happens once. The voice is then kept on this PC and works without the internet.",
                     progress=done / total if total else 0.0)
    if not ready:
        detail = ("Reading will start as soon as the voice is ready." if start_pending
                  else "This takes a few seconds each time the app starts.")
        return State("getting_ready", "info", ICON["busy"], "Getting the voice ready...", detail)
    problem = problem_for(status)
    if problem:
        return State("problem", "bad", ICON["warn"], problem[0], note or problem[1], problem[2])
    if paused and silenced:
        return State("silenced", "warn", ICON["mute"], "Silenced",
                     note or f"Waiting messages were dropped. Press Resume or {PAUSE_KEY} to read chat again.")
    if paused:
        carry_on = f"Press Resume or {PAUSE_KEY} to carry on."
        return State("paused", "warn", ICON["pause"], "Paused", note or (f"{channel} · {waiting} waiting. {carry_on}" if channel else carry_on))
    if not channel:
        detail = ("Stopped. Press Start reading to go again." if stopped_by_user
                  else "Press Start reading." if channel_typed
                  else "Type your Twitch channel name, then press Start reading.")
        return State("not_reading", "muted", ICON["stop"], "Not reading", note or detail)
    if "Trying again in" in status:
        return State("chat_lost", "warn", ICON["warn"], "Chat connection lost", note or _retry_detail(status), status)
    if status.startswith("Reading chat"):
        queue = f"{waiting} waiting to be read" if waiting else "nothing waiting"
        return State("reading", "ok", ICON["play"], "Reading chat", note or f"{channel} · {queue}")
    return State("connecting", "info", ICON["busy"], f"Connecting to {channel}...", note)


# -- list rows ----------------------------------------------------------------
# Short enough to fit the "What happened" column at every window size (the icon in front already says "not read").
REWORD = {
    "cleared by you": "skipped by you",
    "blocked by the safety filter": "blocked word",
    "from the other channel in a shared chat": "other shared channel",
    "writer was timed out or banned": "writer timed out or banned",
    "a ! command": "a command (starts with !)",
}
# the same reasons in full, for the tooltip
FULL_REASON = {
    "blocked by the safety filter": "a blocked word or a personal detail (phone number, e-mail, address)",
    "from the other channel in a shared chat": "it came from the other channel in a shared chat",
}


@dataclass(frozen=True)
class RowView:
    kind: str        # read | skip | fault
    icon: str
    message: str
    result: str
    tooltip: str


def row_view(entry: dict) -> RowView:
    """One log entry from the controller -> what the row shows. Spec 5.3."""
    text = entry.get("text") or ""
    if entry.get("status") == "spoken":
        return RowView("read", ICON["tick"], text, "Read aloud", f"{text}\nRead aloud")
    detail = entry.get("detail") or ""
    message = text or "(hidden)"
    if detail.startswith("could not be spoken"):
        return RowView("fault", ICON["warn"], message, "sound problem", f"{message}\nNot read: {detail}")
    if detail.startswith("could not be read"):
        return RowView("fault", ICON["warn"], message, "could not be read", f"{message}\nNot read: {detail}")
    return RowView("skip", ICON["dash"], message, REWORD.get(detail, detail), f"{message}\nNot read: {FULL_REASON.get(detail, detail)}")


def counters_text(read: int, not_read: int) -> str:
    return "" if read == 0 and not_read == 0 else f"{read:,} read aloud · {not_read:,} not read"


def jump_text(new_rows: int) -> str:
    return f"Jump to newest ({new_rows} new)" if new_rows else "Jump to newest"


def empty_state(*, cleared: bool, ready: bool, channel_typed: bool, reading: bool) -> tuple[str, str]:
    """(title, body) for a list with no rows. Spec 5.6."""
    if cleared:
        return "List cleared", "New messages will show up here. The voice was not interrupted."
    if not ready:
        return "Nothing here yet", "Chat messages will show up here, with a note saying if each one was read aloud."
    if reading:
        return "Waiting for chat", "Messages will show up here as people write."
    if not channel_typed:
        return "Three steps", ("1. Type your Twitch channel name at the top.\n2. Press Start reading.\n"
                               "3. Press Test voice in Settings if you cannot hear anything.")
    return "Nothing here yet", "Press Start reading. Chat messages will show up here."


# -- settings page --------------------------------------------------------------
def groups_on(cfg) -> str:
    n = sum(bool(v) for v in (cfg.block_hate, cfg.block_strong, cfg.block_mild_swearing, cfg.block_personal))
    return f"{n} of 4 groups on"


def speed_text(speed: float) -> str:
    return f"{speed:.1f}x" + ("  normal" if abs(speed - 1.0) < 0.025 else "")


def shortcuts_text(keys: dict) -> str:
    """One line, short enough for the narrowest window. The modifier is written once, so F10 is not read as plain F10."""
    def last(name: str) -> str:
        return keys[name].rsplit("+", 1)[-1]

    lead = keys["panic"].rsplit("+", 1)[0]
    return (f"Shortcuts, also in a game: {lead} + {last('panic')} Silence now \u00b7 {last('pause')} Pause or Resume "
            f"\u00b7 {last('skip')} Skip this one \u00b7 {last('clear')} Skip all")


def licence_text(version: str = "") -> str:
    """One short line for the Settings footer. The full notice is in LICENSE and the README."""
    from . import SOURCE_URL

    lead = f"Chat Reader {version} \u00b7 " if version else ""
    return f"{lead}Free software (GNU GPL version 3 or later) \u00b7 Source: {SOURCE_URL.split('://', 1)[-1]}"

def valid_bot_name(typed: str) -> str:
    """The login for a typed bot name, or "" when it cannot be a Twitch name (spaces, symbols, too long)."""
    import re as _re

    from .controller import bot_login

    login = bot_login(typed)
    return login if _re.fullmatch(r"[a-z0-9_]{1,25}", login) else ""


def word_action_label(picked: list[tuple[int, bool]]) -> str:
    """Button text for the selected rows of the Blocked words table. picked = [(group number, blocked now), ...]"""
    if picked and all(tier == 0 for tier, _ in picked):
        return "Remove selected"
    states = {blocked for tier, blocked in picked if tier != 0}
    if states == {True}:
        return "Allow selected"
    if states == {False}:
        return "Block selected"
    return "Allow or block selected"


def failed_shortcuts_text(failed: list[str]) -> str:
    return "Not working because another program uses them: " + ", ".join(failed) if failed else ""


# -- bots the streamer added ----------------------------------------------------
def is_added_bot(login: str, extra_bots: list[str]) -> bool:
    from .controller import bot_login

    wanted = bot_login(login)
    return bool(wanted) and any(bot_login(n) == wanted for n in extra_bots)


def bot_menu_label(name: str, login: str, extra_bots: list[str]) -> str:
    who = name or login
    if is_added_bot(login or name, extra_bots):
        return f"Read {who} again (remove from bots)"
    return f"Never read {who} (add to bots)"


def toggle_bot(login: str, extra_bots: list[str]) -> tuple[list[str], bool]:
    """Add the login to the list, or remove every spelling of it. Returns (new list, True when it was added)."""
    from .controller import bot_login

    wanted = bot_login(login)
    if not wanted:
        return list(extra_bots), False
    if is_added_bot(login, extra_bots):
        return [n for n in extra_bots if bot_login(n) != wanted], False
    return list(extra_bots) + [login.strip().lstrip("@")], True


# -- blocked words window --------------------------------------------------------
def word_status(tier: int, group_on: bool, word_off: bool) -> tuple[str, bool]:
    """(status text, True when the word is blocked right now)."""
    if tier == 0:
        return "Blocked", True
    if not group_on:
        return "Allowed (group is off)", False
    return ("Allowed", False) if word_off else ("Blocked", True)


def word_count_text(shown_limit: int, total: int) -> str:
    if total > shown_limit:
        return f"Showing the first {shown_limit:,} of {total:,}. Type in Find to see fewer."
    return f"{total:,} word{'' if total == 1 else 's'}"


def allowed_text(n: int) -> str:
    return "" if n == 0 else f"{n:,} word{'' if n == 1 else 's'} allowed one by one."
