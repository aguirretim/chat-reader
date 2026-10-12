"""Copy the licence text of every bundled component into the build's licenses\\ folder.
Usage: collect_licenses.py <out licenses folder>

Sources, in order: the licence files each installed package carries in its own metadata folder (exact
version shipped); files inside the package itself (onnxruntime); the Python installation; and the
canonical texts kept in third_party_licenses\\texts for components that ship none.
This does not choose or state a licence for Chat Reader itself.
"""
import shutil
import sys
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from components import RUNTIME, closure  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEXTS = ROOT / "third_party_licenses" / "texts"
out = Path(sys.argv[1])
pkgs = out / "packages"
names = ("LICEN", "COPYING", "NOTICE", "AUTHORS", "THIRDPARTY")
copied, without = 0, []

# the runtime packages and what they need, plus PyInstaller itself (its bootloader is inside ChatReader.exe;
# its own dependencies are build tools and are not shipped)
for dist in list(closure(RUNTIME).values()) + [metadata.distribution("pyinstaller")]:
    name = dist.metadata["Name"]
    found = [f for f in (dist.files or []) if ".dist-info" in str(f) and any(n in f.name.upper() for n in names)]
    if name == "ttkbootstrap":  # the Bootstrap Icons font it bundles has its own licence file
        found += [f for f in (dist.files or []) if str(f).replace("\\", "/") == "ttkbootstrap/assets/icons/LICENSE"]
    if name == "onnxruntime":   # keeps its texts inside the package folder
        found += [f for f in (dist.files or []) if str(f).replace("\\", "/") in ("onnxruntime/LICENSE", "onnxruntime/ThirdPartyNotices.txt")]
    if not found:
        without.append(name)
        continue
    for f in found:
        src = Path(dist.locate_file(f))
        parts = Path(str(f)).parts
        inside = parts[1:] if parts[0].endswith(".dist-info") else parts[-1:]      # path below the metadata folder
        if str(f).replace("\\", "/") == "ttkbootstrap/assets/icons/LICENSE":
            inside = ("Bootstrap-Icons-LICENSE",)
        rel = Path(*(inside[1:] if inside[0] == "licenses" and len(inside) > 1 else inside))
        dest = pkgs / f"{name}-{dist.version}" / rel
        if dest.exists():
            raise SystemExit(f"two licence files would land on {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copied += 1

base = Path(sys.base_prefix)
extra = [
    (base / "LICENSE.txt", "Python-LICENSE.txt"),
    (base / "tcl" / "tk8.6" / "license.terms", "Tcl-Tk-license.terms"),
    (TEXTS / "GPL-3.0.txt", "GPL-3.0.txt"),
    (TEXTS / "LGPL-3.0.txt", "LGPL-3.0.txt"),
    (TEXTS / "Apache-2.0.txt", "Apache-2.0.txt"),
    (TEXTS / "espeak-ng-COPYING.txt", "espeak-ng-1.52.0-COPYING.txt"),
    (TEXTS / "espeakng-loader-LICENSE.txt", "espeakng-loader-0.2.4-LICENSE.txt"),
    (TEXTS / "PortAudio-LICENSE.txt", "PortAudio-LICENSE.txt"),
    (TEXTS / "GPL-COMPONENT-SOURCES.txt", "GPL-COMPONENT-SOURCES.txt"),
]
missing = []
for src, name in extra:
    if src.exists():
        shutil.copyfile(src, out / name)
        copied += 1
    else:
        missing.append(str(src))
covered_elsewhere = {"espeakng-loader", "flatbuffers"}   # MIT text above; Apache-2.0 text above
uncovered = [n for n in without if n.lower() not in covered_elsewhere]
print(f"{copied} licence files copied; packages with no licence file of their own: {', '.join(without) or 'none'}")
if missing or uncovered:
    print("MISSING:", missing, "NO TEXT FOR:", uncovered)
    sys.exit(1)
