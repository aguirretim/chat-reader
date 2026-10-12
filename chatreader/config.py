"""Settings, kept as one small JSON file in the user's own app-data folder."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "Chat Reader"
APP_ID = "ChatReader"
VERSION = "0.1.0"


def data_dir() -> Path:
    base = os.environ.get("CR_DATA_DIR") or os.path.join(os.environ.get("LOCALAPPDATA") or str(Path.home()), APP_ID)
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_dir() -> Path:
    """Folder that holds the exe (packaged) or the project (run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def models_dir() -> Path:
    return app_dir() / "models"


@dataclass
class AppConfig:
    channel: str = ""
    voice: str = "af_heart"
    device_name: str = ""          # "" = Windows default
    volume: int = 80               # 0..100
    speed: float = 1.0             # 0.7..1.5
    say_names: bool = True
    block_mild_swearing: bool = False
    skip_bots: bool = True
    max_words: int = 40
    max_pending: int = 6
    max_age_s: int = 30
    fetch_emote_lists: bool = True  # asks 7TV / BetterTTV / FrankerFaceZ for the channel's emote names
    hotkeys: bool = True
    low_memory: bool = False       # voice engine arena off: about 170 MB instead of up to 690 MB, about twice the wait

    @classmethod
    def load(cls) -> "AppConfig":
        path = data_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        cfg = cls()
        for f in fields(cls):
            if f.name not in raw:
                continue
            default, value = getattr(cfg, f.name), raw[f.name]
            # a wrong type in a hand-edited file falls back to the default instead of crashing the app
            if isinstance(default, bool):
                ok = isinstance(value, bool)
            elif isinstance(default, int):
                ok = isinstance(value, int) and not isinstance(value, bool)
            elif isinstance(default, float):
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
                value = float(value) if ok else value
            else:
                ok = isinstance(value, str)
            if ok:
                setattr(cfg, f.name, value)
        cfg.volume = min(100, max(0, cfg.volume))
        cfg.speed = min(1.5, max(0.7, cfg.speed))
        cfg.max_words = min(200, max(5, cfg.max_words))
        cfg.max_pending = min(50, max(1, cfg.max_pending))
        cfg.max_age_s = min(600, max(5, cfg.max_age_s))
        return cfg

    def save(self) -> None:
        path = data_dir() / "settings.json"
        text = json.dumps(asdict(self), indent=2)   # build the text first, then replace the file in one step
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, "utf-8")
        os.replace(tmp, path)
