"""The desktop window.

pywebview hosts the interface in Windows' built-in WebView2, which means a real
frameless app window and full HTML/canvas rendering without dragging Electron
and a second toolchain into the project.

Threading: pywebview must own the main thread, so the asyncio loop -- and with
it the whole audio pipeline -- runs on a worker thread. Everything crossing
between them goes through run_coroutine_threadsafe or evaluate_js, never a
shared mutable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path

import webview

from ..bus import BUS
from ..config import BUNDLE, CONFIG, FROZEN

log = logging.getLogger("jarvis.ui")

WEB_DIR = BUNDLE / "jarvis" / "ui" / "web" if FROZEN else Path(__file__).resolve().parent / "web"


class Api:
    """Methods callable from JavaScript."""

    def __init__(self, bridge: "Bridge"):
        self._bridge = bridge

    def trigger(self) -> None:
        """Push-to-talk: wake him without the wake word."""
        self._bridge.emit("ui.trigger")

    def interrupt(self) -> None:
        """Stop him mid-sentence."""
        self._bridge.emit("ui.interrupt")

    def ask(self, text: str) -> None:
        """Typed input, for when speaking aloud is not an option."""
        self._bridge.ask(text)

    def set_mute(self, muted: bool) -> None:
        app = self._bridge.app
        if app and app.mic:
            app.mic.muted = bool(muted)

    def get_state(self) -> dict:
        app = self._bridge.app
        if not app:
            return {"state": "booting"}
        return {
            "state": app.state.value,
            "muted": bool(app.mic.muted) if app.mic else False,
            "voice": app.voice.voice if app.voice else "",
            "model": CONFIG.get("llm.model"),
            "memories": app.memory.count() if app.memory else 0,
        }

    def levels(self) -> dict:
        """Live audio levels, polled by the reactor animation."""
        app = self._bridge.app
        if not app:
            return {"mic": 0.0, "out": 0.0}
        return {
            "mic": round(float(app.mic.level), 4) if app.mic else 0.0,
            "out": round(float(app.player.envelope), 4) if app.player else 0.0,
        }

    def telemetry(self) -> dict:
        try:
            import psutil
            battery = psutil.sensors_battery()
            return {
                "cpu": psutil.cpu_percent(interval=None),
                "mem": psutil.virtual_memory().percent,
                "battery": round(battery.percent) if battery else None,
                "charging": bool(battery.power_plugged) if battery else False,
            }
        except Exception:
            return {}

    def toggle_fullscreen(self) -> bool:
        """F11 / the maximise button. Returns the new state."""
        if self._bridge.window:
            self._bridge.window.toggle_fullscreen()
            self._bridge.fullscreen = not self._bridge.fullscreen
        return self._bridge.fullscreen

    def minimize(self) -> None:
        if self._bridge.window:
            self._bridge.window.minimize()

    def quit(self) -> None:
        self._bridge.stop()


class Bridge:
    """Owns the asyncio loop and relays bus events into the page."""

    def __init__(self, args):
        self.args = args
        self.app = None
        self.window: webview.Window | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closing = False
        self.fullscreen = bool(CONFIG.get("ui.fullscreen", False))

    # ── loop thread ────────────────────────────────────────────────
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._boot_and_run())
        except Exception:
            log.exception("backend loop died")

    async def _boot_and_run(self) -> None:
        from ..main import Jarvis, setup_logging

        setup_logging(CONFIG.get("system.log_level", "INFO"))
        self.app = Jarvis(CONFIG)
        BUS.on("*", self._forward)

        if not await self.app.boot():
            self.push("boot_failed", {
                "message": "Ollama is not reachable. Run: ollama serve"})
            return

        self._ready.set()
        self.push("ready", await asyncio.to_thread(lambda: {}))
        await self.app.run(greet=not self.args.quiet)

    # ── bus -> page ────────────────────────────────────────────────
    def _forward(self, event: dict) -> None:
        kind = event.pop("event", "")
        if kind in ("wake.score",):  # far too chatty for the DOM
            return
        self.push(kind, event)

    def push(self, kind: str, payload: dict | None = None) -> None:
        # Once the window is gone, evaluate_js raises ObjectDisposedException
        # from the .NET side and pywebview logs a full traceback for each one.
        # During shutdown the pipeline is still emitting events, so without this
        # guard closing the app produces a wall of errors.
        if not self.window or self._closing:
            return
        try:
            data = json.dumps({"type": kind, **(payload or {})}, default=str)
            self.window.evaluate_js(f"window.onJarvis({data})")
        except Exception:
            log.debug("could not push %r to the page", kind)

    # ── page -> loop ───────────────────────────────────────────────
    def emit(self, kind: str, **payload) -> None:
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(BUS.emit(kind, **payload), self.loop)

    def ask(self, text: str) -> None:
        if self.app and self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.app.handle(text), self.loop)

    def stop(self) -> None:
        self._closing = True   # stop pushing into a window that is going away
        if self.app and self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.app.shutdown(), self.loop)
        time.sleep(0.3)
        if self.window:
            self.window.destroy()


def _register_hotkey(bridge: Bridge) -> None:
    """Global hotkey to wake him from any application."""
    combo = CONFIG.get("ui.hotkey", "ctrl+alt+j")
    if not combo:
        return
    try:
        import keyboard

        keyboard.add_hotkey(combo, lambda: bridge.emit("ui.trigger"))
        log.info("global hotkey: %s", combo)
    except Exception:
        log.warning("could not register the %s hotkey", combo)


def run_windowed(args) -> int:
    bridge = Bridge(args)
    api = Api(bridge)

    bridge.window = webview.create_window(
        "J.A.R.V.I.S.",
        str(WEB_DIR / "index.html"),
        js_api=api,
        width=CONFIG.get("ui.width", 1100),
        height=CONFIG.get("ui.height", 720),
        min_size=(720, 520),
        frameless=CONFIG.get("ui.frameless", True),
        fullscreen=CONFIG.get("ui.fullscreen", False),
        easy_drag=True,
        on_top=CONFIG.get("ui.always_on_top", False),
        background_color="#05070C",
        resizable=True,
    )

    bridge.start()
    _register_hotkey(bridge)

    webview.start(debug=False)
    bridge.stop()
    return 0
