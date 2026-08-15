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
    r"""Where to search.

    A config entry may be a bare folder name -- "desktop", "documents" -- or a
    full path. A name expands to *both* the folder Windows has registered and
    the plain C:\Users\<user>\<Name> one, because OneDrive redirects some
    folders and not others. On this machine both exist for Desktop, Documents
    and Pictures, and they hold different files.

    That pairing is not tidiness. JARVIS writes to the local Documents by
    preference and to the redirected Desktop by necessity, so searching only
    one of each pair would fail to find files JARVIS had just created itself --
    which is exactly how a screenshot "saved to the Desktop" went missing.
    """
    from ..folders import KNOWN_FOLDER_IDS, known_folder, local_folder

    out: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path).lower()
        if key not in seen and path.exists():
            seen.add(key)
            out.append(path)

    for entry in CONFIG.get("tools.file_search_roots", []):
        name = str(entry).strip()
        if name.lower() in KNOWN_FOLDER_IDS:
            add(known_folder(name.lower()))
            add(local_folder(name.lower()))
        else:
            add(Path(name).expanduser())
    return out


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
        where = ", ".join(str(r) for r in _roots())
        return (f"No files matching {name}. I searched "
                f"{where or 'no folders -- none are configured'}.")

    found.sort(key=lambda x: -x[0])  # most recently modified first
    lines = [f"Found {len(found)} file(s) matching {name}:"]
    for mtime, p in found[:max(1, min(int(limit), 25))]:
        size = p.stat().st_size
        unit = f"{size/1e6:.1f}MB" if size > 1e6 else f"{size/1e3:.0f}KB"
        lines.append(f"- {p.name} ({unit}, modified "
                     f"{time.strftime('%d %b %Y', time.localtime(mtime))}) "
                     f"in {p.parent}")
    return "\n".join(lines)


def _looks_binary(raw: bytes) -> bool:
    """A file whose bytes are not text.

    Reading one with errors="replace" produces pages of replacement characters,
    which the model will then happily summarise as though it were prose. A null
    byte is conclusive; a high proportion of control characters is close enough.
    """
    if b"\x00" in raw:
        return True
    if not raw:
        return False
    control = sum(1 for b in raw if b < 9 or 14 <= b < 32)
    return control / len(raw) > 0.05


def _resolve(path: str) -> tuple[Path | None, str, int]:
    """Find the file he meant. Returns (path, how_it_was_found, n_candidates).

    The disclosure matters: asked to read "notes.txt", an earlier version would
    quietly read whichever notes.txt it happened to find first and report the
    contents as though there had been no ambiguity at all.
    """
    p = Path(path).expanduser()
    if p.exists():
        return p, "exact", 1

    matches: list[Path] = []
    for root in _roots():
        try:
            matches.extend(root.rglob(p.name))
        except OSError:
            continue
        if len(matches) > 20:
            break
    if not matches:
        return None, "missing", 0

    matches.sort(key=lambda m: -m.stat().st_mtime if m.exists() else 0)
    return matches[0], "searched", len(matches)


@tool(category="files")
def read_file(path: str, max_chars: int = 3000) -> str:
    """Read the contents of a text file.

    Args:
        path: Full path to the file, or a filename to search for.
        max_chars: Maximum characters to return.
    """
    resolved, how, count = _resolve(path)
    if resolved is None:
        where = ", ".join(str(r) for r in _roots()) or "the configured folders"
        return f"I can't find {path}. I looked in {where}."

    if resolved.is_dir():
        return f"{resolved.name} is a folder, not a file."

    # Say which file this actually is whenever it was not the one named.
    preamble = ""
    if how == "searched":
        preamble = f"(That name matched {count} file(s); this is {resolved}.)\n"

    try:
        raw = resolved.read_bytes()
    except Exception as e:
        return f"Could not read {resolved.name}: {e}"

    if not raw.strip():
        return f"{preamble}{resolved.name} is empty -- there is nothing in it."

    if _looks_binary(raw) or resolved.suffix.lower() not in TEXT_SUFFIXES:
        size = len(raw)
        return (f"{preamble}{resolved.name} is not a readable text file "
                f"({size/1024:.0f} KB of binary data), so I can't read it out.")

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return f"Could not decode {resolved.name}: {e}"

    total = len(text)
    if total > max_chars:
        # Quantified, so a summary of part of a file is never mistaken for a
        # summary of the whole thing.
        return (f"{preamble}Contents of {resolved.name} "
                f"(first {max_chars} of {total} characters):\n"
                f"{text[:max_chars]}\n"
                f"[truncated -- {total - max_chars} characters not shown]")
    return f"{preamble}Contents of {resolved.name} ({total} characters):\n{text}"


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
        where = ", ".join(str(r) for r in _roots())
        return f"No files found in {where or 'any configured folder'}."
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
    resolved, how, count = _resolve(path)
    if resolved is None:
        where = ", ".join(str(r) for r in _roots())
        return f"I can't find {path}. I looked in {where}."
    try:
        os.startfile(str(resolved))
    except Exception as e:
        return f"Could not open {resolved.name}: {e}"
    if how == "searched" and count > 1:
        return (f"Opened {resolved.name}. That name matched {count} files; "
                f"I opened the most recent one.")
    return f"Opened {resolved.name}."
