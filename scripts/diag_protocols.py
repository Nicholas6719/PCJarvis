"""Named protocols: the forgiving bits, and the bits that must not be forgiving.

Two things matter here and they pull in opposite directions.

The naming has to be loose, because he is speaking: "work", "work mode" and
"the work protocol" are one thing and none of them should need to be said
exactly. But the loose phrasing must not swallow everything shaped like two
words -- "aeroplane mode" is not a protocol, and answering it with a dead end
is worse than letting the model handle it. That is what the intent guard is
for, and it is the first thing this checks.

The other is that a protocol must refuse to hold anything irreversible. Canon's
Clean Slate protocol destroys every suit Tony owns on one spoken phrase, which
is marvellous in a film and indefensible here.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jarvis.brain.intents import match  # noqa: E402
from jarvis.tools import protocols, registry  # noqa: E402

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


async def main() -> int:
    registry.load_all()

    print("\n[naming] loose enough to speak, tight enough to be safe")
    # A bare "work" is deliberately NOT enough. Tony says "initiate the House
    # Party protocol", never "house party", and a single common word claiming
    # the turn would swallow half of ordinary conversation.
    for phrase in ("initiate the work protocol", "run work", "work mode",
                   "engage work", "the work protocol"):
        got = match(phrase)
        check(f"claims {phrase!r}",
              bool(got) and got[0] == "run_protocol"
              and got[1].get("name", "").strip() == "work",
              str(got[1]) if got else "no match")

    print("\n[guard] undefined phrases fall through to the model")
    for phrase in ("aeroplane mode", "initiate the house party protocol",
                   "start the dishwasher", "run a diagnostic",
                   "engage warp drive", "work"):
        got = match(phrase)
        check(f"leaves {phrase!r} alone", got is None,
              str(got[0]) if got else "")

    print("\n[safety] nothing irreversible may be saved into a phrase")
    for bad in ("shutdown_computer", "sleep_computer", "run_command"):
        said = protocols.create_protocol("danger", json.dumps([{"tool": bad}]))
        check(f"refuses {bad}", "will not put" in said, said[:52])
    check("and did not save it", not protocols.exists("danger"))

    print("\n[validation] bad definitions are rejected, not stored")
    check("rejects unparseable steps",
          "JSON" in protocols.create_protocol("x", "not json"))
    check("rejects an unknown tool",
          "no tool called" in protocols.create_protocol(
              "x", json.dumps([{"tool": "nope"}])))
    check("rejects an empty list",
          "at least one step" in protocols.create_protocol("x", "[]"))
    check("rejects a nameless protocol",
          "name" in protocols.create_protocol("", json.dumps([{"tool": "get_time"}])))

    print("\n[running] steps execute, failures are reported not swallowed")
    protocols.create_protocol(
        "selftest",
        json.dumps([{"tool": "get_battery", "args": {}, "report": True}]),
        "Check the battery.")
    said = await protocols.run_protocol("selftest")
    check("runs and reports", "Selftest protocol." in said, said[:60])
    check("no step count when nothing failed", " of " not in said, said[:60])

    data = protocols._load()
    data["selftest"]["steps"].append({"tool": "vanished", "args": {}})
    protocols._save(data)
    said = await protocols.run_protocol("selftest")
    check("names what failed", "vanished" in said, said[:70])
    check("still ran the good step", "Battery" in said, said[:70])

    said = await protocols.run_protocol("no such thing")
    check("unknown protocol lists what exists", "I know:" in said, said[:60])

    print("\n[tidy] deleting works")
    check("deleted", "Deleted" in protocols.delete_protocol("selftest"))
    check("gone", not protocols.exists("selftest"))
    check("shipped defaults survived", protocols.exists("work")
          and protocols.exists("good night"))

    print("\n" + "=" * 66)
    print(f" {passed} passed, {failed} failed")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
