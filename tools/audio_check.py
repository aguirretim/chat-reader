"""Open every WASAPI speaker the app lists and write a moment of SILENCE through the app's own play().
Proves named speakers work (they need Windows to resample 24 kHz). Makes no sound.
"""
import os
import sys
import threading

os.environ["CR_NULL_AUDIO"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

from chatreader import audio  # noqa: E402

done = threading.Event()
audio.start(lambda names, err: done.set())
done.wait(30)
names = audio.output_devices()
ok = 0
for name in [""] + names:
    try:
        used = audio.play(np.zeros(2400, dtype=np.float32), 24000, name, 80, threading.Event())
        ok += 1
    except Exception as exc:
        print(f"FAILED  {name or 'Windows default'}: {exc}")
print(f"{ok} of {len(names) + 1} speakers opened at 24 kHz (Windows default + {len(names)} named)")
sys.exit(0 if ok == len(names) + 1 else 1)
