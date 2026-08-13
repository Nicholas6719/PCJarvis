"""Dismissal, self-shutdown, and the phrases that must reach neither.

Three outcomes have to stay cleanly separated:

  DISMISS        stand down, stay resident, wake word still works
  SHUTDOWN_SELF  close the application entirely
  neither        anything about the *computer*, which is destructive and
                 must go through the model and a spoken confirmation

The dangerous confusion is the third one. "Go to sleep" said to JARVIS means
JARVIS; "put the computer to sleep" means the laptop. Getting that wrong once
suspended the machine unasked.

    python scripts/diag_dismiss.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.main import DISMISS, SHUTDOWN_SELF  # noqa: E402

DISMISSALS = [
    "that's all",
    "that's all, go to sleep",
    "thank you, go to sleep",
    "thank you very much, go to sleep",
    "thanks, go to sleep",
    "good work, go to sleep",
    "good job, go to sleep",
    "nice work, go to sleep",
    "well done, go to sleep",
    "excellent, go to sleep",
    "perfect, that's all",
    "return to wake mode",
    "thank you, return to wake mode",
    "thanks, return to wake mode",
    "go back to wake mode",
    "no, go to sleep",
    "ok that's all",
    "alright, stand down",
    "jarvis, that's all",
    "cheers, goodbye",
    "got it, thanks, that's all",
    "stop listening",
    "we're done",
    "never mind",
    "that's it",
]

SHUTDOWNS = [
    "shut down",
    "shutdown",
    "jarvis, shut down",
    "thank you, shut down",
    "good work, shut down",
    "shut yourself down",
    "shut down jarvis",
    "exit",
    "quit",
    "power down",
    "power off",
    "close yourself",
    "close the app",
    "terminate",
    "ok, shut down",
]

# Must reach the model: destructive, and about the machine rather than JARVIS.
NEITHER = [
    "shut down my computer",
    "shut down the pc",
    "shut down my laptop",
    "please shut down the computer",
    "put the computer to sleep",
    "make my laptop go to sleep",
    "restart the computer",
    "what time do I go to sleep",
    "that's all I know about Rome",
    "stop the music",
    "close Spotify",
    "quit Notepad",
    "never mind the weather, what's my battery",
    "exit the browser",
    "turn off the lights",
]


def main() -> int:
    failures: list[str] = []

    print("=" * 70)
    print(" dismissal / self-shutdown / neither")
    print("=" * 70)

    print("\n[dismiss] stand down but stay resident")
    for text in DISMISSALS:
        ok = bool(DISMISS.match(text)) and not SHUTDOWN_SELF.match(text)
        if not ok:
            failures.append(f"dismiss missed: {text!r}")
        print(f"  {'ok  ' if ok else 'FAIL'} {text}")

    print("\n[shutdown] close the application")
    for text in SHUTDOWNS:
        ok = bool(SHUTDOWN_SELF.match(text))
        if not ok:
            failures.append(f"shutdown missed: {text!r}")
        print(f"  {'ok  ' if ok else 'FAIL'} {text}")

    print("\n[neither] must reach the model (destructive / unrelated)")
    for text in NEITHER:
        d, s = bool(DISMISS.match(text)), bool(SHUTDOWN_SELF.match(text))
        ok = not d and not s
        if not ok:
            failures.append(
                f"wrongly caught: {text!r} "
                f"({'DISMISS' if d else ''}{'SHUTDOWN' if s else ''})")
        print(f"  {'ok  ' if ok else 'FAIL'} {text}"
              + ("" if ok else f"   <- {'DISMISS' if d else 'SHUTDOWN'}"))

    total = len(DISMISSALS) + len(SHUTDOWNS) + len(NEITHER)
    print("\n" + "=" * 70)
    print(f" {total - len(failures)}/{total} passed")
    for f in failures:
        print(f"   - {f}")
    print("=" * 70)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
