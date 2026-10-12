Chat Reader 0.1 (test build)

Reads a Twitch channel's chat aloud. The voice runs on this computer: no account,
no sign-in, no internet service to pay for.

HOW TO USE
  1. Double-click ChatReader.exe.
  2. Wait a few seconds until the "Start reading" button turns on.
  3. Type a channel name (or paste its twitch.tv link) and press Start reading.

  Test voice       says one sentence so you can set the volume and pick the speaker.
  Ctrl+Alt+F10     pause / resume
  Ctrl+Alt+F11     skip the message being read
  Ctrl+Alt+F12     stop and clear everything waiting
  The icon near the clock has the same controls. Closing the window quits.

WHAT IT DOES NOT READ
  Bots, "!" commands, links, emotes, messages a moderator deleted, messages from
  someone who was just timed out, and messages the safety filter blocks. The list
  in the window shows each message and what happened to it.

PRIVACY
  Chat is read the same way a logged-out viewer sees it. The app cannot post.
  Messages are shown in the window only; they are not saved or sent anywhere.
  To leave emotes out, the app asks 7TV, BetterTTV and FrankerFaceZ for the
  channel's emote names (only the channel's id is sent).
  Settings are kept in %LOCALAPPDATA%\ChatReader\settings.json.

IF WINDOWS OR YOUR ANTIVIRUS WARNS
  This test build is not signed. Windows may say "Windows protected your PC":
  click More info, then Run anyway. Keep the whole folder together; the program
  needs the files next to it.

LICENCES
  The voice is Kokoro-82M v1.0 (Apache-2.0), run with ONNX Runtime (MIT) through
  kokoro-onnx (MIT). Pronunciation uses espeak-ng and phonemizer (GPL-3.0), so
  this program as a whole is distributed under GPL-3.0-or-later. Word lists used
  by the safety filter have their licences in the "licenses" folder.
