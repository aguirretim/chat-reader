"""Finding the two voice files, and downloading them when they are missing or damaged.

The installer and the zip carry the files, so an installed copy never downloads anything. A copy run
from source (the files are too large for the code repository) fetches them once, on first start.

Where they are looked for, in order:  <app folder>\\models  then  <settings folder>\\models
Where a download goes:  beside the app for a run from source; the settings folder for a packaged app
(so nothing is written into the program folder and an uninstall leaves no stray files there).

No download happens when both files are present and pass the check. The check is the exact size, plus a
SHA-256 that is computed once per file and remembered (voice-check.json in the settings folder, keyed by
path, size and modified time), so 120 MB are not read again on every start.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import VERSION, app_dir, data_dir

# The release these exact files came from. The tag matters: "model-files-v1.1" has a file with the same
# name and other contents (114,119,327 bytes).
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


@dataclass(frozen=True)
class VoiceFile:
    name: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return RELEASE + self.name


FILES = (
    VoiceFile("kokoro-v1.0.int8.onnx", 92_361_271, "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"),
    VoiceFile("voices-v1.0.bin", 28_214_398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
)
TOTAL = sum(f.size for f in FILES)
CHUNK = 256 * 1024
TIMEOUT_S = 30


class VoiceFilesError(Exception):
    """kind: "offline" (could not reach the server or the connection broke), "damaged" (arrived but failed the
    check), "disk" (could not write), "stopped" (the app is closing)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind, self.detail = kind, detail


# -- where --------------------------------------------------------------------
def search_dirs() -> list[Path]:
    return [app_dir() / "models", data_dir() / "models"]


def download_dir() -> Path:
    return (data_dir() if getattr(sys, "frozen", False) else app_dir()) / "models"


# -- the check ------------------------------------------------------------------
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _memo_path() -> Path:
    return data_dir() / "voice-check.json"


def _load_memo() -> dict:
    try:
        data = json.loads(_memo_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_memo(memo: dict) -> None:
    try:
        tmp = _memo_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(memo, indent=1), "utf-8")
        os.replace(tmp, _memo_path())
    except OSError:
        pass  # the check then simply runs again next time


def is_valid(path: Path, spec: VoiceFile, memo: dict | None = None) -> bool:
    """True when the file is the pinned one. Hashes at most once per file version."""
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size != spec.size:
        return False
    own = memo is None
    memo = _load_memo() if own else memo
    key = str(path.resolve())
    seen = memo.get(key)
    if isinstance(seen, dict) and seen.get("size") == st.st_size and seen.get("mtime_ns") == st.st_mtime_ns:
        return seen.get("sha256") == spec.sha256
    digest = sha256_of(path)
    memo[key] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
    if own:
        _save_memo(memo)
    return digest == spec.sha256


def find_valid_dir() -> Path | None:
    """The first folder that holds both files, checked. None when a download is needed."""
    memo = _load_memo()
    before = json.dumps(memo, sort_keys=True)
    found = None
    for folder in search_dirs():
        if all(is_valid(folder / f.name, f, memo) for f in FILES):
            found = folder
            break
    if json.dumps(memo, sort_keys=True) != before:
        _save_memo(memo)
    return found


# -- the download -----------------------------------------------------------------
def _open(url: str, start: int, opener):
    headers = {"User-Agent": f"ChatReader/{VERSION} (voice files)"}
    if start:
        headers["Range"] = f"bytes={start}-"
    return opener(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S)


def fetch(spec: VoiceFile, folder: Path, on_bytes: Callable[[int], None], stop: threading.Event | None = None,
          opener=urllib.request.urlopen) -> Path:
    """Download one file into `folder`. Writes <name>.part, checks it, and only then gives it its real name.

    A .part left by a broken download is continued when the server allows it, and started again when not.
    on_bytes(n) is called with the number of bytes of this file that are on disk so far.
    """
    final, part = folder / spec.name, folder / (spec.name + ".part")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        have = part.stat().st_size if part.exists() else 0
        if have > spec.size:
            part.unlink()
            have = 0
    except OSError as exc:
        raise VoiceFilesError("disk", str(exc)) from None
    if have < spec.size:
        try:
            response = _open(spec.url, have, opener)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and have:        # the server will not continue from there: start again
                part.unlink(missing_ok=True)
                return fetch(spec, folder, on_bytes, stop, opener)
            raise VoiceFilesError("offline", f"the server answered {exc.code}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise VoiceFilesError("offline", str(getattr(exc, "reason", exc))) from None
        with response:
            status = getattr(response, "status", None) or response.getcode()
            resumed = have > 0 and status == 206
            if not resumed:
                have = 0
            try:
                with open(part, "ab" if resumed else "wb") as out:
                    on_bytes(have)
                    while True:
                        if stop is not None and stop.is_set():
                            raise VoiceFilesError("stopped")
                        try:
                            block = response.read(CHUNK)
                        except (OSError, TimeoutError, urllib.error.URLError) as exc:   # the connection broke part-way
                            raise VoiceFilesError("offline", str(exc)) from None
                        if not block:
                            break
                        out.write(block)
                        have += len(block)
                        on_bytes(have)
            except OSError as exc:
                raise VoiceFilesError("disk", str(exc)) from None
    if have < spec.size:
        raise VoiceFilesError("offline", "the connection closed before the file was complete")   # the .part stays, to continue
    if have != spec.size or sha256_of(part) != spec.sha256:
        part.unlink(missing_ok=True)            # nothing worth continuing from
        raise VoiceFilesError("damaged", f"{spec.name} did not match its checksum")
    try:
        os.replace(part, final)
    except OSError as exc:
        raise VoiceFilesError("disk", str(exc)) from None
    return final


def ensure(on_progress: Callable[[int, int], None] | None = None, stop: threading.Event | None = None,
           opener=urllib.request.urlopen) -> Path:
    """The folder with both voice files, downloading whatever is missing. on_progress(done bytes, total bytes)
    is called only when a download is really happening. Raises VoiceFilesError."""
    found = find_valid_dir()
    if found is not None:
        return found
    folder = download_dir()
    report = on_progress or (lambda done, total: None)
    memo = _load_memo()
    need = [f for f in FILES if not is_valid(folder / f.name, f, memo)]
    done_before = TOTAL - sum(f.size for f in need)
    report(done_before, TOTAL)
    for spec in need:
        fetch(spec, folder, lambda n, base=done_before: report(base + n, TOTAL), stop, opener)
        done_before += spec.size
        is_valid(folder / spec.name, spec, memo)     # remember the hash that was just checked
    _save_memo(memo)
    if not all(is_valid(folder / f.name, f, memo) for f in FILES):
        raise VoiceFilesError("damaged", "the files changed while they were being checked")
    return folder
