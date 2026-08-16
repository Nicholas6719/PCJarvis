"""The memory vault: files are the truth, the database is only the index.

The point of moving the facts out of SQLite and into markdown was that you can
open the folder and correct him. So the thing worth testing is not "can he
remember something" -- he always could -- but whether the folder actually
governs:

    delete a file   he forgets it
    edit a file     he believes the new version
    add a file      he learns it, without being told

If any of those only work when JARVIS is restarted, the feature is a filing
system rather than a memory, so each is checked against a live instance.

Runs against a throwaway vault, never the real one.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jarvis.brain import vault  # noqa: E402
from jarvis.brain.memory import Memory  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}" + (f"   {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"   {detail}" if detail else ""))


class Cfg:
    """The real config, with the vault and database pointed somewhere safe."""

    def __init__(self, vault_dir: Path, db: Path):
        self.over = {"memory.vault_path": str(vault_dir),
                     "memory.db_path": str(db)}

    def get(self, key, default=None):
        if key in self.over:
            return self.over[key]
        return CONFIG.get(key, default)


tmp = Path(tempfile.mkdtemp(prefix="jarvis_vault_"))
vault_dir = tmp / "Memory"
db = tmp / "index.db"

try:
    m = Memory(Cfg(vault_dir, db))

    print("\n[writing] remembering something creates a file you can read")
    m.remember("I ride a Cannondale", "preference")
    files = {p.name for p in vault_dir.glob("*.md")}
    check("a file appeared", any("cannondale" in f for f in files), str(files))
    check("README was written too", "README.md" in files)
    check("and README is not itself a fact",
          len(vault.scan(vault_dir)) == 1, str(len(vault.scan(vault_dir))))
    check("he can recall it", "Cannondale" in m.recall_for("what bike"),
          m.recall_for("what bike")[:50])

    print("\n[adding] a file written by hand is learned, with no restart")
    (vault_dir / "office.md").write_text(
        "---\ncategory: fact\n---\n\nMy office is upstairs\n", encoding="utf-8")
    check("learned from the folder", "upstairs" in m.recall_for("where is my office"),
          m.recall_for("where is my office")[:50])
    check("count went up", m.count() == 2, str(m.count()))

    print("\n[editing] correcting a file corrects him")
    (vault_dir / "office.md").write_text(
        "---\ncategory: fact\n---\n\nMy office is in the basement\n",
        encoding="utf-8")
    said = m.recall_for("where is my office")
    check("believes the new version", "basement" in said, said[:50])
    check("and not the old one", "upstairs" not in said, said[:50])

    print("\n[deleting] removing a file makes him forget")
    (vault_dir / "office.md").unlink()
    said = m.recall_for("where is my office")
    check("forgotten", "basement" not in said and "upstairs" not in said,
          said[:60])
    check("count went back down", m.count() == 1, str(m.count()))

    print("\n[forgetting] and asking him to forget removes the file")
    m.forget("Cannondale")
    remaining = [p.name for p in vault_dir.glob("*.md") if p.name != "README.md"]
    check("file is gone", not remaining, str(remaining))
    check("nothing left", m.count() == 0, str(m.count()))

    print("\n[malformed] a junk file must not take memory down with it")
    (vault_dir / "empty.md").write_text("", encoding="utf-8")
    (vault_dir / "headings.md").write_text("# Just a heading\n", encoding="utf-8")
    (vault_dir / "good.md").write_text("A plain line with no frontmatter\n",
                                       encoding="utf-8")
    check("skips the unusable, keeps the usable",
          m.count() == 1, f"count={m.count()}")
    check("and reads the plain one",
          "plain line" in m.recall_for("plain line").lower(),
          m.recall_for("plain line")[:50])

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 66)
print(f" {passed} passed, {failed} failed")
print("=" * 66)
sys.exit(1 if failed else 0)
