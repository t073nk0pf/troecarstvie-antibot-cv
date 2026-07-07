from __future__ import annotations

import json

from src.antibot_cv.automation.session import SessionState
from src.antibot_cv.telemetry.event_logger import EventLogger, frame_hash
from src.antibot_cv.telemetry.session_summary import LatencyTracker, summarize_latency, write_session_summary
from tests.conftest import blank_frame


def test_jsonl_serialization(tmp_path) -> None:
    logger = EventLogger("session1", tmp_path, dry_run=True)
    logger.log_event("state_transition", state="TARGET_FOUND", cycle_id=1)
    record = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").strip())
    assert record["session_id"] == "session1"
    assert record["event_type"] == "state_transition"
    assert record["dry_run"] is True


def test_session_summary(tmp_path) -> None:
    session = SessionState(requested_cycles=3)
    session.completed_cycles = 2
    session.total_actions = 5
    latencies = LatencyTracker()
    latencies.values_by_type["battle_to_ability4"] = [10, 20, 30]
    summary = write_session_summary(tmp_path / "summary.json", session, dry_run=True, latencies=latencies)
    assert summary["completed_cycles"] == 2
    assert summary["latencies"]["battle_to_ability4"]["median"] == 20


def test_latency_calculation() -> None:
    summary = summarize_latency([10.0, 20.0, 100.0])
    assert summary["min"] == 10.0
    assert summary["median"] == 20.0
    assert summary["max"] == 100.0
    assert summary["mad"] == 10.0
    assert summary["standard_deviation"] is not None


def test_frame_hash_stable() -> None:
    frame = blank_frame()
    assert frame_hash(frame) == frame_hash(frame.copy())
