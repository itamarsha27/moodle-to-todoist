"""
Local dedup state: data/synced_tasks.json, which maps each Moodle event UID to the
Todoist task created for it.

    {
      "version": 2,
      "tasks": {
        "1234@moodle.example.ac.il": {
          "todoist_id": "8476…", "due": "2026-11-03T23:59:00+02:00",
          "previous_due": null, "fingerprint": "…", "course": "…", "title": "…",
          "first_synced": "…"
        }
      }
    }

The file is written atomically after every change, so a crash halfway through a
run can never "forget" a task that was already created. That is what prevents duplicates.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)
SCHEMA_VERSION = 2


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.tasks: dict[str, dict] = {}
        self.dirty = False

    def load(self) -> "StateStore":
        if not self.path.exists():
            log.info("No state file yet at %s; starting fresh", self.path)
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"State file {self.path} is corrupt: {exc}") from exc
        if data.get("version") != SCHEMA_VERSION:
            # Older/unknown schema: start fresh. The marker-based recovery in
            # sync.py re-links existing Todoist tasks, so nothing is duplicated.
            log.warning("State schema %r != %r; rebuilding from Todoist", data.get("version"), SCHEMA_VERSION)
            return self
        self.tasks = data.get("tasks", {})
        log.info("Loaded %d tracked tasks from state", len(self.tasks))
        return self

    def put(self, uid: str, record: dict) -> None:
        self.tasks[uid] = record
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        payload = {"version": SCHEMA_VERSION, "tasks": dict(sorted(self.tasks.items()))}
        _atomic_write_json(self.path, payload)
        self.dirty = False

    def prune(self, now: datetime, days: int) -> int:
        cutoff = now - timedelta(days=days)
        old = [u for u, r in self.tasks.items() if datetime.fromisoformat(r["due"]) < cutoff]
        for uid in old:
            del self.tasks[uid]
        if old:
            self.dirty = True
        return len(old)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write via temp file + rename, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
