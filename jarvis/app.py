"""Standalone application entry point.

This is what the packaged JARVIS.exe runs. It differs from `jarvis.main` in the
things a double-clicked application has to do for itself and a command line
does not:

  - there is no console, so a crash before the window opens would otherwise be
    completely silent; failures are shown in a message box and written to the log
  - Ollama cannot be assumed to be running, so it is started if absent
  - the integrated-GPU flag must be set in this process's environment before
    Ollama is launched, or inference silently falls back to the CPU
  - only one copy should run at a time; a second launch surfaces the first
"""
from __future__ import annotations

import asyncio
import ctypes
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # running from the frozen bundle
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MUTEX_NAME = "Global\\JARVIS_SingleInstance_Mutex"
OLLAMA_PORT = 11434


def message_box(title: str, text: str, style: int = 0x10) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, style)
    except Exception:
        print(f"{title}: {text}")


def already_running() -> bool:
    """A named mutex is the standard Windows way to enforce one instance."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def ollama_up() -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", OLLAMA_PORT)) == 0


def start_ollama() -> bool:
    """Launch Ollama and wait for it to answer. Returns False on timeout."""
    if ollama_up():
        return True

    exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
    if not exe.exists():
        from shutil import which
        found = which("ollama")
        if not found:
            return False
        exe = Path(found)

    try:
        subprocess.Popen(
            [str(exe), "serve"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False

    for _ in range(40):  # up to ~20 seconds
        if ollama_up():
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    multiprocessing.freeze_support()

    if already_running():
        message_box("J.A.R.V.I.S.",
                    "JARVIS is already running.\n\n"
                    "Look for the window, or press Ctrl+Alt+J.", 0x40)
        return 0

    # Must be set before Ollama starts. Ollama detects the Radeon 780M but drops
    # it for being integrated unless this is present -- the difference between
    # inference on the GPU and on the CPU.
    os.environ.setdefault("OLLAMA_IGPU_ENABLE", "1")
    # Memory settings, all measured on this machine. The 780M takes its VRAM
    # out of system RAM, so the model costs roughly 8 GB of a 15 GB laptop --
    # these keep that from becoming the whole machine.
    os.environ.setdefault("OLLAMA_KEEP_ALIVE", "15m")
    os.environ.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
    os.environ.setdefault("OLLAMA_NUM_PARALLEL", "1")
    os.environ.setdefault("OLLAMA_FLASH_ATTENTION", "1")
    os.environ.setdefault("OLLAMA_KV_CACHE_TYPE", "q8_0")

    # Reclaim anything a previous run leaked before starting a new server,
    # otherwise the machine can be several gigabytes down before we begin.
    try:
        from jarvis.health import reap_orphaned_model_hosts

        killed, freed = reap_orphaned_model_hosts()
        if killed:
            print(f"reclaimed {freed:.1f} GB from {killed} orphaned "
                  f"model host(s)")
    except Exception:
        pass

    if not start_ollama():
        message_box(
            "J.A.R.V.I.S. cannot start",
            "Ollama is not running and could not be started.\n\n"
            "Install it from https://ollama.com, then launch JARVIS again.")
        return 1

    try:
        from jarvis.main import amain, build_arg_parser, setup_logging
        from jarvis.config import CONFIG

        setup_logging(CONFIG.get("system.log_level", "INFO"))

        # The same flags jarvis.main's CLI takes, plus --selftest: a
        # packaged-app-only diagnostic (see ui/window.py's Bridge.run) that
        # has no place in the plain command-line entry point.
        parser = build_arg_parser()
        parser.add_argument("--selftest", action="store_true",
                            help="exercise window transitions, then exit")
        args = parser.parse_args()

        if args.no_ui or args.say or args.ask:
            try:
                return asyncio.run(amain(args))
            except KeyboardInterrupt:
                return 0

        from jarvis.ui.window import run_windowed
        return run_windowed(args)

    except Exception:
        import traceback
        detail = traceback.format_exc()
        try:
            from jarvis.config import LOGS_DIR
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            (LOGS_DIR / "crash.log").write_text(detail, encoding="utf-8")
            where = f"\n\nWritten to {LOGS_DIR / 'crash.log'}"
        except Exception:
            where = ""
        message_box("J.A.R.V.I.S. crashed",
                    f"{detail[-1200:]}{where}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
