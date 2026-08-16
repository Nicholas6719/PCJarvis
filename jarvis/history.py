"""Readings kept over time, so he can answer "compared to what?".

He could already tell you memory is at 61%. He could not tell you it has been
climbing all week, which is the more useful sentence and the one the films'
JARVIS actually says -- he offers conclusions, not readouts. A number with no
baseline is a readout.

Sampled on the ambient watch tick, which is already running and already reading
most of these, so the marginal cost is one small insert a minute.

Kept deliberately dumb. No aggregation tables, no rollups, no background
compaction -- one row per sample, pruned past the horizon. At a sample a minute
that is around twenty thousand rows a fortnight, which SQLite answers instantly
and which nobody ever has to maintain. The moment this needs a maintenance job
it has become a worse idea than it was.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.history")

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    at       REAL NOT NULL,
    cpu      REAL,
    memory   REAL,
    disk     REAL,
    battery  REAL,
    plugged  INTEGER
);
CREATE INDEX IF NOT EXISTS readings_at ON readings (at);

CREATE TABLE IF NOT EXISTS context (
    at   REAL NOT NULL,
    app  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS context_at ON context (at);

"""

KEEP_DAYS = 14

_db: sqlite3.Connection | None = None
_lock = threading.Lock()
_last_prune = 0.0

FIELDS = {"cpu": "the processor", "memory": "memory", "disk": "the disk",
          "battery": "the battery"}


def configure(data_dir: Path) -> None:
    global _db
    try:
        path = Path(data_dir) / "readings.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(str(path), check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript(SCHEMA)
        _db.commit()
    except Exception:
        log.exception("could not open the readings store; history disabled")
        _db = None


def record(cpu: float | None = None, memory: float | None = None,
           disk: float | None = None, battery: float | None = None,
           plugged: bool | None = None) -> None:
    """One sample. Never raises -- history is a nicety, not a dependency."""
    global _last_prune
    if _db is None:
        return
    try:
        with _lock:
            _db.execute(
                "INSERT INTO readings (at, cpu, memory, disk, battery, plugged)"
                " VALUES (?,?,?,?,?,?)",
                (time.time(), cpu, memory, disk, battery,
                 None if plugged is None else int(plugged)))

            # Prune once an hour rather than once a sample. Deleting nothing,
            # sixty times an hour, is still sixty write transactions.
            if time.time() - _last_prune > 3600:
                _last_prune = time.time()
                cutoff = time.time() - KEEP_DAYS * 86400
                _db.execute("DELETE FROM readings WHERE at < ?", (cutoff,))
                _db.execute("DELETE FROM context WHERE at < ?", (cutoff,))
            _db.commit()
    except Exception:
        log.debug("could not record a reading", exc_info=True)


def foreground_app() -> str:
    """The application in front, by process name.

    The process name and not the window title, deliberately. Titles carry
    the subject of the email, the name of the document, the page being read
    -- none of which should end up in a log on disk merely because he asked
    what he had been doing. "code" and "chrome" answer that question
    perfectly well.
    """
    try:
        import ctypes

        import psutil

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        name = psutil.Process(pid.value).name() or ""
        return name[:-4] if name.lower().endswith(".exe") else name
    except Exception:
        log.debug("could not read the foreground application", exc_info=True)
        return ""


def record_app(name: str) -> None:
    if _db is None or not name:
        return
    try:
        with _lock:
            _db.execute("INSERT INTO context (at, app) VALUES (?,?)",
                        (time.time(), name))
            _db.commit()
    except Exception:
        log.debug("could not record the foreground application", exc_info=True)


def app_summary(hours: float = 8.0, limit: int = 4) -> str:
    """What he has been in, most first.

    Samples are a minute apart, so counting them is counting minutes -- near
    enough for "most of the afternoon" and far simpler than tracking spans
    across gaps, sleeps and restarts.
    """
    if _db is None:
        return "I have not been keeping track."
    since = time.time() - max(0.25, float(hours)) * 3600
    rows = _db.execute(
        "SELECT app, COUNT(*) n FROM context WHERE at >= ? "
        "GROUP BY app ORDER BY n DESC", (since,)).fetchall()
    rows = [r for r in rows if r["n"] >= 2]
    if not rows:
        return "Nothing much, as far as I can tell."

    total = sum(r["n"] for r in rows)
    parts = []
    for row in rows[:limit]:
        minutes = row["n"]
        if minutes >= 90:
            span = f"{minutes / 60:.0f} hours"
        else:
            span = f"{minutes} minutes"
        parts.append(f"{row['app']} for {span}")
    line = ", ".join(parts[:-1])
    line = f"{line}, and {parts[-1]}" if len(parts) > 1 else parts[0]
    return f"Mostly {line}."


def _window(field: str, start: float, end: float) -> dict | None:
    if _db is None:
        return None
    row = _db.execute(
        f"SELECT AVG({field}) avg, MIN({field}) lo, MAX({field}) hi, "
        f"COUNT({field}) n FROM readings WHERE at >= ? AND at < ? "
        f"AND {field} IS NOT NULL",
        (start, end)).fetchone()
    if not row or not row["n"]:
        return None
    return {"avg": row["avg"], "lo": row["lo"], "hi": row["hi"], "n": row["n"]}


def summarise(component: str = "memory", days: float = 7.0) -> str:
    """How it has been, and whether that is unusual.

    The comparison window is the same length immediately before, which is what
    makes the answer a conclusion rather than another reading. Without a
    baseline "memory averaged 58%" means nothing at all.
    """
    field = (component or "memory").lower().strip()
    aliases = {"ram": "memory", "processor": "cpu", "storage": "disk",
               "drive": "disk", "power": "battery"}
    field = aliases.get(field, field)
    if field not in FIELDS:
        return (f"I have no history for {component}. I keep the processor, "
                f"memory, the disk and the battery.")

    if _db is None:
        return "I have not been keeping readings."

    now = time.time()
    span = max(0.25, float(days)) * 86400
    recent = _window(field, now - span, now)
    if not recent or recent["n"] < 5:
        return (f"I have not watched {FIELDS[field]} for long enough to say. "
                f"Give it a few hours.")

    earlier = _window(field, now - 2 * span, now - span)
    label = FIELDS[field]
    period = "today" if days <= 1 else f"over the last {int(days)} days"

    line = f"{label.capitalize()} has averaged {recent['avg']:.0f}% {period}"
    # "between 71 and 71" is a strange thing to say about a number that has
    # not moved. Only give the range when there is one.
    if recent["hi"] - recent["lo"] >= 2:
        line += f", between {recent['lo']:.0f} and {recent['hi']:.0f}."
    else:
        line += ", and barely moved."

    if not earlier or earlier["n"] < 5:
        # Say so rather than implying a comparison that was never made.
        return line + " I have nothing earlier to compare it against yet."

    change = recent["avg"] - earlier["avg"]
    if abs(change) < 3:
        return line + " Much the same as the period before."
    direction = "higher" if change > 0 else "lower"
    return (line + f" That is {abs(change):.0f} points {direction} than the "
            f"{int(days)} days before.")


def count() -> int:
    if _db is None:
        return 0
    try:
        return _db.execute("SELECT COUNT(*) c FROM readings").fetchone()["c"]
    except Exception:
        return 0


def close() -> None:
    global _db
    if _db is not None:
        try:
            _db.close()
        except Exception:
            pass
        _db = None
