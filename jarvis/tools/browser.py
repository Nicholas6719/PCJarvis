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
import re
import threading
import time
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


# Verbs the model leaves attached to the target: "go to youtube", "open up
# reddit". Stripping spaces without removing these produced "gotoyoutube",
# which matched nothing and failed the request.
_LEAD = re.compile(
    r"^(?:please\s+)?(?:go\s+to|open\s+up|open|take\s+me\s+to|pull\s+up|"
    r"bring\s+up|show\s+me|navigate\s+to|visit|launch)\s+", re.I)
_TRAIL = re.compile(r"\s+(?:for\s+me|please|website|site|page|dot\s*com)$", re.I)


def _force_foreground(hwnd) -> bool:
    """Actually bring a window to the front.

    Windows refuses SetForegroundWindow to a process that does not own the
    active window -- which is exactly our situation, since JARVIS is the active
    window when he is asked to open something. The accepted way round it is to
    attach to the foreground thread's input queue for the duration of the call.
    Without this the browser opens behind everything, which is what happened.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9

    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)

        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        current_thread = kernel32.GetCurrentThreadId()
        foreground = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(foreground, None)

        attached = []
        for thread in {target_thread, fg_thread}:
            if thread and thread != current_thread:
                if user32.AttachThreadInput(current_thread,
                                            wintypes.DWORD(thread), True):
                    attached.append(thread)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            for thread in attached:
                user32.AttachThreadInput(current_thread,
                                         wintypes.DWORD(thread), False)
        return bool(user32.GetForegroundWindow() == hwnd)
    except Exception:
        log.debug("could not force the window forward", exc_info=True)
        return False


def _foreground(timeout: float = 6.0) -> bool:
    """Wait for a new window to appear and put it in front.

    Also asks JARVIS to step aside. If he is asked to open something, he should
    get out of the way -- there is no point opening YouTube behind a
    full-screen assistant.
    """
    try:
        import pygetwindow as gw

        from ..bus import BUS
    except Exception:
        return False

    try:
        before = {w._hWnd for w in gw.getAllWindows() if w.title.strip()}
    except Exception:
        before = set()

    # Step aside first, so the new window has somewhere to land.
    try:
        BUS.emit_threadsafe("window.minimize")
    except Exception:
        pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.2)
        try:
            fresh = [w for w in gw.getAllWindows()
                     if w.title.strip() and w._hWnd not in before]
        except Exception:
            continue
        if fresh:
            time.sleep(0.4)          # let it finish drawing
            return _force_foreground(fresh[-1]._hWnd)
    return False


def _open(url: str, foreground: bool = True) -> bool:
    """Launch, and bring forward WITHOUT making him wait for it.

    Waiting for the browser window to appear took six seconds, all of it
    silence before he said "opened YouTube". The launch either works or it
    does not -- the arranging of windows afterwards is not something the
    reply should be held up for.
    """
    try:
        webbrowser.open(url)
    except Exception:
        try:
            os.startfile(url)
        except Exception:
            log.exception("could not open %s", url)
            return False
    if foreground:
        threading.Thread(target=_foreground, daemon=True).start()
    return True


def _open_to_interact(url: str) -> bool:
    """Open somewhere he can be asked to click something afterward.

    _open() launches his ordinary browser -- his own logins, gone the moment
    a purchase would be needed rather than an assistant defaulting to being
    able to buy things. This is the other one: JARVIS's own browser, kept
    open across calls, that click_button can actually reach through the
    DevTools protocol. Windows own accessibility layer cannot see a page's
    content at all regardless of which browser shows it -- confirmed
    directly, zero links found on a real page -- so this is the only one of
    the two that anything can click into afterward.

    Falls back to the ordinary browser if the managed one cannot start for
    any reason, so a missing Brave install degrades to today's behaviour
    rather than a failure he has never seen before.
    """
    try:
        from .. import browsing

        if browsing.navigate(url):
            threading.Thread(target=browsing.bring_to_front,
                            daemon=True).start()
            return True
    except Exception:
        log.debug("managed browser unavailable, falling back", exc_info=True)
    return _open(url)


# Where a search actually lives on each site. YouTube had one of these and
# nothing else did, which is why asking for a specific comic on Amazon
# produced the Amazon homepage: the model had no way to express "search
# Amazon", so it handed the whole sentence to open_website, which stripped it
# back to a bare domain and opened that.
SITE_SEARCH = {
    "amazon":        "https://www.amazon.com/s?k={q}",
    "youtube":       "https://www.youtube.com/results?search_query={q}",
    "ebay":          "https://www.ebay.com/sch/i.html?_nkw={q}",
    "google":        "https://www.google.com/search?q={q}",
    "duckduckgo":    "https://duckduckgo.com/?q={q}",
    "reddit":        "https://www.reddit.com/search/?q={q}",
    "wikipedia":     "https://en.wikipedia.org/w/index.php?search={q}",
    "github":        "https://github.com/search?q={q}",
    "imdb":          "https://www.imdb.com/find/?q={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
    "spotify":       "https://open.spotify.com/search/{q}",
    "maps":          "https://www.google.com/maps/search/{q}",
    "twitter":       "https://twitter.com/search?q={q}",
    "x":             "https://twitter.com/search?q={q}",
    "netflix":       "https://www.netflix.com/search?q={q}",
    "bestbuy":       "https://www.bestbuy.com/site/searchpage.jsp?st={q}",
    "walmart":       "https://www.walmart.com/search?q={q}",
    "etsy":          "https://www.etsy.com/search?q={q}",
}


def _site_key(text: str) -> str:
    """Which known site a phrase refers to, if any."""
    low = (text or "").lower()
    for name in SITE_SEARCH:
        if name in low:
            return name
    return ""


@tool(category="browser")
def search_site(site: str, query: str) -> str:
    """Search a particular website and show the results.

    Use whenever he names a site and a thing: "find the Spider-Man comic on
    Amazon", "search YouTube for the trailer", "look for that on eBay".
    This is what to call instead of open_website when there is something
    specific to look for -- open_website only opens a front page.

    Args:
        site: The site, e.g. "amazon", "youtube", "ebay", "reddit".
        query: What to look for on it.
    """
    wanted = _site_key(site) or _site_key(query)
    term = (query or "").strip()
    if not term:
        return "What should I look for?"

    if not wanted:
        # Unknown site: a site-scoped search on a real engine still puts him
        # in front of the right results, which beats refusing.
        domain = (site or "").strip().lower().replace(" ", "")
        if not domain:
            return search_in_browser(term)
        if "." not in domain:
            domain += ".com"
        url = ("https://duckduckgo.com/?q="
               + urllib.parse.quote(f"site:{domain} {term}"))
        return (f"Searching {domain} for {term}." if _open(url)
                else f"I could not open {domain}.")

    url = SITE_SEARCH[wanted].format(q=urllib.parse.quote(term))
    opened = _open_to_interact(url)

    # Cards on screen as well as the real page in his browser. Drawn from a
    # site-scoped search rather than by embedding the site: Amazon sends no
    # framing headers today, but sites break out of frames with JavaScript and
    # change those headers without warning, so an embed works right up until
    # the morning it silently does not.
    try:
        from ddgs import DDGS

        from ..panel import show

        with DDGS() as ddgs:
            hits = list(ddgs.text(f"site:{wanted}.com {term}", max_results=4))
        if hits:
            show("results", title=f"{wanted} · {term}",
                 items=[{"title": (h.get("title") or "")[:90],
                         "snippet": (h.get("body") or "").replace(chr(10), " ")[:120],
                         "url": h.get("href") or h.get("link") or ""}
                        for h in hits])
    except Exception:
        log.debug("could not fetch cards for the site search", exc_info=True)

    return (f"Here are the {wanted.capitalize()} results for {term}."
            if opened else f"I could not open {wanted}.")


def _salvage_query(text: str, site: str) -> str:
    """Pull a usable search term out of a sentence about a site.

    What arrived was a whole product listing: "Amazon.com: The Amazing
    Spider-Man Comic: Check each product page for other buying options.
    Price and other details may vary based on product size and color."
    Searching for all of that finds nothing, so the site name goes, the
    listing boilerplate goes, and what is left is capped -- a search term
    longer than a few words is noise rather than precision.

    Written without a regex on purpose. The first version used one, a
    stray escape put a control character in the pattern, and it silently
    matched nothing at all while looking perfectly correct.
    """
    # Everything from here on is stock listing text, never the title.
    noise = ("check each", "price and other", "may vary", "free delivery",
             "in stock", "other buying", "product page", "see options",
             "learn more", "shop now", "best seller")
    lowered = text.lower()
    cut = len(text)
    for phrase in noise:
        found = lowered.find(phrase)
        if found > 0:
            cut = min(cut, found)
    trimmed = text[:cut]

    words = []
    for raw in trimmed.replace(":", " ").replace("|", " ").split():
        token = raw.strip(" .,;")
        if not token:
            continue
        low = token.lower()
        if low == site or low.startswith(site + "."):
            continue          # the site name is not part of the query
        words.append(token)
        if len(words) >= 8:
            break
    return " ".join(words).strip(" .,")


@tool(category="browser")
def open_website(site: str) -> str:
    """Open a website in the default browser.

    Args:
        site: A known name like "YouTube", or any domain or URL.
    """
    key = _TRAIL.sub("", _LEAD.sub("", site.strip())).strip()
    key = key.lower().removeprefix("the ").strip(" .,")

    # A whole sentence with a site name in it means he wants something ON that
    # site, not its front page. This arrived once as "Amazon.com: The Amazing
    # Spider-Man Comic: Check each product page for other buying options",
    # which was truncated to "amazon.com" and opened the homepage -- twice,
    # because the second attempt did exactly the same thing.
    if len(key) > 28 and " " in key:
        known = _site_key(key)
        if known:
            salvaged = _salvage_query(key, known)
            if salvaged:
                return search_site(known, salvaged)

    url = SITES.get(key)
    if not url:
        cleaned = key.replace(" dot ", ".").replace(" ", "")
        # Try the bare name as a .com before giving up: "netflix" should work
        # even if it is not in the map.
        if "." not in cleaned:
            if cleaned.isalnum() and len(cleaned) > 2:
                url = f"https://{cleaned}.com"
            else:
                return (f"I don't know a site called {site}. Give me the "
                        f"address and I'll open it.")
        else:
            url = cleaned if cleaned.startswith("http") else f"https://{cleaned}"

    return f"Opened {key}." if _open(url) else f"I couldn't open {key}."


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
    else:
        # Google Maps can use the browser's location, but only after a
        # permission prompt that often never gets answered. An approximate
        # origin from the IP means the route just appears. Failing that, Maps
        # still opens and asks -- either way he gets directions rather than a
        # refusal about not knowing where he is.
        here = _approximate_location()
        if here:
            params["origin"] = here

    url = "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(params)
    return (f"Directions to {destination} are on screen." if _open(url)
            else "I couldn't open the map.")


def _approximate_location() -> str:
    try:
        import httpx

        with httpx.Client(timeout=4.0) as client:
            data = client.get("http://ip-api.com/json/").json()
        if data.get("status") == "success":
            return f"{data['lat']},{data['lon']}"
    except Exception:
        log.debug("could not resolve an approximate location", exc_info=True)
    return ""


@tool(category="browser")
def open_folder(name: str) -> str:
    """Open a folder in File Explorer.

    Args:
        name: Downloads, Documents, Desktop, Pictures, Music, Videos, Home,
            or a full path.
    """
    from pathlib import Path

    from ..folders import save_folder

    # Resolved through the shell, not guessed. "Desktop" here has to be the one
    # that actually appears on screen -- opening the unredirected folder shows
    # him an empty directory and looks like a bug.
    known = {
        "downloads": save_folder("downloads"),
        "documents": save_folder("documents"),
        "desktop": save_folder("desktop"),
        "pictures": save_folder("pictures"),
        "music": save_folder("music"),
        "videos": save_folder("videos"),
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
        threading.Thread(target=_foreground, daemon=True).start()
        return f"Opened {target.name or str(target)}."
    except Exception as e:
        return f"I couldn't open that folder: {e}"


# ══════════════════════════════════════════════════════════════════
#  What is on screen
# ══════════════════════════════════════════════════════════════════
# Read from the window title, which is the only thing available without
# automating the browser itself. It is enough for "what page am I on", and it
# costs nothing -- no extension, no debugging port, no second process.
BROWSERS = ("google chrome", "mozilla firefox", "microsoft edge", "brave",
            "opera", "vivaldi", "chromium")

# Chrome and Edge join with a hyphen, Firefox with an em dash. Edge also puts
# a zero-width space inside its own name, which is why matching is done on a
# normalised copy rather than the raw title.
_SEPARATORS = (" - ", " — ", " – ")


def _normalise(title: str) -> str:
    return "".join(c for c in title if c.isprintable() and ord(c) < 0x2000).lower()


def _split_browser_title(title: str) -> tuple[str, str] | None:
    """(page, browser) if this looks like a browser window, else None."""
    flat = _normalise(title)
    browser = next((b for b in BROWSERS if flat.endswith(b)), None)
    if browser is None:
        return None
    cut = max((title.rfind(s) for s in _SEPARATORS), default=-1)
    page = title[:cut].strip() if cut > 0 else ""
    # Edge appends "and 4 more pages" and a profile name; drop the noise.
    for noise in (" and ", " - Personal", " - Work"):
        if noise in page:
            page = page.split(noise)[0].strip()
    return (page or "a blank tab"), browser.title()


@tool(category="browser")
def current_page() -> str:
    """Say what web page is currently open.

    Use for "what page am I on", "what am I looking at", "what site is this".
    """
    try:
        from .. import browsing

        if browsing.is_foreground():
            url = browsing.current_url()
            if url:
                return f"You are on {url}."
    except Exception:
        log.debug("could not check the managed browser", exc_info=True)

    try:
        import pygetwindow as gw

        active = gw.getActiveWindow()
        if active is not None and active.title.strip():
            found = _split_browser_title(active.title)
            if found:
                page, browser = found
                return f"You are on {page}, in {browser}."

        # Not focused on a browser. Rather than say nothing useful, look for a
        # browser window anywhere and be explicit that it is not the front one.
        for w in gw.getAllWindows():
            if not w.title.strip():
                continue
            found = _split_browser_title(w.title)
            if found:
                page, browser = found
                return (f"Your browser is not in front at the moment. The page "
                        f"open in {browser} is {page}.")

        front = active.title.strip() if active is not None else ""
        if front:
            return f"No browser open. You are in {front}."
        return "I cannot see a browser window open."
    except Exception as e:
        return f"Could not tell what is on screen: {e}"


@tool(category="browser")
def open_new_tab() -> str:
    """Open a new, empty tab in the browser that is already open."""
    try:
        import pyautogui
        import pygetwindow as gw

        target = None
        for w in gw.getAllWindows():
            if w.title.strip() and _split_browser_title(w.title):
                target = w
                break
        if target is None:
            return "No browser is open, so there is nothing to add a tab to."
        try:
            target.activate()
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "t")
        return "New tab open."
    except Exception as e:
        return f"Could not open a tab: {e}"


@tool(category="browser")
def close_tab() -> str:
    """Close the browser tab that is currently in front.

    Only ever closes the focused browser tab, never the window and never
    another application -- and it says how to undo it, because a tab closed by
    mistake is a form half filled in and lost.
    """
    try:
        import pyautogui
        import pygetwindow as gw

        active = gw.getActiveWindow()
        if active is None or not active.title.strip():
            return "I cannot tell which window is in front, so I have left it alone."
        found = _split_browser_title(active.title)
        if not found:
            # Refusing here matters: ctrl+w in the wrong application closes a
            # document, not a tab.
            return (f"The window in front is {active.title.strip()}, which is "
                    f"not a browser, so I have not closed anything.")
        page, browser = found
        pyautogui.hotkey("ctrl", "w")
        return f"Closed {page}. Control shift T brings it back."
    except Exception as e:
        return f"Could not close the tab: {e}"
