"""What the window shows, tested without a window. Design source: notes\\ui_design_spec.md."""
import re
import time

import pytest

from chatreader import theme
from chatreader import viewmodel as vm
from chatreader.config import AppConfig

NIGHT = time.mktime((2026, 10, 11, 21, 20, 27, 0, 0, -1))
AFTER_MIDNIGHT = time.mktime((2026, 10, 12, 0, 0, 5, 0, 0, -1))


# -- time ---------------------------------------------------------------------
def test_time_is_in_the_windows_format_with_seconds():
    shown = vm.windows_time(NIGHT)
    assert shown in ("9:20:27 PM", "21:20:27") or re.fullmatch(r"\d{1,2}.20.27.*", shown), shown
    assert shown.count("20") >= 1 and "27" in shown


def test_fallback_time_is_12_hour_without_a_leading_zero():
    assert vm.fallback_time(NIGHT) == "9:20:27 PM"
    assert vm.fallback_time(AFTER_MIDNIGHT) == "12:00:05 AM"


def test_windows_time_falls_back_when_the_windows_call_fails(monkeypatch):
    class Broken:
        def __getattr__(self, name):
            raise OSError("no such function")

    monkeypatch.setattr(vm.ctypes, "windll", Broken(), raising=False)
    assert vm.windows_time(NIGHT) == "9:20:27 PM"
    assert "2026" in vm.windows_date(NIGHT)


def test_long_date_names_the_day():
    shown = vm.windows_date(AFTER_MIDNIGHT)
    assert "2026" in shown and "12" in shown


def test_date_line_only_when_the_day_changes():
    assert vm.date_changed(None, NIGHT) is False
    assert vm.date_changed(NIGHT, NIGHT + 60) is False
    assert vm.date_changed(NIGHT, AFTER_MIDNIGHT) is True
    assert vm.date_changed(AFTER_MIDNIGHT - 6, AFTER_MIDNIGHT) is True


# -- state area ---------------------------------------------------------------
READY = dict(ready=True)


def state(**kw):
    return vm.window_state(**kw)


def test_getting_ready_comes_before_everything_but_a_voice_failure():
    s = state(ready=False, status="Reading chat in x", paused=True, channel="x")
    assert (s.kind, s.tone, s.word) == ("getting_ready", "info", "Getting the voice ready...")
    assert state(ready=False, start_pending=True).detail == "Reading will start as soon as the voice is ready."
    failed = state(voice_error="A voice file is missing.", ready=False)
    assert (failed.kind, failed.tone, failed.word, failed.detail) == ("voice_failed", "bad", "The voice could not start", "A voice file is missing.")


def test_not_reading_and_stopped():
    s = state(**READY)
    assert (s.kind, s.tone, s.word) == ("not_reading", "muted", "Not reading")
    assert s.detail == "Type your Twitch channel name, then press Start reading."
    assert state(**READY, stopped_by_user=True).detail == "Stopped. Press Start reading to go again."


def test_connecting_then_reading_with_the_waiting_count():
    assert state(**READY, channel="demo", status="Connecting to demo...").word == "Connecting to demo..."
    assert state(**READY, channel="demo", status="").kind == "connecting"
    r = state(**READY, channel="demo", status="Reading chat in demo", waiting=2)
    assert (r.kind, r.tone, r.word, r.detail) == ("reading", "ok", "Reading chat", "demo · 2 waiting to be read")
    assert state(**READY, channel="demo", status="Reading chat in demo").detail == "demo · nothing waiting"


def test_paused_never_says_reading_chat():
    s = state(**READY, channel="demo", status="Reading chat in demo", paused=True, waiting=4)
    assert (s.kind, s.tone, s.word) == ("paused", "warn", "Paused")
    assert s.detail == "demo · 4 waiting. Press Resume or Ctrl+Alt+F10 to carry on."
    assert state(**READY, paused=True).detail == "Press Resume or Ctrl+Alt+F10 to carry on."
    assert s.paused_like


def test_silenced_wins_over_paused_and_needs_the_pause_flag():
    s = state(**READY, channel="demo", status="Reading chat in demo", paused=True, silenced=True)
    assert (s.kind, s.word) == ("silenced", "Silenced") and "Waiting messages were dropped" in s.detail
    assert state(**READY, channel="demo", status="Reading chat in demo", paused=False, silenced=True).kind == "reading"


def test_chat_lost_counts_seconds_in_words():
    s = state(**READY, channel="demo", status="Chat connection lost (closed). Trying again in 5 s.")
    assert (s.kind, s.tone, s.word, s.detail) == ("chat_lost", "warn", "Chat connection lost", "Trying again in 5 seconds.")
    assert state(**READY, channel="demo", status="Cannot reach Twitch. Trying again in 1 s.").detail == "Trying again in 1 second."


@pytest.mark.parametrize("status, word, detail_has, tooltip_has", [
    ("Sound problem: Error opening OutputStream", "Sound problem", "Play the voice on", "OutputStream"),
    ("Windows sound could not be started: no device", "Sound problem", "Windows sound could not be started.", "no device"),
    ("Twitch has no channel called demo. Check the spelling.", "Channel not found", "Check the spelling", ""),
    ("That does not look like a Twitch channel name.", "Check the channel name", "twitch.tv/name", ""),
    ("Twitch says: Login unsuccessful", "Message from Twitch", "Login unsuccessful", ""),
])
def test_problem_texts(status, word, detail_has, tooltip_has):
    s = state(**READY, channel="demo", status=status)
    assert (s.kind, s.tone, s.word) == ("problem", "bad", word)
    assert detail_has in s.detail and tooltip_has in s.tooltip
    assert "OutputStream" not in s.detail          # the raw error never reaches the detail line


def test_problem_outranks_paused_and_a_note_replaces_the_detail():
    assert state(**READY, channel="demo", status="Sound problem: x", paused=True).kind == "problem"
    assert state(**READY, channel="demo", status="Reading chat in demo", note="lanternfox added to bots. Undo in Settings.").detail \
        == "lanternfox added to bots. Undo in Settings."


def test_ordinary_status_is_not_a_problem():
    assert vm.problem_for("Reading chat in demo") is None and vm.problem_for("") is None


# -- rows -----------------------------------------------------------------------
def row(**kw):
    return vm.row_view({"t": NIGHT, "name": "PixelMoth", "login": "pixelmoth", "text": "hello", "status": "skipped", "detail": "", **kw})


def test_read_row():
    r = row(status="spoken", detail="PixelMoth says hello")
    assert (r.kind, r.icon, r.message, r.result) == ("read", vm.ICON["tick"], "hello", "Read aloud")


@pytest.mark.parametrize("detail, shown", [
    ("a bot", "a bot"),
    ("cleared by you", "skipped by you"),
    ("skipped by you", "skipped by you"),
    ("blocked by the safety filter", "blocked word"),
    ("from the other channel in a shared chat", "other shared channel"),
    ("chat was too fast", "chat was too fast"),
])
def test_not_read_reasons(detail, shown):
    r = row(detail=detail)
    assert (r.kind, r.icon, r.result) == ("skip", vm.ICON["dash"], shown)


def test_blocked_row_never_shows_text():
    r = row(text="", detail="blocked by the safety filter")
    assert r.message == "(hidden)" and r.tooltip.startswith("(hidden)")


def test_fault_rows_keep_the_raw_error_for_the_tooltip_only():
    r = row(detail="could not be spoken: Error opening OutputStream: Invalid device")
    assert (r.kind, r.icon, r.result) == ("fault", vm.ICON["warn"], "sound problem")
    assert "Invalid device" in r.tooltip
    assert row(detail="could not be read (ValueError)").result == "could not be read"


def test_counters_and_jump_text():
    assert vm.counters_text(0, 0) == ""
    assert vm.counters_text(5, 6) == "5 read aloud · 6 not read"
    assert vm.counters_text(1200, 0) == "1,200 read aloud · 0 not read"
    assert vm.jump_text(3) == "Jump to newest (3 new)"


def test_empty_states():
    assert vm.empty_state(cleared=True, ready=True, channel_typed=True, reading=True)[0] == "List cleared"
    assert vm.empty_state(cleared=False, ready=False, channel_typed=False, reading=False)[0] == "Nothing here yet"
    assert vm.empty_state(cleared=False, ready=True, channel_typed=False, reading=False)[0] == "Three steps"
    assert vm.empty_state(cleared=False, ready=True, channel_typed=True, reading=False)[1].startswith("Press Start reading")
    assert vm.empty_state(cleared=False, ready=True, channel_typed=True, reading=True)[0] == "Waiting for chat"


# -- settings page ----------------------------------------------------------------
def test_groups_on_counts_the_four_switches():
    assert vm.groups_on(AppConfig()) == "3 of 4 groups on"
    assert vm.groups_on(AppConfig(block_hate=False, block_strong=False, block_personal=False)) == "0 of 4 groups on"


def test_speed_and_shortcut_lines():
    assert vm.speed_text(1.0) == "1.0x  normal"
    assert vm.speed_text(0.7) == "0.7x" and vm.speed_text(1.5) == "1.5x" and vm.speed_text(1.05) == "1.1x"
    keys = {"panic": "Ctrl+Alt+F9", "pause": "Ctrl+Alt+F10", "skip": "Ctrl+Alt+F11", "clear": "Ctrl+Alt+F12"}
    line = vm.shortcuts_text(keys)
    assert line == "Shortcuts, also in a game: Ctrl+Alt + F9 Silence now · F10 Pause or Resume · F11 Skip this one · F12 Skip all"
    assert vm.failed_shortcuts_text([]) == ""
    assert vm.failed_shortcuts_text(["Ctrl+Alt+F9"]) == "Not working because another program uses them: Ctrl+Alt+F9"


# -- bots you added ---------------------------------------------------------------
def test_bot_menu_and_toggle_compare_without_case_or_at_sign():
    assert vm.bot_menu_label("LanternFox", "lanternfox", []) == "Never read LanternFox (add to bots)"
    assert vm.bot_menu_label("LanternFox", "lanternfox", ["@LanternFox "]) == "Read LanternFox again (remove from bots)"
    names, added = vm.toggle_bot("lanternfox", ["other_bot"])
    assert (names, added) == (["other_bot", "lanternfox"], True)
    names, added = vm.toggle_bot("LANTERNFOX", ["other_bot", "@LanternFox", "lanternfox"])
    assert (names, added) == (["other_bot"], False)
    assert vm.toggle_bot("  ", ["a"]) == (["a"], False)


# -- blocked words window -----------------------------------------------------------
def test_word_status_and_count_lines():
    assert vm.word_status(1, True, False) == ("Blocked", True)
    assert vm.word_status(1, True, True) == ("Allowed", False)
    assert vm.word_status(2, False, False) == ("Allowed (group is off)", False)
    assert vm.word_status(0, False, True) == ("Blocked", True)          # your own words ignore the groups
    assert vm.word_count_text(600, 2894) == "Showing the first 600 of 2,894. Type in Find to see fewer."
    assert vm.word_count_text(600, 12) == "12 words" and vm.word_count_text(600, 1) == "1 word"
    assert vm.allowed_text(3) == "3 words allowed one by one." and vm.allowed_text(1) == "1 word allowed one by one."
    assert vm.allowed_text(0) == ""          # nothing to say, so no line
    assert vm.word_action_label([]) == "Allow or block selected"
    assert vm.word_action_label([(1, True), (2, True)]) == "Allow selected"
    assert vm.word_action_label([(1, False)]) == "Block selected"
    assert vm.word_action_label([(1, True), (2, False)]) == "Allow or block selected"
    assert vm.word_action_label([(0, True)]) == "Remove selected"


# -- theme --------------------------------------------------------------------------
def test_theme_setting_resolves_to_dark_or_light():
    assert theme.resolve("dark") == "dark" and theme.resolve("light") == "light"
    assert theme.resolve("system", system_light=True) == "light" and theme.resolve("system", system_light=False) == "dark"
    assert theme.resolve("purple") == "dark" and theme.resolve("") == "dark"      # an old or odd settings file


def test_both_palettes_have_every_colour():
    assert set(theme.DARK) == set(theme.LIGHT)
    assert all(re.fullmatch(r"#[0-9A-F]{6}", v) for v in list(theme.DARK.values()) + list(theme.LIGHT.values()))


def _contrast(a: str, b: str) -> float:
    def lum(c):
        vals = [int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, bl = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in vals]
        return 0.2126 * r + 0.7152 * g + 0.0722 * bl

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("pal", [theme.DARK, theme.LIGHT])
def test_text_contrast_meets_4_5_to_1(pal):
    for fg, bg in (("text", "window"), ("text", "card"), ("text", "raised"), ("text", "selected"), ("muted", "card"),
                   ("muted", "window"), ("muted", "selected"), ("on_accent", "accent"), ("on_danger", "danger")):
        assert _contrast(pal[fg], pal[bg]) >= 4.5, (fg, bg, round(_contrast(pal[fg], pal[bg]), 2))
    for fg, bg in (("ok", "card"), ("warn", "card"), ("bad", "card"), ("info", "card"), ("border", "card"), ("accent", "card")):
        assert _contrast(pal[fg], pal[bg]) >= 3.0, (fg, bg)


def test_window_opens_inside_a_small_screen():
    assert theme.fitted_size(920, 600, (1024, 560)) == (920, 505)
    assert theme.fitted_size(920, 600, (3440, 1400)) == (920, 600)
    w, h = theme.fitted_size(640, 520, (1024, 560))
    assert w + 16 <= 1024 and h + 39 + 16 <= 560 + 16


def test_colour_mixing():
    assert theme.mix("#000000", "#FFFFFF", 0.5) == "#808080"
    assert theme.mix("#A990FF", "#FFFFFF", 0.0) == "#A990FF"


def test_theme_setting_survives_a_save_and_a_bad_value_loads_as_dark(tmp_path, monkeypatch):
    import json

    from chatreader import config

    monkeypatch.setenv("CR_DATA_DIR", str(tmp_path))
    AppConfig(theme="light", extra_bots=["helper_bot9"]).save()
    back = AppConfig.load()
    assert (back.theme, back.extra_bots) == ("light", ["helper_bot9"])
    (config.data_dir() / "settings.json").write_text(json.dumps({"theme": "neon", "channel": "demo"}), "utf-8")
    assert AppConfig.load().theme == "dark"
    (config.data_dir() / "settings.json").write_text(json.dumps({"channel": "demo"}), "utf-8")   # a file from before this setting
    assert AppConfig.load().theme == "dark" and AppConfig.load().channel == "demo"


def test_download_states():
    s = vm.window_state(ready=False, download=(45_000_000, 120_575_669))
    assert (s.kind, s.tone, s.word) == ("getting_ready", "info", "Getting the voice, 43 of 115 MB")
    assert abs(s.progress - 0.3732) < 0.001
    failed = vm.window_state(ready=False, download=(45_000_000, 120_575_669), download_error="offline")
    assert (failed.kind, failed.tone, failed.word) == ("voice_download_failed", "bad", "The voice could not be downloaded")
    assert "internet connection" in failed.detail and failed.progress is None
    assert "did not arrive intact" in vm.window_state(ready=False, download_error="damaged").detail
    assert "disk space" in vm.window_state(ready=False, download_error="disk").detail
    done = vm.window_state(ready=False, download=(120_575_669, 120_575_669))
    assert done.word == "Getting the voice ready..." and done.progress is None
    assert vm.window_state(ready=True, download=(1, 2), download_error="offline").kind == "not_reading"   # once ready, old download news is ignored


def test_licence_line():
    assert vm.licence_text() == "Free software (GNU GPL version 3 or later) · Source: github.com/aguirretim/chat-reader"
    assert vm.licence_text("0.1.0").startswith("Chat Reader 0.1.0 · Free software")

# -- wording added after the UI critique ----------------------------------------------
def test_every_skip_reason_is_short_and_the_tooltip_keeps_the_full_one():
    from chatreader.controller import REASONS

    for detail in list(REASONS.values()) + ["blocked by the safety filter"]:
        r = row(status="skipped", detail=detail)
        assert not r.result.startswith("Not read") and len(r.result) <= 27, r.result
        assert "\nNot read: " in r.tooltip
    assert row(status="skipped", detail="a ! command").result == "a command (starts with !)"
    assert row(status="skipped", detail="writer was timed out or banned").result == "writer timed out or banned"
    assert "personal detail" in row(status="skipped", detail="blocked by the safety filter").tooltip


def test_not_reading_detail_follows_the_channel_box():
    kw = dict(voice_error="", ready=True, start_pending=False, status="", paused=False, silenced=False, channel="", waiting=0)
    assert vm.window_state(**kw).detail == "Type your Twitch channel name, then press Start reading."
    assert vm.window_state(**kw, channel_typed=True).detail == "Press Start reading."
    assert vm.window_state(**kw, channel_typed=True, stopped_by_user=True).detail.startswith("Stopped.")


def test_bot_names_must_look_like_twitch_names():
    assert vm.valid_bot_name("@Helper_Bot9 ") == "helper_bot9"
    for bad in ("two words", "semi;colon", "x" * 26, "", "@"):
        assert vm.valid_bot_name(bad) == "", bad