"""A static sweep of the tree: unused imports, dead code, stale config.

Written with the standard library only. The runtime has to stay free and
local, and a dev-only linter is one more thing to install and keep working;
the ast module already knows everything this needs.

Word boundaries here are spelled with lookarounds rather than the usual escape,
because a backslash in a generated file is how the last two bugs got in.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = sorted((ROOT / "jarvis").rglob("*.py"))


def bounded(name: str) -> re.Pattern:
    return re.compile("(?<![A-Za-z0-9_])" + re.escape(name) + "(?![A-Za-z0-9_])")


def import_nodes(tree):
    """Every import statement in the module."""
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))]


def names_of(node):
    """The names an import statement introduces."""
    out = []
    for a in node.names:
        if a.name == "*":
            continue
        if isinstance(node, ast.Import):
            out.append(a.asname or a.name.split(".")[0])
        else:
            out.append(a.asname or a.name)
    return out


def unused_imports() -> list[str]:
    """Imports whose name is never mentioned again.

    A noqa marker means the import is there for its side effects, not its
    name. registry.load_all imports ten tool modules purely so their @tool
    decorators run; an audit that called those unused, followed literally,
    would silently unregister every tool JARVIS has.
    """
    findings = []
    for path in SRC:
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            findings.append(f"{path.relative_to(ROOT)}: SYNTAX ERROR {e}")
            continue

        nodes = import_nodes(tree)
        skip_lines = set()
        for n in nodes:
            skip_lines.update(range(n.lineno, (n.end_lineno or n.lineno) + 1))
        body = chr(10).join(l for i, l in enumerate(lines, 1)
                            if i not in skip_lines)

        for n in nodes:
            stmt = chr(10).join(
                lines[n.lineno - 1:(n.end_lineno or n.lineno)])
            if "noqa" in stmt:
                continue
            for name in names_of(n):
                if name == "annotations":
                    continue
                if not bounded(name).search(body):
                    findings.append(
                        f"{path.relative_to(ROOT)}:{n.lineno}  "
                        f"unused import {name!r}")
    return findings


def dead_definitions() -> list[str]:
    """Module-level defs never named anywhere else in the tree."""
    whole = "\n".join(p.read_text(encoding="utf-8") for p in SRC)
    scripts = ROOT / "scripts"
    if scripts.exists():
        whole += "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                           for p in scripts.rglob("*.py"))

    findings = []
    for path in SRC:
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                continue
            name = node.name
            if name.startswith("__"):
                continue
            # A @tool is reached through the registry, never by name.
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if any("tool" in d for d in decorators):
                continue
            hits = len(bounded(name).findall(whole))
            if hits <= 1:   # only its own definition
                findings.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}  "
                    f"'{name}' is defined and never used")
    return findings


def declared_keys(text: str) -> set[str]:
    """Every settable key in the YAML, as a dotted path.

    Indentation-aware, because voice_chain.room.mix is three levels deep and
    a parser that only understood two reported it as never declared.
    A key counts as settable when it carries a value inline, or when its
    children are list items -- file_search_roots is a list and is read by
    its own name.
    """
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([a-z_][a-z0-9_]*):(.*)$", raw)
        if m:
            lines.append((len(m.group(1)), m.group(2),
                          m.group(3).split("#")[0].strip(), False))
        elif raw.lstrip().startswith("-"):
            lines.append((len(raw) - len(raw.lstrip()), "", "", True))

    declared: set[str] = set()
    stack: list[tuple[int, str]] = []
    for i, (indent, key, value, is_item) in enumerate(lines):
        if is_item:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])

        follows_a_list = (i + 1 < len(lines) and lines[i + 1][3]
                          and lines[i + 1][0] > indent)
        if value or follows_a_list:
            declared.add(path)
        stack.append((indent, key))
    return declared


def read_keys() -> tuple[set[str], set[str]]:
    """Config keys the code actually reads, found through the AST.

    Regex found this text inside a docstring in config.py --
    "Dotted-path access over the YAML tree: cfg.get(...)" -- and reported the
    key as live when nothing read it at all. Parsing the tree instead means
    comments and examples cannot be mistaken for code.
    """
    keys: set[str] = set()
    sections: set[str] = set()
    for path in SRC:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute) or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue

            # The receiver has to look like config. Without this, every
            # httpx client.get("https://...") counted as reading a setting,
            # because a URL contains dots too.
            recv = fn.value
            if isinstance(recv, ast.Name):
                recv_name = recv.id
            elif isinstance(recv, ast.Attribute):
                recv_name = recv.attr
            else:
                recv_name = ""
            if recv_name not in ("cfg", "CONFIG", "config", "_cfg"):
                continue

            if fn.attr == "get" and "." in first.value:
                keys.add(first.value)
            elif fn.attr == "section":
                sections.add(first.value)
    return keys, sections


def config_drift() -> list[str]:
    cfg = ROOT / "config.yaml"
    if not cfg.exists():
        return ["config.yaml missing"]

    declared = declared_keys(cfg.read_text(encoding="utf-8"))
    read, whole_sections = read_keys()
    read |= {k for k in declared if k.split(".")[0] in whole_sections}

    findings = []
    for key in sorted(declared - read):
        findings.append(f"config.yaml declares {key!r} but nothing reads it")
    for key in sorted(read - declared):
        findings.append(f"code reads {key!r} but config.yaml does not set it")
    return findings

def is_create_task(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
    return name == 'create_task'


def unanchored_tasks() -> list[str]:
    """create_task calls whose result nobody keeps.

    asyncio holds only a weak reference to a task. A task nobody else
    holds can be collected while it is awaiting and simply stop, with no
    error anywhere. This has now caused two separate bugs here -- a timer
    that never fired, and six unanchored tasks in main.py including the
    proactive announcement -- so it is worth a permanent check.

    Two shapes are unsafe: a bare create_task(...) statement, and one
    returned from a lambda whose value the caller discards.
    """
    findings = []
    for path in SRC:
        src = path.read_text(encoding='utf-8')
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and is_create_task(node.value):
                findings.append(
                    f'{path.relative_to(ROOT)}:{node.lineno}  '
                    f'create_task result discarded (may be collected mid-await)')
            elif isinstance(node, ast.Lambda):
                for inner in ast.walk(node):
                    if is_create_task(inner):
                        findings.append(
                            f'{path.relative_to(ROOT)}:{inner.lineno}  '
                            f'create_task inside a lambda; caller discards it')
    return findings


def main() -> int:
    sections = [
        ("UNUSED IMPORTS", unused_imports()),
        ("DEAD DEFINITIONS", dead_definitions()),
        ("CONFIG DRIFT", config_drift()),
        ("UNANCHORED TASKS", unanchored_tasks()),
    ]
    total = 0
    for title, findings in sections:
        print("=" * 62)
        print(f"  {title}   ({len(findings)})")
        print("=" * 62)
        for f in findings:
            print(f"  {f}")
        if not findings:
            print("  clean")
        print()
        total += len(findings)
    print(f"{total} finding(s) across {len(SRC)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
