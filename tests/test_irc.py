from chatreader.irc import IrcClient, normalise_channel, parse_line, to_event, unescape_tag

PRIVMSG = (
    "@badge-info=;badges=moderator/1,subscriber/12;color=#1E90FF;display-name=Cool_Guy;emotes=25:6-10;"
    "first-msg=0;id=abc-123;mod=1;room-id=4242;user-id=77;user-type=mod "
    ":cool_guy!cool_guy@cool_guy.tmi.twitch.tv PRIVMSG #somechannel :hello Kappa world"
)


def test_normalise_channel_accepts_names_and_links():
    assert normalise_channel("  SomeChannel ") == "somechannel"
    assert normalise_channel("#some_channel") == "some_channel"
    assert normalise_channel("@name1") == "name1"
    assert normalise_channel("https://www.twitch.tv/SomeChannel/videos?x=1") == "somechannel"
    assert normalise_channel("twitch.tv/abc") == "abc"


def test_normalise_channel_rejects_bad_input():
    for bad in ("", "   ", "has space", "semi;colon", "a" * 26, "name\r\nJOIN #other", "ünïcode"):
        assert normalise_channel(bad) == "", bad


def test_unescape_tag():
    assert unescape_tag(r"a\sb\:c\\d") == "a b;c\\d"
    assert unescape_tag("plain") == "plain"
    assert unescape_tag("ends\\") == "ends"


def test_parse_privmsg():
    m = parse_line(PRIVMSG)
    assert m["cmd"] == "PRIVMSG"
    assert m["params"] == ["#somechannel"]
    assert m["trailing"] == "hello Kappa world"
    assert m["tags"]["display-name"] == "Cool_Guy"


def test_message_text_may_contain_space_colon():
    m = parse_line(":a!a@a PRIVMSG #c :time is 12 :30 ok")
    assert m["trailing"] == "time is 12 :30 ok"


def test_to_event_fields_and_emote_fragments():
    ev = to_event(parse_line(PRIVMSG))
    assert ev["chatter_user_login"] == "cool_guy"
    assert ev["chatter_user_name"] == "Cool_Guy"
    assert ev["message_id"] == "abc-123"
    assert ev["broadcaster_user_login"] == "somechannel"
    assert {"set_id": "moderator", "id": "1"} in ev["badges"]
    assert ev["message"]["fragments"] == [
        {"type": "text", "text": "hello "},
        {"type": "emote", "text": "Kappa"},
        {"type": "text", "text": " world"},
    ]


def test_emote_positions_count_code_points_not_bytes():
    # the emoji is one code point (two UTF-16 units): Kappa is at 4-8 in code points, 5-9 in UTF-16
    line = "@emotes=25:4-8;id=x :u!u@u PRIVMSG #c :\U0001F600 x Kappa"
    frags = to_event(parse_line(line))["message"]["fragments"]
    assert {"type": "emote", "text": "Kappa"} in frags


def test_out_of_range_emote_span_is_ignored():
    ev = to_event(parse_line("@emotes=25:50-60 :u!u@u PRIVMSG #c :short"))
    assert ev["message"]["fragments"] == [{"type": "text", "text": "short"}]


def test_action_message_is_unwrapped():
    ev = to_event(parse_line("@id=x :u!u@u PRIVMSG #c :\x01ACTION waves\x01"))
    assert ev["message"]["text"] == "waves"


def test_to_event_ignores_other_commands():
    assert to_event(parse_line(":tmi.twitch.tv ROOMSTATE #c")) is None


def _client():
    events, sent = [], []
    return IrcClient("somechannel", lambda k, d: events.append((k, d))), events, sent


def test_ping_is_answered_and_nothing_else_is_ever_sent():
    c, events, sent = _client()
    c.handle_line("PING :tmi.twitch.tv", sent.append)
    c.handle_line(PRIVMSG, sent.append)
    c.handle_line("@target-msg-id=abc-123 :tmi.twitch.tv CLEARMSG #somechannel :hello", sent.append)
    assert sent == [b"PONG :tmi.twitch.tv\r\n"]


def test_message_delete_timeout_and_clear_events():
    c, events, sent = _client()
    c.handle_line(PRIVMSG, sent.append)
    c.handle_line("@login=cool_guy;target-msg-id=abc-123 :tmi.twitch.tv CLEARMSG #somechannel :hello Kappa world", sent.append)
    c.handle_line("@ban-duration=600;target-user-id=77 :tmi.twitch.tv CLEARCHAT #somechannel :Cool_Guy", sent.append)
    c.handle_line(":tmi.twitch.tv CLEARCHAT #somechannel", sent.append)
    kinds = [k for k, _ in events]
    assert kinds == ["room", "message", "delete_message", "clear_user", "clear_user"]
    assert events[0] == ("room", "4242")
    assert events[2] == ("delete_message", "abc-123")
    assert events[3] == ("clear_user", "cool_guy")
    assert events[4] == ("clear_user", "")


def test_room_id_is_reported_once():
    c, events, sent = _client()
    c.handle_line("@room-id=4242 :tmi.twitch.tv ROOMSTATE #somechannel", sent.append)
    c.handle_line(PRIVMSG, sent.append)
    assert [k for k, _ in events].count("room") == 1


def test_reconnect_request_raises_so_the_loop_reconnects():
    c, events, sent = _client()
    try:
        c.handle_line(":tmi.twitch.tv RECONNECT", sent.append)
    except OSError:
        return
    raise AssertionError("RECONNECT must raise OSError")


# -- the connection loop, on a fake socket (added after the independent review) --------------------
import socket as _socket
import threading
import time


class FakeSocket:
    """Hands out prepared byte chunks, then either idles (timeouts) or closes."""

    def __init__(self, chunks, then="idle"):
        self.chunks, self.then, self.sent, self.closed = list(chunks), then, [], False

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, t):
        pass

    def recv(self, n):
        if self.chunks:
            return self.chunks.pop(0)
        if self.then == "close":
            return b""
        time.sleep(0.02)
        raise _socket.timeout()

    def close(self):
        self.closed = True


def run_client(sockets, seconds, **attrs):
    events, made = [], []

    def connect():
        s = sockets.pop(0) if sockets else FakeSocket([], then="idle")
        made.append((time.time(), s))
        return s

    c = IrcClient("somechannel", lambda k, d: events.append((k, d)), connect=connect)
    for k, v in attrs.items():
        setattr(c, k, v)
    c.start()
    time.sleep(seconds)
    c.stop()
    c.join(5)
    assert not c.is_alive()
    return c, events, made


JOIN_ECHO = b":justinfan1!justinfan1@justinfan1.tmi.twitch.tv JOIN #somechannel\r\n"


def test_only_login_join_and_pong_are_written():
    sock = FakeSocket([JOIN_ECHO, b"PING :tmi.twitch.tv\r\n", (PRIVMSG + "\r\n").encode()])
    c, events, made = run_client([sock], 0.4)
    assert sock.sent[0] == b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"
    assert sock.sent[1].startswith(b"NICK justinfan") and sock.sent[2] == b"JOIN #somechannel\r\n"
    assert sock.sent[3:] == [b"PONG :tmi.twitch.tv\r\n"]
    assert ("status", "Reading chat in somechannel") in events
    assert [k for k, _ in events].count("message") == 1
    assert sock.closed and events[-1] == ("status", "Not connected")


def test_a_line_split_across_two_reads_is_joined():
    whole = (PRIVMSG + "\r\n").encode()
    sock = FakeSocket([whole[:40], whole[40:]])
    c, events, made = run_client([sock], 0.3)
    assert [d["message"]["text"] for k, d in events if k == "message"] == ["hello Kappa world"]


def test_reconnect_wait_grows_when_twitch_keeps_closing_the_connection():
    socks = [FakeSocket([b":tmi.twitch.tv NOTICE * :Login unsuccessful\r\n"], then="close") for _ in range(3)]
    c, events, made = run_client(socks, 7.0)
    gaps = [round(b[0] - a[0]) for a, b in zip(made, made[1:])]
    assert gaps[:2] == [2, 4], gaps          # 2 s, then 4 s: it backs off instead of hammering every 2 s


def test_missing_channel_is_reported():
    c, events, made = run_client([FakeSocket([])], 0.5, JOIN_WAIT_S=0.2)
    assert ("status", "Twitch has no channel called somechannel. Check the spelling.") in events


def test_no_message_is_delivered_after_stop():
    events = []
    release = threading.Event()

    class Slow(FakeSocket):
        def recv(self, n):
            release.wait(5)
            return super().recv(n)

    lines = b"".join((PRIVMSG.replace("abc-123", f"id{i}") + "\r\n").encode() for i in range(5))
    sock = Slow([lines])
    c = IrcClient("somechannel", lambda k, d: events.append((k, d)), connect=lambda: sock)
    c.start()
    time.sleep(0.2)
    c.stop()            # stop first...
    release.set()       # ...then let the read that was already in flight return five lines
    c.join(5)
    assert [k for k, _ in events if k == "message"] == []
