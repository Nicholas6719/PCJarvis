"""Standing watches: do they fire once, at the right moment, and survive?

The failure modes here are specific and all of them are quiet.

A process watch that fires the instant it is created, because the program has
not started yet and therefore is not running, is technically correct and
completely useless -- "tell me when the build is done" answered before the
build begins. So a process watch has to see the thing running before it can
see it stop.

A watch that fires twice is a broken timer. A watch that does not survive a
restart is a promise forgotten the moment you stop looking, which is worse than
never having made it, because you stopped holding the thing yourself.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psutil  # noqa: E402

from jarvis import standing  # noqa: E402

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}" + (f"   {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"   {detail}" if detail else ""))


def fake_processes(names):
    standing._running_processes = lambda: set(names)


def fake_battery(percent, plugged=True):
    psutil.sensors_battery = lambda: SimpleNamespace(
        percent=percent, power_plugged=plugged, secsleft=9999)


tmp = Path(tempfile.mkdtemp(prefix="jarvis_standing_"))
try:
    standing.configure(tmp)
    fake_battery(50)

    print("\n[process] must see it running before it can see it stop")
    fake_processes({"chrome"})
    standing.add("process", target="handbrake", description="when handbrake finishes")
    check("silent while it has never been seen", standing.check() == [])
    check("still held", len(standing.all_watches()) == 1)

    fake_processes({"chrome", "handbrake"})
    check("silent while it is running", standing.check() == [])

    fake_processes({"chrome"})
    said = standing.check()
    check("speaks when it exits", any("handbrake" in s.lower() for s in said),
          str(said))
    check("and is done with", not standing.all_watches())
    check("never speaks twice", standing.check() == [])

    print("\n[battery] both directions")
    standing.cancel()
    fake_battery(62)
    standing.add("battery", level=80, direction="at", description="at 80")
    check("silent below the level", standing.check() == [])
    fake_battery(81)
    said = standing.check()
    check("speaks on reaching it", any("81" in s for s in said), str(said))

    standing.add("battery", level=20, direction="below", description="below 20")
    fake_battery(45)
    check("silent above the floor", standing.check() == [])
    fake_battery(18)
    said = standing.check()
    check("speaks on falling to it", any("18" in s for s in said), str(said))

    print("\n[download] fires on the file it was told about")
    standing.cancel()
    standing.add("download", target="installer", description="installer")
    check("silent when something else lands",
          standing.check(["holiday.jpg"]) == [])
    said = standing.check(["installer.exe"])
    check("speaks for the right file",
          any("installer.exe" in s for s in said), str(said))

    standing.add("download", target="", description="next download")
    said = standing.check(["anything.zip"])
    check("an unnamed watch takes whatever lands next",
          any("anything.zip" in s for s in said), str(said))

    print("\n[keeping] they outlive the process that made them")
    standing.cancel()
    standing.add("process", target="blender", description="when blender finishes")
    standing.configure(tmp)          # as though JARVIS had restarted
    check("still there after a restart", len(standing.all_watches()) == 1,
          str(len(standing.all_watches())))
    check("and still not armed, so it cannot fire early",
          standing.all_watches()[0].get("armed") is False)

    print("\n[tidying] cancelling, and letting stale ones go")
    standing.add("battery", level=90, direction="at", description="at 90")
    check("cancel by description", len(standing.cancel("blender")) == 1)
    check("the other survives", len(standing.all_watches()) == 1)
    check("cancel everything", len(standing.cancel()) == 1)
    check("nothing left", not standing.all_watches())

    old = standing.add("process", target="ghost", description="ancient")
    old["created"] = time.time() - (8 * 24 * 3600)
    standing._save()
    standing.configure(tmp)
    check("a watch older than a week is dropped", not standing.all_watches())

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 66)
print(f" {passed} passed, {failed} failed")
print("=" * 66)
sys.exit(1 if failed else 0)
