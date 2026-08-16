"""Noticing that you do the same thing every morning.

The research on the films is explicit that JARVIS anticipates -- he suggests
routes, learns working patterns, manages a schedule around habits nobody
described to him. That is the quality hardest to fake and the easiest to get
wrong, because the failure mode is not being unhelpful, it is being presumptuous.

So the rules here are all restraint:

  * He observes, he never acts. Spotting a habit produces one remark and an
    offer. Nothing is created, changed or scheduled without being asked.
  * He mentions a given habit exactly once, ever. An offer declined is an
    answer, and repeating it is nagging -- the fastest way to make someone
    disable a feature.
  * A habit needs several days behind it. Two mornings is a coincidence.
  * At most one offer a day, however many patterns exist.

The bar is deliberately high enough that this will say nothing for weeks at a
time. That is the correct behaviour: an assistant constantly announcing
patterns it has noticed is not perceptive, it is exhausting.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger("jarvis.patterns")

STORE: Path | None = None
_offered: dict = {}

MIN_DAYS = 3           # fewer than this is a coincidence
LOOK_BACK_DAYS = 14
MIN_SAMPLES_IN_HOUR = 5    # roughly five minutes in that app, that hour
OFFER_EVERY_S = 24 * 3600

# Things everyone has open constantly. Announcing that you tend to use Explorer
# in the morning is true, useless, and slightly insulting.
IGNORE = {"explorer", "searchhost", "shellexperiencehost", "textinputhost",
          "dwm", "csrss", "sihost", "startmenuexperiencehost", "jarvis",
          "lockapp", "applicationframehost", "systemsettings"}


def configure(data_dir: Path) -> None:
    global STORE, _offered
    STORE = Path(data_dir) / "patterns.json"
    try:
        if STORE.exists():
            loaded = json.loads(STORE.read_text(encoding="utf-8"))
            _offered = dict(loaded) if isinstance(loaded, dict) else {}
    except Exception:
        log.debug("could not read the pattern history", exc_info=True)
        _offered = {}


def _save() -> None:
    if STORE is None:
        return
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(_offered, indent=2), encoding="utf-8")
    except Exception:
        log.debug("could not write the pattern history", exc_info=True)


def _spoken_hour(hour: int) -> str:
    suffix = "am" if hour < 12 else "pm"
    return f"{hour % 12 or 12} {suffix}"


def find() -> tuple[str, int, int] | None:
    """The strongest habit not already mentioned: (app, hour, days).

    A habit is the same application, in the same hour of the day, on several
    different days. Counting distinct days rather than samples matters: eight
    hours in the editor on one Sunday is not a routine.
    """
    from . import history

    if history._db is None:
        return None
    try:
        since = time.time() - LOOK_BACK_DAYS * 86400
        rows = history._db.execute(
            "SELECT at, app FROM context WHERE at >= ?", (since,)).fetchall()
    except Exception:
        log.debug("could not read the context history", exc_info=True)
        return None

    # (app, hour) -> {date: samples}
    buckets: dict[tuple[str, int], dict] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        app = (row["app"] or "").lower()
        if not app or app in IGNORE:
            continue
        when = datetime.fromtimestamp(row["at"])
        buckets[(app, when.hour)][when.date()] += 1

    best = None
    for (app, hour), days in buckets.items():
        strong = [d for d, n in days.items() if n >= MIN_SAMPLES_IN_HOUR]
        if len(strong) < MIN_DAYS:
            continue
        if _offered.get(f"{app}:{hour}"):
            continue
        if best is None or len(strong) > best[2]:
            best = (app, hour, len(strong))
    return best


def may_offer() -> bool:
    """At most one of these a day, whatever has been noticed."""
    return time.time() - float(_offered.get("_last", 0)) >= OFFER_EVERY_S


def offer() -> str:
    """One sentence, or nothing. Marks it so it is never said again."""
    if not may_offer():
        return ""
    found = find()
    if not found:
        return ""

    app, hour, days = found
    _offered[f"{app}:{hour}"] = time.time()
    _offered["_last"] = time.time()
    _save()
    log.info("noticed a habit: %s around %02d:00 on %d days", app, hour, days)

    return (f"You have had {app} open around {_spoken_hour(hour)} on {days} "
            f"separate days. I could make that a protocol, if you like.")
