"""Loads `.env` from the repo root into the process environment.

The web app and the CLI call this at startup, so a key in `.env` works the same
way locally, in Docker and in the devcontainer. A variable already set in the
environment wins over `.env`.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Set each KEY=value line from path, skipping blanks, comments and keys already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value
