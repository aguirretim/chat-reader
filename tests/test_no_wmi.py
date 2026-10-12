"""The app must not use Python's WMI helper: the installed app crashed inside _wmi.pyd at start."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = r"""
import sys, platform, subprocess
calls = []
import _wmi
real = _wmi.exec_query
class Spy:
    def exec_query(self, q):
        calls.append(q); return real(q)
platform._wmi = Spy()            # only matters if the query function still reaches the helper
spawned = []
real_popen = subprocess.Popen
def popen(*a, **k):
    spawned.append(a); return real_popen(*a, **k)
subprocess.Popen = popen
orig_query = platform._wmi_query
def counted(*a, **k):
    calls.append(a); return orig_query(*a, **k)
if "--plain" in sys.argv:
    platform._wmi_query = counted
else:
    from chatreader.__main__ import no_wmi
    no_wmi()
u = platform.uname()
platform.win32_ver(); platform.machine(); platform.platform()
print(u.system, u.release, u.machine, len(calls), len(spawned))
"""


def run(*args):
    out = subprocess.run([sys.executable, "-c", PROBE, *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_platform_answers_without_wmi_and_without_starting_a_program():
    system, release, machine, wmi_calls, spawned = run()
    assert system == "Windows" and release and machine in ("AMD64", "ARM64", "x86")
    assert wmi_calls == "0" and spawned == "0"


def test_the_probe_can_see_wmi_being_used():
    """Without the fix the same questions do go to WMI, so the test above is able to fail."""
    assert int(run("--plain")[3]) > 0