"""Sound output through a chosen speaker (PortAudio, WASAPI shared mode).

PortAudio start-up took 4.3 s on the test PC (179 device entries), so it is loaded on a
background thread and the window does not wait for it.

CR_NULL_AUDIO=1 (tests): open the device and write a moment of silence instead of the speech,
so automated runs prove the device path without making a sound.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

_sd = None
_ready = threading.Event()
_error: str = ""


def start(on_ready: Callable[[list[str], str], None]) -> None:
    """Load PortAudio in the background, then call on_ready(device names, error text)."""

    def work():
        global _sd, _error
        try:
            import sounddevice

            _sd = sounddevice
        except Exception as exc:  # a PC with no sound hardware or a broken driver must still open the app
            _error = f"Windows sound could not be started: {exc}"
        _ready.set()
        on_ready(output_devices(), _error)

    threading.Thread(target=work, daemon=True, name="audio-init").start()


def wait_ready(timeout: float | None = None) -> bool:
    return _ready.wait(timeout)


def _wasapi_outputs() -> list[tuple[int, str]]:
    if _sd is None:
        return []
    try:
        apis = _sd.query_hostapis()
        wasapi = next((i for i, a in enumerate(apis) if "WASAPI" in a["name"]), None)
        out = []
        for idx, dev in enumerate(_sd.query_devices()):
            if dev["max_output_channels"] >= 1 and (wasapi is None or dev["hostapi"] == wasapi):
                out.append((idx, dev["name"]))
        return out
    except Exception:
        return []


def output_devices() -> list[str]:
    """Speaker names, WASAPI only (the other Windows sound systems list the same hardware again)."""
    return [name for _, name in _wasapi_outputs()]


def _device_index(name: str):
    if not name:
        return None  # Windows default
    for idx, dev_name in _wasapi_outputs():
        if dev_name == name:
            return idx
    return None  # the saved speaker is unplugged: fall back to the default rather than fail


def _open(rate: int, device_name: str):
    """Open an output stream on the named speaker, or on the Windows default when the name is empty or gone."""
    index = _device_index(device_name)
    if index is None:
        return _sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=None)
    # A WASAPI shared-mode device only accepts its own mix rate (44.1 or 48 kHz); the voice is 24 kHz.
    # auto_convert asks Windows to resample. Without it every named speaker fails with "Invalid sample rate".
    return _sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=index,
                            extra_settings=_sd.WasapiSettings(auto_convert=True))


def _restart_portaudio() -> None:
    """PortAudio reads the device list once. After a speaker is unplugged or the default changes, read it again."""
    try:
        _sd._terminate()
        _sd._initialize()
    except Exception:
        pass


def shape_samples(samples, volume: int):
    """Apply the volume, then hold every sample inside -1..1.

    The voice sometimes overshoots full scale (8 of 179 benchmark files did). Sent to the sound card
    as it is, an overshoot distorts; held at the limit it is at worst a slightly flat peak.
    """
    import numpy as np

    data = np.asarray(samples, dtype=np.float32) * (max(0, min(100, volume)) / 100.0)
    return np.clip(data, -1.0, 1.0)


def play(samples, rate: int, device_name: str, volume: int, stop: threading.Event) -> str:
    """Play and return the name of the device used. Returns early when `stop` is set."""
    wait_ready()
    if _sd is None:
        raise RuntimeError(_error or "Windows sound is not available.")
    import numpy as np

    null_audio = os.environ.get("CR_NULL_AUDIO") == "1"
    data = shape_samples(samples, volume)
    try:
        stream = _open(rate, device_name)
    except Exception:
        _restart_portaudio()               # the device list was stale: refresh it and use whatever is there now
        try:
            stream = _open(rate, device_name)
        except Exception:
            stream = _open(rate, "")       # last resort: the Windows default speaker
    with stream:
        used = _sd.query_devices(stream.device)["name"]
        if null_audio:
            stream.write(np.zeros(rate // 5, dtype=np.float32))
            return used
        block = max(1, rate // 20)  # 50 ms: Stop and Skip take effect within that
        for start_at in range(0, len(data), block):
            if stop.is_set():
                stream.abort()
                break
            stream.write(data[start_at : start_at + block])
    return used