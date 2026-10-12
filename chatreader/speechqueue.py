"""The line of messages waiting to be spoken.

Rules (all adjustable):
  - at most `max_pending` wait; when one more arrives the oldest is dropped, so speech stays close to live chat
  - a message older than `max_age_s` when its turn comes is dropped
  - the same text again within `repeat_window_s` is dropped (copy-paste spam)
  - a message deleted by a moderator, or written by someone who was timed out or banned, is removed
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Item:
    text: str                 # exactly what the voice will say
    login: str = ""
    message_id: str = ""
    received: float = field(default_factory=time.time)
    raw: str = ""             # what was typed, for the on-screen log
    name: str = ""            # display name, for the on-screen log


class SpeechQueue:
    def __init__(self, max_pending: int = 6, max_age_s: float = 30.0, repeat_window_s: float = 20.0,
                 on_drop: Callable[[Item, str], None] | None = None, clock: Callable[[], float] = time.time):
        self.max_pending = max_pending
        self.max_age_s = max_age_s
        self.repeat_window_s = repeat_window_s
        self.on_drop = on_drop or (lambda item, reason: None)
        self.clock = clock
        self._items: deque[Item] = deque()
        self._recent: deque[tuple[float, str]] = deque()
        self._cond = threading.Condition()

    def __len__(self) -> int:
        with self._cond:
            return len(self._items)

    def put(self, item: Item, allow_repeat: bool = False) -> bool:
        """Add a message. Returns False when it was dropped as a repeat."""
        dropped: list[tuple[Item, str]] = []
        # Compare what was typed, not what will be said: the spoken text starts with the writer's name,
        # so the same line pasted by three people would otherwise look like three different messages.
        key = " ".join((item.raw or item.text).lower().split())
        with self._cond:
            now = self.clock()
            while self._recent and now - self._recent[0][0] > self.repeat_window_s:
                self._recent.popleft()
            if not allow_repeat and any(k == key for _, k in self._recent):
                dropped.append((item, "repeat"))
            else:
                self._recent.append((now, key))
                self._items.append(item)
                while len(self._items) > max(1, self.max_pending):
                    dropped.append((self._items.popleft(), "queue_full"))
                self._cond.notify()
        for it, reason in dropped:
            self.on_drop(it, reason)
        return not (dropped and dropped[0][0] is item)

    def get(self, timeout: float | None = None) -> Item | None:
        """Next message to speak, or None after `timeout`. Stale messages are dropped on the way."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            stale: list[Item] = []
            with self._cond:
                while self._items and self.clock() - self._items[0].received > self.max_age_s:
                    stale.append(self._items.popleft())
                item = self._items.popleft() if self._items else None
                if item is None and not stale:
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        return None
                    self._cond.wait(remaining)
            for it in stale:
                self.on_drop(it, "too_old")
            if item is not None:
                return item
            if not stale and deadline is not None and time.monotonic() >= deadline:
                return None

    def put_back(self, item: Item) -> None:
        """Return a message taken with get() to the front of the line (pause arrived before it was spoken)."""
        with self._cond:
            self._items.appendleft(item)
            self._cond.notify()

    def _remove(self, keep: Callable[[Item], bool], reason: str) -> int:
        with self._cond:
            gone = [it for it in self._items if not keep(it)]
            self._items = deque(it for it in self._items if keep(it))
        for it in gone:
            self.on_drop(it, reason)
        return len(gone)

    def remove_message(self, message_id: str) -> int:
        return self._remove(lambda it: it.message_id != message_id, "deleted_by_moderator") if message_id else 0

    def remove_user(self, login: str) -> int:
        login = (login or "").lower()
        return self._remove(lambda it: it.login.lower() != login, "user_timed_out") if login else 0

    def clear(self, reason: str = "cleared") -> int:
        return self._remove(lambda it: False, reason)
