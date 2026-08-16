"""Additional capabilities: relative controls, network, uptime, help, notes.

Mostly small things that turn out to matter, because they are what people
actually say. "Turn it up" is far more common than "set the volume to 65", and
until it exists the request either fails or takes a five-second detour through
the language model to arrive at the same place.
"""
from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import psutil

from .registry import tool
from .system import _volume_interface

log = logging.getLogger("jarvis.tools.extras")

NOTES_FILE = Path.home() / "Documents" / "JARVIS" / "notes.md"  # local, not OneDrive


# ══════════════════════════════════════════════════════════════════
#  Relative controls -- what people actually say
# ══════════════════════════════════════════════════════════════════
@tool(category="system")
def adjust_volume(direction: str, amount: int = 15) -> str:
    """Turn the volume up or down by a step.

    Args:
        direction: "up" or "down".
        amount: How many percentage points to move.
    """
    try:
        interface = _volume_interface()
        current = round(interface.GetMasterVolumeLevelScalar() * 100)
        step = abs(int(amount))
        target = current + step if direction.lower().startswith("u") else current - step
        target = max(0, min(100, target))
        interface.SetMasterVolumeLevelScalar(target / 100.0, None)
        return f"Volume {target}%."
    except Exception as e:
        return f"Could not change the volume: {e}"


@tool(category="system")
def adjust_brightness(direction: str, amount: int = 20) -> str:
    """Make the screen brighter or dimmer by a step.

    Args:
        direction: "up" or "down".
        amount: How many percentage points to move.
    """
    try:
        read = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-WmiObject -Namespace root/WMI -Class "
             "WmiMonitorBrightness).CurrentBrightness"],
            capture_output=True, text=True, timeout=10)
        current = int((read.stdout or "50").strip().splitlines()[0])
    except Exception:
        current = 50

    step = abs(int(amount))
    target = current + step if direction.lower().startswith("u") else current - step
    target = max(0, min(100, target))
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-WmiObject -Namespace root/WMI -Class "
             f"WmiMonitorBrightnessMethods).WmiSetBrightness(1,{target})"],
            capture_output=True, timeout=10, check=True)
        return f"Brightness {target}%."
    except Exception as e:
        return f"Could not change the brightness: {e}"


# ══════════════════════════════════════════════════════════════════
#  Machine facts
# ══════════════════════════════════════════════════════════════════
@tool(category="system")
def get_network_status() -> str:
    """Report whether the machine is online, and on which network."""
    online = False
    try:
        import socket

        with socket.create_connection(("1.1.1.1", 53), timeout=2.5):
            online = True
    except Exception:
        online = False

    ssid = ""
    try:
        r = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                           capture_output=True, text=True, timeout=6)
        for line in (r.stdout or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("SSID") and "BSSID" not in stripped:
                ssid = stripped.split(":", 1)[1].strip()
                break
    except Exception:
        pass

    if not online:
        return "No internet connection."
    return f"Online, connected to {ssid}." if ssid else "Online."


@tool(category="system")
def get_uptime() -> str:
    """Report how long the machine has been running since its last restart."""
    booted = datetime.fromtimestamp(psutil.boot_time())
    delta = datetime.now() - booted
    days, rem = delta.days, delta.seconds
    hours, minutes = rem // 3600, (rem % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes and not days:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return f"Up for {', '.join(parts) or 'less than a minute'}."


@tool(category="system")
def get_top_processes(count: int = 5) -> str:
    """List what is using the most CPU right now.

    Args:
        count: How many to report.
    """
    try:
        for p in psutil.process_iter(["name"]):
            try:
                p.cpu_percent(None)     # first call primes the measurement
            except psutil.Error:
                pass
        time.sleep(0.6)

        rows = []
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                rows.append((p.cpu_percent(None), p.info["name"],
                             (p.info["memory_info"].rss if p.info["memory_info"]
                              else 0) / 1e6))
            except psutil.Error:
                continue
        # psutil reports cpu_percent per core, so a busy process on 16
        # threads reads as 1600%. Normalise it, and drop the idle process,
        # which is always top of the list and means the opposite of busy.
        cores = psutil.cpu_count() or 1
        ignore = {"system idle process", "idle", "memory compression"}
        rows = [(cpu / cores, name, mem) for cpu, name, mem in rows
                if name and name.lower() not in ignore]
        rows.sort(key=lambda r: -r[0])
        top = [r for r in rows if r[0] > 0.5][:max(1, min(int(count), 8))]
        if not top:
            return "Nothing is using significant CPU."
        return "Heaviest right now: " + "; ".join(
            f"{name} at {cpu:.0f}% and {mem:.0f} MB" for cpu, name, mem in top)
    except Exception as e:
        return f"Could not read the process list: {e}"


# ══════════════════════════════════════════════════════════════════
#  Notes -- quick capture, distinct from memory
# ══════════════════════════════════════════════════════════════════
@tool(category="documents")
def add_note(text: str) -> str:
    """Append a timestamped note to the running notes file.

    Distinct from remember: this is a jotting he will read later, not a fact
    about him that should shape future answers.

    Args:
        text: The note.
    """
    if not text.strip():
        return "There was nothing to note down."
    try:
        NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(f"- [{datetime.now():%Y-%m-%d %H:%M}] {text.strip()}\n")
        return "Noted."
    except Exception as e:
        return f"Could not write the note: {e}"


@tool(category="documents")
def read_notes(count: int = 10) -> str:
    """Read back the most recent notes.

    Args:
        count: How many to read.
    """
    if not NOTES_FILE.exists():
        return "You have no notes yet."
    lines = [ln.strip() for ln in
             NOTES_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return "You have no notes yet."
    recent = lines[-max(1, min(int(count), 25)):]
    return f"Your last {len(recent)} note(s): " + " ".join(
        ln.lstrip("- ") for ln in recent)


# ══════════════════════════════════════════════════════════════════
#  Self-description
# ══════════════════════════════════════════════════════════════════
@tool(category="general")
def list_capabilities(area: str = "") -> str:
    """Describe what JARVIS can do. Use when asked what he is capable of.

    Args:
        area: Narrow it to one area -- system, web, files, media, memory,
            timers, documents. Leave empty for an overview.
    """
    from .registry import REGISTRY

    areas: dict[str, list[str]] = {}
    for name, spec in REGISTRY.items():
        areas.setdefault(spec.category, []).append(name.replace("_", " "))

    key = (area or "").lower().strip()
    if key and key in areas:
        return (f"For {key} I can: " + ", ".join(sorted(areas[key])) + ".")

    summary = {
        "system": "check the machine, open and close apps, volume, brightness, "
                  "screenshots, the clipboard, lock and power",
        "web": "search the web, read a page, weather and news",
        "files": "find, read and open your files",
        "media": "control whatever is playing",
        "memory": "remember things about you and recall them",
        "timers": "set and cancel timers",
        "documents": "make PDFs, save notes, export our conversation",
        "browser": "open sites, search YouTube, get directions, and tell you "
                   "what page you are on",
        "text": "proofread, rewrite, summarise or translate whatever you have "
                "copied",
    }
    # A category with no entry here is dropped from the overview entirely, so
    # a whole area can go unmentioned while its tools work perfectly well.
    missing = sorted(set(areas) - set(summary) - {"general"})
    if missing:
        log.debug("no overview text for: %s", ", ".join(missing))

    have = [text for cat, text in summary.items() if cat in areas]
    return ("I can " + "; ".join(have)
            + f". That is {len(REGISTRY)} tools in total. Ask about any one "
              "area for the detail.")


@tool(category="system")
def get_time_until(event_time: str) -> str:
    """Work out how long until a given clock time today.

    Args:
        event_time: A time like "5pm", "17:30", "9:15 am".
    """
    import re

    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$",
                 event_time.strip(), re.I)
    if not m:
        return f"I couldn't read {event_time} as a time."
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = (m.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    now = datetime.now()
    target = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    delta = target - now
    hours, minutes = delta.seconds // 3600, (delta.seconds % 3600) // 60
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minutes."
    return f"{minutes} minutes."


# ══════════════════════════════════════════════════════════════════
#  Holding his tongue
# ══════════════════════════════════════════════════════════════════
@tool(category="quiet")
def begin_quiet_hours() -> str:
    """Stop volunteering things until told otherwise. Use for "goodnight"."""
    from .. import quiet

    # Deliberately silent, both ways. He never wants to hear about quiet
    # hours -- being told "quiet hours are now on" is itself the interruption
    # the feature exists to prevent, and saying goodnight to something that
    # answers back rather defeats the point.
    quiet.begin()
    return ""


@tool(category="quiet")
def end_quiet_hours() -> str:
    """Resume speaking up. Use for "good morning" or "let's get to work"."""
    from .. import quiet

    if not quiet.end():
        # Only reachable through the model: the intent is guarded on quiet
        # hours actually running. Nothing to end, so this is just a greeting,
        # and greeting him back is the right answer.
        return "Good morning, sir."
    return ""


@tool(category="quiet")
def snooze_observation(hours: float = 8.0) -> str:
    """Stop mentioning the thing just mentioned, for a while.

    Use for "stop telling me about that" or "don't mention that again".

    Args:
        hours: How long to leave it alone.
    """
    from .. import quiet

    last = quiet.last_spoken()
    if not last:
        return "I have not mentioned anything unprompted yet."
    quiet.snooze(last, hours)
    subject = last.replace("_", " ")
    return f"I shall say nothing further about {subject}."


@tool(category="quiet")
def clear_snoozes() -> str:
    """Undo every snooze, so he mentions those things again."""
    from .. import quiet

    n = quiet.clear_snoozes()
    if not n:
        return "Nothing was snoozed."
    return f"I will mention {n} thing(s) again."


@tool(category="system")
def get_trend(component: str = "memory", days: float = 7.0) -> str:
    """How something has been over time, rather than what it is right now.

    Use for "how has my memory been this week", "has the CPU been busier than
    usual", "how has the battery been holding up".

    Args:
        component: "cpu", "memory", "disk" or "battery".
        days: How far back to look.
    """
    from .. import history

    return history.summarise(component, days)
