"""Where the policy numbers come from, and where paths come from.

One indirection layer, so a test or a replay can point the library at a
different facts sheet or a different store without touching code.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def facts_path() -> Path:
    return Path(os.environ.get("MB_FACTS", REPO_ROOT / "facts.yaml"))


def store_path() -> Path:
    return Path(os.environ.get("MB_STORE", REPO_ROOT / "build" / "eventstore.db"))


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def load_facts() -> dict[str, Any]:
    return _load(str(facts_path()))


def activity_rank() -> dict[str, int]:
    """The declared tie-break ordering. See facts.yaml for why it is declared."""
    return dict(load_facts().get("activity_rank") or {})
