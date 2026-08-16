"""Things he is holding on to, and will tell you about when they happen.

    "Tell me when that download finishes."
    "Let me know when the build is done."
    "Tell me when it's charged."

This is the honest version of the autonomy the films show. JARVIS fabricates a
suit overnight; a laptop cannot do anything like that, and pretending otherwise
would be theatre. But the part that actually matters is smaller and completely
achievable: a request that outlives the sentence which created it. Everything
else he does begins and ends inside one exchange. These do not.

Four kinds, chosen because each is genuinely observable rather than guessed at:

    process     a named program exits -- builds, renders, installers, exports
    battery     the charge reaches a level, in either direction
    download    a file finishes arriving
    file        something appears in a folder

They fire once and then they are gone. A standing watch that keeps announcing
itself is a broken timer, and one that has to be cancelled after it fires is a
chore.

They also survive a restart, which is most of the point: the whole reason to
hand something to him is so you can stop holding it yourself, and a promise
forgotten when the application closes is worse than never having made it.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

log = logging.getLogger("jarvis.standing")

STORE: Path | None = None
_watches: list[dict] = []

# A watch nobody ever collects is a slow leak of promises. A week is long
# enough for anything worth waiting on and short enough that a forgotten one
# does not surface months later with no context.
MAX_AGE_S = 7 * 24 * 3600


def configure(data_dir: Path) -> None:
    global STORE
    STORE = Path(data_dir) / "watches.json"
    _load()


def _load() -> None:
    global _watches
    if STORE is None or not STORE.exists():
        return
    try:
        loaded = json.loads(STORE.read_text(encoding="utf-8"))
        _watches = [w for w in loaded if isinstance(w, dict)] if isinstance(loaded, list) else []
        expired = [w for w in _watches if time.time() - w.get("created", 0) > MAX_AGE_S]
        if expired:
            for w in expired:
                _watches.remove(w)
            log.info("dropped %d standing watch(es) older than a week", len(expired))
            _save()
    except Exception:
        log.debug("could not read standing watches", exc_info=True)


def _save() -> None:
    if STORE is None:
        return
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(_watches, indent=2), encoding="utf-8")
    except Exception:
        log.debug("could not write standing watches", exc_info=True)


# ── keeping them ───────────────────────────────────────────────────
def add(kind: str, target: str = "", level: float = 0.0,
        direction: str = "at", description: str = "") -> dict:
    watch = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "target": target,
        "level": float(level),
        "direction": direction,
        "description": description,
        "created": time.time(),
        # A process watch has to see the thing running before it can see it
        # stop. Without this, "tell me when the build is done" fires instantly
        # when the build has not started yet -- technically true, useless.
        "armed": kind != "process",
    }
    _watches.append(watch)
    _save()
    log.info("standing watch: %s", description or kind)
    return watch


def all_watches() -> list[dict]:
    return list(_watches)


def cancel(query: str = "") -> list[dict]:
    """Drop matching watches. An empty query drops all of them."""
    global _watches
    q = (query or "").lower().strip()
    if not q:
        dropped, _watches[:] = list(_watches), []
        _save()
        return dropped
    dropped = [w for w in _watches
               if q in w.get("description", "").lower()
               or q in w.get("target", "").lower()]
    for w in dropped:
        _watches.remove(w)
    _save()
    return dropped


# ── checking them ──────────────────────────────────────────────────
def _running_processes() -> set[str]:
    import psutil

    names = set()
    for p in psutil.process_iter(["name"]):
        name = (p.info.get("name") or "").lower()
        if name:
            names.add(name)
            if name.endswith(".exe"):
                names.add(name[:-4])
    return names


def check(arrived_downloads: list[str] | None = None) -> list[str]:
    """Everything that has just come true, as sentences he can say.

    Fired watches are removed here rather than by the caller, so a slow or
    failed announcement cannot cause the same thing to be reported twice.
    """
    if not _watches:
        return []

    said: list[str] = []
    done: list[dict] = []

    try:
        import psutil

        battery = psutil.sensors_battery()
        processes = _running_processes()
    except Exception:
        log.debug("could not read the machine for standing watches", exc_info=True)
        return []

    for w in list(_watches):
        kind = w.get("kind")
        try:
            if kind == "process":
                target = (w.get("target") or "").lower()
                running = any(target in name for name in processes)
                if running and not w.get("armed"):
                    # Seen it start. Only now can it meaningfully finish.
                    w["armed"] = True
                    _save()
                elif w.get("armed") and not running:
                    said.append(f"{w.get('target')} has finished.")
                    done.append(w)

            elif kind == "battery" and battery is not None:
                level = w.get("level", 0)
                if w.get("direction") == "below":
                    if battery.percent <= level:
                        said.append(f"Battery is down to {battery.percent:.0f}%.")
                        done.append(w)
                elif battery.percent >= level:
                    said.append(f"Battery has reached {battery.percent:.0f}%.")
                    done.append(w)

            elif kind == "download":
                for name in (arrived_downloads or []):
                    target = (w.get("target") or "").lower()
                    if not target or target in name.lower():
                        said.append(f"{name} has finished downloading.")
                        done.append(w)
                        break

            elif kind == "file":
                folder = Path(w.get("description_path") or w.get("target", ""))
                pattern = w.get("target", "")
                if folder.is_dir() and any(
                        pattern.lower() in p.name.lower()
                        for p in folder.iterdir() if p.is_file()):
                    said.append(f"{pattern} has appeared.")
                    done.append(w)
        except Exception:
            log.debug("standing watch %s failed to evaluate", w.get("id"),
                      exc_info=True)

    for w in done:
        if w in _watches:
            _watches.remove(w)
    if done:
        _save()
    return said
