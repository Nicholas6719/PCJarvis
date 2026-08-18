"""A browser JARVIS can act inside, not just launch.

open_website has always used the system's ordinary default browser --
webbrowser.open() -- and that is right for "just open YouTube": his own
profile, his own logins, out of JARVIS's way immediately. But it also means
JARVIS cannot click anything in it afterward, because Windows own
accessibility layer cannot see a web page's content at all. Confirmed
directly: asked Windows for every link on a real Wikipedia page, it found
none -- a browser's content is invisible to that whole approach unless the
browser is specifically told to expose it, which Chromium does not do by
default.

What Chromium DOES expose, deliberately, is the DevTools protocol -- the same
channel its own inspector uses -- and only to a browser process that was
started with a debug port open. That is the actual constraint this module
exists to work around: you cannot attach that channel to a browser already
running, only to one launched with it from the start. So this is not "JARVIS
reaching into your browser" -- it launches and keeps its own, separate,
persistent one, and clicks inside that. Verified end to end before writing a
line of the tool: launched Brave with the port open, asked its own
accessibility tree for a link by visible text (1815 named links on that same
Wikipedia page CDP could see and UIA could not), scrolled it into view,
clicked it with a synthetic mouse event, and confirmed by reading the URL
back afterward -- it had actually navigated.

The trade-off worth being honest about: this browser starts with no logins,
no history, nothing of his. That is not an oversight -- an assistant that can
click "Buy Now" should not default to doing it inside an account that can
actually buy something. For anything that needs his real identity, the honest
answer is to open it in his ordinary browser instead, which is what
open_website already does and continues to do unchanged.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.request
from pathlib import Path

from .refusals import DANGEROUS  # noqa: F401 -- re-exported for interact.py

log = logging.getLogger("jarvis.browsing")

# Deliberately not 9222, the default every tutorial and half the malware
# scanners on the machine already expect on that port. One that is
# unambiguously ours.
_PORT = 45_333

_CANDIDATES = (
    Path.home() / "AppData/Local/BraveSoftware/Brave-Browser/Application/brave.exe",
    Path("C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe"),
    Path("C:/Program Files (x86)/BraveSoftware/Brave-Browser/Application/brave.exe"),
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
)

_proc: subprocess.Popen | None = None
_browser_exe: Path | None = None


def _find_browser() -> Path | None:
    for path in _CANDIDATES:
        if path.exists():
            return path
    return None


def _profile_dir() -> Path:
    from .config import DATA_DIR

    d = DATA_DIR / "browser_profile"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _debug_url() -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{_PORT}/json/version",
                                    timeout=1.0) as r:
            json.loads(r.read())
        return f"http://127.0.0.1:{_PORT}"
    except Exception:
        return None


def _pids() -> set[int]:
    """This browser's process and every child it has spawned.

    Needed to tell our own window apart from an ordinary Brave he already has
    open -- process name alone cannot distinguish two Brave windows, but a
    window's owning PID either descends from ours or it does not.
    """
    if _proc is None:
        return set()
    try:
        import psutil

        root = psutil.Process(_proc.pid)
        return {root.pid} | {c.pid for c in root.children(recursive=True)}
    except Exception:
        return {_proc.pid}


def ensure_open() -> bool:
    """Make sure the managed browser is running and reachable. Idempotent."""
    global _proc, _browser_exe

    if _debug_url():
        return True

    if _browser_exe is None:
        _browser_exe = _find_browser()
    if _browser_exe is None:
        log.info("no Chromium-family browser found to manage")
        return False

    try:
        _proc = subprocess.Popen([
            str(_browser_exe),
            f"--remote-debugging-port={_PORT}",
            "--remote-allow-origins=*",
            f"--user-data-dir={_profile_dir()}",
            "--no-first-run", "--no-default-browser-check",
            "about:blank",
        ])
    except Exception:
        log.exception("could not launch the managed browser")
        return False

    for _ in range(50):        # ~10s
        if _debug_url():
            return True
        time.sleep(0.2)
    log.warning("managed browser launched but never opened its debug port")
    return False


def is_foreground() -> bool:
    """Is the window he is looking at right now this managed browser."""
    if _proc is None or _proc.poll() is not None:
        return False
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value in _pids()
    except Exception:
        return False


class _CDP:
    """One request/response round trip per call. This is not a persistent
    connection -- a page navigating or crashing between calls is normal, and
    reopening fresh each time is simpler than keeping a socket alive across
    that."""

    def __init__(self, ws_url: str):
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=8)
        self._id = 0

    def send(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self.ws.send(json.dumps(
            {"id": self._id, "method": method, "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self._id:
                if "error" in r:
                    raise RuntimeError(r["error"].get("message", "CDP error"))
                return r.get("result", {})

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def _active_tab() -> dict | None:
    base = _debug_url()
    if not base:
        return None
    with urllib.request.urlopen(f"{base}/json", timeout=3) as r:
        tabs = json.loads(r.read())
    pages = [t for t in tabs if t.get("type") == "page"]
    return pages[0] if pages else None


def navigate(url: str) -> bool:
    if not ensure_open():
        return False
    tab = _active_tab()
    if tab is None:
        return False
    cdp = _CDP(tab["webSocketDebuggerUrl"])
    try:
        cdp.send("Page.navigate", {"url": url})
        return True
    except Exception:
        log.exception("navigate failed")
        return False
    finally:
        cdp.close()


def current_url() -> str | None:
    tab = _active_tab()
    return tab.get("url") if tab else None


# Finds the element itself: exact text, then a case-insensitive substring,
# never further than that. A fuzzy match on a page full of decoys ("Buy now",
# "Buy now and save") is exactly where a wrong click costs real money.
_FIND_JS = r"""
(function(target) {
    const sel = "a, button, input[type=submit], input[type=button], "
              + "[role=button], [role=link], summary";
    const els = [...document.querySelectorAll(sel)];
    const visible = el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };
    const textOf = el => (el.innerText || el.value || el.getAttribute("aria-label")
                          || "").trim();

    let hit = els.find(el => visible(el) && textOf(el) === target);
    if (!hit) {
        const needle = target.toLowerCase();
        hit = els.find(el => visible(el)
                        && textOf(el).toLowerCase().includes(needle));
    }
    if (!hit) return null;

    hit.scrollIntoView({block: "center"});
    const r = hit.getBoundingClientRect();
    return {
        text: textOf(hit),
        href: hit.href || null,
        x: r.x + r.width / 2, y: r.y + r.height / 2,
    };
})(%s)
"""


def find_and_click(text: str) -> str:
    """(result sentence). Never raises -- every failure is a spoken reason."""
    if not ensure_open():
        return "I do not have a browser open that I can click into."

    tab = _active_tab()
    if tab is None:
        return "I do not have a page open to click on."

    cdp = _CDP(tab["webSocketDebuggerUrl"])
    try:
        cdp.send("Runtime.enable")
        js = _FIND_JS % json.dumps(text)
        result = cdp.send("Runtime.evaluate",
                          {"expression": js, "returnByValue": True})
        found = result.get("result", {}).get("value")

        if not found:
            return f"I do not see anything called {text!r} on this page."

        if DANGEROUS.search(found["text"]):
            return (f"{found['text']!r} looks like it would buy, delete or "
                    f"discard something. I will not click that one myself.")

        x, y = found["x"], found["y"]
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            cdp.send("Input.dispatchMouseEvent",
                    {"type": kind, "x": x, "y": y, "button": "left",
                     "clickCount": 1})
        return f"Clicked {found['text']!r}."
    except Exception as e:
        log.exception("click failed")
        return f"I found the page but could not click on it: {e}"
    finally:
        cdp.close()


def bring_to_front() -> bool:
    """Put the managed browser's window where he can see it.

    navigate() reusing an already-open tab does not raise any window at all
    -- there is no "new window appeared" for the existing open-things logic
    to notice, so a search after the first one would silently update a page
    sitting behind everything else.
    """
    if _proc is None:
        return False
    try:
        import pygetwindow as gw

        from .bus import BUS
        from .tools.browser import _force_foreground

        mine = _pids()
        for w in gw.getAllWindows():
            if not w.title.strip():
                continue
            try:
                import ctypes

                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(
                    w._hWnd, ctypes.byref(pid))
                if pid.value in mine:
                    BUS.emit_threadsafe("window.minimize")
                    return _force_foreground(w._hWnd)
            except Exception:
                continue
    except Exception:
        log.debug("could not bring the managed browser forward", exc_info=True)
    return False


def close() -> None:
    global _proc
    if _proc is not None:
        try:
            _proc.terminate()
        except Exception:
            pass
        _proc = None
