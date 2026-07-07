from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.antibot_cv.automation.session import SessionState


@dataclass
class LatencyTracker:
    values_by_type: dict[str, list[float]] = field(default_factory=dict)
    _start_by_type: dict[str, float] = field(default_factory=dict)

    def start(self, latency_type: str, now: float | None = None) -> None:
        self._start_by_type[latency_type] = now if now is not None else time.monotonic()

    def finish(self, latency_type: str, now: float | None = None) -> float | None:
        if latency_type not in self._start_by_type:
            return None
        end = now if now is not None else time.monotonic()
        elapsed_ms = (end - self._start_by_type.pop(latency_type)) * 1000
        self.values_by_type.setdefault(latency_type, []).append(elapsed_ms)
        return elapsed_ms

    def summaries(self) -> dict[str, dict[str, float | None]]:
        return {key: summarize_latency(values) for key, values in self.values_by_type.items()}


def summarize_latency(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None, "mad": None, "standard_deviation": None}
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    return {
        "min": min(values),
        "median": median,
        "max": max(values),
        "mad": statistics.median(deviations),
        "standard_deviation": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


def write_session_summary(
    path: str | Path,
    session: SessionState,
    *,
    dry_run: bool,
    latencies: LatencyTracker,
) -> dict[str, object]:
    ended_at = time.time()
    summary: dict[str, object] = {
        "started_at": session.started_wall,
        "ended_at": ended_at,
        "duration_s": max(0.0, ended_at - session.started_wall),
        "requested_cycles": session.requested_cycles,
        "completed_cycles": session.completed_cycles,
        "incomplete_cycles": session.incomplete_cycles,
        "total_actions": session.total_actions,
        "viewport_moves": session.viewport_moves,
        "targets_detected": session.targets_detected,
        "battles_detected": session.battles_detected,
        "attack_actions": session.attack_actions,
        "ability4_actions": session.ability4_actions,
        "combat_actions": session.combat_actions,
        "exit_actions": session.exit_actions,
        "hunt_actions": session.hunt_actions,
        "resource_waits": session.resource_waits,
        "recoveries": session.recoveries,
        "errors": session.errors,
        "emergency_stop": session.emergency_stop,
        "dry_run": dry_run,
        "latencies": {
            "battle_to_ability4": summarize_latency(latencies.values_by_type.get("battle_to_ability4", [])),
            "battle_end_to_exit": summarize_latency(latencies.values_by_type.get("battle_end_to_exit", [])),
            "statistics_to_hunt": summarize_latency(latencies.values_by_type.get("statistics_to_hunt", [])),
        },
    }
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(_finite(summary), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def _finite(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite(item) for item in value]
    return value
