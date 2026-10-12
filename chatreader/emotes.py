"""Names of the channel's 7TV / BetterTTV / FrankerFaceZ emotes, so they are not read aloud as words.

Twitch's own emotes are marked in the chat data; these three services are separate, public and need
no key. Only the channel's numeric id is sent. Any failure just means those emote names get spoken.
"""
from __future__ import annotations

import json
import urllib.request

from .config import VERSION

_UA = {"User-Agent": f"ChatReader/{VERSION} (desktop chat reader)"}


def _get(url: str):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=10) as r:
            return json.loads(r.read(4_000_000).decode("utf-8"))
    except Exception:
        return None


def _7tv(d) -> list[str]:
    if not isinstance(d, dict):
        return []
    emotes = d.get("emotes") or (d.get("emote_set") or {}).get("emotes") or []
    return [e.get("name", "") for e in emotes if isinstance(e, dict)]


def _bttv_global(d) -> list[str]:
    return [e.get("code", "") for e in d if isinstance(e, dict)] if isinstance(d, list) else []


def _bttv_channel(d) -> list[str]:
    if not isinstance(d, dict):
        return []
    return [e.get("code", "") for k in ("channelEmotes", "sharedEmotes") for e in d.get(k) or [] if isinstance(e, dict)]


def _ffz(d) -> list[str]:
    if not isinstance(d, dict):
        return []
    return [e.get("name", "") for s in (d.get("sets") or {}).values() for e in s.get("emoticons") or [] if isinstance(e, dict)]


def fetch(room_id: str) -> frozenset[str]:
    if not str(room_id).isdigit():
        return frozenset()
    names = (
        _7tv(_get("https://7tv.io/v3/emote-sets/global"))
        + _bttv_global(_get("https://api.betterttv.net/3/cached/emotes/global"))
        + _ffz(_get("https://api.frankerfacez.com/v1/set/global"))
        + _7tv(_get(f"https://7tv.io/v3/users/twitch/{room_id}"))
        + _bttv_channel(_get(f"https://api.betterttv.net/3/cached/users/twitch/{room_id}"))
        + _ffz(_get(f"https://api.frankerfacez.com/v1/room/id/{room_id}"))
    )
    return frozenset(n for n in names if n)
