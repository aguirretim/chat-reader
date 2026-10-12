"""prepare(message, context) -> Decision: the step between "chat message arrives" and "text handed to the voice".

`message` is the `event` object of an EventSub `channel.chat.message` notification
(or any dict with the same field names). Only these fields are read:
  chatter_user_login, chatter_user_name, message.text, message.fragments[], badges[],
  source_broadcaster_user_id, channel_points_custom_reward_id
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import fold, speech
from .blocklist import Blocklist

_DATA = Path(__file__).parent / "data"


@dataclass
class Settings:
    """Defaults are what a beginner gets with no set-up."""

    # who and what is skipped
    skip_bots: bool = True
    extra_bots: frozenset = frozenset()   # logins the streamer added to the bot list
    skip_commands: bool = True            # messages starting with "!"
    skip_own_messages: bool = True
    skip_shared_chat: bool = True         # messages from the other channel in a shared chat
    drop_streamer_mention: bool = True    # "@Streamer nice shot" -> "nice shot"
    skip_non_latin: bool = True          # mostly non-Latin text: an English voice cannot read it
    # content
    links: str = "drop"                   # drop | say  ("say" speaks the word "link")
    emoji: str = "drop"                   # drop | name
    emotes: str = "drop"                  # drop | famous (speak the few in emote_words.json)
    expand_abbreviations: bool = True
    numbers: str = "app"                  # app | engine (leave digits for the voice engine)
    max_words: int = 40
    cut_suffix: str = ""                  # e.g. "message cut"
    # names
    say_name: bool = True
    name_template: str = "{name} says {text}"
    # safety
    block_tier2: bool = True              # strong profanity, explicit sexual words, extremist references
    block_tier3: bool = False             # mild swearing and insults
    block_personal_details: bool = True   # phone numbers, e-mail and street addresses, IP addresses
    on_blocked_name: str = "skip"         # skip | anonymous ("someone says ...")
    anonymous_name: str = "someone"


@dataclass
class Context:
    settings: Settings = field(default_factory=Settings)
    broadcaster_login: str = ""
    third_party_emotes: frozenset[str] = frozenset()   # 7TV / BTTV / FFZ names for this channel, case-sensitive
    nicknames: dict[str, str] = field(default_factory=dict)        # login -> how to say it
    replacements: dict[str, str] = field(default_factory=dict)     # streamer's word replacement list
    ignored_users: frozenset[str] = frozenset()
    last_speaker: str = ""                # set by the caller to drop the name on back-to-back messages


@dataclass(frozen=True)
class Decision:
    speak: bool
    text: str        # exactly what goes to the voice ("" when speak is False)
    reason: str      # "ok" or why it was skipped / blocked
    tier: str        # "" | "tier1" | "tier2" | "tier3" | "custom" | "personal"
    notes: tuple[str, ...] = ()   # what was changed on the way (for the app's log)


class Cleaner:
    """Holds the loaded lists. Create once, call prepare() per message."""

    def __init__(self, blocklist: Blocklist | None = None) -> None:
        self.blocklist = blocklist or Blocklist.load_default()
        self.bots = {
            line.strip().lower()
            for line in (_DATA / "bots.txt").read_text("utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        self.emote_words = {k: v for k, v in json.loads((_DATA / "emote_words.json").read_text("utf-8")).items() if not k.startswith("_")}

    # -- helpers ---------------------------------------------------------------
    def _max_tier(self, s: Settings) -> int:
        return 3 if s.block_tier3 else (2 if s.block_tier2 else 1)

    def _text_from_fragments(self, message: dict, s: Settings, notes: list[str], broadcaster: str = "") -> tuple[str, int]:
        msg = message.get("message") or {}
        fragments = msg.get("fragments")
        if not fragments:
            return msg.get("text", "") or "", 0
        parts: list[str] = []
        removed = 0
        for frag in fragments:
            kind = frag.get("type", "text")
            text = frag.get("text", "")
            if kind == "text":
                parts.append(text)
            elif kind == "mention":
                m = frag.get("mention") or {}
                name = m.get("user_name") or text.lstrip("@")
                if s.drop_streamer_mention and broadcaster and (m.get("user_login") or name).lower() == broadcaster:
                    parts.append(" ")
                else:
                    parts.append(" " + (speech.sayable_name(name) or speech.sayable_name(m.get("user_login") or "") or "someone") + " ")
            elif kind == "emote":
                removed += 1
                if s.emotes == "famous" and text in self.emote_words:
                    parts.append(" " + self.emote_words[text] + " ")
                else:
                    parts.append(" ")
            else:  # cheermote, gif, anything new
                removed += 1
                parts.append(" ")
        if removed:
            notes.append(f"emotes:{removed}")
        return "".join(parts), removed

    def _drop_third_party(self, text: str, ctx: Context, notes: list[str]) -> tuple[str, int]:
        if not ctx.third_party_emotes:
            return text, 0
        s = ctx.settings
        kept = []
        removed = 0
        for word in text.split():
            if word in ctx.third_party_emotes:
                removed += 1
                if s.emotes == "famous" and word in self.emote_words:
                    kept.append(self.emote_words[word])
            else:
                kept.append(word)
        if removed:
            notes.append(f"third_party_emotes:{removed}")
        return " ".join(kept), removed

    # -- main entry point --------------------------------------------------------
    def prepare(self, message: dict, context: Context | None = None) -> Decision:
        ctx = context or Context()
        s = ctx.settings
        notes: list[str] = []
        login = (message.get("chatter_user_login") or "").lower()
        display = message.get("chatter_user_name") or login

        def skip(reason: str, tier: str = "") -> Decision:
            return Decision(False, "", reason, tier, tuple(notes))

        # 1. who
        if s.skip_bots and (login in self.bots or login in s.extra_bots or any(b.get("set_id") == "bot-badge" for b in message.get("badges") or ())):
            return skip("bot")
        if login and login in ctx.ignored_users:
            return skip("ignored_user")
        if s.skip_own_messages and login and login == ctx.broadcaster_login.lower():
            return skip("own_message")
        if s.skip_shared_chat and message.get("source_broadcaster_user_id") and str(message.get("source_broadcaster_user_id")) != str(message.get("broadcaster_user_id")):
            return skip("shared_chat")

        # 2. raw text without Twitch emotes, cheermotes, gifs
        raw, removed = self._text_from_fragments(message, s, notes, ctx.broadcaster_login.lower())
        if s.skip_commands and raw.lstrip().startswith("!"):
            return skip("command")
        raw, removed3 = self._drop_third_party(raw, ctx, notes)
        if not raw.strip():
            return skip("emote_only" if removed + removed3 else "empty")

        # 3. safety, on the canonical form of everything that was typed
        max_tier = self._max_tier(s)
        hit = self.blocklist.check(raw, max_tier)
        if hit:
            return skip(f"blocked:{hit.category}:{hit.how}", _tier(hit))
        if s.block_personal_details and speech.personal_details(raw) == "email":
            return skip("personal:email", "personal")

        # 4. links
        text, links = speech.handle_links(raw, s.links)
        if links:
            notes.append(f"links:{links}")
            if not re.search(r"\w", text.replace("link", "")):
                return skip("link_only")

        # 5. things no voice should attempt
        if speech.looks_like_art(text):
            return skip("ascii_art")
        text = speech.clean_characters(text)
        text, emoji_count = speech.handle_emoji(text, s.emoji)
        if emoji_count:
            notes.append(f"emoji:{emoji_count}")
        if not re.search(r"\w", text):
            return skip("emoji_only" if emoji_count else "no_words")
        if s.skip_non_latin and speech.latin_share(text) < 0.5:
            return skip("non_latin")

        # 6. names, and words split across the name and the message
        name_atoms = fold.atoms(display) + [a for a in fold.atoms(speech.sayable_name(display)) if a]
        say_name = s.say_name and ctx.last_speaker != login
        # a name is only a risk when it is spoken; with "say names" off it is not checked
        name_hit = s.say_name and (self.blocklist.check_atoms(fold.atoms(display), max_tier) or self.blocklist.check(speech.sayable_name(display), max_tier))
        if name_hit:
            if s.on_blocked_name == "skip":
                return skip(f"blocked_name:{name_hit.category}:{name_hit.how}", _tier(name_hit))
            display, say_name = "", say_name
            notes.append("name_hidden")
        if say_name and not name_hit:
            # a word split across the name and the start of the message
            msg_atoms = fold.atoms(text)
            edge = self.blocklist.check_atoms(name_atoms[-2:] + msg_atoms[:3], max_tier)
            if edge and edge.how in ("joined", "spelled"):
                return skip(f"blocked:{edge.category}:name_plus_message", _tier(edge))
        if s.block_personal_details:
            kind = speech.personal_details(text)
            if kind:
                return skip(f"personal:{kind}", "personal")

        # 7. make it sound right
        before = text
        streamer = frozenset({ctx.broadcaster_login.lower()}) if s.drop_streamer_mention and ctx.broadcaster_login else frozenset()
        text = speech.speak_mentions(text, streamer)
        text = speech.drop_emoticons(text)
        text = speech.collapse_repeats(text)
        text, deduped = speech.dedupe_phrases(text)
        if deduped:
            notes.append("repeats_removed")
        words = [w for w in text.split() if not speech.is_keyboard_mash(w.strip(".,!?"))]
        if len(words) != len(text.split()):
            notes.append("keyboard_mash_removed")
        text = " ".join(words)
        text = speech.soften_caps(text)
        for old, new in ctx.replacements.items():
            text = re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, text, flags=re.I)
        if s.numbers == "app":
            text = speech.speak_numbers(text)
        if s.expand_abbreviations:
            text = speech.expand_abbreviations(text)
        text = speech.tidy(text)
        if not re.search(r"\w", text):
            return skip("nothing_left")
        text, cut = speech.limit_words(text, s.max_words, s.cut_suffix)
        if cut:
            notes.append("cut")

        # 8. name
        if say_name:
            name = ctx.nicknames.get(login) or (speech.sayable_name(display) if display else "") or speech.sayable_name(login) or s.anonymous_name
            if name_hit:
                name = s.anonymous_name
            spoken = s.name_template.format(name=name, text=text)
        else:
            spoken = text

        # 9. safety again, on exactly what will be spoken (catches words made by the rewording)
        if spoken != before:
            hit = self.blocklist.check(spoken, max_tier)
            if hit:
                return skip(f"blocked:{hit.category}:after_cleanup", _tier(hit))
        return Decision(True, spoken, "ok", "", tuple(notes))


def _tier(hit) -> str:
    return "custom" if hit.category == "custom" else f"tier{hit.tier}"


_default: Cleaner | None = None


def prepare(message: dict, context: Context | None = None) -> Decision:
    """Module-level entry point using the bundled lists."""
    global _default
    if _default is None:
        _default = Cleaner()
    return _default.prepare(message, context)
