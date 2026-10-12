"""Memory and wait for three ways of running the voice, over 80 messages of mixed length.
Usage: arena_probe.py <default|same|off>
"""
import ctypes
import os
import random
import statistics as st
import sys
import time
from ctypes import wintypes

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import espeakng_loader  # noqa: E402
import onnxruntime as ort  # noqa: E402
from kokoro_onnx import Kokoro  # noqa: E402


def private_mb():
    class P(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("pf", wintypes.DWORD)] + [(n, ctypes.c_size_t) for n in "abcdefghi"]
    p = P(); p.cb = ctypes.sizeof(P)
    k = ctypes.windll.kernel32; k.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    ctypes.windll.psapi.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(p), p.cb)
    return round(p.i / 2**20)


mode = sys.argv[1]
opts = ort.SessionOptions()
opts.intra_op_num_threads = 4
opts.enable_cpu_mem_arena = mode != "off"
providers = [("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"})] if mode == "same" else ["CPUExecutionProvider"]
sess = ort.InferenceSession(os.path.join(ROOT, "models", "kokoro-v1.0.int8.onnx"), sess_options=opts, providers=providers)
k = Kokoro.from_session(sess, os.path.join(ROOT, "models", "voices-v1.0.bin"))
words = "the stream is great today and that play was wild can you believe how fast this boss went down lol what a time to be here".split()
rng = random.Random(7)
msgs = [" ".join(rng.choice(words) for _ in range(rng.choice([1, 2, 3, 5, 8, 12, 20, 30, 40]))) for _ in range(80)]
waits, mem = [], []
for i, m in enumerate(msgs):
    t = time.time()
    k.create("Someone says " + m, voice="af_heart")
    waits.append(time.time() - t)
    if i in (9, 39, 79):
        mem.append(private_mb())
print(f"{mode:8} wait median {st.median(waits):.2f}s  p90 {sorted(waits)[71]:.2f}s  max {max(waits):.2f}s | private MB after 10/40/80 messages: {mem}")
