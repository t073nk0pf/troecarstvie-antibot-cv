from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


M1_RECOVERY_PHASES = (
    "death_detected",
    "revive_requested",
    "revive_confirmed",
    "notice_closed",
    "resources_ready",
    "checkpoint_arrived",
    "original_destination_arrived",
    "hunt_opened",
    "death_recovery_completed",
)

_TERMINAL_PHASE = M1_RECOVERY_PHASES[-1]
_PHASE_INDEX = {phase: index for index, phase in enumerate(M1_RECOVERY_PHASES)}


def assess_m1_recovery(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Assess recorded M1 recovery evidence without performing live actions.

    Attempts retain the order in which their ``recovery_id`` first appears.
    The first occurrence of each phase must follow the M1 sequence exactly.
    Repeated non-terminal phases are tolerated, while a repeated terminal phase
    invalidates the attempt. ``consecutive_complete_attempts`` is the trailing
    completed streak used for current readiness; ``longest_complete_streak``
    preserves the historical maximum.
    """

    grouped: dict[str, list[str]] = {}
    ignored_events = 0

    for event in events:
        if not isinstance(event, Mapping):
            ignored_events += 1
            continue
        recovery_id = event.get("recovery_id")
        phase = event.get("event_type", event.get("phase"))
        if (
            recovery_id is None
            or not str(recovery_id).strip()
            or not isinstance(phase, str)
            or phase not in _PHASE_INDEX
        ):
            ignored_events += 1
            continue
        grouped.setdefault(str(recovery_id), []).append(str(phase))

    attempts = [_assess_attempt(recovery_id, phases) for recovery_id, phases in grouped.items()]

    complete_attempts = sum(1 for attempt in attempts if attempt["passed"])
    longest_streak = 0
    current_streak = 0
    for attempt in attempts:
        if attempt["passed"]:
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
        else:
            current_streak = 0

    trailing_streak = 0
    for attempt in reversed(attempts):
        if not attempt["passed"]:
            break
        trailing_streak += 1
    latest_attempt_passed = bool(attempts) and bool(attempts[-1]["passed"])

    return {
        "offline_ready": latest_attempt_passed,
        "complete_attempts": complete_attempts,
        "consecutive_complete_attempts": trailing_streak,
        "longest_complete_streak": longest_streak,
        "latest_attempt_passed": latest_attempt_passed,
        "attempts": attempts,
        "ignored_events": ignored_events,
    }


def _assess_attempt(recovery_id: str, phases: list[str]) -> dict[str, Any]:
    seen: set[str] = set()
    accepted: list[str] = []
    next_index = 0
    failure_reason: str | None = None

    for phase in phases:
        if phase in seen:
            if phase == _TERMINAL_PHASE:
                failure_reason = f"duplicate_terminal:{_TERMINAL_PHASE}"
                break
            continue

        expected = (
            M1_RECOVERY_PHASES[next_index]
            if next_index < len(M1_RECOVERY_PHASES)
            else None
        )
        if phase != expected:
            failure_reason = f"out_of_order:{phase}:expected:{expected or 'none'}"
            break

        seen.add(phase)
        accepted.append(phase)
        next_index += 1

    if failure_reason is None and next_index < len(M1_RECOVERY_PHASES):
        failure_reason = f"missing_phase:{M1_RECOVERY_PHASES[next_index]}"

    passed = failure_reason is None
    return {
        "recovery_id": recovery_id,
        "passed": passed,
        "reason": "complete" if passed else failure_reason,
        "phases": accepted,
    }
