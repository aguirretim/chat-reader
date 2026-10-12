"""Find which step fails when the app's files sit under a folder with non-English letters.
Usage: nonascii_probe.py <folder holding a copy of dist\\ChatReader> <step: data|lib|model|voices>
Each step moves ONE thing to the odd folder and keeps the rest on plain paths. Prints OK or dies.
"""
import os
import sys

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import espeakng_loader  # noqa: E402
import onnxruntime as ort  # noqa: E402
from kokoro_onnx import Kokoro  # noqa: E402
from kokoro_onnx.config import EspeakConfig  # noqa: E402

from chatreader import tts  # noqa: E402

odd, step = sys.argv[1], sys.argv[2]
internal = os.path.join(odd, "_internal")
data = espeakng_loader.get_data_path()
lib = espeakng_loader.get_library_path()
model = os.path.join(ROOT, "models", tts.MODEL_FILE)
voices = os.path.join(ROOT, "models", tts.VOICES_FILE)
if step == "data":
    data = tts.ascii_espeak_data(os.path.join(internal, "espeakng_loader", "espeak-ng-data"))
    print("data path handed to espeak:", data.encode("unicode_escape").decode(), flush=True)
elif step == "lib":
    lib = os.path.join(internal, "espeakng_loader", "espeak-ng.dll")
elif step == "model":
    model = os.path.join(odd, "models", tts.MODEL_FILE)
elif step == "voices":
    voices = os.path.join(odd, "models", tts.VOICES_FILE)
print(f"step {step}: starting", flush=True)
sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
k = Kokoro.from_session(sess, voices, espeak_config=EspeakConfig(lib_path=lib, data_path=data))
samples, rate = k.create("Testing one two three.", voice="af_heart")
print(f"step {step}: OK, {len(samples) / rate:.1f} s of audio", flush=True)
