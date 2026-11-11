"""Timed traces for asks and reports.

Each request is written to logs/freshflow.log and kept in a short in-memory
list. The debug panel reads that list. Nothing here is sent to a model.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "freshflow.log"

logger = logging.getLogger("freshflow")
_lock = threading.Lock()
_recent: deque[dict] = deque(maxlen=40)
_configured = False


def configure() -> None:
    """Attach a file and a console handler once."""
    global _configured
    if _configured:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    _configured = True


def _brief(info: dict) -> str:
    parts = []
    for key, value in info.items():
        if value is None:
            continue
        text = str(value).replace("\n", " ")
        if len(text) > 160:
            text = text[:157] + "..."
        parts.append(f"{key}={text}")
    return " ".join(parts)


class Trace:
    """One request. Call step() at each boundary, then finish()."""

    def __init__(self, kind: str, **fields):
        configure()
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.fields = fields
        self.steps: list[dict] = []
        self.error: str | None = None
        self._started = time.perf_counter()
        self._mark = self._started
        logger.info("start %s id=%s %s", kind, self.id, _brief(fields))

    def step(self, name: str, **info) -> None:
        now = time.perf_counter()
        entry = {"name": name, "ms": round((now - self._mark) * 1000, 1)}
        entry.update({key: value for key, value in info.items() if value is not None})
        self.steps.append(entry)
        self._mark = now
        logger.info("step %s id=%s %sms %s", name, self.id, entry["ms"], _brief(info))

    def fail(self, message: str) -> None:
        self.error = message
        logger.error("fail id=%s %s", self.id, message)

    def finish(self, **info) -> dict:
        elapsed = round((time.perf_counter() - self._started) * 1000, 1)
        record = {
            "id": self.id,
            "kind": self.kind,
            "ms": elapsed,
            "error": self.error,
            "fields": self.fields,
            "steps": self.steps,
            **info,
        }
        with _lock:
            _recent.appendleft(record)
        logger.info("done id=%s ms=%s error=%s", self.id, elapsed, self.error or "-")
        return {
            "id": self.id,
            "kind": self.kind,
            "ms": elapsed,
            "error": self.error,
            "steps": self.steps,
            "log": str(LOG_FILE),
        }


def recent(limit: int = 20) -> list[dict]:
    with _lock:
        return list(_recent)[:limit]
