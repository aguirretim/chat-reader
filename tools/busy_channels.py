"""Count chat messages per channel for a few seconds (counts only, read-only, anonymous).
Usage: busy_channels.py <seconds> <channel> [<channel> ...]
"""
import sys
import threading
import time

sys.path.insert(0, __file__.rsplit("\\", 2)[0])
from chatreader.irc import IrcClient  # noqa: E402

seconds = float(sys.argv[1])
counts = {c: 0 for c in sys.argv[2:]}
lock = threading.Lock()
clients = []
for ch in counts:
    def on_event(kind, data, ch=ch):
        if kind == "message":
            with lock:
                counts[ch] += 1
    client = IrcClient(ch, on_event)
    client.start()
    clients.append(client)
    time.sleep(0.6)
time.sleep(seconds)
for c in clients:
    c.stop()
for ch, n in sorted(counts.items(), key=lambda kv: -kv[1]):
    print(f"{n / seconds * 60:6.0f} messages/min  {ch}")
