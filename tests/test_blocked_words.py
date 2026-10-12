"""Switching safety groups and single words on and off. Words are picked from the list itself, never written here."""
import json

import pytest

from chatreader import config
from chatreader.config import AppConfig
from chatreader.controller import Controller
from chatreader.textclean.blocklist import Blocklist
from chatreader.textclean.pipeline import Cleaner
from chatreader.wordlist import masked, visible_rows

FULL = Blocklist.load_default()


def sample(tier: int, skip: int = 0) -> str:
    """A plain word from this group that the full list really catches as this group."""
    found = 0
    for word, t, _ in FULL.entries():
        if t == tier and word.isascii() and word.isalpha() and len(word) >= 5:
            hit = FULL.check(word, 3)
            if hit and hit.tier == tier:
                if found == skip:
                    return word
                found += 1
    raise AssertionError(f"no sample word for group {tier}")


def make(**cfg):
    return Controller(AppConfig(fetch_emote_lists=False, **cfg), engine=object(), play=lambda *a: None, cleaner=Cleaner(FULL))


def blocked(ctl, word: str) -> bool:
    return ctl._cleaner.blocklist.check(f"well {word} then", 3) is not None


def test_defaults_block_hate_and_strong_but_not_mild():
    ctl = make()
    assert blocked(ctl, sample(1)) and blocked(ctl, sample(2))
    assert not blocked(ctl, sample(3))


@pytest.mark.parametrize("tier, setting", [(1, "block_hate"), (2, "block_strong")])
def test_a_group_can_be_switched_off_and_back_on(tier, setting):
    ctl = make()
    word = sample(tier)
    setattr(ctl.cfg, setting, False)
    ctl.apply_config()
    assert not blocked(ctl, word)
    other = 2 if tier == 1 else 1
    assert blocked(ctl, sample(other)), "switching one group off must leave the other on"
    setattr(ctl.cfg, setting, True)
    ctl.apply_config()
    assert blocked(ctl, word)


def test_mild_group_can_be_switched_on():
    ctl = make(block_mild_swearing=True)
    assert blocked(ctl, sample(3))


def test_one_word_can_be_switched_off_without_touching_its_group():
    ctl = make()
    first, second = sample(2, 0), sample(2, 1)
    ctl.cfg.words_off = [first]
    ctl.apply_config()
    assert not blocked(ctl, first)
    assert blocked(ctl, second)
    ctl.cfg.words_off = []
    ctl.apply_config()
    assert blocked(ctl, first)


def test_own_word_is_blocked_even_with_every_group_off():
    ctl = make(block_hate=False, block_strong=False, words_added=["pineapple"])
    assert blocked(ctl, "pineapple")
    assert not blocked(ctl, sample(1))
    ctl.cfg.words_added = []
    ctl.apply_config()
    assert not blocked(ctl, "pineapple")


def test_the_full_list_is_still_listed_after_groups_are_switched_off():
    ctl = make(block_hate=False, words_off=[sample(2)])
    assert len(ctl.blocked_words()) == len(FULL.entries())


def test_personal_details_switch_reaches_the_cleaner():
    ctl = make(block_personal=False)
    assert ctl.ctx.settings.block_personal_details is False


def test_word_lists_survive_a_save_and_a_load(tmp_path, monkeypatch):
    monkeypatch.setenv("CR_DATA_DIR", str(tmp_path))
    cfg = AppConfig(block_hate=False, words_off=["a", "b"], words_added=["pineapple"])
    cfg.save()
    back = AppConfig.load()
    assert (back.block_hate, back.words_off, back.words_added) == (False, ["a", "b"], ["pineapple"])
    (config.data_dir() / "settings.json").write_text(json.dumps({"words_off": "oops", "words_added": [1, 2]}), "utf-8")
    back = AppConfig.load()
    assert back.words_off == [] and back.words_added == []


def test_table_rows_filter_by_search_and_group():
    entries = [("apple", 1, "hate"), ("apricot", 2, "strong"), ("banana", 3, "mild")]
    assert [r[0] for r in visible_rows(entries, ["cherry"], "", 0)] == ["cherry", "apple", "apricot", "banana"]
    assert [r[0] for r in visible_rows(entries, ["cherry"], "ap", 0)] == ["apple", "apricot"]
    assert [r[0] for r in visible_rows(entries, ["cherry"], "", 2)] == ["apricot"]
    assert [r[0] for r in visible_rows(entries, ["cherry"], "", -1)] == ["cherry"]
    assert masked("banana") == "b*****"
