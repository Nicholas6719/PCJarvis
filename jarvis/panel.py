"""What he puts on screen while he works.

The old interface showed the same six readouts forever, whether or not any of
them mattered, and showed nothing at all about what he was actually doing. This
is the other way round: the screen is empty until there is something worth
looking at, and then it shows exactly that.

The rule that keeps it from becoming the old HUD again is that **most things
should not open it**. The time, the battery, the volume -- he says those and the
screen stays as it was. A panel that opens for everything is a panel you stop
looking at, and then the one that mattered goes unnoticed too.

So a tool has to ask, explicitly, by calling show(). Nothing here inspects
results and guesses. That means adding a new kind of card is a deliberate act
rather than something that happens by accident, which is the point.

The panel closes itself after a while. Nothing here manages that -- the page
does, because the timer belongs next to the thing being timed, and a countdown
that survives a reload is worse than one that does not.
"""
from __future__ import annotations

import logging

from .bus import BUS

log = logging.getLogger("jarvis.panel")

# Every kind the interface knows how to draw. Anything else is refused rather
# than sent, because a payload the page cannot render opens an empty panel --
# which looks exactly like a bug and is impossible to tell apart from one.
KINDS = {
    "results",   # search results, as cards
    "images",    # a strip of pictures
    "status",    # the system, on request only
    "playing",   # what is playing, with its artwork
    "screen",    # text read off his screen
    "weather",   # the forecast
    "text",      # a passage: a document, a memory, a definition
}


def show(kind: str, **data) -> None:
    """Put something on screen. Silently does nothing if nothing is listening."""
    if kind not in KINDS:
        log.debug("refusing to show unknown panel kind %r", kind)
        return
    try:
        # Tools run on worker threads; this is the existing bridge for that.
        BUS.emit_threadsafe("panel", kind=kind, **data)
    except Exception:
        # The panel is a nicety. It must never be why a tool fails.
        log.debug("could not show the %s panel", kind, exc_info=True)


def clear() -> None:
    try:
        BUS.emit_threadsafe("panel.clear")
    except Exception:
        log.debug("could not clear the panel", exc_info=True)
