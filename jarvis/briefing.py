"""What he says when you come back.

He greeted you identically whether you had been gone five minutes or two days,
which is the clearest possible way of announcing that something has just been
switched on.

What this is not, deliberately: a summary. It never recaps the state of the
machine, never reports a trend, never tells you how long you were gone. It says
the things that happened while you were not there to hear them, and nothing
else. A briefing that reads out a status report every time you come back from
the kitchen gets switched off within a day.

It never repeats. Anything he already said out loud while you were present was
delivered, and saying it again ten minutes later is worse than not saying it at
all -- so the queue holds only what was genuinely suppressed, and reading it
clears it.

Most returns produce nothing at all, because most of the time nothing happened
while you were away. Silence is the correct and common answer.
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.briefing")

# However much piled up, this is spoken aloud the moment he comes back to the
# desk. Three things is already a lot to be told before you have sat down.
MAX_ITEMS = 3


def missed() -> str:
    """Everything held back while he was away, or nothing. Clears the queue."""
    try:
        from . import quiet

        held = quiet.take_deferred()
        if not held:
            return ""

        parts = []
        for item in held[:MAX_ITEMS]:
            # First sentence only. The observations carry a recommendation
            # after the fact -- "the disk is at 93%. Worth clearing some
            # space." -- which is useful said alone and too long in a list.
            first = item.split(". ")[0].rstrip(".")
            if first:
                parts.append(first + ".")
        return " ".join(parts)
    except Exception:
        log.debug("could not assemble what was missed", exc_info=True)
        return ""
