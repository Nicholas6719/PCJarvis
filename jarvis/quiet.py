"""When he holds his tongue.

Two separate mechanisms, because they answer two different complaints.

**Quiet hours** are for "not now, generally". You say goodnight and he stops
volunteering things until you say good morning. Nothing is lost -- the
observations still happen, he simply keeps them to himself -- and anything
genuinely urgent still comes through, because a battery about to die at 3am is
worth waking up for and a disk at 93% is not.

**Snoozing** is for "not that, specifically". One observation has become
annoying and you want it gone without silencing everything else.

Both persist, because both would be useless otherwise: quiet hours you have to
re-declare after every restart is not a night mode, and an observation you
silence at nine that returns at ten past has not been silenced.

Quiet hours expire on their own after a while. Saying goodnight and forgetting
to say good morning should not mean he never speaks again, and the failure mode
of a permanently mute assistant is much worse than one that starts talking
again a little early.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("jarvis.quiet")

STORE: Path | None = None          # set by configure()
_state: dict = {"quiet_since": 0.0, "expires_at": 0.0,
                "snoozed": {}, "deferred": []}
_expire_hours = 12.0


def configure(data_dir: Path, expire_hours: float = 12.0) -> None:
    global STORE, _expire_hours
    STORE = Path(data_dir) / "quiet.json"
    _expire_hours = float(expire_hours)
    _load()


def _load() -> None:
    global _state
    if STORE is None or not STORE.exists():
        return
    try:
        loaded = json.loads(STORE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            _state = {"quiet_since": float(loaded.get("quiet_since", 0.0)),
                      "expires_at": float(loaded.get("expires_at", 0.0)),
                      "snoozed": dict(loaded.get("snoozed", {})),
                      "deferred": list(loaded.get("deferred", []))}
    except Exception:
        log.debug("could not read the quiet state", exc_info=True)


def _save() -> None:
    if STORE is None:
        return
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(_state, indent=2), encoding="utf-8")
    except Exception:
        log.debug("could not write the quiet state", exc_info=True)


# ── quiet hours ────────────────────────────────────────────────────
def begin() -> bool:
    """Start quiet hours. False if they were already running."""
    if active():
        return False
    _state["quiet_since"] = time.time()
    _state["expires_at"] = time.time() + _expire_hours * 3600
    _save()
    log.info("quiet hours begin")
    return True


def end() -> bool:
    """End quiet hours. False if they were not running."""
    if not active():
        return False
    _state["quiet_since"] = 0.0
    _state["expires_at"] = 0.0
    _save()
    log.info("quiet hours end")
    return True


def active() -> bool:
    if not _state.get("quiet_since"):
        return False
    if time.time() >= _state.get("expires_at", 0.0):
        # Lapsed on its own. Clear it rather than leaving a stale flag that
        # every later call has to reason about.
        _state["quiet_since"] = 0.0
        _state["expires_at"] = 0.0
        _save()
        log.info("quiet hours lapsed")
        return False
    return True



# ── snoozing one observation ───────────────────────────────────────
def snooze(observation_id: str, hours: float = 8.0) -> None:
    if not observation_id:
        return
    _state.setdefault("snoozed", {})[observation_id] = time.time() + hours * 3600
    _save()
    log.info("snoozed %s for %.0fh", observation_id, hours)


def snoozed(observation_id: str) -> bool:
    until = _state.get("snoozed", {}).get(observation_id, 0.0)
    if not until:
        return False
    if time.time() >= until:
        _state["snoozed"].pop(observation_id, None)
        _save()
        return False
    return True


def clear_snoozes() -> int:
    n = len(_state.get("snoozed", {}))
    _state["snoozed"] = {}
    _save()
    return n



# ── what he kept to himself ────────────────────────────────────────
# Suppressing an observation used to discard it. Holding it instead costs
# nothing and means the morning has something to report: the point of quiet
# hours is that he stops talking, not that he stops noticing.
def defer(observation_id: str, text: str) -> None:
    held = _state.setdefault("deferred", [])
    if any(h.get("id") == observation_id for h in held):
        return                      # one mention each, however many times
    held.append({"id": observation_id, "text": text, "at": time.time()})
    del held[:-8]                   # a backlog nobody wants read out
    _save()


def take_deferred() -> list[str]:
    """Everything held back, and clear it. Reporting twice is worse."""
    held = _state.get("deferred", [])
    _state["deferred"] = []
    if held:
        _save()
    return [h.get("text", "") for h in held if h.get("text")]


# ── what he last said unprompted ───────────────────────────────────
# So "stop telling me about that" has something to point at. Held in memory
# only: after a restart there is no "that" to refer to anyway.
_last_spoken: str = ""


def note_spoken(observation_id: str) -> None:
    global _last_spoken
    _last_spoken = observation_id or ""


def last_spoken() -> str:
    return _last_spoken
