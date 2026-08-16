"""The objection he voices once, before doing as he is told.

This is the behaviour the films are built on and the one ours was missing
entirely. Tony orders something unwise; JARVIS states the risk flatly -- the
odds, the power reserve, the thing about to go wrong -- and then does it. He is
neither a yes-man nor an obstacle. The warning is delivered once, in the same
register as everything else, and the decision stays Tony's.

Note carefully what this is NOT. It is not a confirmation prompt: those already
exist for genuinely irreversible things, and they ask before acting. A caution
asks nothing. It is said, the action happens anyway, and he does not raise it
again.

Every check here has to be cheap and, more importantly, quiet. A caution that
fires when nothing is wrong is worse than no caution at all -- he would learn
to ignore them, which costs the real ones their weight. So each condition below
is one that is genuinely detectable rather than guessed at, and when in doubt
this module says nothing.
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.cautions")

# Actions worth pausing on. Anything that ends the session or closes something
# holding state the user may not have saved.
_ENDS_SESSION = {"shutdown_computer", "sleep_computer", "lock_screen"}

# Editors mark unsaved work in the window title, and they are consistent about
# it: a leading asterisk, or a bullet before the filename. Word and the browsers
# do not, so this finds some unsaved work rather than all of it -- which is the
# right trade, because the alternative is guessing and then crying wolf.
_DIRTY_MARKS = ("●", "•")     # ● and • , used by VS Code and others


def _unsaved_windows() -> list[str]:
    """Open windows that look like they hold unsaved work."""
    try:
        import pygetwindow as gw

        dirty = []
        for title in gw.getAllTitles():
            title = (title or "").strip()
            if not title:
                continue
            if title.startswith("*") or any(m in title for m in _DIRTY_MARKS):
                dirty.append(title.lstrip("*").strip())
        return dirty
    except Exception:
        log.debug("could not read window titles", exc_info=True)
        return []


def _pending_timers() -> list[str]:
    """Timers still counting down, described the way he would say them."""
    try:
        from .tools import timers

        return timers.pending_descriptions()
    except Exception:
        log.debug("could not read pending timers", exc_info=True)
        return []


def _app_word(titles: list[str]) -> str:
    """Name the unsaved work the way he would say it out loud.

    Window titles run "file - project - Application", so the last segment is
    the application and the first is the document. The application is the more
    useful of the two spoken: "unsaved work in Notepad" tells him where to
    look, where "Untitled has unsaved changes" does not.
    """
    apps: list[str] = []
    for title in titles:
        parts = [p.strip() for p in title.split(" - ") if p.strip()]
        app = parts[-1] if parts else title.strip()
        if app and app not in apps:
            apps.append(app)

    if not apps:
        return ""
    if len(apps) == 1:
        return f"there is unsaved work in {apps[0]}"
    if len(apps) == 2:
        return f"there is unsaved work in {apps[0]} and {apps[1]}"
    return (f"there is unsaved work in {apps[0]} and "
            f"{len(apps) - 1} other applications")


def caution_for(tool_name: str, arguments: dict) -> str:
    """One flat sentence to say before doing it, or empty for silence.

    Called before the tool runs, which matters: after a shutdown has cancelled
    the timers there is nothing left to warn about.
    """
    try:
        if tool_name in _ENDS_SESSION:
            reasons = []

            timers_left = _pending_timers()
            if timers_left:
                if len(timers_left) == 1:
                    reasons.append(f"you have a timer with {timers_left[0]} left")
                else:
                    reasons.append(f"you have {len(timers_left)} timers running")

            if tool_name != "lock_screen":
                unsaved = _app_word(_unsaved_windows())
                if unsaved:
                    reasons.append(unsaved)

            if not reasons:
                return ""
            # No stock preamble. A fixed opener like "For the record" is
            # charming once and a tic by the fourth time, and the dryness is
            # supposed to come from stating the fact and proceeding anyway,
            # not from a catchphrase.
            joined = ", and ".join(reasons)
            return joined[0].upper() + joined[1:] + "."

        if tool_name == "close_app":
            wanted = str(arguments.get("name", "")).lower().strip()
            if not wanted:
                return ""
            for title in _unsaved_windows():
                if wanted in title.lower():
                    return "That has unsaved changes."
            return ""

    except Exception:
        # A caution is a nicety. It must never be the reason an action fails.
        log.debug("caution check failed for %s", tool_name, exc_info=True)
    return ""
