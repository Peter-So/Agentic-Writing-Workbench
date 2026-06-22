"""Shared path helpers for the migrated writing project."""

from __future__ import annotations

import os
from pathlib import Path


def find_writing_root(start: str | Path | None = None) -> Path:
    """Return the writing project root, preferring WRITING_ROOT when set."""
    env_root = os.environ.get("WRITING_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    current = Path(start or __file__).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "project.yaml").exists() and candidate.name == "writing":
            return candidate
        if (candidate / "novel-acquisition").exists() and (candidate / "novels").exists():
            return candidate

    return Path(__file__).resolve().parent


WRITING_ROOT = find_writing_root()
NOVEL_ACQUISITION_DIR = WRITING_ROOT / "novel-acquisition"
REFERENCE_NOVELS_DIR = Path(
    os.environ.get("WRITING_REFERENCE_NOVELS_DIR", WRITING_ROOT / "references" / "novels")
).expanduser()
OUTPUTS_DIR = Path(os.environ.get("WRITING_OUTPUTS_DIR", WRITING_ROOT / "outputs")).expanduser()
DEFAULT_NOVEL_DIR = Path(
    os.environ.get("WRITING_NOVEL_DIR", WRITING_ROOT / "novels" / "001")
).expanduser()


def env_file() -> Path:
    """Return the first configured/local env file candidate."""
    configured = os.environ.get("WRITING_ENV_FILE")
    if configured:
        return Path(configured).expanduser()

    candidates = [
        WRITING_ROOT / ".env",
        WRITING_ROOT.parent.parent / ".env.shared",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
