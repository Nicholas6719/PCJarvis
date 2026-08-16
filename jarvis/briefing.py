"""What he says when you come back.

He greeted you identically whether you had been gone five minutes or two days,
which is the single clearest way an assistant announces that it has just been
switched on. The films' JARVIS reports on arrival -- that is most of what makes
him feel like he was still there while you were out.

Nothing here is new information. It is assembled entirely from things he
already had and previously threw away: observations held back during quiet
hours, how long since he last saw you, what you were in the middle of, and
whether any reading has moved enough to be worth a sentence.

The hard constraint is length. This is spoken, it happens every single time,
and a briefing that takes fifteen seconds to say will be resented by the third
day. Two sentences. Most of the time it should be nothing at all -- coming back
after ten minutes to a status report is not a butler, it is an alarm system.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("jarvis.briefing")

# Below this it is the same session and there is nothing to report.
MIN_AWAY_S = 90 * 60

# Above this, hours stop being the useful unit.
DAY_S = 20 * 3600


def _away_phrase(seconds: float) -> str:
    if seconds >= DAY_S * 2:
        return f"It has been {seconds / 86400:.0f} days."
    if seconds >= DAY_S:
        return "It has been a day."
    return f"It has been {seconds / 3600:.0f} hours."


def compose(cfg, last_seen: float) -> str:
    """The whole briefing, or an empty string for silence.

    Silence is the common case and the right default.
    """
    try:
        from . import history, quiet

        parts: list[str] = []
        away = time.time() - last_seen if last_seen else 0

        if away and away >= MIN_AWAY_S:
            parts.append(_away_phrase(away))

        # Things he noticed and kept to himself. These are the reason the
        # briefing is worth having at all.
        held = quiet.take_deferred()
        if held:
            # Two at most, and only the first sentence of each. The
            # observations are written to stand alone, so they carry a
            # recommendation after the fact -- "the disk is at 93%. Worth
            # clearing some space." Useful when said on its own, three
            # sentences too long inside a greeting. Anything still true will
            # be noticed again anyway.
            for item in held[:2]:
                first = item.split(". ")[0].rstrip(".")
                if first:
                    parts.append(first + ".")

        # Only mention a reading if it has genuinely moved. "Memory has been
        # normal" is not worth saying out loud.
        if away >= MIN_AWAY_S:
            for component in ("memory", "disk"):
                line = history.summarise(component, 7)
                if "points higher" in line:
                    parts.append(line.split(".")[-2].strip() + ".")
                    break

        if not parts:
            return ""

        # Never more than two sentences, however much was collected.
        return " ".join(parts[:3])
    except Exception:
        log.debug("could not compose a briefing", exc_info=True)
        return ""


def what_was_i_doing(hours: float = 8.0) -> str:
    from . import history

    return history.app_summary(hours)
