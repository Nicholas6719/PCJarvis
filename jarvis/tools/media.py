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
    """Bridge winsdk's async API into our sync tool functions.

    Always executed on a fresh worker thread, never on whatever thread happened
    to call in. WinRT objects are apartment-bound, and the previous version ran
    the coroutine on a new event loop inside the *calling* thread whenever one
    was already running. That worked once and then degraded: repeated polling
    started returning nothing at all, so "what's playing" went quiet and the
    Spotify verification could not tell whether a track had started.

    A dedicated thread gets a clean apartment and the plain asyncio.run path
    every single time.
    """
    import concurrent.futures

    def worker():
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            return asyncio.run(coro)
        except Exception:
            log.debug("media call failed", exc_info=True)
            return None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(worker).result(timeout=8)
    except Exception:
        log.debug("media call timed out", exc_info=True)
        return None


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
        # "Spotify.exe" -> "Spotify". Splitting on "." took the last part
        # and announced everything as playing "on exe".
        raw = (s.source_app_user_model_id or "").split("!")[0]
        app = raw.rsplit("\\", 1)[-1]
        if app.lower().endswith(".exe"):
            app = app[:-4]
        app = app.strip()

        text = f"{state}: {title}"
        if artist:
            text += f" by {artist}"
        return text + (f" on {app}." if app and len(app) > 1 else ".")
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
