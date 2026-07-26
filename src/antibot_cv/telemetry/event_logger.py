from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from src.antibot_cv import __version__


class EventLogger:
    def __init__(
        self,
        session_id: str,
        run_dir: str | Path,
        *,
        dry_run: bool,
        application_version: str = __version__,
        max_bytes: int = 25 * 1024 * 1024,
        backup_count: int = 3,
    ) -> None:
        self.session_id = session_id
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "events.jsonl"
        self.dry_run = dry_run
        self.application_version = application_version
        self.max_bytes = max(1024, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self._lock = threading.Lock()

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            current_size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if current_size + incoming_bytes <= self.max_bytes:
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))

    def log_event(self, event_type: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "session_id": self.session_id,
            "cycle_id": fields.pop("cycle_id", None),
            "battle_id": fields.pop("battle_id", None),
            "ts_wall": fields.pop("ts_wall", time.time()),
            "ts_monotonic": fields.pop("ts_monotonic", time.monotonic()),
            "state": fields.pop("state", None),
            "previous_state": fields.pop("previous_state", None),
            "event_type": event_type,
            "action_type": fields.pop("action_type", None),
            "x_screen": fields.pop("x_screen", None),
            "y_screen": fields.pop("y_screen", None),
            "x_frame": fields.pop("x_frame", None),
            "y_frame": fields.pop("y_frame", None),
            "detector_id": fields.pop("detector_id", None),
            "detector_confidence": fields.pop("detector_confidence", None),
            "target_id": fields.pop("target_id", None),
            "track_id": fields.pop("track_id", None),
            "scan_direction": fields.pop("scan_direction", None),
            "reaction_latency_ms": fields.pop("reaction_latency_ms", None),
            "frame_hash": fields.pop("frame_hash", None),
            "dry_run": fields.pop("dry_run", self.dry_run),
            "application_version": self.application_version,
        }
        record.update(fields)
        serialized = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock:
            self._rotate_if_needed(len(serialized.encode("utf-8")))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(serialized)


class InMemoryEventLogger:
    def __init__(self, *, dry_run: bool = True) -> None:
        self.dry_run = dry_run
        self.events: list[dict[str, Any]] = []

    def log_event(self, event_type: str, **fields: Any) -> None:
        record = {
            "event_type": event_type,
            "ts_wall": fields.pop("ts_wall", time.time()),
            "ts_monotonic": fields.pop("ts_monotonic", time.monotonic()),
            "dry_run": fields.pop("dry_run", self.dry_run),
            **fields,
        }
        self.events.append(record)


def frame_hash(frame: object | None) -> str | None:
    if frame is None:
        return None
    try:
        data = memoryview(frame).tobytes()  # type: ignore[arg-type]
    except TypeError:
        try:
            data = frame.tobytes()  # type: ignore[attr-defined]
        except AttributeError:
            return None
    return hashlib.sha1(data).hexdigest()[:16]
