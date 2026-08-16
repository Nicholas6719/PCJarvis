"""Does he speak up when it matters, and stay quiet the rest of the time?

Restraint is the whole design problem with speaking unprompted, and restraint
is the hard thing to test: you cannot drain a battery on demand, and waiting
three hours to see whether one remark fires is not a test.

So the readings are faked and driven through deliberate sequences. What is
being checked is not "does it notice a low battery" -- that part is trivial --
but the things that actually go wrong with this kind of feature:

  * a threshold that is crossed once produces one remark, not one per tick
  * a value hovering on the line does not chatter
  * a condition that clears and returns is allowed to speak again
  * a dismissed JARVIS stays quiet about trivia and still speaks up about
    something urgent
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psutil  # noqa: E402

from jarvis.watch import Watcher  # noqa: E402

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


class Cfg:
    def __init__(self, **over):
        self.values = {
            "watch.battery_low": 20,
            "watch.battery_critical": 10,
            "watch.disk_full_percent": 92,
            "watch.memory_pressure_percent": 92,
            "watch.cpu_busy_percent": 85,
            "watch.cpu_busy_minutes": 2,
            "watch.interval_s": 60,
            "watch.long_session_hours": 3,
            "watch.downloads": True,
        }
        self.values.update(over)

    def get(self, key, default=None):
        return self.values.get(key, default)


def fake_battery(percent, plugged):
    psutil.sensors_battery = lambda: SimpleNamespace(
        percent=percent, power_plugged=plugged, secsleft=9999)


def ids(observations):
    return [o.id for o in observations]


# ── battery ────────────────────────────────────────────────────────
print("\n[battery] crossing a threshold speaks once, not every minute")
w = Watcher(Cfg())

fake_battery(80, False)
check("quiet at 80%", ids(w._check_power()) == [])

fake_battery(19, False)
check("speaks when it first drops below 20%",
      ids(w._check_power()) == ["battery_low"])

fake_battery(18, False)
check("silent at 18%", ids(w._check_power()) == [])
fake_battery(17, False)
check("still silent at 17%", ids(w._check_power()) == [])
fake_battery(21, False)
check("silent back at 21% (inside the re-arm gap)",
      ids(w._check_power()) == [])

fake_battery(9, False)
check("speaks again when it becomes critical",
      ids(w._check_power()) == ["battery_critical"])
fake_battery(8, False)
check("silent at 8%", ids(w._check_power()) == [])

fake_battery(95, True)
check("silent once plugged in", ids(w._check_power()) == [])

# Cleared properly, so a genuine recurrence is allowed to speak. The cooldown
# is what stops it being said twice in five minutes, so it is stepped over here.
w._gates["battery_low"].last_said = 0
fake_battery(19, False)
check("speaks again on a genuine second occurrence",
      ids(w._check_power()) == ["battery_low"])


# ── hovering ───────────────────────────────────────────────────────
print("\n[hovering] a value sitting on the line must not chatter")
w = Watcher(Cfg())
said = 0
for percent in (93, 91, 93, 92, 94, 91, 93):
    psutil.virtual_memory = lambda p=percent: SimpleNamespace(percent=p)
    said += len(w._check_memory())
check("one remark across seven ticks either side of the line",
      said == 1, f"said {said}")


# ── processor ──────────────────────────────────────────────────────
print("\n[processor] a spike is normal; only sustained load is worth a word")
w = Watcher(Cfg())
psutil.cpu_percent = lambda interval=None: 99
check("silent on the first busy tick", ids(w._check_processor()) == [])
check("speaks once load is sustained",
      ids(w._check_processor()) == ["cpu_busy"])
check("does not repeat while it stays busy",
      ids(w._check_processor()) == [])

w2 = Watcher(Cfg())
psutil.cpu_percent = lambda interval=None: 99
w2._check_processor()
psutil.cpu_percent = lambda interval=None: 4
check("a spike that subsides is never mentioned",
      ids(w2._check_processor()) == [])


# ── downloads ──────────────────────────────────────────────────────
print("\n[downloads] announces what lands, not what was already there")
import jarvis.folders as folders  # noqa: E402

tmp = Path(ROOT) / ".diag_downloads"
tmp.mkdir(exist_ok=True)
for stale in tmp.iterdir():
    stale.unlink()
(tmp / "already_here.zip").write_text("x")
folders.save_folder = lambda name: tmp

w = Watcher(Cfg())
check("first pass says nothing about existing files",
      ids(w._check_downloads()) == [])

(tmp / "installer.exe.crdownload").write_text("partial")
check("a download still in flight is not announced",
      ids(w._check_downloads()) == [])

(tmp / "installer.exe.crdownload").unlink()
(tmp / "installer.exe").write_text("done")
got = w._check_downloads()
check("announces the finished file", ids(got) == ["download_done"])
check("names it", got and "installer.exe" in got[0].text,
      got[0].text if got else "")

check("says nothing on the next tick", ids(w._check_downloads()) == [])

for f in tmp.iterdir():
    f.unlink()
tmp.rmdir()


# ── dismissed ──────────────────────────────────────────────────────
print("\n[dismissed] trivia waits; something urgent does not")
sleeping = SimpleNamespace(value="sleeping")
listening = SimpleNamespace(value="listening")

w = Watcher(Cfg(), state_getter=lambda: sleeping)
from jarvis.watch import Observation  # noqa: E402

check("holds back an ordinary observation while dismissed",
      w._may_speak(Observation("disk_full", "The disk is nearly full.")) is False)
check("lets a critical one through while dismissed",
      w._may_speak(Observation("battery_critical", "Battery is at 9%.",
                               critical=True)) is True)

w = Watcher(Cfg(), state_getter=lambda: listening)
check("says ordinary things when he is present",
      w._may_speak(Observation("disk_full", "The disk is nearly full.")) is True)


# ── switched off ───────────────────────────────────────────────────
print("\n[disabled] settings that turn an observation off")
w = Watcher(Cfg(**{"watch.downloads": False}))
check("downloads can be disabled", ids(w._check_downloads()) == [])
w = Watcher(Cfg(**{"watch.long_session_hours": 0}))
check("the long-session remark can be disabled",
      ids(w._check_session()) == [])


# -- shutting down ------------------------------------------------
print("\n[shutdown] stopping must be immediate, not eventual")

import asyncio  # noqa: E402
import time as _time  # noqa: E402


async def _stop_promptly():
    """The loop opens by waiting two minutes for the machine to settle.

    With a plain sleep there, closing JARVIS shut the window and unloaded
    the model and then sat holding 1.2 GB until that wait expired -- about
    a hundred seconds of a process that was supposed to be gone.
    """
    w = Watcher(Cfg())
    task = asyncio.create_task(w.run())
    await asyncio.sleep(0.3)
    t0 = _time.time()
    w.stop()
    try:
        await asyncio.wait_for(task, timeout=10)
    except asyncio.TimeoutError:
        return None
    return _time.time() - t0


took = asyncio.run(_stop_promptly())
check("stops during the startup wait", took is not None,
      "hung" if took is None else f"{took:.3f}s")
check("and stops within a second", took is not None and took < 1.0,
      f"{took:.3f}s" if took is not None else "hung")


print("\n" + "=" * 66)
print(f" {passed} passed, {failed} failed")
print("=" * 66)
sys.exit(1 if failed else 0)
