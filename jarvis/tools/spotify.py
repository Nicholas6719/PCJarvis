"""Spotify, driven through the desktop app -- no account link, no API key.

The usual way to make "play some jazz" work is the Spotify Web API, which means
registering a developer application, a client ID and secret, and an OAuth
round trip. That is a lot of ceremony for a music button, and it puts an account
credential in a local config file.

This does it without any of that, in two steps:

  1. Resolve the request to a Spotify URI using an ordinary web search. Every
     track, album and playlist has a public open.spotify.com URL containing its
     22-character ID, and those are indexed.
  2. Hand `spotify:track:<id>` to the desktop app, which plays it immediately.

Nothing is authenticated, nothing is stored, and it drives the real player he
already has open. "Play music" with no target skips the search entirely and
simply resumes whatever was last playing, which is both faster and what people
usually mean.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import urllib.parse

import psutil

from .registry import tool

log = logging.getLogger("jarvis.tools.spotify")

_SPOTIFY_URL = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist|artist)/"
    r"([A-Za-z0-9]{22})")

# Words that mean "a genre or a mood", not "this exact song" -- these should
# land on a playlist rather than a single track.
_MOOD_WORDS = {
    "jazz", "rock", "pop", "classical", "lofi", "lo-fi", "hip", "hop", "rap",
    "country", "metal", "blues", "funk", "soul", "electronic", "edm", "house",
    "techno", "ambient", "focus", "study", "chill", "relaxing", "workout",
    "gym", "party", "sleep", "coding", "instrumental", "acoustic", "indie",
    "reggae", "disco", "punk", "folk", "christmas", "music", "songs", "beats",
    "playlist", "mix", "radio", "vibes",
}

_STOPWORDS = {"the", "a", "an", "by", "some", "play", "song", "track", "on",
              "spotify", "please", "me", "my", "for", "of", "to"}


def is_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() == "spotify.exe":
                return True
        except psutil.Error:
            continue
    return False


def ensure_running(wait: float = 6.0) -> bool:
    """Start Spotify if it is not already up, and wait for it to appear."""
    if is_running():
        return True
    log.info("starting Spotify")
    try:
        os.startfile("spotify:")
    except Exception:
        try:
            subprocess.Popen(["spotify"], shell=True)
        except Exception:
            log.exception("could not start Spotify")
            return False

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if is_running():
            time.sleep(1.5)      # let it finish coming up before we drive it
            return True
        time.sleep(0.4)
    return False


def _score(kind: str, title: str, query: str, want_playlist: bool) -> float:
    """Rank a candidate. Search order alone is not good enough -- a plain
    search for a famous song frequently returns a cover version first."""
    title_words = set(re.findall(r"[a-z']+", title.lower()))
    query_words = {w for w in re.findall(r"[a-z']+", query.lower())
                   if w not in _STOPWORDS}
    if not query_words:
        return 0.0

    overlap = len(title_words & query_words) / len(query_words)
    score = overlap * 10

    # Prefer the kind that was actually asked for.
    if want_playlist:
        score += {"playlist": 6, "album": 2, "track": 0, "artist": 1}.get(kind, 0)
    else:
        score += {"track": 6, "album": 3, "artist": 2, "playlist": 1}.get(kind, 0)

    # Every query word present is a strong signal it is the real thing rather
    # than a cover or a tribute.
    if query_words <= title_words:
        score += 4
    return score


def resolve_uri(query: str, want_playlist: bool | None = None) -> tuple[str, str]:
    """Find a Spotify URI for a spoken request. Returns (uri, description)."""
    if want_playlist is None:
        words = set(re.findall(r"[a-z-]+", query.lower()))
        want_playlist = bool(words & _MOOD_WORDS)

    try:
        from ddgs import DDGS

        candidates: list[tuple[float, str, str, str]] = []
        with DDGS() as ddgs:
            for result in ddgs.text(f"{query} site:open.spotify.com",
                                    max_results=8):
                blob = " ".join(filter(None, [
                    result.get("href", ""), result.get("title", ""),
                    result.get("body", "")]))
                title = result.get("title", "")
                for kind, spotify_id in _SPOTIFY_URL.findall(blob):
                    candidates.append(
                        (_score(kind, title, query, want_playlist),
                         kind, spotify_id, title))
    except Exception as e:
        log.warning("spotify lookup failed: %s", e)
        return "", ""

    if not candidates:
        return "", ""

    candidates.sort(key=lambda c: -c[0])
    _, kind, spotify_id, title = candidates[0]
    # Spotify puts " | Spotify" and similar on the end of every page title.
    clean = re.split(r"\s*[|–—]\s*", title)[0].strip()
    return f"spotify:{kind}:{spotify_id}", clean or query


def _open_uri(uri: str) -> bool:
    try:
        os.startfile(uri)
        return True
    except Exception:
        log.exception("could not open %s", uri)
        return False


def _current_track() -> str:
    """What the active player reports right now, as 'title - artist'."""
    try:
        from .media import _run, _session

        async def read():
            session = await _session()
            if not session:
                return ""
            props = await session.try_get_media_properties_async()
            title = (props.title or "").strip()
            artist = (props.artist or "").strip()
            return f"{title} - {artist}".strip(" -")
        return _run(read()) or ""
    except Exception:
        log.debug("could not read the current track", exc_info=True)
        return ""


def _wait_for_change(before: str, timeout: float = 6.0) -> str:
    """Poll until what is playing changes. Returns the new track, or ''.

    This is the whole point of the module. Opening a Spotify URI is a request,
    not a guarantee: a track URI starts playing from idle but does not reliably
    interrupt something already going, and a playlist URI merely navigates to
    the page. Reporting "Playing X" on the strength of having asked is how
    "Playing the lofi beats playlist" was announced while ZZ Top kept going.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        current = _current_track()
        if current and current != before:
            return current
    return ""


def _pause() -> None:
    """Stop playback outright. Unlike the media key this is not a toggle, so
    it cannot accidentally start something."""
    try:
        from .media import _run, _session

        async def go():
            session = await _session()
            if session:
                await session.try_pause_async()
        _run(go())
    except Exception:
        log.debug("pause failed", exc_info=True)


def _send_play() -> None:
    """Press the play key. Resumes whatever the active player had queued."""
    try:
        import ctypes

        VK_MEDIA_PLAY_PAUSE = 0xB3
        KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
        ctypes.windll.user32.keybd_event(
            VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_EXTENDEDKEY, 0)
        ctypes.windll.user32.keybd_event(
            VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    except Exception:
        log.debug("media key failed", exc_info=True)


# ══════════════════════════════════════════════════════════════════
@tool(category="media")
def play_music(query: str = "") -> str:
    """Play music in Spotify. The main way to start playback.

    With no query this resumes whatever was last playing, which is what "play
    music" almost always means. With one, it finds and plays that.

    Args:
        query: A song, artist, album, playlist, genre or mood. Leave empty to
            simply resume.
    """
    if not ensure_running():
        return "I couldn't start Spotify."

    before = _current_track()

    # No target: resume whatever was queued. That is what "play music" means
    # nine times in ten, and it needs no search at all.
    if not query.strip():
        _send_play()
        playing = _wait_for_change(before, timeout=3.0) or _current_track()
        return f"Playing {playing}." if playing else "Spotify is playing."

    uri, description = resolve_uri(query)
    if not uri:
        _open_uri(f"spotify:search:{urllib.parse.quote(query)}")
        return (f"I couldn't find {query}, so I've put the search up in "
                f"Spotify for you.")

    # Measured, not assumed. A URI reliably starts playback only when Spotify
    # is NOT already playing -- opened over a running track it merely
    # navigates, and the requested song never starts:
    #
    #   open once (idle)      Take Five requested -> Take Five playing    HIT
    #   pause, then open      So What requested   -> So What playing      HIT
    #   open twice            Blue in Green       -> previous track       miss
    #   open, then play key   Round Midnight      -> previous track       miss
    #
    # So: stop what is going, then ask.
    if before:
        _pause()
        time.sleep(0.8)

    if not _open_uri(uri):
        return f"I found {description} but couldn't hand it to Spotify."

    # A playlist or album URI lands on the page rather than starting.
    if not uri.startswith("spotify:track:"):
        time.sleep(2.0)
        _send_play()

    # Report what is ACTUALLY playing, not what was requested.
    playing = _wait_for_change(before)
    if playing:
        return f"Playing {playing}."

    current = _current_track()
    if current and current == before:
        return (f"I've opened {description} in Spotify, but it's still playing "
                f"{before}. Press play to start it.")
    return (f"I've opened {description} in Spotify. It hasn't started on its "
            f"own -- say play, or press play.")


@tool(category="media")
def play_playlist(name: str) -> str:
    """Find and play a playlist by name or mood.

    Args:
        name: e.g. "lofi beats", "workout", "jazz for studying".
    """
    if not ensure_running():
        return "I couldn't start Spotify."
    uri, description = resolve_uri(name, want_playlist=True)
    if not uri:
        _open_uri(f"spotify:search:{urllib.parse.quote(name)}")
        return f"I couldn't find a {name} playlist, so here is the search."
    _open_uri(uri)
    time.sleep(1.6)
    _send_play()
    return f"Playing {description}."


@tool(category="media")
def open_spotify() -> str:
    """Open the Spotify app without changing what is playing."""
    return "Spotify is open." if ensure_running() else "I couldn't open Spotify."
