"""File tools: find, read, summarize, open.

Searches are scoped to the roots in config rather than the whole disk -- both
because it is faster and because JARVIS has no business trawling system
directories to answer a question about a document.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from ..config import CONFIG
from .registry import tool

log = logging.getLogger("jarvis.tools.files")

TEXT_SUFFIXES = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".csv",
    ".log", ".ini", ".cfg", ".html", ".css", ".xml", ".sh", ".ps1", ".bat",
    ".java", ".c", ".cpp", ".h", ".rs", ".go", ".sql", ".toml",
}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
             "AppData", "$RECYCLE.BIN", ".cache", "dist", "build"}


def _roots() -> list[Path]:
    roots = [Path(p) for p in CONFIG.get("tools.file_search_roots", [])]
    return [r for r in roots if r.exists()]


@tool(category="files")
def find_files(name: str, limit: int = 10) -> str:
    """Search for files by name in the user's documents, desktop and downloads.

    Args:
        name: Part of the filename to look for.
        limit: Maximum results to return.
    """
    needle = name.lower().strip()
    if not needle:
        return "Give me something to search for."

    found: list[tuple[float, Path]] = []
    for root in _roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if needle in fn.lower():
                    p = Path(dirpath) / fn
                    try:
                        found.append((p.stat().st_mtime, p))
                    except OSError:
                        pass
            if len(found) > 400:  # plenty to rank from; stop walking
                break

    if not found:
        return f"No files matching {name}."

    found.sort(key=lambda x: -x[0])  # most recently modified first
    lines = [f"Found {len(found)} file(s) matching {name}:"]
    for mtime, p in found[:max(1, min(int(limit), 25))]:
        size = p.stat().st_size
        unit = f"{size/1e6:.1f}MB" if size > 1e6 else f"{size/1e3:.0f}KB"
        lines.append(f"- {p.name} ({unit}, modified "
                     f"{time.strftime('%d %b %Y', time.localtime(mtime))}) "
                     f"in {p.parent}")
    return "\n".join(lines)


@tool(category="files")
def read_file(path: str, max_chars: int = 3000) -> str:
    """Read the contents of a text file.

    Args:
        path: Full path to the file, or a filename to search for.
        max_chars: Maximum characters to return.
    """
    p = Path(path).expanduser()
    if not p.exists():
        # Fall back to searching for it by name.
        for root in _roots():
            matches = list(root.rglob(p.name))
            if matches:
                p = matches[0]
                break
        else:
            return f"I can't find {path}."

    if p.is_dir():
        return f"{p.name} is a folder, not a file."
    if p.suffix.lower() not in TEXT_SUFFIXES:
        return f"{p.name} is not a readable text file."

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Could not read {p.name}: {e}"

    truncated = len(text) > max_chars
    return (f"Contents of {p.name}:\n{text[:max_chars]}"
            + ("\n[truncated]" if truncated else ""))


@tool(category="files")
def list_recent_files(count: int = 10) -> str:
    """List the most recently modified files in the user's folders.

    Args:
        count: How many files to list.
    """
    found: list[tuple[float, Path]] = []
    for root in _roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                p = Path(dirpath) / fn
                try:
                    found.append((p.stat().st_mtime, p))
                except OSError:
                    pass

    if not found:
        return "No recent files found."
    found.sort(key=lambda x: -x[0])
    lines = ["Most recently modified:"]
    for mtime, p in found[:max(1, min(int(count), 25))]:
        lines.append(f"- {p.name} ({time.strftime('%d %b, %I:%M %p', time.localtime(mtime))})")
    return "\n".join(lines)


@tool(category="files")
def open_file(path: str) -> str:
    """Open a file or folder in its default application.

    Args:
        path: Full path, or a filename to search for.
    """
    p = Path(path).expanduser()
    if not p.exists():
        for root in _roots():
            matches = list(root.rglob(p.name))
            if matches:
                p = matches[0]
                break
        else:
            return f"I can't find {path}."
    try:
        os.startfile(str(p))
        return f"Opened {p.name}."
    except Exception as e:
        return f"Could not open {p.name}: {e}"
