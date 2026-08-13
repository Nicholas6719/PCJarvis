"""Media control via the Windows System Media Transport Controls.

SMTC is the same interface the keyboard media keys drive, so this works with
Spotify, the browser, any player at all -- with no API key, no OAuth, and no
account linking. That keeps the whole thing local and free, which was the
point.

The one thing SMTC cannot do is start a *specific* track by name; that needs
the Spotify Web API and a one-time OAuth. Everything else is here.
"""
from __future__ import annotations

import asyncio
import logging

from .registry import tool

log = logging.getLogger("jarvis.tools.media")


async def _session():
    """The media session currently in control, or None."""
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    )

    manager = await SessionManager.request_async()
    return manager.get_current_session()


def _run(coro):
    """Bridge winsdk's async API into our sync tool functions."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside a loop (tools run via asyncio.to_thread), so use a new one.
    new_loop = asyncio.new_event_loop()
    try:
        return new_loop.run_until_complete(coro)
    finally:
        new_loop.close()
    del loop


@tool(category="media")
def play_pause() -> str:
    """Play or pause whatever media is currently active."""
    async def go():
        s = await _session()
        if not s:
            return "Nothing is playing."
        await s.try_toggle_play_pause_async()
        return "Toggled playback."
    return _run(go())


@tool(category="media")
def pause_media() -> str:
    """Pause the currently playing media."""
    async def go():
        s = await _session()
        if not s:
            return "Nothing is playing."
        await s.try_pause_async()
        return "Paused."
    return _run(go())


@tool(category="media")
def resume_media() -> str:
    """Resume paused media."""
    async def go():
        s = await _session()
        if not s:
            return "Nothing to resume."
        await s.try_play_async()
        return "Playing."
    return _run(go())


@tool(category="media")
def next_track() -> str:
    """Skip to the next track."""
    async def go():
        s = await _session()
        if not s:
            return "Nothing is playing."
        await s.try_skip_next_async()
        return "Skipped."
    return _run(go())


@tool(category="media")
def previous_track() -> str:
    """Go back to the previous track."""
    async def go():
        s = await _session()
        if not s:
            return "Nothing is playing."
        await s.try_skip_previous_async()
        return "Went back."
    return _run(go())


@tool(category="media")
def now_playing() -> str:
    """Report what is currently playing."""
    async def go():
        s = await _session()
        if not s:
            return "Nothing is playing."
        props = await s.try_get_media_properties_async()
        info = s.get_playback_info()

        title = (props.title or "").strip()
        artist = (props.artist or "").strip()
        if not title:
            return "Something is playing, but it reports no title."

        # PlaybackStatus: 4 = playing, 5 = paused
        state = {4: "Playing", 5: "Paused"}.get(
            int(info.playback_status), "Loaded"
        )
        app = (s.source_app_user_model_id or "").split("!")[0].split(".")[-1]
        text = f"{state}: {title}"
        if artist:
            text += f" by {artist}"
        return text + (f" on {app}." if app else ".")
    return _run(go())


@tool(category="media")
def open_spotify_search(query: str) -> str:
    """Open a search in Spotify for a song, artist or album.

    SMTC cannot start a specific track, so this opens the search in the Spotify
    app instead, ready to play.

    Args:
        query: What to search for, e.g. "Back in Black" or "Led Zeppelin".
    """
    import os
    import urllib.parse

    try:
        os.startfile(f"spotify:search:{urllib.parse.quote(query)}")
        return f"Opened a Spotify search for {query}."
    except Exception as e:
        return f"Could not open Spotify: {e}"
