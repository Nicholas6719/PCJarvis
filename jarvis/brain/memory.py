"""Persistent memory: SQLite for durability, FTS5 for keyword recall, and
local ONNX embeddings for semantic recall.

Two kinds of memory live here. Facts are things he told JARVIS to remember and
persist forever. Conversation turns are logged for continuity and searchability
but are not injected wholesale into context.

Recall combines both retrieval methods: keyword search catches exact terms like
names and model numbers, vector search catches paraphrase. Neither alone is
sufficient.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from . import vault
import time

import numpy as np

from ..config import MODELS_DIR, ROOT

log = logging.getLogger("jarvis.memory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    category   TEXT DEFAULT 'general',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    hits       INTEGER DEFAULT 0,
    embedding  BLOB
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
    USING fts5(content, content='facts', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content)
        VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content)
        VALUES('delete', old.id, old.content);
    INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS turns (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(timestamp DESC);
"""


class Memory:
    def __init__(self, cfg):
        self.cfg = cfg
        path = ROOT / cfg.get("memory.db_path", "data/jarvis.db")
        path.parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._lock = threading.Lock()

        self.recall_limit = cfg.get("memory.recall_limit", 5)
        # Calibrated, not guessed. On bge-small the baseline similarity between
        # any two English sentences is high, so a low bar means recall never
        # returns nothing and JARVIS answers unrelated questions from unrelated
        # memories. Measured against real stored facts:
        #   genuinely related    0.65 - 0.77
        #   entirely unrelated   0.43 - 0.49
        # 0.58 sits in the gap.
        self.min_similarity = cfg.get("memory.min_similarity", 0.58)
        self._embedder = None
        if cfg.get("memory.semantic", True):
            self._load_embedder()

        # The facts themselves live as markdown, one file each, so they can
        # be read and corrected without a database client. This table is the
        # index over them -- the embeddings that make recall semantic rather
        # than literal. The files win every disagreement.
        self.vault = Path(cfg.get(
            "memory.vault_path",
            str(Path.home() / "Documents" / "JARVIS" / "Memory")))
        # None, not (): an empty tuple is what a folder that does not
        # exist yet fingerprints as, so starting there made the very
        # first sync decide nothing had changed and skip the migration.
        self._vault_seen: tuple | None = None
        self._synced_at = 0.0
        self._sync_vault()

        log.info("memory ready: %d facts in %s", self.count(), self.vault)

    # ── the vault ──────────────────────────────────────────────────
    def _sync_vault(self, force: bool = False) -> None:
        """Make the index match the files.

        Called before every recall, so it has to cost nothing when nothing
        has changed: the fingerprint is names and modification times only,
        and no file is opened unless one of them moved. Re-embedding is the
        expensive part, so a fact whose text is unchanged keeps its vector.
        """
        try:
            # No time-based throttle. The fingerprint is the guard and it is
            # already cheap -- a glob and a stat, nothing opened -- whereas a
            # two-second window meant an edit made just before a question was
            # not seen, which is the one moment it matters.
            self._synced_at = time.time()

            seen = vault.fingerprint(self.vault)
            if seen == self._vault_seen and not force:
                return
            self._vault_seen = seen

            facts = vault.scan(self.vault)
            if not facts and not self.vault.is_dir():
                self._migrate_into_vault()
                facts = vault.scan(self.vault)
                self._vault_seen = vault.fingerprint(self.vault)

            wanted = {f["content"]: f for f in facts}
            with self._lock:
                have = {r["content"]: r["id"] for r in self.db.execute(
                    "SELECT id, content FROM facts").fetchall()}

                for content, fact in wanted.items():
                    if content in have:
                        continue
                    vector = self._embed(content)
                    now = time.time()
                    self.db.execute(
                        "INSERT INTO facts (content, category, created_at, "
                        "updated_at, embedding) VALUES (?,?,?,?,?)",
                        (content, fact["category"], now, now,
                         vector.tobytes() if vector is not None else None))

                # A file he deleted is a fact he wants forgotten.
                for content, row_id in have.items():
                    if content not in wanted:
                        self.db.execute("DELETE FROM facts WHERE id=?", (row_id,))
                self.db.commit()
        except Exception:
            log.exception("could not sync the memory vault; using the index")

    def _migrate_into_vault(self) -> None:
        """First run: write out whatever the database already held."""
        rows = self.db.execute(
            "SELECT content, category FROM facts").fetchall()
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / "README.md").write_text(vault.README, encoding="utf-8")
        for row in rows:
            vault.write(self.vault, row["content"], row["category"])
        if rows:
            log.info("moved %d remembered facts into %s", len(rows), self.vault)

    # ── embeddings ─────────────────────────────────────────────────
    def _load_embedder(self) -> None:
        try:
            from fastembed import TextEmbedding

            self._embedder = TextEmbedding(
                model_name=self.cfg.get("memory.embed_model",
                                        "BAAI/bge-small-en-v1.5"),
                cache_dir=str(MODELS_DIR / "embeddings"),
            )
            log.info("semantic recall enabled")
        except Exception:
            log.warning("embeddings unavailable; keyword recall only")
            self._embedder = None

    def _embed(self, text: str) -> np.ndarray | None:
        if not self._embedder:
            return None
        try:
            return np.array(list(self._embedder.embed([text]))[0], dtype=np.float32)
        except Exception:
            log.exception("embedding failed")
            return None

    # ── writing ────────────────────────────────────────────────────
    def remember(self, content: str, category: str = "general") -> str:
        content = content.strip()
        if not content:
            return "Nothing to remember."

        now = time.time()
        vector = self._embed(content)
        blob = vector.tobytes() if vector is not None else None

        with self._lock:
            # If we already know something very similar, update rather than
            # accumulate near-duplicates.
            existing = self._most_similar(content, threshold=0.92)
            if existing:
                self.db.execute(
                    "UPDATE facts SET content=?, updated_at=?, embedding=? "
                    "WHERE id=?",
                    (content, now, blob, existing["id"]),
                )
                self.db.commit()
                return f"Updated what I knew about that."

            self.db.execute(
                "INSERT INTO facts (content, category, created_at, updated_at, "
                "embedding) VALUES (?,?,?,?,?)",
                (content, category, now, now, blob),
            )
            self.db.commit()

        # The file is the fact; the row above is only how it is found quickly.
        try:
            vault.write(self.vault, content, category)
            self._vault_seen = vault.fingerprint(self.vault)
        except Exception:
            log.exception("remembered it, but could not write the vault file")
        return "Noted."

    def log_turn(self, role: str, content: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO turns (role, content, timestamp) VALUES (?,?,?)",
                (role, content, time.time()),
            )
            self.db.commit()

    def forget(self, query: str) -> str:
        matches = self.search(query, limit=3)
        if not matches:
            return f"I have nothing stored about {query}."
        with self._lock:
            self.db.execute("DELETE FROM facts WHERE id=?", (matches[0]["id"],))
            self.db.commit()

        # Remove the file as well, or the next sync would read it back in and
        # he would remember the thing he was just told to forget.
        try:
            for fact in vault.scan(self.vault):
                if fact["content"] == matches[0]["content"]:
                    Path(fact["path"]).unlink(missing_ok=True)
            self._vault_seen = vault.fingerprint(self.vault)
        except Exception:
            log.exception("forgot it, but could not remove the vault file")
        return f"Forgotten: {matches[0]['content'][:80]}"

    # ── reading ────────────────────────────────────────────────────
    def _all_with_vectors(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT id, content, category, embedding FROM facts "
            "WHERE embedding IS NOT NULL"
        ).fetchall()

    def _most_similar(self, text: str, threshold: float = 0.8) -> dict | None:
        vector = self._embed(text)
        if vector is None:
            return None
        best, best_score = None, 0.0
        norm = np.linalg.norm(vector) + 1e-9
        for row in self._all_with_vectors():
            other = np.frombuffer(row["embedding"], dtype=np.float32)
            score = float(vector @ other / (norm * (np.linalg.norm(other) + 1e-9)))
            if score > best_score:
                best, best_score = row, score
        return dict(best) if best and best_score >= threshold else None

    def search(self, query: str, limit: int | None = None) -> list[dict]:
        # The folder is the truth, so read it before answering from the
        # index. Without this, a fact corrected in Obsidian stayed corrected
        # only until someone restarted him.
        self._sync_vault()
        """Hybrid recall: keyword matches merged with semantic neighbours."""
        limit = limit or self.recall_limit
        results: dict[int, dict] = {}

        # Keyword pass.
        try:
            escaped = '"' + query.replace('"', '""') + '"'
            for row in self.db.execute(
                "SELECT f.id, f.content, f.category, bm25(facts_fts) AS rank "
                "FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
                "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
                (escaped, limit),
            ).fetchall():
                results[row["id"]] = {**dict(row), "score": 1.0}
        except sqlite3.OperationalError:
            pass  # FTS syntax rejection on odd input is not worth surfacing

        # Semantic pass.
        vector = self._embed(query)
        if vector is not None:
            norm = np.linalg.norm(vector) + 1e-9
            scored = []
            for row in self._all_with_vectors():
                other = np.frombuffer(row["embedding"], dtype=np.float32)
                score = float(vector @ other / (norm * (np.linalg.norm(other) + 1e-9)))
                if score >= self.min_similarity:
                    scored.append((score, row))
            scored.sort(key=lambda x: -x[0])
            for score, row in scored[:limit]:
                if row["id"] not in results:
                    results[row["id"]] = {**dict(row), "score": score}

        out = sorted(results.values(), key=lambda r: -r["score"])[:limit]
        if out:
            with self._lock:
                self.db.executemany(
                    "UPDATE facts SET hits = hits + 1 WHERE id=?",
                    [(r["id"],) for r in out],
                )
                self.db.commit()
        return out

    def context_block(self) -> str:
        """The most-used facts, injected into every system prompt."""
        rows = self.db.execute(
            "SELECT content FROM facts ORDER BY hits DESC, updated_at DESC LIMIT ?",
            (self.recall_limit * 2,),
        ).fetchall()
        return "\n".join(f"- {r['content']}" for r in rows)

    def recall_for(self, query: str) -> str:
        matches = self.search(query)
        return "\n".join(f"- {m['content']}" for m in matches)

    def count(self) -> int:
        # Sync first, or this reports what the index last believed rather
        # than what is in the folder now.
        self._sync_vault()
        return self.db.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]

    def recent_turns(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT role, content, timestamp FROM turns "
            "ORDER BY timestamp DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def close(self) -> None:
        with self._lock:
            self.db.commit()
            self.db.close()
