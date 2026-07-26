import json

from src.antibot_cv.telemetry.m1_recovery import M1_RECOVERY_PHASES, assess_m1_recovery


def _events(
    recovery_id: str,
    phases: tuple[str, ...] = M1_RECOVERY_PHASES,
) -> list[dict[str, str]]:
    return [
        {"recovery_id": recovery_id, "event_type": phase}
        for phase in phases
    ]


def test_one_complete_attempt_is_offline_ready_and_allows_intermediate_duplicates() -> None:
    events = _events("recovery-1")
    events.insert(2, {"recovery_id": "recovery-1", "event_type": "revive_requested"})
    events.insert(7, {"recovery_id": "recovery-1", "event_type": "resources_ready"})

    result = assess_m1_recovery(events)

    assert result["offline_ready"] is True
    assert result["complete_attempts"] == 1
    assert result["consecutive_complete_attempts"] == 1
    assert result["attempts"] == [
        {
            "recovery_id": "recovery-1",
            "passed": True,
            "reason": "complete",
            "phases": list(M1_RECOVERY_PHASES),
        }
    ]
    json.dumps(result)


def test_three_complete_attempts_report_three_consecutive() -> None:
    result = assess_m1_recovery(
        _events("recovery-1") + _events("recovery-2") + _events("recovery-3")
    )

    assert result["offline_ready"] is True
    assert result["complete_attempts"] == 3
    assert result["consecutive_complete_attempts"] == 3
    assert all(attempt["passed"] for attempt in result["attempts"])


def test_missing_phase_fails_attempt() -> None:
    phases = M1_RECOVERY_PHASES[:-1]

    result = assess_m1_recovery(_events("missing", phases))

    assert result["offline_ready"] is False
    assert result["consecutive_complete_attempts"] == 0
    assert result["attempts"][0]["passed"] is False
    assert result["attempts"][0]["reason"] == "missing_phase:death_recovery_completed"


def test_out_of_order_phase_fails_attempt() -> None:
    phases = list(M1_RECOVERY_PHASES)
    phases[2], phases[3] = phases[3], phases[2]

    result = assess_m1_recovery(_events("out-of-order", tuple(phases)))

    assert result["offline_ready"] is False
    assert result["attempts"][0]["reason"] == "out_of_order:notice_closed:expected:revive_confirmed"


def test_duplicate_terminal_fails_otherwise_complete_attempt() -> None:
    events = _events("duplicate-terminal")
    events.append(
        {
            "recovery_id": "duplicate-terminal",
            "event_type": "death_recovery_completed",
        }
    )

    result = assess_m1_recovery(events)

    assert result["offline_ready"] is False
    assert result["complete_attempts"] == 0
    assert result["attempts"][0]["passed"] is False
    assert result["attempts"][0]["reason"] == "duplicate_terminal:death_recovery_completed"


def test_latest_failed_attempt_clears_current_offline_readiness() -> None:
    events = _events("complete") + _events("failed", M1_RECOVERY_PHASES[:-1])

    result = assess_m1_recovery(events)

    assert result["complete_attempts"] == 1
    assert result["longest_complete_streak"] == 1
    assert result["consecutive_complete_attempts"] == 0
    assert result["latest_attempt_passed"] is False
    assert result["offline_ready"] is False
