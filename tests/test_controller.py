"""The whole path from a raw chat line to "spoken", with a fake voice and a fake speaker."""
import queue
import threading
import time

import pytest

from chatreader.config import AppConfig
from chatreader.controller import Controller, friendly_reason
from chatreader.irc import IrcClient, parse_line, to_event


class FakeEngine:
    def __init__(self):
        self.said = []

    def load(self):
        pass

    def synth(self, text, voice, speed):
        self.said.append((text, voice, speed))
        return [0.0] * 10, 24000


class FakeIrc:
    def __init__(self, channel, on_event):
        self.channel, self.on_event, self.stopped = channel, on_event, False

    def start(self):
        pass

    def stop(self):
        self.stopped = True


def line(text, login="viewer1", name="Viewer1", mid="m1", extra=""):
    return f"@display-name={name};id={mid};room-id=1;user-id=5{extra} :{login}!{login}@{login}.tmi.twitch.tv PRIVMSG #streamer :{text}"


def event(text, **kw):
    return to_event(parse_line(line(text, **kw)))


@pytest.fixture
def ctl():
    played = []
    gate = threading.Event()
    gate.set()

    def play(samples, rate, device, volume, stop):
        gate.wait(5)
        played.append((device, volume))

    c = Controller(AppConfig(fetch_emote_lists=False), engine=FakeEngine(), play=play, irc_factory=FakeIrc)
    c.played, c.gate = played, gate
    c.start()
    assert c._voice_ready.wait(10)
    yield c
    c.shutdown()


def logs(c, want, timeout=5.0):
    """Collect log entries until `want` of them have arrived."""
    out, end = [], time.time() + timeout
    while len(out) < want and time.time() < end:
        try:
            kind, data = c.events.get(timeout=0.1)
        except queue.Empty:
            continue
        if kind == "log":
            out.append(data)
    return out


def test_message_is_cleaned_and_spoken(ctl):
    ctl.connect("Streamer")
    ctl.handle_message(event("hello everyone"))
    (entry,) = logs(ctl, 1)
    assert entry["status"] == "spoken"
    assert ctl._engine.said[-1][0].endswith("says hello everyone")
    assert ctl.played


def test_command_and_bot_are_not_read(ctl):
    ctl.connect("streamer")
    ctl.handle_message(event("!discord"))
    ctl.handle_message(event("subscribe now", login="nightbot", name="Nightbot", mid="m2"))
    entries = logs(ctl, 2)
    assert [e["status"] for e in entries] == ["skipped", "skipped"]
    assert entries[0]["detail"] == "a ! command" and entries[1]["detail"] == "a bot"
    assert ctl._engine.said == []


def test_names_can_be_turned_off(ctl):
    ctl.cfg.say_names = False
    ctl.apply_config()
    ctl.connect("streamer")
    ctl.handle_message(event("good game"))
    logs(ctl, 1)
    assert "says" not in ctl._engine.said[-1][0]


def test_bad_channel_name_is_refused(ctl):
    assert ctl.connect("not a channel") is False
    assert ctl.connected_channel == ""


def test_deleted_message_waiting_in_line_is_never_spoken(ctl):
    ctl.connect("streamer")
    ctl.gate.clear()                                   # hold the speaker on the first message
    ctl.handle_message(event("first message here", mid="a"))
    time.sleep(0.3)
    ctl.handle_message(event("second message here", mid="b", login="other", name="Other"))
    ctl._on_irc("delete_message", "b")
    ctl.gate.set()
    entries = logs(ctl, 2)
    by_status = {e["status"]: e for e in entries}
    assert by_status["skipped"]["detail"] == "deleted by a moderator"
    assert all("second" not in said[0] for said in ctl._engine.said)


def test_timed_out_user_is_removed_and_current_speech_stops(ctl):
    ctl.connect("streamer")
    stopped = []

    def play(samples, rate, device, volume, stop):
        stopped.append(stop.wait(5))

    ctl._play = play
    ctl.handle_message(event("i am about to be timed out", login="troll", name="Troll", mid="t1"))
    time.sleep(0.4)
    ctl.handle_message(event("another one from me", login="troll", name="Troll", mid="t2"))
    ctl._on_irc("clear_user", "troll")
    entries = logs(ctl, 2)
    assert stopped == [True]
    # the one being spoken is cut off AND logged as not read; the one waiting is never voiced
    assert [e["status"] for e in entries] == ["skipped", "skipped"]
    assert {e["detail"] for e in entries} == {"writer was timed out or banned"}
    assert len(ctl._engine.said) == 1


def test_pause_holds_messages_and_resume_reads_them(ctl):
    ctl.connect("streamer")
    ctl.set_paused(True)
    ctl.handle_message(event("held while paused"))
    time.sleep(0.5)
    assert ctl._engine.said == []
    ctl.set_paused(False)
    (entry,) = logs(ctl, 1)
    assert entry["status"] == "spoken"


def test_clear_empties_the_line(ctl):
    ctl.connect("streamer")
    ctl.set_paused(True)
    ctl.handle_message(event("one two three", mid="1"))
    ctl.handle_message(event("four five six", mid="2", login="b", name="B"))
    ctl.clear()
    ctl.set_paused(False)
    entries = logs(ctl, 2)
    assert {e["detail"] for e in entries} == {"cleared by you"}
    assert ctl._engine.said == []


def test_blocked_text_is_not_shown_in_the_log(ctl):
    class Blocker:
        def prepare(self, message, context):
            from chatreader.textclean.pipeline import Decision

            return Decision(False, "", "blocked:hate:exact", "tier1")

    ctl._cleaner = Blocker()
    ctl.connect("streamer")
    ctl.handle_message(event("something awful"))
    (entry,) = logs(ctl, 1)
    assert entry["text"] == "" and entry["detail"] == "blocked by the safety filter"


def test_a_failing_cleaner_does_not_stop_the_reader(ctl):
    class Broken:
        def prepare(self, message, context):
            raise ValueError("boom")

    good = ctl._cleaner
    ctl._cleaner = Broken()
    ctl.connect("streamer")
    ctl.handle_message(event("x"))
    ctl._cleaner = good
    ctl.handle_message(event("still working fine", mid="z"))
    entries = logs(ctl, 2)
    assert entries[0]["status"] == "skipped" and entries[1]["status"] == "spoken"


def test_sound_failure_is_reported_and_reader_continues(ctl):
    calls = []

    def play(samples, rate, device, volume, stop):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("device unplugged")

    ctl._play = play
    ctl.connect("streamer")
    ctl.handle_message(event("first try here", mid="1"))
    ctl.handle_message(event("second try here", mid="2", login="b", name="B"))
    entries = logs(ctl, 2, timeout=8)
    assert entries[0]["status"] == "skipped" and "device unplugged" in entries[0]["detail"]
    assert entries[1]["status"] == "spoken"


def test_disconnect_stops_the_chat_thread(ctl):
    ctl.connect("streamer")
    irc = ctl._irc
    ctl.disconnect()
    assert irc.stopped and ctl.connected_channel == ""


def test_friendly_reason():
    assert friendly_reason("blocked:hate:spelled") == "blocked by the safety filter"
    assert friendly_reason("personal:email") == "blocked by the safety filter"
    assert friendly_reason("queue_full") == "chat was too fast"
    assert friendly_reason("something_new") == "something_new"


def test_real_irc_client_feeds_the_controller(ctl):
    client = IrcClient("streamer", ctl._on_irc)
    ctl.connected_channel = "streamer"
    ctl.ctx.broadcaster_login = "streamer"
    client.handle_line(line("what a play that was"), lambda b: None)
    (entry,) = logs(ctl, 1)
    assert entry["status"] == "spoken" and entry["name"] == "Viewer1"


# -- added after the independent review (notes\app_review_v0.md) ---------------------------------

class SlowEngine(FakeEngine):
    """Voice generation that takes a moment, so a test can act while it is running."""

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def synth(self, text, voice, speed):
        self.started.set()
        self.release.wait(5)
        return super().synth(text, voice, speed)


def test_lines_from_a_stopped_chat_thread_are_ignored(ctl):
    ctl.connect("oldchan")
    old_callback = ctl._irc.on_event
    ctl.disconnect()
    ctl.connect("newchan")
    old_callback("message", event("late line from the old channel"))
    old_callback("status", "Not connected")
    ctl._irc.on_event("message", event("line from the new channel", mid="n1"))
    entries = logs(ctl, 1)
    assert len(entries) == 1 and entries[0]["text"] == "line from the new channel"
    assert all("old channel" not in said[0] for said in ctl._engine.said)
    statuses = []
    while not ctl.events.empty():
        kind, data = ctl.events.get_nowait()
        if kind == "status":
            statuses.append(data)
    assert "Not connected" not in statuses


def test_same_line_from_three_people_is_read_once(ctl):
    ctl.connect("streamer")
    ctl.set_paused(True)
    for i, who in enumerate(("ann", "bob", "cat")):
        ctl.handle_message(event("copy paste spam line", login=who, name=who.title(), mid=f"s{i}"))
    ctl.set_paused(False)
    entries = logs(ctl, 3)
    assert sorted(e["status"] for e in entries) == ["skipped", "skipped", "spoken"]
    assert len(ctl._engine.said) == 1


def test_pause_while_the_voice_is_being_made_keeps_the_message(ctl):
    ctl._engine = SlowEngine()
    ctl.connect("streamer")
    ctl.handle_message(event("do not lose this one"))
    assert ctl._engine.started.wait(5)
    ctl.set_paused(True)
    ctl._engine.release.set()
    time.sleep(0.5)
    assert ctl.played == [] and len(ctl.queue) == 1
    ctl.set_paused(False)
    (entry,) = logs(ctl, 1)
    assert entry["status"] == "spoken" and len(ctl.played) == 1


def test_pause_during_playback_reads_it_again_after_resume(ctl):
    plays = []

    def play(samples, rate, device, volume, stop):
        plays.append(stop.wait(1.0))     # True = cut short

    ctl._play = play
    ctl.connect("streamer")
    ctl.handle_message(event("interrupted by pause"))
    time.sleep(0.4)
    ctl.set_paused(True)
    time.sleep(0.4)
    ctl.set_paused(False)
    (entry,) = logs(ctl, 1, timeout=6)
    assert plays == [True, False]
    assert entry["status"] == "spoken"


def test_skip_during_playback_is_logged_as_not_read(ctl):
    ctl._play = lambda samples, rate, device, volume, stop: stop.wait(5)
    ctl.connect("streamer")
    ctl.handle_message(event("skip me please"))
    time.sleep(0.4)
    ctl.skip()
    (entry,) = logs(ctl, 1)
    assert entry["status"] == "skipped" and entry["detail"] == "skipped by you"


def test_delete_while_being_spoken_cuts_it_off(ctl):
    cut = []
    ctl._play = lambda samples, rate, device, volume, stop: cut.append(stop.wait(5))
    ctl.connect("streamer")
    ctl.handle_message(event("about to be deleted", mid="d1"))
    time.sleep(0.4)
    ctl._on_irc("delete_message", "d1")
    (entry,) = logs(ctl, 1)
    assert cut == [True] and entry["detail"] == "deleted by a moderator"


def test_delete_that_arrives_while_the_voice_is_being_made(ctl):
    ctl._engine = SlowEngine()
    ctl.connect("streamer")
    ctl.handle_message(event("deleted during generation", mid="g1"))
    assert ctl._engine.started.wait(5)
    ctl._on_irc("delete_message", "g1")      # not in the line any more, not yet playing
    ctl._engine.release.set()
    (entry,) = logs(ctl, 1)
    assert entry["status"] == "skipped" and ctl.played == []


def test_test_voice_can_be_pressed_again_straight_away(ctl):
    ctl.test_voice()
    ctl.test_voice()
    entries = logs(ctl, 2)
    assert [e["status"] for e in entries] == ["spoken", "spoken"]


def test_stop_reading_cuts_the_current_message(ctl):
    cut = []
    ctl._play = lambda samples, rate, device, volume, stop: cut.append(stop.wait(5))
    ctl.connect("streamer")
    ctl.handle_message(event("still talking when stopped"))
    time.sleep(0.4)
    ctl.disconnect()
    (entry,) = logs(ctl, 1)
    assert cut == [True] and entry["detail"] == "you stopped reading"

def test_guards_for_a_message_already_taken_from_the_line(ctl):
    """A delete, a time-out or a clear that lands after the message left the line but before it is marked current."""
    from chatreader.speechqueue import Item

    now = time.time()
    msg = Item(text="x", login="ann", message_id="id1", received=now)
    assert ctl._blocked_before_speaking(msg) == ""
    ctl._deleted_ids.append("id1")
    assert ctl._blocked_before_speaking(msg) == "deleted_by_moderator"
    ctl._deleted_ids.clear()
    ctl._removed_users["ann"] = now + 1
    assert ctl._blocked_before_speaking(msg) == "user_timed_out"
    assert ctl._blocked_before_speaking(Item(text="y", login="ann", received=now + 2)) == ""   # written after the time-out ended
    ctl._removed_users.clear()
    ctl._cleared_at = now + 1
    assert ctl._blocked_before_speaking(msg) == "cleared"


# -- added for the lead's design inputs -----------------------------------------------------------

def test_markdown_link_is_read_as_its_text_only(ctl):
    ctl.connect("streamer")
    ctl.handle_message(event("look at [this cool thing](https://example.com/x) now", mid="1"))
    ctl.handle_message(event("see [the docs](docs/readme.md) and [click here](javascript:alert) friends", mid="2", login="b", name="B"))
    logs(ctl, 2)
    first, second = ctl._engine.said[0][0], ctl._engine.said[1][0]
    # without the stripping the clean-up step leaves "( now", "(docs/readme.md)" and "(javascript:alert)" for the voice
    assert first.endswith("look at this cool thing now")
    assert "the docs" in second and "click here" in second
    assert "readme" not in second and "javascript" not in second and "(" not in first + second


def test_spoken_text_is_cut_at_the_character_cap(ctl):
    ctl.cfg.max_chars = 60
    ctl.connect("streamer")
    ctl.handle_message(event("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"))
    logs(ctl, 1)
    said = ctl._engine.said[-1][0]
    assert len(said) <= 60 and said == said.strip()
    assert said.split()[-1] in "one two three four five six seven eight nine ten eleven twelve".split()


def test_name_is_dropped_for_a_quick_follow_up_and_said_again_later(ctl):
    ctl.connect("streamer")
    ctl.handle_message(event("first thing i wanted to say", mid="1"))
    ctl.handle_message(event("second thing right after", mid="2"))
    logs(ctl, 2)
    assert "says" in ctl._engine.said[0][0] and "says" not in ctl._engine.said[1][0]
    ctl._last_speaker_at -= ctl.cfg.name_window_s + 1          # as if 31 s had passed
    ctl.handle_message(event("third thing much later", mid="3"))
    logs(ctl, 1)
    assert "says" in ctl._engine.said[2][0]


def test_panic_clears_and_stays_silent_until_resume(ctl):
    cut = []
    ctl._play = lambda samples, rate, device, volume, stop: cut.append(stop.wait(5))
    ctl.connect("streamer")
    ctl.handle_message(event("being spoken right now", mid="1"))
    time.sleep(0.4)
    ctl.handle_message(event("waiting in the line", mid="2", login="b", name="B"))
    ctl.panic()
    entries = logs(ctl, 2)
    assert cut == [True] and ctl.paused and len(ctl.queue) == 0
    assert {e["detail"] for e in entries} == {"cleared by you"}
    ctl.handle_message(event("arrives while silenced", mid="3", login="c", name="C"))
    time.sleep(0.5)
    assert len(ctl._engine.said) == 1 and len(ctl.queue) == 1      # held, not spoken


def test_emote_names_match_whole_tokens_and_exact_case(ctl):
    ctl.connect("streamer")
    ctl.ctx.third_party_emotes = frozenset({"Clap", "OMEGALUL"})
    ctl.handle_message(event("please clap for the Clapper Clap OMEGALUL"))
    logs(ctl, 1)
    said = ctl._engine.said[-1][0].lower()
    assert "please clap for the clapper" in said and "omegalul" not in said and not said.rstrip().endswith("clap")

def test_a_name_added_to_the_bot_list_is_not_read(ctl):
    ctl.cfg.extra_bots = ["@Helper_Bot9 ", "https://twitch.tv/OtherBot", ""]
    ctl.apply_config()
    ctl.connect("streamer")
    ctl.handle_message(event("thanks for the follow", login="helper_bot9", name="Helper_Bot9", mid="b1"))
    ctl.handle_message(event("new video is out", login="otherbot", name="OtherBot", mid="b2"))
    ctl.handle_message(event("hello there", login="viewer7", name="Viewer7", mid="b3"))
    entries = logs(ctl, 3)
    assert [e["status"] for e in entries] == ["skipped", "skipped", "spoken"]
    assert entries[0]["detail"] == "a bot" and entries[1]["detail"] == "a bot"


def test_added_bots_are_read_when_skip_bots_is_off(ctl):
    ctl.cfg.extra_bots = ["helper_bot9"]
    ctl.cfg.skip_bots = False
    ctl.apply_config()
    ctl.connect("streamer")
    ctl.handle_message(event("thanks for the follow", login="helper_bot9", name="Helper_Bot9", mid="b4"))
    (entry,) = logs(ctl, 1)
    assert entry["status"] == "spoken"


def test_log_events_carry_the_login_for_the_window(ctl):
    ctl.connect("streamer")
    ctl.handle_message(event("hello there friends", login="pixelmoth", name="PixelMoth", mid="L1"))
    ctl.handle_message(event("!drops", login="lanternfox", name="LanternFox", mid="L2"))
    entries = logs(ctl, 2)
    assert {(e["login"], e["status"]) for e in entries} == {("pixelmoth", "spoken"), ("lanternfox", "skipped")}