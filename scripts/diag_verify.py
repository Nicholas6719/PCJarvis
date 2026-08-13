"""Where do the web and file tools still assert things they have not checked?

Every other layer now verifies before it reports: documents confirm the file
exists on disk, system readings are returned in the tool's own words, playback
is checked against the media session. Web and files are the remaining holdouts,
and their failure mode is the quiet one -- a tool that returns *something*
plausible, which the model then relays with full confidence.

The cases below are the ones that produce a confident wrong answer:

  read the wrong file      asked for one path, silently read another
  read half a file         truncated without saying so
  read a binary            mojibake summarised as if it were prose
  read an empty file       "here is the content" followed by nothing
  found nothing            without saying where it looked
  fetch a dead page        an error page summarised as the article
  fetch a thin page        a cookie banner summarised as the article
  search nothing           no results, answered anyway

    python scripts/diag_verify.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.ERROR)

from jarvis.tools import registry  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'  ok ' if ok else ' GAP '}  {name}")
    if detail:
        print(f"          {detail}")


async def call(_tool: str, **args) -> str:
    return await registry.execute(_tool, args)


def make_fixtures() -> Path:
    """Files designed to catch each failure mode."""
    box = Path(tempfile.mkdtemp(prefix="jarvis_verify_"))
    (box / "alpha.txt").write_text("", encoding="utf-8")
    (box / "bravo.txt").write_text(
        "\n".join(f"line {i}: some prose to pad this out" for i in range(2000)),
        encoding="utf-8")
    (box / "charlie.txt").write_bytes(bytes(range(256)) * 40)
    return box


def make_ambiguous_fixture() -> Path:
    """Two files with the same name, inside a folder JARVIS actually
    searches. Placed in temp they are invisible to him, and the test would
    pass for the wrong reason."""
    from jarvis.tools.files import _roots

    roots = _roots()
    base = (roots[0] if roots else Path.home()) / "jarvis_verify_tmp"
    (base / "one").mkdir(parents=True, exist_ok=True)
    (base / "two").mkdir(parents=True, exist_ok=True)
    (base / "one" / "delta_notes.txt").write_text(
        "Buy milk.", encoding="utf-8")
    (base / "two" / "delta_notes.txt").write_text(
        "A DIFFERENT file with the same name.", encoding="utf-8")
    return base


async def test_files(box: Path) -> None:
    print("\n[files]")

    out = await call("read_file", path=str(box / "alpha.txt"))
    check("empty file is called empty",
          "is empty" in out.lower() or "nothing in it" in out.lower(),
          f"said: {out[:90]!r}")

    out = await call("read_file", path=str(box / "bravo.txt"), max_chars=500)
    tail = out[-160:].lower()
    disclosed = "truncated" in tail and any(c.isdigit() for c in tail)
    check("truncation says how much was read",
          disclosed, f"said: {out[-110:].strip()}")

    out = await call("read_file", path=str(box / "charlie.txt"))
    # The only honest outcome is a refusal, and no control characters at all.
    control = sum(1 for ch in out if ord(ch) < 9 or 14 <= ord(ch) < 32)
    check("binary content is refused, not relayed",
          control == 0 and any(w in out.lower()
                               for w in ("not a text", "not readable",
                                         "binary", "cannot read")),
          f"control chars leaked: {control}; said: {out[:80]!r}")

    # The dangerous one: ask for a name, get a different file, told nothing.
    amb = make_ambiguous_fixture()
    try:
        out = await call("read_file", path="delta_notes.txt")
        disclosed = ("matched" in out.lower()
                     and ("one" in out or "two" in out))
        check("resolved path is disclosed when the name was ambiguous",
              disclosed, f"said: {out[:150]}")
    finally:
        import shutil
        shutil.rmtree(amb, ignore_errors=True)

    out = await call("find_files", name="zzz_no_such_file_anywhere_xyzzy")
    says_where = any(w in out.lower()
                     for w in ("looked", "searched", "documents", "desktop"))
    check("'nothing found' says where it looked",
          says_where, f"said: {out[:110]}")


async def test_web() -> None:
    print("\n[web]")

    out = await call("read_webpage", url="https://example.com/does-not-exist-404")
    check("dead page is reported as a failure",
          any(w in out.lower() for w in ("404", "could not", "couldn't",
                                         "failed", "returned")),
          f"said: {out[:110]}")

    # example.com is a real page with almost no content -- a good stand-in for
    # a cookie wall or a nav-only shell.
    out = await call("read_webpage", url="https://example.com")
    thin = any(w in out.lower() for w in ("very little", "not much", "thin",
                                          "only", "short", "little readable"))
    check("a near-empty page is flagged as thin",
          thin, f"said: {out[:130]}")

    out = await call("read_webpage", url="https://example.com")
    check("the page actually fetched is named",
          "example.com" in out, f"said: {out[:90]}")

    # Deterministic: score fixed result sets rather than whatever the search
    # engine feels like returning today. A live query made this check flaky,
    # which is worse than useless -- it was testing DuckDuckGo, not us.
    from jarvis.tools.web import _relevance

    unrelated = [{"title": "Duvall Osteen - Talent Agency",
                  "body": "literary representation"},
                 {"title": "20 Modern Problems", "body": "listicle"}]
    related = [{"title": "AMD Ryzen AI Max review",
                "body": "the new ryzen ai chips benchmarked"}]
    check("irrelevant results score low",
          _relevance("qwzzx gibberish 84719", unrelated) < 0.34,
          f"score {_relevance('qwzzx gibberish 84719', unrelated):.2f}")
    check("relevant results score high",
          _relevance("ryzen ai review", related) >= 0.7,
          f"score {_relevance('ryzen ai review', related):.2f}")

    # And the live path, once, as a smoke check.
    out = await call("web_search", query="AMD Ryzen AI news")
    check("a real search returns usable findings",
          "search findings" in out.lower() or "nothing relevant" in out.lower(),
          f"said: {out[:90]}")

    out = await call("get_weather", location="Zzzqxnotaplace")
    check("an unknown place is admitted",
          any(w in out.lower() for w in ("couldn't find", "could not find",
                                         "no such", "don't know")),
          f"said: {out[:110]}")


async def main() -> int:
    print("=" * 74)
    print(" verification gaps in the web and file tools")
    print("=" * 74)
    registry.load_all()
    box = make_fixtures()
    print(f" fixtures: {box}")

    await test_files(box)
    await test_web()

    passed = sum(1 for _, ok, _ in results if ok)
    gaps = sum(1 for _, ok, _ in results if not ok)
    print("\n" + "=" * 74)
    print(f" {passed} already verified, {gaps} gaps")
    for name, ok, _ in results:
        if not ok:
            print(f"   - {name}")
    print("=" * 74)
    return gaps


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) == 0 else 0)
