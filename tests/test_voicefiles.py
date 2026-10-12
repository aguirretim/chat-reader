"""Finding and downloading the voice files, against a stand-in for the web server. No network is used."""
import hashlib
import io
import queue
import sys
import urllib.error

import pytest

from chatreader import voicefiles as vf
from chatreader.config import AppConfig
from chatreader.controller import Controller

DATA = {"voice.onnx": bytes(range(256)) * 1200, "voices.bin": b"voices!" * 5000}     # 307,200 and 35,000 bytes


class Response:
    def __init__(self, body: bytes, status: int, cut_after: int | None = None):
        self.body, self.status, self.cut_after, self.sent = io.BytesIO(body), status, cut_after, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self.status

    def read(self, n):
        if self.cut_after is not None and self.sent >= self.cut_after:
            raise ConnectionResetError("connection reset by peer")
        if self.cut_after is not None:
            n = min(n, self.cut_after - self.sent)
        block = self.body.read(n)
        self.sent += len(block)
        return block


class Server:
    """Stands in for urllib.request.urlopen. Records every request."""

    def __init__(self, files=None, honour_range=True, cut_after=None, down=False, corrupt=()):
        self.files = dict(files or DATA)
        self.honour_range, self.cut_after, self.down, self.corrupt = honour_range, cut_after, down, set(corrupt)
        self.requests: list[tuple[str, str | None]] = []

    def __call__(self, request, timeout=None):
        name = request.full_url.rsplit("/", 1)[-1]
        rng = request.get_header("Range")
        self.requests.append((name, rng))
        if self.down:
            raise urllib.error.URLError("getaddrinfo failed")
        body = self.files[name]
        if name in self.corrupt:
            body = body[:-1] + bytes([body[-1] ^ 0xFF])
        if rng and self.honour_range:
            start = int(rng.split("=")[1].rstrip("-"))
            return Response(body[start:], 206, self.cut_after)
        return Response(body, 200, self.cut_after)


@pytest.fixture
def place(tmp_path, monkeypatch):
    """A fake app folder and settings folder, and two small stand-in voice files pinned by size and hash."""
    app, data = tmp_path / "app", tmp_path / "data"
    monkeypatch.setenv("CR_DATA_DIR", str(data))
    monkeypatch.setattr(vf, "app_dir", lambda: app)
    files = tuple(vf.VoiceFile(n, len(b), hashlib.sha256(b).hexdigest()) for n, b in DATA.items())
    monkeypatch.setattr(vf, "FILES", files)
    monkeypatch.setattr(vf, "TOTAL", sum(f.size for f in files))
    monkeypatch.setattr(vf, "CHUNK", 4096)
    monkeypatch.delattr(sys, "frozen", raising=False)
    return app, data


def put(folder, names=DATA):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(DATA[n])


def test_pinned_values_describe_the_real_release():
    real = vf
    assert [(f.name, f.size) for f in real.FILES] == [("kokoro-v1.0.int8.onnx", 92_361_271), ("voices-v1.0.bin", 28_214_398)]
    assert all(len(f.sha256) == 64 for f in real.FILES)
    assert all(f.url.startswith("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/") for f in real.FILES)


def test_files_already_present_make_no_request_and_are_hashed_only_once(place, monkeypatch):
    app, data = place
    put(app / "models")
    server = Server()
    progress = []
    assert vf.ensure(lambda d, t: progress.append(d), opener=server) == app / "models"
    assert server.requests == [] and progress == []
    hashed = []
    real = vf.sha256_of
    monkeypatch.setattr(vf, "sha256_of", lambda p: hashed.append(p) or real(p))
    assert vf.ensure(opener=server) == app / "models"
    assert server.requests == [] and hashed == [], "the second start must not read the files again"


def test_files_in_the_settings_folder_are_found_too(place):
    app, data = place
    put(data / "models")
    server = Server()
    assert vf.ensure(opener=server) == data / "models" and server.requests == []


def test_missing_files_are_downloaded_checked_and_renamed(place):
    app, data = place
    server, progress = Server(), []
    folder = vf.ensure(lambda d, t: progress.append((d, t)), opener=server)
    assert folder == app / "models"                                   # a run from source keeps them beside the app
    assert {n: (folder / n).read_bytes() for n in DATA} == DATA
    assert not list(folder.glob("*.part"))
    assert [name for name, rng in server.requests] == list(DATA) and all(rng is None for _, rng in server.requests)
    done = [d for d, _ in progress]
    assert done == sorted(done) and progress[-1] == (vf.TOTAL, vf.TOTAL) and progress[0][0] == 0


def test_a_packaged_app_downloads_into_the_settings_folder(place, monkeypatch):
    app, data = place
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert vf.ensure(opener=Server()) == data / "models"
    assert not (app / "models").exists()


def test_only_the_missing_file_is_requested(place):
    app, data = place
    put(app / "models", ["voices.bin"])
    server = Server()
    vf.ensure(opener=server)
    assert [name for name, _ in server.requests] == ["voice.onnx"]


def test_a_download_that_fails_its_check_is_thrown_away(place):
    app, data = place
    with pytest.raises(vf.VoiceFilesError) as err:
        vf.ensure(opener=Server(corrupt=["voice.onnx"]))
    assert err.value.kind == "damaged"
    assert not (app / "models" / "voice.onnx").exists() and not list((app / "models").glob("*.part"))


def test_a_file_of_the_right_size_and_wrong_contents_is_replaced(place):
    app, data = place
    put(app / "models")
    (app / "models" / "voice.onnx").write_bytes(b"\0" * len(DATA["voice.onnx"]))
    server = Server()
    vf.ensure(opener=server)
    assert [name for name, _ in server.requests] == ["voice.onnx"]
    assert (app / "models" / "voice.onnx").read_bytes() == DATA["voice.onnx"]


def test_a_cut_connection_keeps_what_arrived_and_the_next_try_continues(place):
    app, data = place
    with pytest.raises(vf.VoiceFilesError) as err:
        vf.ensure(opener=Server(cut_after=100_000))
    assert err.value.kind == "offline"
    part = app / "models" / "voice.onnx.part"
    assert part.stat().st_size == 100_000 and not (app / "models" / "voice.onnx").exists()
    server, progress = Server(), []
    vf.ensure(lambda d, t: progress.append(d), opener=server)
    assert server.requests[0] == ("voice.onnx", "bytes=100000-")
    assert (app / "models" / "voice.onnx").read_bytes() == DATA["voice.onnx"] and not part.exists()
    assert progress[1] == 100_000, "progress starts from what was already on disk"


def test_a_server_that_ignores_range_restarts_the_file_cleanly(place):
    app, data = place
    (app / "models").mkdir(parents=True)
    (app / "models" / "voice.onnx.part").write_bytes(b"junk from an older try" * 100)
    vf.ensure(opener=Server(honour_range=False))
    assert (app / "models" / "voice.onnx").read_bytes() == DATA["voice.onnx"]


def test_no_network_says_offline_and_writes_no_file(place):
    app, data = place
    with pytest.raises(vf.VoiceFilesError) as err:
        vf.ensure(opener=Server(down=True))
    assert err.value.kind == "offline"
    assert not [p for p in (app / "models").glob("*") if p.is_file()]


def test_closing_the_app_stops_the_download(place):
    import threading

    stop = threading.Event()
    stop.set()
    with pytest.raises(vf.VoiceFilesError) as err:
        vf.ensure(stop=stop, opener=Server())
    assert err.value.kind == "stopped"


# -- through the controller ---------------------------------------------------------
class NoEngine:
    def load(self):
        pass


def events_of(ctl, kind, want=1, timeout=5.0):
    import time

    out, end = [], time.time() + timeout
    while len(out) < want and time.time() < end:
        try:
            k, data = ctl.events.get(timeout=0.1)
        except queue.Empty:
            continue
        if k == kind:
            out.append(data)
    return out


def test_controller_reports_a_failed_download_and_try_again_recovers(place, monkeypatch):
    calls = []

    def ensure(on_progress=None, stop=None, opener=None):
        calls.append(1)
        if len(calls) == 1:
            raise vf.VoiceFilesError("offline", "no route")
        on_progress(vf.TOTAL, vf.TOTAL)
        return place[0] / "models"

    monkeypatch.setattr(vf, "ensure", ensure)
    import chatreader.tts as tts

    monkeypatch.setattr(tts, "KokoroEngine", lambda folder, low_memory=False: NoEngine())
    ctl = Controller(AppConfig(fetch_emote_lists=False), play=lambda *a: None)
    ctl._load_voice()
    assert events_of(ctl, "voice_files") == [{"state": "failed", "kind": "offline"}]
    assert not ctl._voice_ready.is_set() and not ctl._voice_loading.is_set()
    ctl.retry_voice()
    assert ctl._voice_ready.wait(5) and len(calls) == 2


def test_controller_makes_no_download_event_when_the_files_are_there(place, monkeypatch):
    put(place[0] / "models")
    import chatreader.tts as tts

    monkeypatch.setattr(tts, "KokoroEngine", lambda folder, low_memory=False: NoEngine())
    monkeypatch.setattr(vf.urllib.request, "urlopen", lambda *a, **k: pytest.fail("a request was made although the files are present"))
    ctl = Controller(AppConfig(fetch_emote_lists=False), play=lambda *a: None)
    ctl._load_voice()
    kinds = []
    while not ctl.events.empty():
        kinds.append(ctl.events.get_nowait()[0])
    assert "voice_files" not in kinds and ctl._voice_ready.is_set()
