r"""Compile and import every module, before anything is packaged.

This exists because a docstring containing C:\Users passed review, passed the
test suites -- which import only the modules they touch -- and would have
reached the packaged app. `\U` is an invalid escape, so the module raised
SyntaxError the moment anything imported it.

compileall catches syntax. Importing catches more: a bad `from x import y`, a
circular import, a name referenced at module scope that no longer exists. Both
run here, over the whole tree, so a broken edit fails at the gate rather than
in front of him.
"""
from __future__ import annotations

import compileall
import importlib
import io
import pkgutil
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Importing these starts real work -- audio devices, model loads. The gate is a
# static check; anything here is covered by the runtime suites instead.
SKIP = set()


def compile_tree() -> list[str]:
    failures: list[str] = []
    for folder in ("jarvis", "scripts"):
        target = ROOT / folder
        if not target.exists():
            continue
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            ok = compileall.compile_dir(str(target), quiet=2, force=True)
        if not ok:
            failures.append(f"{folder}: {buf.getvalue().strip() or 'compile failed'}")
    return failures


def import_tree() -> tuple[int, list[str]]:
    import jarvis

    failures: list[str] = []
    count = 0
    for mod in pkgutil.walk_packages(jarvis.__path__, prefix="jarvis."):
        name = mod.name
        if name in SKIP:
            continue
        count += 1
        try:
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                importlib.import_module(name)
        except Exception:
            failures.append(f"{name}\n"
                            + "".join(traceback.format_exc(limit=3)).rstrip())
    return count, failures


def main() -> int:
    print("=" * 62)
    print("  GATE  compile + import")
    print("=" * 62)

    compile_failures = compile_tree()
    print(f"\ncompile   {'FAIL' if compile_failures else 'ok'}")
    for f in compile_failures:
        print(f"   {f}")

    count, import_failures = import_tree()
    print(f"import    {'FAIL' if import_failures else 'ok'}  "
          f"({count} modules)")
    for f in import_failures:
        print(f"\n   {f}\n")

    total = len(compile_failures) + len(import_failures)
    print("\n" + "=" * 62)
    if total:
        print(f"  GATE FAILED -- {total} problem(s)")
        return 1
    print("  GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
