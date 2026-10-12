"""Everything between "a chat line arrived" and "sound came out", with no window attached.

The window (ui.py) only calls methods here and reads `events`. Tests drive this class with a
fake voice, a fake speaker and hand-made chat lines: no network, no sound.
"""
from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Callable

from . import audio, emotes
from .config import AppConfig, models_dir
from .irc import IrcClient, normalise_channel
from .speechqueue import Item, SpeechQueue
from .textclean.pipeline import Cleaner, Context, Settings

# why a message was not read, in words a streamer understands
REASONS = {
    "bot": "a bot", "command": "a ! command", "emote_only": "only emotes", "empty": "empty", "link_only": "only a link",
    "ascii_art": "picture made of symbols", "emoji_only": "only emoji", "no_words": "no words", "non_latin": "not in Latin letters",
    "nothing_left": "nothing left to say", "own_message": "your own message", "ignored_user": "ignored user",
    "shared_chat": "from the other channel in a shared chat", "repeat": "same as a recent message",
    "queue_full": "chat was too fast", "too_old": "waited too long", "deleted_by_moderator": "deleted by a moderator",
    "user_timed_out": "writer was timed out or banned", "cleared": "cleared by you", "skipped": "skipped by you",
    "stopped": "you stopped reading",
}


def friendly_reason(reason: str) -> str:
    if reason.startswith(("blocked", "personal")):
        return "blocked by the safety filter"
    return REASONS.get(reason, reason)


class Controller:
    def __init__(self, cfg: AppConfig, engine=None, play: Callable | None = None, cleaner: Cleaner | None = None,
                 irc_factory: Callable | None = None, emote_fetcher: Callable | None = None):
        self.cfg = cfg
        self.events: queue.Queue = queue.Queue()
        self._engine = engine
        self._play = play or audio.play
        self._cleaner = cleaner
        self._irc_factory = irc_factory or IrcClient
        self._emote_fetcher = emote_fetcher or emotes.fetch
        self.ctx = Context(settings=Settings())
        self.queue = SpeechQueue(on_drop=self._on_drop)
        self.paused = False
        self.connected_channel = ""
        self._irc = None
        self._session = 0                       # goes up on every connect / disconnect; older chat threads are ignored
        self._stop_current = threading.Event()
        self._stop_reason = ""                  # why _stop_current was set: skipped | paused | cleared | deleted... | stopped
        self._shutdown = threading.Event()
        self._current: Item | None = None
        self._lock = threading.Lock()
        self._voice_ready = threading.Event()
        # A delete or a clear can arrive in the instant between "taken from the line" and "now speaking";
        # these records close that gap.
        self._deleted_ids: deque[str] = deque(maxlen=500)
        self._removed_users: dict[str, float] = {}
        self._cleared_at = 0.0
        self.apply_config()

    # -- life cycle -------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._load_voice, daemon=True, name="voice-load").start()
        threading.Thread(target=self._speak_loop, daemon=True, name="speaker").start()

    def shutdown(self) -> None:
        self._shutdown.set()
        self.disconnect()

    def _load_voice(self) -> None:
        self.events.put(("voice", "loading"))
        try:
            if self._cleaner is None:
                self._cleaner = Cleaner()
            if self._engine is None:
                from .tts import KokoroEngine

                self._engine = KokoroEngine(models_dir(), low_memory=self.cfg.low_memory)
            self._engine.load()
        except Exception as exc:
            self.events.put(("voice", f"error: {exc}"))
            return
        self._voice_ready.set()
        self.events.put(("voice", "ready"))

    # -- settings ---------------------------------------------------------------
    def apply_config(self) -> None:
        s = self.ctx.settings
        s.say_name = self.cfg.say_names
        s.block_tier3 = self.cfg.block_mild_swearing
        s.skip_bots = self.cfg.skip_bots
        s.max_words = self.cfg.max_words
        self.queue.max_pending = self.cfg.max_pending
        self.queue.max_age_s = float(self.cfg.max_age_s)

    # -- chat -------------------------------------------------------------------
    def connect(self, channel_text: str) -> bool:
        channel = normalise_channel(channel_text)
        if not channel:
            self.events.put(("status", "That does not look like a Twitch channel name."))
            return False
        self.disconnect()
        self.ctx.broadcaster_login = channel
        self.ctx.third_party_emotes = frozenset()
        self.ctx.last_speaker = ""
        self._removed_users.clear()
        self.connected_channel = channel
        session = self._session
        self._irc = self._irc_factory(channel, lambda kind, data: self._on_irc(kind, data, session))
        self._irc.start()
        return True

    def disconnect(self) -> None:
        self._session += 1                      # from here on, lines from the old chat thread are dropped
        irc, self._irc = self._irc, None
        if irc is not None:
            irc.stop()
        self.connected_channel = ""
        self._clear("stopped")

    def _on_irc(self, kind: str, data, session: int | None = None) -> None:
        if session is not None and session != self._session:
            return  # a chat thread that was told to stop is still finishing its last read
        if kind == "message":
            self.handle_message(data)
        elif kind == "delete_message":
            self._deleted_ids.append(data)
            self.queue.remove_message(data)
            with self._lock:
                if self._current is not None and self._current.message_id == data:
                    self._interrupt("deleted_by_moderator")
        elif kind == "clear_user":
            if data:
                now = time.time()
                for login in [u for u, t in self._removed_users.items() if now - t > 600]:
                    del self._removed_users[login]
                self._removed_users[data] = now
                self.queue.remove_user(data)
                with self._lock:
                    if self._current is not None and self._current.login == data:
                        self._interrupt("user_timed_out")
            else:  # a moderator cleared the whole chat
                self._clear("deleted_by_moderator")
        elif kind == "room":
            if self.cfg.fetch_emote_lists:
                threading.Thread(target=self._load_emotes, args=(data, self._session), daemon=True, name="emotes").start()
        elif kind == "status":
            self.events.put(("status", data))

    def _load_emotes(self, room_id: str, session: int) -> None:
        names = self._emote_fetcher(room_id)
        if names and session == self._session:
            self.ctx.third_party_emotes = names

    def handle_message(self, event: dict) -> None:
        """One chat message: clean it, then queue it or log why it was not read."""
        if self._cleaner is None:
            return  # still loading; chat lines from the first second are not worth holding
        login = event.get("chatter_user_login", "")
        name = event.get("chatter_user_name") or login
        raw = (event.get("message") or {}).get("text", "")
        try:
            decision = self._cleaner.prepare(event, self.ctx)
        except Exception as exc:  # one odd message must never stop the reader
            self._log(name, "", "skipped", f"could not be read ({type(exc).__name__})")
            return
        if not decision.speak:
            hidden = decision.reason.startswith(("blocked", "personal"))
            self._log(name, "" if hidden else raw, "skipped", friendly_reason(decision.reason))
            return
        item = Item(text=decision.text, login=login, message_id=event.get("message_id", ""), raw=raw, name=name)
        if self.queue.put(item):
            self.ctx.last_speaker = login

    def _on_drop(self, item: Item, reason: str) -> None:
        if item.login and self.ctx.last_speaker == item.login:
            self.ctx.last_speaker = ""  # their line was never heard, so their next one must carry the name again
        self._log(item.name or item.login, item.raw, "skipped", friendly_reason(reason))

    def _log(self, name: str, text: str, status: str, detail: str = "") -> None:
        self.events.put(("log", {"t": time.time(), "name": name, "text": text, "status": status, "detail": detail}))

    # -- controls ---------------------------------------------------------------
    def _interrupt(self, reason: str) -> None:
        self._stop_reason = reason
        self._stop_current.set()

    def _clear(self, reason: str) -> None:
        self._cleared_at = time.time()
        self.queue.clear(reason)
        self._interrupt(reason)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused:
            self._interrupt("paused")
        self.events.put(("paused", paused))

    def toggle_pause(self) -> None:
        self.set_paused(not self.paused)

    def skip(self) -> None:
        self._interrupt("skipped")

    def clear(self) -> None:
        self._clear("cleared")

    def test_voice(self, text: str = "This is how I will read your chat.") -> None:
        if self.paused:
            self.events.put(("status", "Paused. Press Resume to hear the test."))
        self.queue.put(Item(text=text, raw=text, name="Test"), allow_repeat=True)

    # -- the speaking thread ------------------------------------------------------
    def _blocked_before_speaking(self, item: Item) -> str:
        if item.message_id and item.message_id in self._deleted_ids:
            return "deleted_by_moderator"
        if item.login and self._removed_users.get(item.login, 0.0) >= item.received:
            return "user_timed_out"
        if item.received < self._cleared_at:
            return "cleared"
        return ""

    def _speak_loop(self) -> None:
        while not self._shutdown.is_set():
            if not self._voice_ready.wait(0.25):
                continue
            if self.paused:
                time.sleep(0.1)
                continue
            item = self.queue.get(timeout=0.25)
            if item is None:
                continue
            with self._lock:
                self._current = item
                self._stop_current.clear()
                self._stop_reason = ""
            name = item.name or item.login
            try:
                if self.paused:  # pause arrived while this thread was waiting for a message
                    self.queue.put_back(item)
                    continue
                samples, rate = self._engine.synth(item.text, self.cfg.voice, self.cfg.speed)
                reason = self._blocked_before_speaking(item) or (self._stop_reason if self._stop_current.is_set() else "")
                if not reason:
                    self._play(samples, rate, self.cfg.device_name, self.cfg.volume, self._stop_current)
                    reason = self._stop_reason if self._stop_current.is_set() else ""
                if reason == "paused":
                    self.queue.put_back(item)   # read it again from the start after Resume
                elif reason:
                    self._log(name, item.raw, "skipped", friendly_reason(reason))
                else:
                    self._log(name, item.raw, "spoken", item.text)
            except Exception as exc:
                self._log(name, item.raw, "skipped", f"could not be spoken: {exc}")
                self.events.put(("status", f"Sound problem: {exc}"))
                time.sleep(1.0)  # do not spin if the sound device keeps failing
            finally:
                with self._lock:
                    self._current = None
