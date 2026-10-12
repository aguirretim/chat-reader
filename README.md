# Chat Reader

Reads a Twitch channel's chat aloud on Windows with a voice that runs on the PC (Kokoro-82M, ONNX, CPU only).
No sign-in, no paid service. Version 0.1.0 is a first working build.

## Run from source

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
# put kokoro-v1.0.int8.onnx and voices-v1.0.bin in models\  (kokoro-onnx GitHub release "model-files-v1.0")
.venv\Scripts\python run.py
```

## Test, build, check

```
.venv\Scripts\python -m pytest -q            # 56 tests, no network, no sound
.venv\Scripts\python tools\mutation_check.py # breaks each fix in turn; its test must go red
.venv\Scripts\python tools\audio_check.py    # opens every speaker with silence
.\build.ps1                                  # dist\ChatReader\ChatReader.exe and dist\ChatReader.zip
.\tools\av_check.ps1 -Channel <busy channel> # marked zip, shell extract, double-click start, live chat, file fingerprint
```

## Layout

| Path | What |
|---|---|
| `chatreader\irc.py` | anonymous, read-only Twitch IRC; writes only CAP, NICK, JOIN, PONG |
| `chatreader\textclean\` | clean-up and safety step (copied from `..\spike\textclean`, 233 tests there) |
| `chatreader\speechqueue.py` | the waiting line: drop oldest, drop stale, drop repeats, remove deleted |
| `chatreader\controller.py` | chat -> clean-up -> line -> voice -> speaker; pause, skip, clear |
| `chatreader\tts.py`, `audio.py`, `hotkeys.py`, `emotes.py`, `config.py`, `ui.py` | voice, sound, hotkeys, emote names, settings, window + tray |

Test switches (environment): `CR_NULL_AUDIO=1` silence instead of speech, `CR_START_MINIMIZED=1`,
`CR_AUTOCONNECT=<channel>`, `CR_AUTOTEST=1`, `CR_TIMING_LOG=<file>` (marks and counts only, never chat text),
`CR_DATA_DIR=<folder>` (settings somewhere other than `%LOCALAPPDATA%\ChatReader`).

## Licence

GPL-3.0-or-later, because the bundle includes espeak-ng and phonemizer (both GPL-3.0). Kokoro weights are Apache-2.0.
