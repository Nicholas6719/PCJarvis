"""What he remembers, as markdown files you can read and edit.

The facts used to live only inside a SQLite database. That worked, and it was
completely opaque: there was no way to see what JARVIS believed about you
without a database client, no way to correct a fact he had misheard, and no way
to add one without saying it out loud.

So the facts are markdown files now. One file per fact, YAML frontmatter on top,
the fact itself as the body -- which happens to be exactly what an Obsidian
vault is. Obsidian is a folder of plain markdown and nothing else; it watches
the folder and picks up outside changes on its own, so JARVIS writing files
directly is safe and needs no plugin, no API and no account. Point Obsidian at
the folder and you get search, links and a graph over everything he knows. Do
not install it and you still have a folder of readable text.

SQLite has not gone anywhere. It is the *index* now rather than the truth: the
embeddings that make recall semantic rather than literal live there, and
rebuilding them from the files is cheap. The files win every disagreement, so
deleting one is how you make him forget something, and editing one is how you
correct him.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from pathlib import Path

log = logging.getLogger("jarvis.vault")

_SLUG = re.compile(r"[^a-z0-9]+")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)

README = """# JARVIS memory

Every file in this folder is one thing JARVIS knows about you.

- **Delete a file** and he forgets it.
- **Edit a file** and he believes the new version.
- **Add a file** -- any markdown with a line of text in it -- and he learns it.

He re-reads this folder whenever he looks something up, so changes take effect
without restarting him.

This is an ordinary folder of markdown. Opening it as an Obsidian vault gives
you search, links and a graph across it, but nothing here needs Obsidian, and
nothing here is in a format only Obsidian can read.
"""


def slugify(text: str, limit: int = 60) -> str:
    out = _SLUG.sub("-", text.lower()).strip("-")
    return (out[:limit].rstrip("-") or "fact")


def parse(path: Path) -> dict | None:
    """Read one fact file. Returns None if there is nothing usable in it."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        log.debug("could not read %s", path, exc_info=True)
        return None

    meta: dict[str, str] = {}
    body = raw
    m = _FRONTMATTER.match(raw)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip().strip('"')
        body = m.group(2)

    # The fact is the first non-empty, non-heading line. Anything else in the
    # file is his own notes, and they are none of our business.
    content = ""
    for line in body.splitlines():
        line = line.strip().lstrip("-*").strip()
        if line and not line.startswith("#"):
            content = line
            break
    if not content:
        return None

    return {
        "content": content,
        "category": meta.get("category", "general"),
        "created": meta.get("created", ""),
        "path": str(path),
        "mtime": path.stat().st_mtime,
    }


def write(folder: Path, content: str, category: str = "general") -> Path:
    """Save one fact. Returns the file written."""
    folder.mkdir(parents=True, exist_ok=True)

    readme = folder / "README.md"
    if not readme.exists():
        readme.write_text(README, encoding="utf-8")

    stem = slugify(content)
    path = folder / f"{stem}.md"
    n = 2
    while path.exists() and (parse(path) or {}).get("content") != content:
        path = folder / f"{stem}-{n}.md"
        n += 1

    path.write_text(
        "---\n"
        f"category: {category}\n"
        f"created: {date.today().isoformat()}\n"
        "---\n\n"
        f"{content}\n",
        encoding="utf-8")
    return path


def scan(folder: Path) -> list[dict]:
    """Every fact currently in the vault.

    README.md is skipped by name: it explains the folder rather than being a
    thing he knows, and without this he would confidently recall that every
    file in the folder is one thing JARVIS knows about you.
    """
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        fact = parse(path)
        if fact:
            out.append(fact)
    return out


def fingerprint(folder: Path) -> tuple:
    """Cheap signature of the folder, to notice edits made outside JARVIS.

    Names and modification times only -- no file is opened. This runs before
    every recall, so it has to cost nothing when nothing has changed.
    """
    if not folder.is_dir():
        return ()
    try:
        # Nanoseconds and size, not whole seconds. Truncating to the second
        # meant a file edited in the same second as the last scan looked
        # untouched, so a correction made straight after a question was
        # silently ignored -- which is exactly when corrections get made.
        return tuple(sorted(
            (p.name, p.stat().st_mtime_ns, p.stat().st_size)
            for p in folder.glob("*.md")
            if p.name.lower() != "readme.md"))
    except OSError:
        return (time.time(),)     # unreadable: force a re-sync
