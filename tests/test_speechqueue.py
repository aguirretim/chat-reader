from chatreader.speechqueue import Item, SpeechQueue


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(**kw):
    clock = Clock()
    drops = []
    q = SpeechQueue(on_drop=lambda item, reason: drops.append((item.text, reason)), clock=clock, **kw)
    return q, clock, drops


def item(text, clock, login="u", mid=""):
    return Item(text=text, login=login, message_id=mid, received=clock.now)


def test_first_in_first_out():
    q, clock, drops = make()
    for t in ("one", "two", "three"):
        q.put(item(t, clock))
    assert [q.get(0).text for _ in range(3)] == ["one", "two", "three"]
    assert q.get(0) is None
    assert drops == []


def test_oldest_is_dropped_when_full():
    q, clock, drops = make(max_pending=2)
    for t in ("one", "two", "three", "four"):
        q.put(item(t, clock))
    assert [q.get(0).text, q.get(0).text] == ["three", "four"]
    assert drops == [("one", "queue_full"), ("two", "queue_full")]


def test_stale_message_is_dropped_at_its_turn():
    q, clock, drops = make(max_age_s=30)
    q.put(item("old", clock))
    clock.now += 31
    q.put(item("fresh", clock))
    assert q.get(0).text == "fresh"
    assert drops == [("old", "too_old")]


def test_only_stale_messages_returns_none():
    q, clock, drops = make(max_age_s=30)
    q.put(item("old", clock))
    clock.now += 31
    assert q.get(0) is None
    assert drops == [("old", "too_old")]


def test_repeat_within_window_is_dropped_and_allowed_after():
    q, clock, drops = make(repeat_window_s=20)
    assert q.put(item("Hello  THERE", clock)) is True
    assert q.put(item("hello there", clock)) is False
    assert drops == [("hello there", "repeat")]
    clock.now += 21
    assert q.put(item("hello there", clock)) is True
    assert len(q) == 2


def test_remove_message_and_user():
    q, clock, drops = make()
    q.put(item("a", clock, login="ann", mid="1"))
    q.put(item("b", clock, login="bob", mid="2"))
    q.put(item("c", clock, login="Ann", mid="3"))
    assert q.remove_message("2") == 1
    assert q.remove_user("ANN") == 2
    assert len(q) == 0
    assert ("b", "deleted_by_moderator") in drops and ("a", "user_timed_out") in drops
    assert q.remove_message("") == 0 and q.remove_user("") == 0


def test_clear():
    q, clock, drops = make()
    q.put(item("a", clock))
    q.put(item("b", clock))
    assert q.clear() == 2
    assert q.get(0) is None
    assert drops == [("a", "cleared"), ("b", "cleared")]


def test_get_times_out():
    q, clock, drops = make()
    assert q.get(timeout=0.05) is None


def test_put_back_goes_to_the_front():
    q, clock, drops = make()
    q.put(item("one", clock))
    q.put(item("two", clock))
    first = q.get(0)
    q.put_back(first)
    assert [q.get(0).text, q.get(0).text] == ["one", "two"]
