"""Tool registration.

A tool is a plain Python function with type hints and a Google-style docstring.
The decorator derives the JSON schema Ollama needs, so adding a new capability
is exactly one decorated function and nothing else.

    @tool(category="system")
    def set_volume(level: int) -> str:
        '''Set the master output volume.

        Args:
            level: Volume from 0 to 100.
        '''
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("jarvis.tools")

_PY_TO_JSON = {
    str: "string", int: "integer", float: "number",
    bool: "boolean", list: "array", dict: "object",
}


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable
    parameters: dict
    category: str = "general"
    destructive: bool = False
    speak_while_running: bool = False   # long tools get a spoken "one moment"
    is_async: bool = False
    aliases: list[str] = field(default_factory=list)

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @property
    def compact_schema(self) -> dict:
        """The same tool, described in as few tokens as possible.

        Every schema sent sits in the cached prompt prefix, and prompt
        evaluation on this machine runs at 99 tokens a second -- so each
        token here costs 10ms on every cache miss. The core set measured
        7,062 characters, of which 3,566 were argument documentation
        written for a person reading the source.

        The model needs the first line and the argument names. The
        reasoning, the examples and the history behind each tool stay in
        the docstring, where they are useful and free.
        """
        summary = (self.description or "").strip()
        # First paragraph only. Docstrings here open with the instruction
        # and then explain themselves at length; the model only reads the
        # instruction.
        summary = summary.split(chr(10) + chr(10))[0].strip()
        if len(summary) > 160:
            cut = summary.rfind(". ", 0, 160)
            summary = summary[:cut + 1] if cut > 40 else summary[:160]

        params = {"type": "object", "properties": {}, "required": []}
        source = self.parameters or {}
        for name, spec in (source.get("properties") or {}).items():
            lean = {"type": spec.get("type", "string")}
            hint = (spec.get("description") or "").strip()
            # One short line per argument, and nothing at all when the name
            # already says it.
            hint = hint.split(". ")[0].strip(" .")
            if hint and len(hint) <= 72 and hint.lower() != name.lower():
                lean["description"] = hint
            if "enum" in spec:
                lean["enum"] = spec["enum"]
            params["properties"][name] = lean
        params["required"] = list(source.get("required") or [])

        return {"type": "function",
                "function": {"name": self.name,
                             "description": summary,
                             "parameters": params}}


REGISTRY: dict[str, Tool] = {}


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into a summary and per-argument help."""
    if not doc:
        return "", {}
    doc = inspect.cleandoc(doc)
    parts = re.split(r"\n\s*(?:Args|Arguments|Params|Parameters):\s*\n", doc, maxsplit=1)
    summary = parts[0].strip()
    args: dict[str, str] = {}
    if len(parts) > 1:
        body = re.split(r"\n\s*(?:Returns|Raises|Examples?|Note):\s*\n",
                        parts[1], maxsplit=1)[0]
        current = None
        for line in body.splitlines():
            m = re.match(r"\s*(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)", line)
            if m:
                current = m.group(1)
                args[current] = m.group(2).strip()
            elif current and line.strip():
                args[current] += " " + line.strip()
    return summary, args


def _json_type(annotation: Any) -> dict:
    """Map a Python annotation onto a JSON-schema fragment."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _json_type(inner[0]) if inner else {"type": "string"}
    if origin in (list, typing.List):
        args = typing.get_args(annotation)
        return {"type": "array", "items": _json_type(args[0]) if args
                else {"type": "string"}}
    if origin in (dict, typing.Dict):
        return {"type": "object"}
    if isinstance(annotation, type) and issubclass(annotation, bool):
        return {"type": "boolean"}
    return {"type": _PY_TO_JSON.get(annotation, "string")}


def tool(
    _fn: Callable | None = None,
    *,
    name: str | None = None,
    category: str = "general",
    destructive: bool = False,
    speak_while_running: bool = False,
):
    """Register a function as a tool JARVIS can call."""

    def wrap(fn: Callable) -> Callable:
        summary, arg_docs = _parse_docstring(fn.__doc__ or "")
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn)

        properties: dict[str, dict] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            spec = _json_type(hints.get(pname, str))
            spec["description"] = arg_docs.get(pname, pname.replace("_", " "))

            annotation = hints.get(pname)
            if typing.get_origin(annotation) is typing.Literal:
                spec["enum"] = list(typing.get_args(annotation))

            properties[pname] = spec
            if param.default is inspect.Parameter.empty:
                required.append(pname)
            else:
                spec["description"] += f" (default: {param.default!r})"

        tool_name = name or fn.__name__
        REGISTRY[tool_name] = Tool(
            name=tool_name,
            description=summary or tool_name.replace("_", " "),
            fn=fn,
            parameters={"type": "object", "properties": properties,
                        "required": required},
            category=category,
            destructive=destructive,
            speak_while_running=speak_while_running,
            is_async=inspect.iscoroutinefunction(fn),
        )
        log.debug("registered tool %s (%s)", tool_name, category)
        return fn

    return wrap(_fn) if _fn else wrap


# ── access ─────────────────────────────────────────────────────────

def get(name: str) -> Tool | None:
    return REGISTRY.get(name)


async def execute(name: str, arguments: dict) -> str:
    """Run a tool. Never raises -- failures come back as text the LLM can read."""
    t = REGISTRY.get(name)
    if not t:
        return f"Error: no such tool '{name}'."

    # Models occasionally invent arguments; drop them rather than crash.
    valid = set(t.parameters.get("properties", {}))
    cleaned = {k: v for k, v in (arguments or {}).items() if k in valid}
    missing = [r for r in t.parameters.get("required", []) if r not in cleaned]
    if missing:
        return f"Error: {name} requires {', '.join(missing)}."

    # The objection, voiced once, before the thing is done -- not a
    # confirmation, which is a separate mechanism that asks first. Computed
    # before the call on purpose: once a shutdown has cancelled the timers
    # there is nothing left to mention.
    from ..cautions import caution_for

    warning = caution_for(name, cleaned)

    try:
        if t.is_async:
            result = await t.fn(**cleaned)
        else:
            result = await asyncio.to_thread(lambda: t.fn(**cleaned))
        text = str(result) if result is not None else "Done."
        if warning:
            text = f"{warning} {text}"
        # A tool result stays in the conversation for the rest of the
        # session, so its size is not paid once -- it is paid on every
        # subsequent turn, and four of these at the old 4,000 characters
        # filled the context window and forced the shift that threw away the
        # cached prefix. He answers in two spoken sentences; 1,200 characters
        # is more than enough to do that from.
        return text[:1200]
    except Exception as e:
        log.exception("tool %s failed", name)
        return f"Error running {name}: {e}"


def load_all() -> int:
    """Import every tool module so their decorators run."""
    from . import (browser, documents, extras, files, interact,  # noqa: F401
                   media, memory_tools, protocols, spotify, system,
                   text, timers, watches, web)

    return len(REGISTRY)
