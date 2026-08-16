"""The words he actually says, fed back to the transcriber.

Whisper accepts an initial prompt that biases decoding, which is how "Jarvis"
stops being transcribed as "Travis". There was already a fixed list of these.
The problem with a fixed list is that it was written once, by me, guessing --
it knows about Ryzen and Lenovo and has never heard of anything he has actually
worked on since.

So the list assembles itself from things he has already told the machine, in
the ordinary course of using it:

    protocols     names he invented and now says out loud
    applications  whatever he actually has open, from the working context
    memory        proper nouns in the facts he has asked to be remembered
    corrections   anything he explicitly taught

Nothing here needs a training step or a setup screen. The words get better
because he used the thing, which is the only kind of learning that survives
contact with a real person.

Two limits worth knowing. The prompt is capped, because Whisper's is finite and
an overlong one starts displacing the words that matter -- so the newest and
most specific terms win. And it only ever biases: a term in the list is more
likely to be heard, never guaranteed, and a word not in it is not forbidden.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("jarvis.vocabulary")

STORE: Path | None = None
_taught: list[str] = []

# Whisper's initial prompt is bounded; past roughly this many words the tail
# stops influencing anything and starts crowding out the head.
MAX_TERMS = 48

BASE = ["Jarvis", "Nicholas", "Windows", "Spotify", "Brave", "VS Code",
        "Ryzen", "AMD", "Lenovo", "Yoga", "PDF", "CPU", "RAM", "GPU",
        "screenshot", "clipboard", "playlist", "battery", "protocol",
        "Obsidian"]

# Words that are already ordinary English. Biasing towards them achieves
# nothing and costs room that a real name could have used.
_COMMON = {"the", "and", "code", "explorer", "settings", "system", "host",
           "search", "python", "runtime", "service", "application", "main",
           "text", "file", "new", "open", "work", "good", "night", "app"}

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'.-]{2,}")


def configure(data_dir: Path) -> None:
    global STORE, _taught
    STORE = Path(data_dir) / "vocabulary.json"
    try:
        if STORE.exists():
            loaded = json.loads(STORE.read_text(encoding="utf-8"))
            _taught = [str(w) for w in loaded][-MAX_TERMS:] if isinstance(loaded, list) else []
    except Exception:
        log.debug("could not read the vocabulary", exc_info=True)
        _taught = []


def _save() -> None:
    if STORE is None:
        return
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(_taught[-MAX_TERMS:], indent=2),
                         encoding="utf-8")
    except Exception:
        log.debug("could not write the vocabulary", exc_info=True)


def teach(word: str) -> bool:
    """Add a term he has explicitly taught. False if it was already known."""
    clean = (word or "").strip().strip(".,!?")
    if not clean or len(clean) < 2:
        return False
    if any(clean.lower() == w.lower() for w in _taught + BASE):
        return False
    _taught.append(clean)
    del _taught[:-MAX_TERMS]
    _save()
    log.info("learned the word %r", clean)
    return True


def taught_words() -> list[str]:
    return list(_taught)


def forget(word: str) -> bool:
    global _taught
    before = len(_taught)
    _taught = [w for w in _taught if w.lower() != (word or "").lower()]
    if len(_taught) != before:
        _save()
        return True
    return False


def _from_protocols() -> list[str]:
    try:
        from .tools.protocols import _load

        return [name for name in _load() if name.lower() not in _COMMON]
    except Exception:
        return []


def _from_apps() -> list[str]:
    """Applications he actually uses, which are exactly what he names aloud."""
    try:
        from . import history

        if history._db is None:
            return []
        rows = history._db.execute(
            "SELECT app, COUNT(*) n FROM context GROUP BY app "
            "ORDER BY n DESC LIMIT 20").fetchall()
        return [r["app"] for r in rows
                if r["app"] and r["app"].lower() not in _COMMON]
    except Exception:
        return []


def _from_memory() -> list[str]:
    """Proper nouns out of the facts he has asked to be remembered."""
    try:
        from . import folders  # noqa: F401  (keeps import order predictable)
        from .brain import vault
        from .config import CONFIG

        path = Path(CONFIG.get(
            "memory.vault_path",
            str(Path.home() / "Documents" / "JARVIS" / "Memory")))
        words: list[str] = []
        for fact in vault.scan(path):
            for word in _WORD.findall(fact.get("content", "")):
                # Capitalised mid-sentence is the cheap test for a name, and
                # names are the whole point -- "Cannondale" needs the help,
                # "bicycle" does not.
                if word[0].isupper() and word.lower() not in _COMMON:
                    words.append(word)
        return words
    except Exception:
        return []


def prompt() -> str:
    """The initial prompt handed to Whisper."""
    seen: set[str] = set()
    terms: list[str] = []

    # Explicit teaching first: he said it in so many words, so it outranks
    # anything inferred, and it is what survives the cap.
    for source in (_taught, _from_protocols(), _from_memory(),
                   _from_apps(), BASE):
        for word in source:
            key = word.lower()
            if key in seen:
                continue
            seen.add(key)
            terms.append(word)
            if len(terms) >= MAX_TERMS:
                return ", ".join(terms) + "."
    return ", ".join(terms) + "."
