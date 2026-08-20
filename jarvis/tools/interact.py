"""Clicking things, by name rather than by coordinate.

Screenshot-and-click was the wrong foundation, and this is why: I asked Windows
what buttons a real Wikipedia page had, and it said none -- a browser's actual
content is invisible to this whole layer unless the browser is told to expose
it, which Chromium does not do by default. What Windows WILL answer honestly is
its own accessibility tree, the one screen readers use, and that tree is
accurate: asked about the taskbar just now it named every pinned icon
correctly, including a two-line status button. So this reaches native Windows
applications only. A web page's own content is a different problem for a
different tool, and pretending this one already solves it would be exactly the
kind of confident, untested claim this whole project has tried to avoid.

The other half of the design is what NOT to click. A misheard word landing on
"Delete Account" or "Buy Now" is real damage in a way that a wrong Spotify
track is not, so anything that reads as a purchase or a destructive action is
refused outright -- not cautioned about and then done anyway, the way a
shutdown is. See jarvis.refusals.
"""
from __future__ import annotations

import logging

from .registry import tool

log = logging.getLogger("jarvis.tools.interact")

# Clickable in the ordinary sense. Deliberately excludes Edit, Text, Pane and
# friends -- this tool presses things, it does not type into them or read them.
_CLICKABLE = ("Button", "Hyperlink", "MenuItem", "TabItem",
              "CheckBox", "RadioButton", "SplitButton", "ListItem")

# Shared with jarvis.browsing -- a wrong click has the same real-world
# consequence whether it lands on a native control or a web element, and
# two separate copies of this list invite them to drift apart.
from ..refusals import DANGEROUS as _DANGEROUS


def _foreground_window():
    """The window he is looking at, as a pywinauto UIA element."""
    from pywinauto import Desktop

    win = Desktop(backend="uia").window(active_only=True)
    # Never our own reflection: clicking our own TALK button from inside a
    # tool call is a loop, not a feature.
    title = (win.window_text() or "")
    if "J.A.R.V.I.S" in title:
        raise LookupError("that is JARVIS's own window")
    return win


def _named_window(app: str):
    from pywinauto import Desktop

    needle = app.lower().strip()
    for w in Desktop(backend="uia").windows():
        title = (w.window_text() or "")
        if needle in title.lower() and "J.A.R.V.I.S" not in title:
            return w
    # Falling off the end returned None, which then flowed through the
    # whole click path and came back as "I do not see anything clickable in
    # that window" -- implying the window exists and is empty. Naming a
    # window that is not open should say so.
    raise LookupError(f"no window matching {app!r}")


def _looks_like_a_browser(window) -> bool:
    """Any Chromium/Firefox-family window, ours or not.

    Reusing browser.py's own name list rather than a second copy that could
    silently stop matching a new Edge title format or similar.
    """
    try:
        from .browser import BROWSERS

        title = (window.window_text() or "").lower()
        return any(b in title for b in BROWSERS)
    except Exception:
        return False


def _clickable(window) -> list:
    seen = []
    for ctrl_type in _CLICKABLE:
        try:
            seen.extend(window.descendants(control_type=ctrl_type))
        except Exception:
            continue
    # Only things with an actual name are namable at all, and only things
    # currently visible -- an off-screen menu item is not something he meant.
    out = []
    for c in seen:
        try:
            name = (c.window_text() or c.element_info.name or "").strip()
            if name and c.is_visible():
                out.append((name, c))
        except Exception:
            continue
    return out


def _best_match(text: str, controls: list) -> tuple:
    """(name, control) for the closest match, or (None, None)."""
    needle = text.lower().strip()

    for name, ctrl in controls:
        if name.lower() == needle:
            return name, ctrl
    for name, ctrl in controls:
        if needle in name.lower():
            return name, ctrl

    import difflib

    names = [n for n, _ in controls]
    close = difflib.get_close_matches(text, names, n=1, cutoff=0.6)
    if close:
        for name, ctrl in controls:
            if name == close[0]:
                return name, ctrl
    return None, None


@tool(category="system")
def click_button(text: str, app: str = "") -> str:
    """Click a button, link or control -- in a native application, or on a
    web page if JARVIS's own browser is what is in front right now.

    Windows own accessibility layer cannot see a web page's actual content
    (confirmed: it found zero links on a real page), so a page open in his
    ordinary browser is not reachable this way. A page opened through
    search_site or open_website, in JARVIS's own browser window, is.

    Args:
        text: The name of the button, link or control, as it reads on screen.
        app: Which application. Leave empty for whatever is in front.
    """
    if not app.strip():
        try:
            from ..browsing import is_foreground

            if is_foreground():
                from ..browsing import find_and_click

                return find_and_click(text)
        except Exception:
            log.debug("could not check for the managed browser", exc_info=True)

    try:
        window = _named_window(app) if app.strip() else _foreground_window()
    except LookupError as e:
        return str(e).capitalize() + "."
    except Exception as e:
        log.exception("could not reach the window")
        return f"I could not look at that window: {e}"

    if _looks_like_a_browser(window):
        # Reaching here means the CDP path above was not taken -- either this
        # is not JARVIS's own browser, or it did not currently have focus.
        # Scanning it anyway would answer with the browser's OWN toolbar:
        # asked to click "sign in" on Amazon, this silently found Brave's own
        # account button instead and reported it clicked, which is a worse
        # failure than admitting it cannot see the page.
        return ("I cannot see the content of that page -- only pages opened "
                "through me can be clicked into. Try asking me to search for "
                "it first.")

    try:
        controls = _clickable(window)
    except Exception as e:
        log.exception("could not enumerate controls")
        return f"I could not see what is clickable there: {e}"

    if not controls:
        return "I do not see anything clickable in that window."

    name, ctrl = _best_match(text, controls)
    if ctrl is None:
        # Honest about what it actually saw, rather than a bare failure --
        # he can often just repeat the request with the real name.
        visible = ", ".join(n for n, _ in controls[:8])
        return (f"I do not see anything called {text!r} there. "
                f"What I can see: {visible}.")

    if _DANGEROUS.search(name):
        return (f"{name!r} looks like it would buy, delete or discard "
                f"something. I will not click that one myself -- you will "
                f"need to do it.")

    try:
        ctrl.click_input()
    except Exception as e:
        log.exception("click failed")
        return f"I found {name!r} but could not click it: {e}"

    return f"Clicked {name!r}."


@tool(category="system")
def list_clickable(app: str = "") -> str:
    """List what can be clicked in a Windows application right now.

    Use before click_button when he is not sure of the exact wording, or when
    a previous click_button could not find a match.

    Args:
        app: Which application. Leave empty for whatever is in front.
    """
    try:
        window = _named_window(app) if app.strip() else _foreground_window()
    except LookupError as e:
        return str(e).capitalize() + "."
    except Exception as e:
        return f"I could not look at that window: {e}"

    try:
        controls = _clickable(window)
    except Exception as e:
        return f"I could not see what is clickable there: {e}"

    if not controls:
        return "Nothing clickable in that window."

    names = [n for n, _ in controls[:15]]
    return "I can see: " + ", ".join(names) + "."
