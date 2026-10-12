"""Read-only Twitch chat over IRC (TLS), with no sign-in.

Logs in as justinfan<digits> with no token. The only lines ever written to the socket are
CAP REQ, NICK, JOIN and PONG: this client cannot post. Twitch tolerates this login but does
not document it, so the app treats a failure here as "try again later", never as a bug to hide.
"""
from __future__ import annotations

import random
import re
import socket
import ssl
import threading
import time
from typing import Callable

HOST, PORT = "irc.chat.twitch.tv", 6697
CHANNEL_RE = re.compile(r"^[a-z0-9_]{1,25}$")
_UNESC = {"s": " ", ":": ";", "\\": "\\", "r": "\r", "n": "\n"}


def normalise_channel(text: str) -> str:
    """Accept "Name", "#name", "@name" or a twitch.tv link. Returns "" when it is not a channel name."""
    t = (text or "").strip().lower()
    t = re.sub(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/", "", t)
    t = t.split("/")[0].split("?")[0].lstrip("#@").strip()
    return t if CHANNEL_RE.match(t) else ""


def unescape_tag(v: str) -> str:
    if "\\" not in v:
        return v
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            out.append(_UNESC.get(v[i + 1], v[i + 1]))
            i += 2
        else:
            if c != "\\":
                out.append(c)
            i += 1
    return "".join(out)


def parse_line(line: str) -> dict:
    """One raw IRC line -> dict(tags, prefix, cmd, params, trailing)."""
    tags, prefix = {}, ""
    rest = line
    if rest.startswith("@"):
        raw_tags, _, rest = rest[1:].partition(" ")
        for kv in raw_tags.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = unescape_tag(v)
    if rest.startswith(":"):
        prefix, _, rest = rest[1:].partition(" ")
    trailing = None
    if " :" in rest:
        rest, _, trailing = rest.partition(" :")
    elif rest.startswith(":"):
        rest, trailing = "", rest[1:]
    parts = rest.split()
    return {"tags": tags, "prefix": prefix, "cmd": parts[0] if parts else "", "params": parts[1:], "trailing": trailing}


def _emote_spans(raw: str) -> list[tuple[int, int]]:
    spans = []
    for part in (raw or "").split("/"):
        if ":" not in part:
            continue
        for rng in part.split(":", 1)[1].split(","):
            a, _, b = rng.partition("-")
            if a.isdigit() and b.isdigit() and int(a) <= int(b):
                spans.append((int(a), int(b)))
    return sorted(spans)


def _fragments(text: str, spans: list[tuple[int, int]]) -> list[dict]:
    """Split the text into text / emote pieces. Twitch counts positions in code points, as Python does."""
    out, pos = [], 0
    for a, b in spans:
        if a < pos or b >= len(text):
            continue  # overlapping or out of range: leave that span as plain text
        if a > pos:
            out.append({"type": "text", "text": text[pos:a]})
        out.append({"type": "emote", "text": text[a : b + 1]})
        pos = b + 1
    if pos < len(text):
        out.append({"type": "text", "text": text[pos:]})
    return out


def to_event(msg: dict) -> dict | None:
    """A parsed PRIVMSG -> the field names of an EventSub channel.chat.message event (what textclean reads)."""
    if msg.get("cmd") != "PRIVMSG" or not msg.get("params"):
        return None
    tags = msg["tags"]
    login = msg["prefix"].split("!")[0].lower() if "!" in msg["prefix"] else ""
    text = msg.get("trailing") or ""
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        text = text[8:-1]  # "/me" messages; emote positions already count from here
    badges = []
    for b in (tags.get("badges") or "").split(","):
        if "/" in b:
            set_id, _, version = b.partition("/")
            badges.append({"set_id": set_id, "id": version})
    return {
        "broadcaster_user_login": msg["params"][0].lstrip("#"),
        "broadcaster_user_id": tags.get("room-id", ""),
        "source_broadcaster_user_id": tags.get("source-room-id") or None,
        "chatter_user_id": tags.get("user-id", ""),
        "chatter_user_login": login,
        "chatter_user_name": tags.get("display-name") or login,
        "message_id": tags.get("id", ""),
        "message": {"text": text, "fragments": _fragments(text, _emote_spans(tags.get("emotes", "")))},
        "badges": badges,
        "channel_points_custom_reward_id": tags.get("custom-reward-id") or None,
    }


class IrcClient(threading.Thread):
    """Background reader for one channel. Calls on_event(kind, data) from its own thread.

    kinds: "status" (str), "message" (event dict), "delete_message" (message id),
           "clear_user" (login, or "" for the whole chat), "room" (room id)
    After stop() no further "message" events are delivered.
    """

    JOIN_WAIT_S = 10.0      # Twitch silently ignores a JOIN for a channel that does not exist
    STABLE_AFTER_S = 60.0   # a connection that lasted this long resets the reconnect wait
    SILENCE_LIMIT_S = 600.0  # Twitch pings every few minutes (interval not published); be generous

    def __init__(self, channel: str, on_event: Callable[[str, object], None], connect=None):
        super().__init__(daemon=True, name="irc")
        self.channel = channel
        self.on_event = on_event
        self._stop_event = threading.Event()   # not "_stop": threading.Thread has a method of that name
        self._connect = connect or self._tls_connect
        self._room_sent = False
        self._joined = False

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    @staticmethod
    def _tls_connect():
        raw = socket.create_connection((HOST, PORT), timeout=15)
        return ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)

    def handle_line(self, line: str, send: Callable[[bytes], None]) -> None:
        """Process one line. Separate from the socket loop so it can be tested without a network."""
        if line.startswith("PING"):
            send(("PONG" + line[4:] + "\r\n").encode())
            return
        m = parse_line(line)
        cmd, tags = m["cmd"], m["tags"]
        if cmd == "PRIVMSG":
            ev = to_event(m)
            if ev:
                self._room(tags.get("room-id"))
                self.on_event("message", ev)
        elif cmd == "CLEARMSG":
            if tags.get("target-msg-id"):
                self.on_event("delete_message", tags["target-msg-id"])
        elif cmd == "CLEARCHAT":
            self.on_event("clear_user", (m["trailing"] or "").lower())
        elif cmd == "ROOMSTATE":
            self._room(tags.get("room-id"))
        elif cmd == "JOIN":
            self._joined = True
            self.on_event("status", f"Reading chat in {self.channel}")
        elif cmd == "RECONNECT":
            raise OSError("Twitch asked for a reconnect")
        elif cmd == "NOTICE":
            self.on_event("status", f"Twitch says: {m['trailing'] or ''}")

    def _room(self, room_id) -> None:
        if room_id and not self._room_sent:
            self._room_sent = True
            self.on_event("room", room_id)

    def _session(self, sock) -> None:
        """One connection, until it drops or stop() is called."""
        sock.sendall(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
        sock.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\n".encode())
        sock.sendall(f"JOIN #{self.channel}\r\n".encode())
        sock.settimeout(1.0)
        self._joined = False
        buf, last_rx, joined_by, warned = b"", time.time(), time.time() + self.JOIN_WAIT_S, False
        while not self.stopped:
            if not self._joined and not warned and time.time() > joined_by:
                warned = True
                self.on_event("status", f"Twitch has no channel called {self.channel}. Check the spelling.")
            try:
                data = sock.recv(65536)
            except (socket.timeout, ssl.SSLWantReadError):
                if time.time() - last_rx > self.SILENCE_LIMIT_S:
                    raise OSError("no data from Twitch for a long time")
                continue
            if not data:
                raise OSError("connection closed by Twitch")
            last_rx = time.time()
            buf += data
            while b"\r\n" in buf:
                if self.stopped:
                    return
                rawline, buf = buf.split(b"\r\n", 1)
                self.handle_line(rawline.decode("utf-8", "replace"), sock.sendall)

    def run(self) -> None:
        backoff = 2
        while not self.stopped:
            self.on_event("status", f"Connecting to {self.channel}...")
            sock, started, reason = None, time.time(), ""
            try:
                sock = self._connect()
                if not self.stopped:
                    self._session(sock)
            except socket.gaierror:
                reason = "Cannot reach Twitch. Is the internet connection working?"
            except (OSError, ssl.SSLError) as exc:
                reason = f"Chat connection lost ({exc})."
            except Exception as exc:  # a line this code did not expect must not end the reader silently
                reason = f"Chat reader problem ({type(exc).__name__})."
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            if self.stopped:
                break
            if time.time() - started >= self.STABLE_AFTER_S:
                backoff = 2   # only a connection that really worked earns a quick retry
            self.on_event("status", f"{reason or 'Chat connection ended.'} Trying again in {backoff} s.")
            self._stop_event.wait(backoff)
            backoff = min(backoff * 2, 60)
        self.on_event("status", "Not connected")