"""Configuration loading. config.yaml is the single source of truth;
config.local.yaml (gitignored) overrides it if present."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import yaml

# Frozen (PyInstaller) and source layouts differ in an important way: code and
# web assets live inside the bundle, but models, config and the memory database
# must sit beside the executable where they can be read, written and updated
# without rebuilding.
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    ROOT = Path(sys.executable).resolve().parent   # writable, next to the exe
    BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))  # read-only, inside the exe
else:
    ROOT = Path(__file__).resolve().parent.parent
    BUNDLE = ROOT

MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"


def _config_path() -> Path:
    """Prefer the editable copy beside the exe; fall back to the bundled one."""
    beside = ROOT / "config.yaml"
    return beside if beside.exists() else BUNDLE / "config.yaml"


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """Dotted-path access over the YAML tree: cfg.get('voice_chain.room.mix')."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @classmethod
    def load(cls) -> "Config":
        with open(_config_path(), encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        local = ROOT / "config.local.yaml"
        if local.exists():
            with open(local, encoding="utf-8") as f:
                data = _deep_merge(data, yaml.safe_load(f) or {})
        for d in (MODELS_DIR, DATA_DIR, LOGS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        return cls(data)

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def section(self, name: str) -> dict:
        return self.get(name, {}) or {}

    def save(self) -> None:
        """Persist current values to config.local.yaml so tuning survives restart."""
        with open(ROOT / "config.local.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(self._data, f, sort_keys=False, allow_unicode=True)

    @property
    def data(self) -> dict:
        return self._data


CONFIG = Config.load()
