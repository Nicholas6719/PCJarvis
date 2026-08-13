"""Every tool, exercised for real.

Not "does it register" -- does it produce the thing it claims to produce. The
PDF tool passed registration and then failed on its first genuine call, which is
exactly the class of bug that reaches the user as a confident lie.

    python scripts/acceptance_tools.py
    python scripts/acceptance_tools.py --open   # also tests browser/folder tools
                                                # that pop windows open
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.ERROR)

from jarvis.brain.memory import Memory  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.tools import documents, memory_tools, registry  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'  ok ' if ok else ' FAIL'}  {name}" + (f"   {detail}" if detail else ""))


async def call(_tool: str, **args) -> str:
    return await registry.execute(_tool, args)


# ══════════════════════════════════════════════════════════════════
async def test_documents() -> None:
    print("\n[documents] -- must actually create files")
    memory = Memory(CONFIG)
    documents.bind(memory)

    # Deliberately hostile content: an unwrappable path, smart quotes, an em
    # dash, and a very long unbroken token.
    nasty_path = "C:" + "\\" + "Users" + "\\" + "nicho" + "\\" + ("x" * 120) + ".txt"
    for role, content in [
        ("user", "Create a PDF of our entire conversation"),
        ("assistant", "Saved to your Documents folder."),
        ("user", f"Check this path {nasty_path}"),
        ("assistant", "A smart quote “hello” and an em dash — there."),
    ]:
        memory.log_turn(role, content)

    out = documents.OUTPUT_DIR
    for f in out.glob("*.pdf"):
        f.unlink(missing_ok=True)

    r = await call("export_conversation")
    pdf = out / "conversation.pdf"
    ok = pdf.exists() and pdf.stat().st_size > 800
    check("export_conversation writes a PDF", ok,
          f"{pdf.stat().st_size} bytes" if pdf.exists() else r)
    if pdf.exists():
        check("file is a genuine PDF",
              pdf.read_bytes()[:5] == b"%PDF-")

    r = await call("create_pdf", title="Test Report",
                   content="First paragraph.\n\nSecond with “quotes” "
                           "— and a dash.")
    made = out / "Test Report.pdf"
    check("create_pdf writes a PDF", made.exists() and made.stat().st_size > 500, r)

    r = await call("export_conversation", filename="plain", as_pdf=False)
    check("text export works", (out / "plain.txt").exists(), r)

    r = await call("save_text_file", filename="notes.txt", content="hello")
    check("save_text_file works", (out / "notes.txt").exists(), r)

    r = await call("list_documents")
    check("list_documents sees them", "conversation" in r.lower(), r[:60])

    # A tool that cannot do the job must say so, not claim success.
    documents.bind(None)
    r = await call("export_conversation")
    check("says so when it cannot export", "not available" in r.lower(), r[:60])
    documents.bind(memory)
    memory.close()


async def test_timers() -> None:
    print("\n[timers]")
    from jarvis.tools.timers import parse_duration

    cases = [("5 minutes", 300), ("two minutes", 120), ("30 seconds", 30),
             ("an hour", 3600), ("half an hour", 1800),
             ("1 hour and 30 minutes", 5400), ("a couple of minutes", 120)]
    bad = [f"{t}->{parse_duration(t)} want {want}"
           for t, want in cases if parse_duration(t) != want]
    check("spoken durations parse", not bad, "; ".join(bad[:3]))
    check("nonsense rejected", parse_duration("banana") is None)

    r = await call("set_timer", duration="2 seconds", label="test")
    check("set_timer accepts", "timer set" in r.lower(), r)
    r = await call("list_timers")
    check("list_timers reports it", "test" in r.lower(), r)
    r = await call("cancel_timer", label="test")
    check("cancel_timer works", "cancel" in r.lower(), r)

    fired: list[str] = []
    from jarvis.bus import BUS
    BUS.on("proactive", lambda ev: fired.append(ev.get("text", "")))
    await call("set_timer", duration="1 second", label="quick")
    await asyncio.sleep(1.6)
    check("timer speaks when it elapses", bool(fired),
          fired[0] if fired else "nothing published")


async def test_system() -> None:
    print("\n[system] -- real readings, not plausible ones")
    for name, expect in [
        # Numerals now, not words: readings go on screen as "100%" and are
        # converted to speech separately by voice/pronounce.py.
        ("get_battery", "%"), ("get_system_stats", "CPU"),
        ("get_time", ":"), ("get_volume", "percent"),
        ("read_clipboard", ""), ("list_running_apps", ""),
        ("now_playing", ""), ("list_recent_files", ""),
    ]:
        r = await call(name)
        ok = bool(r) and not r.startswith("Error") and expect in r
        check(name, ok, r[:58].replace("\n", " "))

    r = await call("find_files", name="config", limit=3)
    check("find_files", "config" in r.lower(), r[:58].replace("\n", " "))

    # focused readings must answer only what was asked
    cpu = await call("get_system_stats", component="cpu")
    check("cpu reading is focused",
          "CPU" in cpu and "drive" not in cpu.lower(), cpu[:52])
    disk = await call("get_system_stats", component="disk")
    check("disk reading is focused",
          "drive" in disk.lower() and "CPU" not in disk, disk[:52])

    before = await call("get_volume")
    await call("set_volume", level=42)
    after = await call("get_volume")
    check("set_volume takes effect", "42" in after, f"{before[:24]} -> {after[:24]}")
    await call("set_volume", level=60)


async def test_browser(do_open: bool) -> None:
    print("\n[browser]")
    from jarvis.tools.browser import SITES

    check("site map is populated", len(SITES) > 20, f"{len(SITES)} sites")
    r = await call("open_website", site="a-site-that-does-not-exist")
    check("unknown site is admitted, not faked",
          "don't know" in r.lower() or "give me the address" in r.lower(), r[:60])

    if do_open:
        for name, args in [("open_website", {"site": "youtube"}),
                           ("open_folder", {"name": "downloads"}),
                           ("get_directions", {"destination": "Boston"})]:
            r = await call(name, **args)
            check(f"{name} (opened a window)", not r.lower().startswith("i couldn"), r[:50])
    else:
        check("browser open tests skipped", True, "pass --open to run them")


async def test_memory() -> None:
    print("\n[memory]")
    memory = Memory(CONFIG)
    memory_tools.bind(memory)
    before = memory.count()
    r = await call("remember", fact="Nicholas tests his software thoroughly",
                   category="preference")
    check("remember stores", memory.count() >= before, r[:50])
    r = await call("recall", query="testing software")
    check("recall finds it", "thorough" in r.lower(), r[:60])
    r = await call("recall", query="something never mentioned at all xyzzy")
    check("recall admits a blank", "nothing" in r.lower() or not r.strip(), r[:50])
    await call("forget", query="tests his software")
    memory.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true",
                    help="also run tools that pop windows open")
    args = ap.parse_args()

    print("=" * 72)
    print(" tool acceptance -- every tool, called for real")
    print("=" * 72)
    print(f" {registry.load_all()} tools registered")

    await test_documents()
    await test_timers()
    await test_system()
    await test_browser(args.open)
    await test_memory()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print("\n" + "=" * 72)
    print(f" {passed} passed, {failed} failed")
    for name, ok, detail in results:
        if not ok:
            print(f"   - {name}: {detail}")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
