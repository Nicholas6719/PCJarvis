"""Protocols that run on their own, at a time.

    "Every weekday at eight, run work mode."

Timers already existed and protocols already existed; this is the wire between
them, and it is what turns a named routine into something a butler does rather
than something you remember to ask for.

The interesting decision is what to do about a schedule that was missed. If
JARVIS was closed at eight and opens at half past ten, running the morning
routine then is not helpful -- it is startling, and it does the wrong thing at
the wrong time with the volume and the screen. So a schedule has a grace
window: fire inside it, and past it let the day go. Missing a run is a much
smaller failure than a routine that ambushes you hours late.

Everything runs through the ordinary protocol machinery, which already refuses
to hold anything irreversible. A scheduled phrase is even less supervised than
a spoken one, so that refusal matters more here, not less.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger("jarvis.schedules")

STORE: Path | None = None
_schedules: list[dict] = []

# How late is still worth running. Past this the day is written off.
GRACE_MINUTES = 30

_TIME = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", re.I)

DAY_SETS = {
    "daily": {0, 1, 2, 3, 4, 5, 6},
    "everyday": {0, 1, 2, 3, 4, 5, 6},
    "weekdays": {0, 1, 2, 3, 4},
    "weekends": {5, 6},
    "monday": {0}, "tuesday": {1}, "wednesday": {2}, "thursday": {3},
    "friday": {4}, "saturday": {5}, "sunday": {6},
}


def configure(data_dir: Path) -> None:
    global STORE
    STORE = Path(data_dir) / "schedules.json"
    _load()


def _load() -> None:
    global _schedules
    if STORE is None or not STORE.exists():
        return
    try:
        loaded = json.loads(STORE.read_text(encoding="utf-8"))
        _schedules = [s for s in loaded if isinstance(s, dict)] if isinstance(loaded, list) else []
    except Exception:
        log.debug("could not read schedules", exc_info=True)


def _save() -> None:
    if STORE is None:
        return
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(_schedules, indent=2), encoding="utf-8")
    except Exception:
        log.debug("could not write schedules", exc_info=True)


def parse_time(text: str) -> tuple[int, int] | None:
    """"8", "8am", "08:30", "5:15 pm" -> (hour, minute)."""
    m = _TIME.match(text or "")
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    suffix = (m.group(3) or "").lower()
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    # No am/pm and an hour that could be either: assume the working day. "run
    # work mode at 8" almost never means twenty past midnight.
    if not suffix and hour < 7:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def parse_days(text: str) -> set[int]:
    key = (text or "daily").lower().strip()
    for name, days in DAY_SETS.items():
        if name in key:
            return days
    return DAY_SETS["daily"]


def add(protocol: str, hour: int, minute: int, days: set[int],
        spoken_days: str = "daily") -> dict:
    entry = {
        "id": uuid.uuid4().hex[:8],
        "protocol": protocol,
        "hour": int(hour),
        "minute": int(minute),
        "days": sorted(days),
        "spoken_days": spoken_days,
        "last_run": 0.0,
    }
    _schedules.append(entry)
    _save()
    log.info("scheduled %s at %02d:%02d (%s)", protocol, hour, minute, spoken_days)
    return entry


def all_schedules() -> list[dict]:
    return list(_schedules)


def cancel(query: str = "") -> list[dict]:
    global _schedules
    q = (query or "").lower().strip()
    if not q:
        dropped, _schedules[:] = list(_schedules), []
        _save()
        return dropped
    dropped = [s for s in _schedules if q in s.get("protocol", "").lower()]
    for s in dropped:
        _schedules.remove(s)
    _save()
    return dropped


def spoken(entry: dict) -> str:
    hour, minute = entry["hour"], entry["minute"]
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12 or 12
    when = f"{display}:{minute:02d} {suffix}" if minute else f"{display} {suffix}"
    return f"{entry['protocol']} at {when}, {entry.get('spoken_days', 'daily')}"


def due() -> list[str]:
    """Protocols whose moment has arrived. Marks them run before returning."""
    if not _schedules:
        return []

    now = datetime.now()
    ready: list[str] = []
    for entry in _schedules:
        try:
            if now.weekday() not in set(entry.get("days", [])):
                continue

            target = now.replace(hour=entry["hour"], minute=entry["minute"],
                                 second=0, microsecond=0)
            if now < target:
                continue
            late_minutes = (now - target).total_seconds() / 60
            if late_minutes > GRACE_MINUTES:
                continue        # too late to be helpful; let the day go

            # Already run today?
            last = entry.get("last_run", 0.0)
            if last and datetime.fromtimestamp(last).date() == now.date():
                continue

            entry["last_run"] = time.time()
            ready.append(entry["protocol"])
        except Exception:
            log.debug("schedule %s failed to evaluate", entry.get("id"),
                      exc_info=True)
    if ready:
        _save()
    return ready
