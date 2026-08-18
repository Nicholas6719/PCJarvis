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
import logging
import threading
from pathlib import Path

import webview

from ..bus import BUS
from ..config import BUNDLE, CONFIG, FROZEN
from .bridge import UIChannel

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

    def toggle_fullscreen(self) -> bool:
        """F11 / the maximise button. Returns the new state."""
        self._bridge.channel.window_op("toggle_fullscreen")
        return not self._bridge.channel.fullscreen_active

    def minimize(self) -> None:
        self._bridge.channel.window_op("minimize")

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
        self.fullscreen = bool(CONFIG.get("ui.fullscreen", True))
        self._is_fullscreen = self.fullscreen
        self._minimized = False
        self.channel = UIChannel(lambda: self.window, lambda: self.app)
        self.channel.want_fullscreen = bool(CONFIG.get("ui.fullscreen", True))
        self.channel.fullscreen_active = self.channel.want_fullscreen

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
        self.channel.start()

        if not await self.app.boot():
            self.push("boot_failed", {
                "message": "Ollama is not reachable. Run: ollama serve"})
            return

        # The engine emits state; the window reacts. Keeping these decoupled
        # means the audio loop never touches window handles.
        BUS.on("window.minimize", lambda _: self.minimize())
        BUS.on("window.restore", lambda _: self.restore())
        BUS.on("app.quit", lambda _: self._quit_from_engine())

        self._ready.set()
        self.push("ready", await asyncio.to_thread(lambda: {}))

        if getattr(self.args, "selftest", False):
            await self._selftest()
            return

        await self.app.run(greet=not self.args.quiet)

    # ── bus -> page ────────────────────────────────────────────────
    def _forward(self, event: dict) -> None:
        kind = event.pop("event", "")
        if kind in ("wake.score",):  # far too chatty for the DOM
            return
        self.push(kind, event)

    def push(self, kind: str, payload: dict | None = None) -> None:
        """Hand an event to the writer thread. Never blocks the event loop.

        This used to call evaluate_js directly from the loop, which stalled the
        audio pipeline on the UI thread -- the interface fell behind and so did
        the listener.
        """
        if self._closing:
            return
        self.channel.send(kind, payload)

    # ── page -> loop ───────────────────────────────────────────────
    def emit(self, kind: str, **payload) -> None:
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(BUS.emit(kind, **payload), self.loop)

    def ask(self, text: str) -> None:
        if self.app and self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.app.handle(text), self.loop)

    async def _selftest(self) -> None:
        """Hammer the window transitions that crashed, against the real
        WebView2, while the writer thread is pushing at 30Hz."""
        import time as _time

        log.info("SELFTEST: exercising window transitions")

        # Nothing this drives is conversation. Recording it put "thank you,
        # go to sleep" and "return to wake mode" at the top of the transcript
        # he later exported to PDF, as though he had said them.
        self.app.record_turns = False
        failures = 0
        for cycle in range(4):
            try:
                # Keep the UI busy so the two threads genuinely contend.
                for i in range(20):
                    self.push("selftest", {"cycle": cycle, "i": i})

                self.minimize()
                await asyncio.sleep(1.2)
                self.restore()
                await asyncio.sleep(1.2)
                log.info("SELFTEST: cycle %d survived", cycle + 1)
            except Exception:
                failures += 1
                log.exception("SELFTEST: cycle %d FAILED", cycle + 1)

        # The voice paths, driven through the real turn handler against the
        # real window: dismissal must minimise and report SLEEPING, waking must
        # restore full screen, and shutdown must actually close the app.
        from ..state import State

        for phrase in ["thank you, go to sleep", "return to wake mode"]:
            try:
                await self.app.handle(phrase)
                await asyncio.sleep(1.4)
                ok = (self.channel.minimized
                      and self.app.state is State.SLEEPING)
                log.info("SELFTEST: %r -> minimised=%s state=%s  %s",
                         phrase, self.channel.minimized,
                         self.app.state.value, "OK" if ok else "WRONG")
                if not ok:
                    failures += 1

                self.restore()
                await asyncio.sleep(1.4)
                log.info("SELFTEST: restored -> minimised=%s fullscreen=%s",
                         self.channel.minimized, self.channel.fullscreen_active)
                if self.channel.minimized or not self.channel.fullscreen_active:
                    failures += 1
            except Exception:
                failures += 1
                log.exception("SELFTEST: voice dismissal FAILED")

        # The file paths, run through the real tools inside the packaged
        # process. A screenshot 'saved to the Desktop' reported success
        # while landing in an unredirected folder that never appears on
        # screen, and every check passed because both folders exist. So
        # this asserts the file reaches the desktop Windows actually
        # renders, and that find_files can then locate what it wrote.
        try:
            from ..folders import save_folder
            from ..tools.documents import create_pdf
            from ..tools.files import find_files
            from ..tools.system import take_screenshot

            desktop = save_folder('desktop')
            before = set(desktop.glob('*'))

            said_shot = await asyncio.to_thread(take_screenshot, 'desktop')
            said_pdf = await asyncio.to_thread(
                create_pdf, 'Selftest', 'Written by the packaged app.',
                'jarvis_selftest', 'desktop')
            log.info('SELFTEST: screenshot -> %s', said_shot)
            log.info('SELFTEST: pdf        -> %s', said_pdf)

            written = sorted(set(desktop.glob('*')) - before)
            log.info('SELFTEST: %d new file(s) on %s', len(written), desktop)
            if len(written) < 2:
                failures += 1
                log.error('SELFTEST: expected 2 files on the visible '
                          'desktop, saw %d', len(written))

            located = await asyncio.to_thread(find_files, 'jarvis_selftest')
            if 'jarvis_selftest' in located:
                log.info('SELFTEST: find_files located what it just wrote')
            else:
                failures += 1
                log.error('SELFTEST: find_files missed it -- %s', located)

            for f in written:
                try:
                    f.unlink()
                except OSError:
                    pass
            log.info('SELFTEST: removed %d test file(s)', len(written))
        except Exception:
            failures += 1
            log.exception('SELFTEST: file paths FAILED')

        # click_button depends on pywinauto's dynamic comtypes bindings for
        # UI Automation, which is exactly the kind of runtime code generation
        # PyInstaller is known to mishandle. Proven correct against source
        # already -- seven, plus, three, equals, verified by reading 7+3=10
        # back off the real Calculator -- but that proves nothing about
        # whether the frozen exe's bundled comtypes can do the same thing.
        try:
            import subprocess

            from ..tools.interact import click_button

            subprocess.Popen(["calc.exe"])
            await asyncio.sleep(2.0)

            for digit in ("seven", "plus", "three", "equals"):
                said = await asyncio.to_thread(click_button, digit, "calc")
                log.info("SELFTEST: click_button(%r) -> %s", digit, said)
                if not said.startswith("Clicked"):
                    failures += 1
                    log.error("SELFTEST: click_button(%r) did not click "
                             "anything", digit)
                await asyncio.sleep(0.3)

            from ..tools.interact import list_clickable

            seen = await asyncio.to_thread(list_clickable, "calc")
            if "10" not in seen and "Ten" not in seen:
                # The result is not a named control, so read it back through
                # the same OCR path a real question would use -- the honest
                # end-to-end check rather than trusting the click alone.
                # Calculator is the foreground window at this point, so the
                # default (whole_screen=False) reads exactly that.
                from ..tools.system import read_screen

                text = await asyncio.to_thread(read_screen)
                if "10" not in text:
                    failures += 1
                    log.error("SELFTEST: expected 7+3=10 on screen, "
                             "read: %s", text[:200])
                else:
                    log.info("SELFTEST: confirmed 7+3=10 on screen via OCR")

            subprocess.run(["taskkill", "/F", "/IM", "CalculatorApp.exe"],
                          capture_output=True)
        except Exception:
            failures += 1
            log.exception("SELFTEST: click_button FAILED")

        # The web-page half rests on websocket-client actually working once
        # frozen, which is exactly the same category of risk pywinauto's
        # comtypes bindings were -- unverified until proven against the real
        # exe. Proven correct against source already: found a real link by
        # its visible text on a live Wikipedia page, scrolled it into view,
        # clicked it with a synthetic mouse event through the DevTools
        # protocol, and confirmed by reading the URL back afterward that it
        # had actually navigated.
        try:
            from .. import browsing

            opened = await asyncio.to_thread(
                browsing.navigate, "https://en.wikipedia.org/wiki/Spider-Man")
            if not opened:
                failures += 1
                log.error("SELFTEST: could not open the managed browser")
            else:
                await asyncio.sleep(2.0)
                clicked = await asyncio.to_thread(
                    browsing.find_and_click, "Marvel Comics")
                log.info("SELFTEST: find_and_click('Marvel Comics') -> %s",
                         clicked)
                await asyncio.sleep(1.5)
                url = await asyncio.to_thread(browsing.current_url)
                if url and "Marvel_Comics" in url:
                    log.info("SELFTEST: confirmed navigation via CDP -- %s",
                             url)
                else:
                    failures += 1
                    log.error("SELFTEST: expected the Marvel_Comics page, "
                             "landed on %s", url)

            # The refusal has to hold on a real click attempt too, not just
            # the regex in isolation -- but Wikipedia has no "Buy Now" button
            # to test it against, and asserting on one that might not exist
            # would make this test meaningless. A page written here, rather
            # than a real site, is guaranteed to have exactly the button
            # being tested against regardless of what any real site does
            # with its own layout tomorrow.
            test_page = ("data:text/html,<button>Buy Now</button>"
                        "<button>Learn more</button>")
            if await asyncio.to_thread(browsing.navigate, test_page):
                await asyncio.sleep(1.0)
                refused = await asyncio.to_thread(
                    browsing.find_and_click, "Buy Now")
                if "will not click" not in refused:
                    failures += 1
                    log.error("SELFTEST: 'Buy Now' was not refused: %s",
                             refused)
                else:
                    log.info("SELFTEST: 'Buy Now' correctly refused")

                allowed = await asyncio.to_thread(
                    browsing.find_and_click, "Learn more")
                if not allowed.startswith("Clicked"):
                    failures += 1
                    log.error("SELFTEST: an ordinary button was refused: %s",
                             allowed)
        except Exception:
            failures += 1
            log.exception("SELFTEST: web click FAILED")
        finally:
            await asyncio.to_thread(browsing.close)

        # And the state the interface reports must match reality.
        log.info("SELFTEST: minimized=%s fullscreen_active=%s",
                 self.channel.minimized, self.channel.fullscreen_active)
        log.info("SELFTEST: %s",
                 "PASSED -- no crash across 4 minimise/restore cycles"
                 if failures == 0 else f"FAILED with {failures} errors")
        self.app.record_turns = True
        _time.sleep(0.5)
        self.stop()

    def _quit_from_engine(self) -> None:
        """He was told to shut down. Close the window and end the process.

        Runs on the asyncio thread, so the actual teardown is handed to a
        plain thread: destroying the window from here would be the same
        cross-thread mistake that crashed restore.
        """
        threading.Thread(target=self.stop, daemon=True).start()

    # ── window control ─────────────────────────────────────────────
    def minimize(self) -> None:
        """He has been dismissed. Get out of the way, keep listening.

        Queued rather than performed here: this runs on the asyncio thread,
        and touching the window from anywhere but the UI writer thread races
        with the 30Hz evaluate_js and takes the process down.
        """
        if not self._closing:
            self.channel.window_op("minimize")

    def restore(self) -> None:
        """Woken. Come back full screen and to the front."""
        if not self._closing:
            self.channel.window_op("restore")

    def stop(self) -> None:
        """Full teardown. Waits for it, so Ollama is actually released.

        The previous version fired the shutdown coroutine and slept 0.3s,
        which was never long enough to unload a model -- the process exited
        first and left the weights resident.
        """
        self._closing = True   # stop pushing into a window that is going away
        self.channel.close()
        if self.app and self.loop and self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self.app.shutdown(), self.loop)
            try:
                future.result(timeout=15)
            except Exception:
                log.debug("shutdown did not complete cleanly", exc_info=True)
        if self.window:
            try:
                self.window.destroy()
            except Exception:
                pass


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
        fullscreen=CONFIG.get("ui.fullscreen", True),
        easy_drag=True,
        on_top=CONFIG.get("ui.always_on_top", False),
        background_color="#05070C",
        resizable=True,
    )

    bridge.start()
    _register_hotkey(bridge)

    # Closing the window by any means -- the button, Alt+F4, the taskbar --
    # lands here, so the teardown is guaranteed rather than hoped for.
    webview.start(debug=False)
    bridge.stop()

    # Belt and braces: if the loop was already gone, stop Ollama directly.
    try:
        from ..health import stop_ollama

        if CONFIG.get("llm.stop_ollama_on_exit", True):
            stop_ollama()
    except Exception:
        pass
    return 0
