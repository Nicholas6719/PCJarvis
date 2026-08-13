"""Browser and navigation.

Deliberately shell-level rather than automation-level: opening a URL through the
default browser works with Brave, Chrome, Edge or anything else, needs no
driver, no extension and no permissions, and cannot break when a browser
updates. The cost is that JARVIS cannot read the page back -- for that he has
read_webpage, which fetches the content directly.
"""
from __future__ import annotations

import logging
import os
import urllib.parse
import webbrowser

from .registry import tool

log = logging.getLogger("jarvis.tools.browser")

SITES = {
    "youtube": "https://youtube.com", "github": "https://github.com",
    "gmail": "https://mail.google.com", "google": "https://google.com",
    "reddit": "https://reddit.com", "twitter": "https://x.com",
    "x": "https://x.com", "amazon": "https://amazon.com",
    "netflix": "https://netflix.com", "spotify": "https://open.spotify.com",
    "maps": "https://maps.google.com", "drive": "https://drive.google.com",
    "calendar": "https://calendar.google.com", "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai", "wikipedia": "https://wikipedia.org",
    "stack overflow": "https://stackoverflow.com",
    "linkedin": "https://linkedin.com", "discord": "https://discord.com",
    "twitch": "https://twitch.tv", "ebay": "https://ebay.com",
    "espn": "https://espn.com", "weather": "https://weather.com",
    "news": "https://news.google.com", "outlook": "https://outlook.com",
    "notion": "https://notion.so", "figma": "https://figma.com",
}


def _open(url: str) -> bool:
    try:
        webbrowser.open(url)
        return True
    except Exception:
        try:
            os.startfile(url)
            return True
        except Exception:
            log.exception("could not open %s", url)
            return False


@tool(category="browser")
def open_website(site: str) -> str:
    """Open a website in the default browser.

    Args:
        site: A known name like "YouTube", or any domain or URL.
    """
    key = site.lower().strip().removeprefix("the ")
    url = SITES.get(key)
    if not url:
        cleaned = key.replace(" dot ", ".").replace(" ", "")
        if "." not in cleaned:
            return (f"I don't know a site called {site}. Give me the address "
                    f"and I'll open it.")
        url = cleaned if cleaned.startswith("http") else f"https://{cleaned}"
    return f"Opened {site}." if _open(url) else f"I couldn't open {site}."


@tool(category="browser")
def search_in_browser(query: str) -> str:
    """Open a web search in the browser for the user to look at themselves.

    For answering a question yourself, use web_search instead -- that returns
    results you can read. This one just puts it on screen.

    Args:
        query: What to search for.
    """
    url = f"https://duckduckgo.com/?q={urllib.parse.quote(query)}"
    return f"Searching for {query}." if _open(url) else "I couldn't open the browser."


@tool(category="browser")
def open_youtube_search(query: str) -> str:
    """Search YouTube and show the results.

    Args:
        query: What to look for.
    """
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    return f"Here are the YouTube results for {query}." if _open(url) \
        else "I couldn't open YouTube."


@tool(category="browser")
def get_directions(destination: str, origin: str = "") -> str:
    """Open route directions to a place.

    Args:
        destination: Where to go.
        origin: Starting point. Leave empty for the current location.
    """
    params = {"api": "1", "destination": destination}
    if origin.strip():
        params["origin"] = origin
    url = "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(params)
    return (f"Directions to {destination} are on screen." if _open(url)
            else "I couldn't open the map.")


@tool(category="browser")
def open_folder(name: str) -> str:
    """Open a folder in File Explorer.

    Args:
        name: Downloads, Documents, Desktop, Pictures, Music, Videos, Home,
            or a full path.
    """
    from pathlib import Path

    known = {
        "downloads": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
        "desktop": Path.home() / "Desktop",
        "pictures": Path.home() / "Pictures",
        "music": Path.home() / "Music",
        "videos": Path.home() / "Videos",
        "home": Path.home(),
        "jarvis": Path.home() / "Documents" / "JARVIS",
        "screenshots": Path.home() / "Pictures" / "JARVIS",
    }
    key = name.lower().strip().removeprefix("my ").removeprefix("the ")
    target = known.get(key, Path(name).expanduser())
    if not target.exists():
        return f"I can't find a folder called {name}."
    try:
        os.startfile(str(target))
        return f"Opened {target.name or str(target)}."
    except Exception as e:
        return f"I couldn't open that folder: {e}"
