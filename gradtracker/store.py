"""Tracker state (your statuses, notes, AI assessments, score weights) with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

STATUSES = {
    "todo": "To apply",
    "applied": "Applied",
    "interviewing": "Interviewing",
    "offer": "Offer",
    "rejected": "Rejected",
    "skip": "Not interested",
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{path} is corrupted ({e}); fix or delete it") from e


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class State:
    """Thread-safe: the web server and a background refresh may touch it concurrently."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self._mtime = None
        self._load()

    def _load(self) -> None:
        self.data: dict = read_json(self.path, {})
        for key in ("positions", "assessments", "weights", "meta"):
            self.data.setdefault(key, {})
        self._mtime = self._disk_mtime()

    def _disk_mtime(self):
        try:
            return os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            return None

    def sync(self) -> None:
        """Reload if another process (e.g. `grad-tracker update`) changed the file on disk."""
        with self.lock:
            if self._disk_mtime() != self._mtime:
                self._load()

    def save(self) -> None:
        with self.lock:
            write_json(self.path, self.data)
            self._mtime = self._disk_mtime()

    # --------------------------------------------------------------- positions
    def position(self, op_id: str) -> dict:
        with self.lock:
            return self.data["positions"].setdefault(op_id, {"status": "todo", "note": "", "first_seen": None})

    def status(self, op_id: str) -> str:
        return self.data["positions"].get(op_id, {}).get("status", "todo")

    def set_status(self, op_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        with self.lock:
            p = self.position(op_id)
            p["status"], p["status_updated"] = status, now()

    def set_note(self, op_id: str, note: str) -> None:
        with self.lock:
            self.position(op_id)["note"] = note[:5000]

    def mark_seen(self, op_ids: list[str], today: str) -> list[str]:
        """Records first/last seen dates; returns ids never seen before."""
        new = []
        with self.lock:
            for i in op_ids:
                p = self.position(i)
                if not p.get("first_seen"):
                    p["first_seen"] = today
                    new.append(i)
                p["last_seen"] = today
        return new

    def acted_on(self, op_id: str) -> bool:
        """You changed something on this position (a status other than To apply, or a note)."""
        p = self.data["positions"].get(op_id) or {}
        return p.get("status", "todo") != "todo" or bool(p.get("note"))

    def adopt_aliases(self, primary: str, aliases: list[str]) -> bool:
        """The same role was merged under `primary`: carry your status/note over from whichever id had it."""
        changed = False
        with self.lock:
            for alias in aliases:
                if alias == primary or not self.acted_on(alias):
                    continue
                if not self.acted_on(primary):
                    old = self.data["positions"].get(primary) or {}
                    moved = dict(self.data["positions"][alias])
                    moved["first_seen"] = min(filter(None, [old.get("first_seen"), moved.get("first_seen")]), default=None)
                    self.data["positions"][primary] = moved
                    changed = True
                self.data["positions"][alias]["merged_into"] = primary
        return changed

    # --------------------------------------------------------------- assessments
    def assessment(self, op_id: str) -> dict | None:
        return self.data["assessments"].get(op_id)

    def set_assessment(self, op_id: str, assessment: dict) -> None:
        with self.lock:
            self.data["assessments"][op_id] = assessment
