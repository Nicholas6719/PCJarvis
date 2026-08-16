"""Named routines, invoked the way Tony invokes them.

    "JARVIS, initiate the House Party protocol."

One phrase, a dozen things at once. It is the most recognisable thing JARVIS
does that has nothing to do with a suit, and it maps onto a laptop almost
exactly: a named sequence of steps, defined once, run by name. "Work mode."
"Good night." Nothing here is clever -- the whole value is that the name is
shorter than the list.

Two decisions worth explaining.

Destructive tools are refused outright. Canon's Clean Slate protocol destroys
every suit Tony owns on a single spoken phrase, which is wonderful in a film
and indefensible on a real machine: a voice macro that can shut the computer
down or run a shell command turns one misheard word into real damage. Those
tools still work when asked for directly, where the existing confirmation gate
catches them. A protocol is a convenience, and convenience is exactly the wrong
place to put an irreversible action.

A failing step does not stop the run. Half a protocol is usually better than
none -- if the volume cannot be set, that is no reason to skip locking the
screen -- so every step is attempted and anything that failed is reported at
the end rather than swallowed.
"""
from __future__ import annotations

import json
import logging
from ..config import DATA_DIR
from .registry import tool

log = logging.getLogger("jarvis.tools.protocols")

STORE = DATA_DIR / "protocols.json"

# Shipped so the feature is usable the moment it exists, rather than being an
# empty box he has to fill before it does anything. Both are deliberately dull
# and reversible, and both are his to rewrite.
DEFAULTS: dict = {
    "work": {
        "description": "Quiet the machine down and report anything amiss.",
        "steps": [
            {"tool": "pause_media", "args": {}},
            {"tool": "set_volume", "args": {"level": 20}},
            {"tool": "get_system_stats", "args": {"component": "memory"}},
        ],
    },
    "good night": {
        "description": "Stop the music, turn the screen down, lock up.",
        "steps": [
            {"tool": "pause_media", "args": {}},
            {"tool": "set_brightness", "args": {"level": 20}},
            {"tool": "lock_screen", "args": {}},
        ],
    },
}


def _load() -> dict:
    if not STORE.exists():
        _save(DEFAULTS)
        return json.loads(json.dumps(DEFAULTS))
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        log.exception("protocols file is unreadable; falling back to defaults")
        return json.loads(json.dumps(DEFAULTS))


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _normalise(name: str) -> str:
    """Match how he says it, not how it was typed.

    Spoken, "the work protocol", "work mode" and "Work" are all the same
    thing, and none of them should need to be typed exactly.
    """
    name = (name or "").lower().strip().strip(".!?")
    for noise in ("the ", "protocol", "mode", "routine"):
        name = name.replace(noise, " ")
    return " ".join(name.split())


def exists(name: str) -> bool:
    """Used as an intent guard, so "work mode" only routes here if defined."""
    wanted = _normalise(name)
    return any(_normalise(k) == wanted for k in _load())


def _find(name: str) -> tuple[str, dict] | None:
    wanted = _normalise(name)
    for key, body in _load().items():
        if _normalise(key) == wanted:
            return key, body
    return None


# ══════════════════════════════════════════════════════════════════
@tool(category="protocols")
async def run_protocol(name: str) -> str:
    """Run a named protocol: a saved sequence of actions.

    Use for "initiate the work protocol", "run good night", "work mode".

    Args:
        name: Which protocol. The wording is forgiving -- "work", "work mode"
            and "the work protocol" all mean the same one.
    """
    from . import registry

    found = _find(name)
    if not found:
        known = ", ".join(_load()) or "none"
        return (f"I have no protocol by that name. I know: {known}.")

    key, body = found
    steps = body.get("steps") or []
    if not steps:
        return f"The {key} protocol has no steps in it."

    failures: list[str] = []
    reports: list[str] = []

    for step in steps:
        tool_name = step.get("tool", "")
        args = step.get("args") or {}
        spec = registry.REGISTRY.get(tool_name)
        if spec is None:
            failures.append(f"{tool_name} no longer exists")
            continue
        if spec.destructive:
            # Should have been caught when it was saved; refuse again here in
            # case the file was edited by hand.
            failures.append(f"{tool_name} is not something I will run unasked")
            continue
        try:
            result = await registry.execute(tool_name, args)
            if step.get("report"):
                reports.append(str(result))
        except Exception as e:
            log.exception("protocol step failed: %s", tool_name)
            failures.append(f"{tool_name} failed ({e})")

    # Say the count only when it is news. "Work protocol. 3 of 3 steps done"
    # is a progress bar read aloud; when everything worked, the name is the
    # whole confirmation.
    line = f"{key.capitalize()} protocol."
    if failures:
        done = len(steps) - len(failures)
        line += f" {done} of {len(steps)} done -- " + "; ".join(failures) + "."
    if reports:
        line += " " + " ".join(reports)
    return line


@tool(category="protocols")
def list_protocols() -> str:
    """List the named protocols that are defined."""
    data = _load()
    if not data:
        return "No protocols are defined."
    parts = [f"{name} ({body.get('description', '').rstrip('.').lower()})"
             if body.get("description") else name
             for name, body in data.items()]
    return "You have " + ", and ".join(parts) + "."


@tool(category="protocols")
def create_protocol(name: str, steps: str, description: str = "") -> str:
    """Define a new named protocol, or replace an existing one.

    Args:
        name: What to call it, e.g. "focus" or "good morning".
        steps: A JSON list of steps, each {"tool": "...", "args": {...}},
            using the exact tool names you already have.
        description: One short line on what it does.
    """
    from . import registry

    clean = (name or "").strip().strip(".!?")
    if not clean:
        return "Give the protocol a name."

    try:
        parsed = json.loads(steps) if isinstance(steps, str) else steps
    except Exception:
        return "I could not read those steps. They need to be a JSON list."
    if not isinstance(parsed, list) or not parsed:
        return "A protocol needs at least one step."

    checked = []
    for step in parsed:
        if not isinstance(step, dict) or "tool" not in step:
            return "Every step needs a tool name."
        tool_name = step["tool"]
        spec = registry.REGISTRY.get(tool_name)
        if spec is None:
            return f"There is no tool called {tool_name}."
        if spec.destructive:
            return (f"I will not put {tool_name} in a protocol. A saved phrase "
                    f"that does something irreversible turns one misheard word "
                    f"into real damage. Ask me directly and I will confirm it "
                    f"first.")
        checked.append({"tool": tool_name, "args": step.get("args") or {},
                        "report": bool(step.get("report"))})

    data = _load()
    existed = any(_normalise(k) == _normalise(clean) for k in data)
    for key in list(data):
        if _normalise(key) == _normalise(clean):
            del data[key]
    data[clean] = {"description": description.strip(), "steps": checked}
    _save(data)

    verb = "Replaced" if existed else "Saved"
    return f"{verb} the {clean} protocol, {len(checked)} steps."


@tool(category="protocols")
def delete_protocol(name: str) -> str:
    """Delete a named protocol.

    Args:
        name: Which one to remove.
    """
    data = _load()
    for key in list(data):
        if _normalise(key) == _normalise(name):
            del data[key]
            _save(data)
            return f"Deleted the {key} protocol."
    return f"I have no protocol called {name}."
