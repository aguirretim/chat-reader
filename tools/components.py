"""List every third-party component the build contains, with the licence its own package metadata declares.
GPL and LGPL components are listed first. Usage: components.py <out.txt>
The licence of Chat Reader itself is NOT decided here; this file is the input for that decision.
"""
import sys
from importlib import metadata

# what PyInstaller bundles for this app (the runtime requirements and their dependencies), not the dev tools
RUNTIME = ["kokoro-onnx", "onnxruntime", "sounddevice", "pystray", "pillow", "numpy", "ttkbootstrap"]
NOT_FROM_PIP = [
    ("Python 3.12 runtime + standard library", "PSF License 2.0", "python.org"),
    ("Tcl/Tk 8.6 (window toolkit)", "Tcl/Tk licence (BSD-style)", "bundled with Python"),
    ("espeak-ng library and data (inside the espeakng-loader package)", "GPL-3.0-or-later", "github.com/espeak-ng/espeak-ng"),
    ("PortAudio (inside the sounddevice package)", "MIT-style PortAudio licence", "portaudio.com"),
    ("PyInstaller bootloader (the .exe start-up stub)", "GPL-2.0-or-later WITH a bootloader exception that allows any licence for the app", "pyinstaller.org"),
    ("Kokoro-82M v1.0 voice model (int8 ONNX) and voices file", "Apache-2.0", "huggingface.co/hexgrad/Kokoro-82M; files from the kokoro-onnx GitHub release"),
    ("Safety-filter word lists", "see the four licence files next to this one", "sources named in those files"),
]


def licence_of(dist) -> str:
    md = dist.metadata
    expr = md.get("License-Expression")
    if expr:
        return expr
    classifiers = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(classifiers)
    lic = (md.get("License") or "").strip().splitlines()
    return lic[0][:80] if lic else "not declared in package metadata"


def closure(names):
    seen, todo = {}, list(names)
    while todo:
        name = todo.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = dist
        for req in dist.requires or []:
            if "extra ==" in req:
                continue
            marker = req.split(";", 1)[1] if ";" in req else ""
            if "sys_platform" in marker and "win32" not in marker and "!=" not in marker:
                continue   # a dependency for another operating system
            todo.append(req.split(";")[0].split("[")[0].strip().split()[0].split("=")[0].split(">")[0].split("<")[0].split("!")[0].split("~")[0])
    return seen


if __name__ == "__main__":
    rows = [(d.metadata["Name"], d.version, licence_of(d)) for d in closure(RUNTIME).values()]
    rows += [(name, "", lic + "  (" + src + ")") for name, lic, src in NOT_FROM_PIP]
    copyleft = [r for r in rows if "GPL" in r[2].upper() or "GNU" in r[2].upper()]
    other = [r for r in rows if r not in copyleft]
    lines = [
        "Third-party components inside this build of Chat Reader",
        "Licences are as declared by each package's own metadata on the build PC; they were not legally reviewed.",
        "",
        "GPL / LGPL components (these affect how the whole program may be distributed):",
    ]
    lines += [f"  {n} {v}: {lic}".replace("  :", ":") for n, v, lic in sorted(copyleft, key=lambda r: r[0].lower())]
    lines += ["", "Other components:"]
    lines += [f"  {n} {v}: {lic}" for n, v, lic in sorted(other, key=lambda r: r[0].lower())]
    lines += ["", "The licence of Chat Reader itself has not been chosen. That decision is the author's, before any copy is given to someone else."]
    open(sys.argv[1], "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"{len(rows)} components listed, {len(copyleft)} GPL/LGPL")
