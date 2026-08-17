"""What the interface shows, gathered once and read many times.

The HUD displays things that live in half a dozen different places -- standing
watches, the memory trend, which application he has been in, how many facts are
remembered, whether he is at the desk. None of that is expensive on its own,
but the interface repaints from a writer thread that must never block: it also
drives the 30Hz level meter, and the audio pipeline sits behind it. Doing SQL
on that thread is how the microphone started dropping frames the first time.

So it is assembled here, on the watch tick that was already running anyway, and
cached. The writer thread only ever copies a dictionary.

Everything is defensive. A panel with no data draws empty; a panel that throws
would take the whole interface down with it, and an interface that goes dark
because the disk was briefly busy is worse than one missing a number.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("jarvis.hud")

_snapshot: dict = {}
_started = time.time()


def latest() -> dict:
    """The last snapshot. Cheap enough to call on the writer thread."""
    return dict(_snapshot)


def _watches() -> list[str]:
    try:
        from . import standing

        out = []
        for watch in standing.all_watches():
            kind = watch.get("kind", "")
            target = watch.get("target", "")
            level = watch.get("level")
            if kind == "process":
                out.append(f"{target} finishes")
            elif kind == "battery":
                out.append(f"battery reaches {level:.0f}%")
            elif kind == "download":
                out.append("a download finishes")
            elif kind == "disk":
                out.append(f"disk drops below {level:.0f}%")
            elif kind == "memory":
                out.append(f"memory drops below {level:.0f}%")
            else:
                out.append(target or kind)
        return out[:4]
    except Exception:
        log.debug("could not read the watches", exc_info=True)
        return []


def _context() -> dict:
    try:
        from . import history

        summary = history.app_summary(hours=8.0, limit=2)
        # app_summary returns a spoken sentence; the HUD wants the pieces.
        return {"summary": summary}
    except Exception:
        return {}


def _trend() -> dict:
    """Seven daily memory readings, for the sparkline."""
    try:
        from . import history

        if history._db is None:
            return {}
        rows = history._db.execute(
            "SELECT AVG(memory) v FROM readings "
            "WHERE at > ? AND memory IS NOT NULL "
            "GROUP BY CAST((? - at) / 86400 AS INT) "
            "ORDER BY CAST((? - at) / 86400 AS INT) DESC",
            (time.time() - 7 * 86400, time.time(), time.time())).fetchall()
        values = [round(r["v"], 1) for r in rows if r["v"] is not None]
        if not values:
            return {}
        return {"memory": values[-7:],
                "label": history.summarise("memory", 7.0)}
    except Exception:
        log.debug("could not read the trend", exc_info=True)
        return {}


def _facts() -> int:
    try:
        from pathlib import Path

        from .brain import vault
        from .config import CONFIG

        folder = Path(CONFIG.get(
            "memory.vault_path",
            str(Path.home() / "Documents" / "JARVIS" / "Memory")))
        return len(vault.scan(folder))
    except Exception:
        return 0


def _storage() -> dict:
    try:
        from pathlib import Path

        import psutil

        usage = psutil.disk_usage(str(Path.home().anchor or "C:\\"))
        return {"free_gb": round(usage.free / 1e9), "percent": usage.percent}
    except Exception:
        return {}


def _identity() -> dict:
    try:
        from .config import CONFIG
        from .tools.registry import REGISTRY

        return {
            "model": str(CONFIG.get("llm.model", "")).split(":")[0],
            "voice": CONFIG.get("tts.voice", ""),
            "tools": len(REGISTRY),
        }
    except Exception:
        return {}


def refresh(present: bool = True) -> dict:
    """Rebuild the snapshot. Called from the watch tick, never from the UI."""
    global _snapshot
    try:
        _snapshot = {
            "watches": _watches(),
            "context": _context(),
            "trend": _trend(),
            "facts": _facts(),
            "storage": _storage(),
            "present": bool(present),
            # Sent as a start time rather than a duration: the page ticks
            # it every second. Sending the elapsed value meant it only
            # changed when a new snapshot arrived, so it read "9s" for a
            # full minute -- the same staleness that made "at the desk"
            # untrustworthy.
            "started_at": _started,
            **_identity(),
        }
    except Exception:
        log.debug("hud snapshot failed", exc_info=True)
    return _snapshot
