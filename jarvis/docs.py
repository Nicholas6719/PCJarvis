"""Searching his own writing by meaning rather than by filename.

He could already find a file called notes.txt. He could not answer "what did I
write about the router", which is how people actually look for their own
things -- you remember what it was about, never what you called it.

The embedder is borrowed from memory rather than loaded a second time. It is
the same model doing the same job, and a second copy would be another 130 MB
resident for no reason on a machine that has none spare.

Kept honest about scale. This indexes plain text and markdown inside the
folders already configured for file search, skips anything large, and caps how
much of any one file it will hold. It is a way to find a paragraph you wrote,
not a search engine, and pretending otherwise would mean a slow index nobody
asked for and a battery cost nobody noticed until it mattered.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.docs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    path      TEXT NOT NULL,
    mtime     REAL NOT NULL,
    ordinal   INTEGER NOT NULL,
    text      TEXT NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks (path);
"""

READABLE = {".md", ".txt", ".log", ".csv"}
MAX_FILE_BYTES = 1_000_000
MAX_CHUNKS_PER_FILE = 40
MAX_FILES = 400
CHUNK_CHARS = 600

_db: sqlite3.Connection | None = None
_lock = threading.Lock()
_embed = None
_indexed_at = 0.0


def configure(data_dir: Path) -> None:
    global _db
    try:
        path = Path(data_dir) / "docs.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(str(path), check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript(SCHEMA)
        _db.commit()
    except Exception:
        log.exception("could not open the document index")
        _db = None


def bind(embed_fn) -> None:
    """Share memory's embedder. Without one, this does nothing at all."""
    global _embed
    _embed = embed_fn


def _chunks(text: str) -> list[str]:
    """Split on blank lines, then pack up to a readable size.

    Paragraph boundaries beat fixed windows: a chunk that begins mid-sentence
    embeds badly and reads worse when it comes back as an answer.
    """
    out: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= CHUNK_CHARS:
            current = f"{current}\n\n{para}".strip()
        else:
            if current:
                out.append(current)
            current = para[:CHUNK_CHARS]
        if len(out) >= MAX_CHUNKS_PER_FILE:
            return out
    if current:
        out.append(current)
    return out


def _candidates() -> list[Path]:
    from .tools.files import _roots

    found: list[Path] = []
    skip = {"node_modules", ".git", ".venv", "__pycache__", "AppData",
            "dist", "build", "models"}
    for root in _roots():
        try:
            for path in root.rglob("*"):
                if len(found) >= MAX_FILES:
                    return found
                if not path.is_file() or path.suffix.lower() not in READABLE:
                    continue
                if any(part in skip for part in path.parts):
                    continue
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                found.append(path)
        except OSError:
            continue
    return found


def reindex(force: bool = False) -> int:
    """Bring the index up to date. Returns how many files were re-read."""
    global _indexed_at
    if _db is None or _embed is None:
        return 0
    if not force and time.time() - _indexed_at < 300:
        return 0
    _indexed_at = time.time()

    changed = 0
    try:
        with _lock:
            known = {r["path"]: r["mtime"] for r in _db.execute(
                "SELECT path, MAX(mtime) mtime FROM chunks GROUP BY path")}
            seen: set[str] = set()

            for path in _candidates():
                key = str(path)
                seen.add(key)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if known.get(key) and abs(known[key] - mtime) < 1:
                    continue

                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                _db.execute("DELETE FROM chunks WHERE path=?", (key,))
                for i, chunk in enumerate(_chunks(text)):
                    vector = _embed(chunk)
                    _db.execute(
                        "INSERT INTO chunks (path, mtime, ordinal, text, "
                        "embedding) VALUES (?,?,?,?,?)",
                        (key, mtime, i, chunk,
                         vector.tobytes() if vector is not None else None))
                changed += 1

            # Files that have gone should not keep answering questions.
            for key in set(known) - seen:
                _db.execute("DELETE FROM chunks WHERE path=?", (key,))
            _db.commit()
    except Exception:
        log.exception("indexing failed")
    if changed:
        log.info("indexed %d document(s)", changed)
    return changed


def search(query: str, limit: int = 3) -> str:
    """The passages that actually answer the question, with their sources."""
    if _db is None:
        return "I have no document index."
    if _embed is None:
        return "Semantic search is unavailable, so I can only find files by name."

    reindex()
    vector = _embed(query)
    if vector is None:
        return "I could not process that query."

    rows = _db.execute(
        "SELECT path, text, embedding FROM chunks WHERE embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return "I have not found any documents to search."

    scored = []
    for row in rows:
        other = np.frombuffer(row["embedding"], dtype=np.float32)
        if other.size != vector.size:
            continue
        score = float(np.dot(vector, other) /
                      ((np.linalg.norm(vector) * np.linalg.norm(other)) or 1))
        scored.append((score, row))
    if not scored:
        return "I have not found any documents to search."

    scored.sort(key=lambda s: -s[0])
    # Same calibration as memory: on this embedder anything under about 0.58
    # is unrelated, and returning it anyway is how a search answers a question
    # it never understood.
    best = [(s, r) for s, r in scored[:limit] if s >= 0.58]
    if not best:
        return (f"I could not find anything of yours about {query}. "
                f"Nothing in your documents comes close.")

    header = (f"Passages from your own documents about '{query}'. Answer from "
              f"these in one or two spoken sentences, and say which file")
    # A score just over the bar is usually the least-bad match rather than a
    # good one -- asked about the boot sound with nothing written about it,
    # the closest passage was a microphone diagnostic. Say so, or he will
    # answer confidently from whatever came back.
    if best[0][0] < 0.66:
        header += (". These are only a loose match, so if they do not really "
                   "answer it, say you have nothing written about this")
    lines = [header + ":"]
    for score, row in best:
        name = Path(row["path"]).name
        lines.append(f"- {name}: {row['text'][:500]}")
    return "\n".join(lines)


def count() -> int:
    if _db is None:
        return 0
    try:
        return _db.execute("SELECT COUNT(DISTINCT path) c FROM chunks").fetchone()["c"]
    except Exception:
        return 0


def close() -> None:
    global _db
    if _db is not None:
        try:
            _db.close()
        except Exception:
            pass
        _db = None
