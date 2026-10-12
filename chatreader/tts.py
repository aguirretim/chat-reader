"""The voice: Kokoro v1.0 (ONNX) on the CPU. No network, no GPU."""
from __future__ import annotations

import os
import time
from pathlib import Path

# numpy's OpenBLAS reserves a buffer per CPU thread at import: 750 MB on a 32-thread CPU (measured).
# Nothing here needs multi-threaded linear algebra.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

MODEL_FILE = "kokoro-v1.0.int8.onnx"
VOICES_FILE = "voices-v1.0.bin"
# (id, label) in the order shown. Grades are the model author's own (VOICES.md).
VOICES = [
    ("af_heart", "Heart (US, female)"),
    ("af_bella", "Bella (US, female)"),
    ("af_nicole", "Nicole (US, female, soft)"),
    ("am_michael", "Michael (US, male)"),
    ("am_fenrir", "Fenrir (US, male)"),
    ("am_puck", "Puck (US, male)"),
    ("bf_emma", "Emma (UK, female)"),
    ("bm_george", "George (UK, male)"),
]
WARM_UP = "This is a short warm up sentence so the first real one is quick."


class VoiceError(RuntimeError):
    pass


def _short_path(path: str) -> str:
    """Windows 8.3 form of a path ("" when the drive does not keep short names)."""
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
    return buf.value if 0 < n < len(buf) else ""


def ascii_espeak_data(data_path: str) -> str:
    """A path to the pronunciation data that contains only plain ASCII letters.

    espeak-ng on Windows reads this path in the system code page and ENDS THE WHOLE PROGRAM when it
    cannot find its files. That happens when the app sits under a folder such as C:\\Users\\José\\.
    Order tried: the path itself, its 8.3 short form, a copy under C:\\ProgramData.
    A short form only helps together with pin_espeak_data(): the phonemizer library turns it back
    into the long form before use.
    """
    if data_path.isascii():
        return data_path
    short = _short_path(data_path)
    if short and short.isascii() and os.path.isdir(short):
        return short
    import shutil

    target = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "ChatReader", "espeak-ng-data")
    if target.isascii():
        try:
            if not os.path.exists(os.path.join(target, "phontab")):
                shutil.copytree(data_path, target, dirs_exist_ok=True)
            return target
        except OSError:
            pass
    raise VoiceError("Move the Chat Reader folder to a place with plain English letters in its path, "
                     "for example C:\\ChatReader, then start it again.")


def pin_espeak_data(path: str) -> None:
    """Make phonemizer hand `path` to espeak-ng exactly as given.

    phonemizer 3.4.0 calls Path.resolve() on the data path (wrapper.py, `data_path` property), which
    expands an 8.3 short path back into the long one with the non-English letters, and the program
    then dies inside espeak-ng. Found by running the packaged app from a folder named "José Muñoz".
    """
    import pathlib

    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    EspeakWrapper.data_path = property(lambda self: pathlib.Path(path))


class KokoroEngine:
    def __init__(self, models: Path, low_memory: bool = False, threads: int | None = None):
        self.models = Path(models)
        self.low_memory = low_memory
        self.threads = threads or max(2, min(4, (os.cpu_count() or 4) // 2))
        self._kokoro = None
        self.load_s = 0.0

    def load(self) -> None:
        model, voices = self.models / MODEL_FILE, self.models / VOICES_FILE
        for f in (model, voices):
            if not f.exists():
                raise VoiceError(f"A voice file is missing: {f.name}. Put the app's 'models' folder back next to the program.")
        t = time.time()
        import espeakng_loader
        import onnxruntime as ort
        from kokoro_onnx import Kokoro
        from kokoro_onnx.config import EspeakConfig

        bundled = espeakng_loader.get_data_path()
        safe = ascii_espeak_data(bundled)
        if safe != bundled:
            pin_espeak_data(safe)
        espeak = EspeakConfig(lib_path=espeakng_loader.get_library_path(), data_path=safe)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        # Arena on: memory settles near 690 MB after long messages instead of 170 MB, and the wait is about half
        # (measured over 80 mixed messages: median 0.73 s against 1.42 s).
        opts.enable_cpu_mem_arena = not self.low_memory
        session = ort.InferenceSession(str(model), sess_options=opts, providers=["CPUExecutionProvider"])
        # phonemizer copies espeak-ng.dll into a new temporary folder on every start and removes it only on
        # a clean exit; 161 such folders (64 MB) were left in %TEMP% after one day of test runs. Give it a
        # folder this app owns and empties at each start instead.
        import shutil
        import tempfile

        from .config import data_dir

        own_tmp = data_dir() / "runtime-tmp"
        shutil.rmtree(own_tmp, ignore_errors=True)   # a copy still loaded by another running instance stays locked
        own_tmp.mkdir(parents=True, exist_ok=True)
        previous_tmp, tempfile.tempdir = tempfile.tempdir, str(own_tmp)
        try:
            self._kokoro = Kokoro.from_session(session, str(voices), espeak_config=espeak)
            self._kokoro.create(WARM_UP, voice=VOICES[0][0], lang="en-us")
        finally:
            tempfile.tempdir = previous_tmp
        self.load_s = time.time() - t

    @property
    def ready(self) -> bool:
        return self._kokoro is not None

    def synth(self, text: str, voice: str, speed: float = 1.0):
        """Returns (float32 samples, sample rate)."""
        if self._kokoro is None:
            raise VoiceError("The voice is not loaded yet.")
        import numpy as np

        samples, rate = self._kokoro.create(text, voice=voice, speed=speed, lang="en-us")
        return np.asarray(samples, dtype=np.float32), int(rate)
