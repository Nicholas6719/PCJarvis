"""Outbound channel from the engine to the page.

This exists because of a specific bug. `window.evaluate_js()` is a blocking
cross-thread marshal into WebView2, and it was being called straight from the
asyncio event loop for every bus event. So the loop that runs wake detection,
VAD and endpointing stalled on the UI thread -- the interface fell behind, and
worse, the listener did too. The UI said "listening" while the pipeline was
several hundred milliseconds in the past.

So nothing touches the WebView from the event loop any more. Events are dropped
into a queue (never blocks) and a single writer thread drains it, coalescing
whatever accumulated into ONE evaluate_js call per tick. Level updates for the
reactor are generated here too, at a fixed rate, rather than the page polling
back across the bridge sixteen times a second.

Two rules make it keep up:
  - High-frequency samples (audio levels) are *replaced* rather than queued, so
    a slow tick can never build a backlog of stale meter readings.
  - Discrete events (state changes, transcripts, tool calls) are never dropped,
    because missing one leaves the interface permanently wrong.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time

log = logging.getLogger("jarvis.ui.bridge")

TICK_HZ = 30           # UI update rate
LEVEL_HZ = 30          # reactor meter rate
TELEMETRY_EVERY = 2.0  # seconds
MAX_BATCH = 40         # events flushed per tick before we yield


class UIChannel:
    def __init__(self, window_getter, app_getter):
        self._window = window_getter
        self._app = app_getter
        self._events: queue.Queue[dict] = queue.Queue(maxsize=2000)
        self._latest_levels: dict | None = None
        self._levels_lock = threading.Lock()
        self._closing = False
        self._thread: threading.Thread | None = None
        self._last_telemetry = 0.0
        self._dropped = 0
        # Window operations are queued, not performed by the caller.
        # minimize/restore/toggle_fullscreen each marshal onto the UI thread
        # with Invoke, and so does evaluate_js -- two threads racing for the
        # same UI thread, one of them at 30Hz, is a crash waiting to happen.
        # Exactly one thread touches the window: this one.
        self._ops: queue.Queue = queue.Queue()
        self.minimized = False
        self.want_fullscreen = True
        self.fullscreen_active = True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._pump, daemon=True,
                                        name="ui-writer")
        self._thread.start()

    def close(self) -> None:
        self._closing = True

    # ── producers (called from the event loop; must never block) ───
    def send(self, kind: str, payload: dict | None = None) -> None:
        """Queue a discrete event. Returns immediately."""
        if self._closing:
            return
        try:
            self._events.put_nowait({"type": kind, **(payload or {})})
        except queue.Full:
            # The page is wedged. Drop rather than block the audio pipeline --
            # a late meter reading is survivable, a stalled listener is not.
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("UI queue full; dropped %d events", self._dropped)

    def window_op(self, name: str) -> None:
        """Queue a window operation for the writer thread to perform."""
        if not self._closing:
            self._ops.put(name)

    def set_levels(self, mic: float, out: float) -> None:
        """Replace the current meter reading. Never queues -- always latest."""
        with self._levels_lock:
            self._latest_levels = {"mic": round(mic, 4), "out": round(out, 4)}

    # ── writer thread ──────────────────────────────────────────────
    def _pump(self) -> None:
        interval = 1.0 / TICK_HZ
        while not self._closing:
            started = time.monotonic()
            try:
                self._flush()
            except Exception:
                log.debug("ui flush failed", exc_info=True)
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, interval - elapsed))

    def _perform(self, window, op: str) -> None:
        """Run one window operation. Only ever called from the writer thread."""
        if op == "minimize":
            window.minimize()
            self.minimized = True
            self.fullscreen_active = False   # Windows drops the style
            log.info("minimised to wake mode")
        elif op == "restore":
            if self.minimized:
                window.restore()
                self.minimized = False
                time.sleep(0.35)          # let Windows finish restoring

            # Ask the PAGE whether it is actually full screen rather than
            # trusting a flag. Windows may restore a minimised window to its
            # previous full-screen state or to a plain one, and toggling blind
            # got it wrong half the time -- which is why waking him came back
            # windowed.
            if self.want_fullscreen:
                for _ in range(3):
                    if self._is_really_fullscreen(window):
                        self.fullscreen_active = True
                        break
                    window.toggle_fullscreen()
                    time.sleep(0.35)
                else:
                    log.warning("could not force full screen on restore")
            log.info("restored (full screen: %s)", self.fullscreen_active)

        elif op == "toggle_fullscreen":
            window.toggle_fullscreen()
            self.fullscreen_active = not self.fullscreen_active

    def _is_really_fullscreen(self, window) -> bool:
        """Measure it, do not assume it.

        The page knows its own viewport, so comparing that against the screen
        is the one check that cannot drift out of sync with reality.
        """
        try:
            result = window.evaluate_js(
                "(window.innerHeight >= screen.height - 4 && "
                " window.innerWidth  >= screen.width  - 4)")
            return bool(result)
        except Exception:
            log.debug("could not measure the window", exc_info=True)
            return False

    def _flush(self) -> None:
        window = self._window()
        if window is None:
            return

        # Window state first, and never interleaved with a JS call.
        while True:
            try:
                op = self._ops.get_nowait()
            except queue.Empty:
                break
            try:
                self._perform(window, op)
            except Exception:
                log.warning("window op %r failed", op, exc_info=True)

        batch: list[dict] = []
        for _ in range(MAX_BATCH):
            try:
                batch.append(self._events.get_nowait())
            except queue.Empty:
                break

        # Sample the meters ourselves rather than making the page ask.
        app = self._app()
        if app is not None:
            mic = float(getattr(app.mic, "level", 0.0)) if app.mic else 0.0
            out = float(getattr(app.player, "envelope", 0.0)) if app.player else 0.0
            self.set_levels(mic, out)

        with self._levels_lock:
            levels = self._latest_levels
            self._latest_levels = None
        if levels:
            batch.append({"type": "levels", **levels})

        now = time.monotonic()
        if now - self._last_telemetry >= TELEMETRY_EVERY:
            self._last_telemetry = now
            telemetry = _telemetry()
            if telemetry:
                batch.append({"type": "telemetry", **telemetry})

            # Assembled on the watch tick; this is a dictionary copy and
            # nothing more. See jarvis/hud.py for why.
            try:
                from ..hud import latest

                panels = latest()
                if panels:
                    batch.append({"type": "hud", **panels})
            except Exception:
                log.debug("no hud snapshot available", exc_info=True)

        if not batch:
            return

        try:
            window.evaluate_js(
                f"window.onJarvisBatch({json.dumps(batch, default=str)})")
        except Exception:
            # Window closing, or the page has not loaded yet. Neither is worth
            # a traceback per event.
            log.debug("evaluate_js failed for %d events", len(batch))


def _telemetry() -> dict:
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
