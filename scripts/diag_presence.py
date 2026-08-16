"""Presence: does he hold things back, and say them once when you return?

The bug being prevented is quiet and specific. Something finishes while nobody
is at the desk, he announces it, the room is empty, and the queue is cleared --
so it is never mentioned again. You were told he would tell you, and he did,
to nobody.

What gets checked here is the asymmetry and the arithmetic around it. Deciding
he has left must take minutes, because a pause to read is not an absence.
Deciding he is back must be instant, because one keystroke is unambiguous. And
a return must produce the missed items exactly once.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jarvis import briefing, presence, quiet  # noqa: E402

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
        self.values = {"presence.enabled": True, "presence.away_after_s": 300}
        self.values.update(over)

    def get(self, key, default=None):
        return self.values.get(key, default)


def idle(seconds):
    presence.idle_seconds = lambda: float(seconds)


tmp = Path(tempfile.mkdtemp(prefix="jarvis_presence_"))
real_idle = presence.idle_seconds
try:
    quiet.configure(tmp, expire_hours=12)

    print("\n[leaving] a pause is not an absence")
    p = presence.Presence(Cfg())
    for seconds in (0, 30, 120, 299):
        idle(seconds)
        p.update()
    check("still present after nearly five minutes", p.present() is True)

    idle(301)
    p.update()
    check("away once the threshold passes", p.present() is False)
    check("no return to report yet", p.take_return() is False)

    print("\n[returning] one keystroke is enough")
    idle(0)
    p.update()
    check("back immediately", p.present() is True)
    check("the return is reported", p.take_return() is True)
    check("and only once", p.take_return() is False,
          "a second briefing would be a repeat")

    print("\n[holding] what happens while nobody is there")
    quiet.take_deferred()
    quiet.defer("download_done", "installer.exe has finished downloading.")
    quiet.defer("disk_full", "The disk is at 93%. Worth clearing some space.")
    said = briefing.missed()
    check("says what was missed", "installer.exe" in said, said[:70])
    check("only the first sentence of each", "Worth clearing" not in said,
          said[:70])
    check("nothing left afterwards", briefing.missed() == "",
          "reporting twice is worse than not reporting")

    print("\n[nothing missed] silence is the common answer")
    check("no return produces no words", briefing.missed() == "")

    print("\n[disabled] the switch actually switches it off")
    off = presence.Presence(Cfg(**{"presence.enabled": False}))
    idle(9999)
    off.update()
    check("always present when disabled", off.present() is True)
    check("so nothing is ever held", off.take_return() is False)

    print("\n[real machine] the reading is genuinely available")
    presence.idle_seconds = real_idle
    seconds = presence.idle_seconds()
    check("Windows reports an idle time", seconds >= 0.0, f"{seconds:.1f}s")

finally:
    presence.idle_seconds = real_idle
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 66)
print(f" {passed} passed, {failed} failed")
print("=" * 66)
sys.exit(1 if failed else 0)
