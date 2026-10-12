import os

import pytest

from chatreader import tts


def make_data(folder):
    folder.mkdir(parents=True)
    (folder / "phontab").write_bytes(b"x")
    return str(folder)


def test_plain_path_is_used_as_it_is(tmp_path):
    data = make_data(tmp_path / "espeak-ng-data")
    assert tts.ascii_espeak_data(data) == data


def test_non_english_folder_gets_a_plain_path_that_still_has_the_data(tmp_path, monkeypatch):
    data = make_data(tmp_path / "José Muñoz" / "espeak-ng-data")
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    safe = tts.ascii_espeak_data(data)
    assert safe.isascii() and os.path.exists(os.path.join(safe, "phontab"))


def test_copy_route_when_the_drive_keeps_no_short_names(tmp_path, monkeypatch):
    data = make_data(tmp_path / "数据" / "espeak-ng-data")
    monkeypatch.setattr(tts, "_short_path", lambda p: "")
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    safe = tts.ascii_espeak_data(data)
    assert safe == str(tmp_path / "pd" / "ChatReader" / "espeak-ng-data")
    assert os.path.exists(os.path.join(safe, "phontab"))


def test_clear_message_when_nothing_works(tmp_path, monkeypatch):
    data = make_data(tmp_path / "José" / "espeak-ng-data")
    monkeypatch.setattr(tts, "_short_path", lambda p: "")
    monkeypatch.setenv("ProgramData", str(tmp_path / "Zoë"))
    with pytest.raises(tts.VoiceError, match="plain English letters"):
        tts.ascii_espeak_data(data)
