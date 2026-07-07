from __future__ import annotations

import pytest

from src.antibot_cv.automation.state_machine import GameState, InvalidTransitionError, StateMachine
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger


def test_valid_transition_logs_event() -> None:
    logger = InMemoryEventLogger()
    machine = StateMachine(logger)
    machine.transition(GameState.TARGET_FOUND, cycle_id=2)
    assert machine.state == GameState.TARGET_FOUND
    assert logger.events[-1]["event_type"] == "state_transition"
    assert logger.events[-1]["previous_state"] == "LOCATION_SEARCH"
    assert logger.events[-1]["state"] == "TARGET_FOUND"
    assert logger.events[-1]["cycle_id"] == 2


def test_invalid_transition_rejected() -> None:
    machine = StateMachine()
    with pytest.raises(InvalidTransitionError):
        machine.transition(GameState.ABILITY_4_USED)


def test_battle_flow_transitions() -> None:
    machine = StateMachine()
    for state in [
        GameState.TARGET_FOUND,
        GameState.TARGET_SELECTED,
        GameState.BATTLE_WAIT,
        GameState.BATTLE_ACTIVE,
        GameState.ABILITY_4_USED,
        GameState.WAIT_BATTLE_END,
        GameState.BATTLE_END_DETECTED,
        GameState.EXIT_BATTLE,
        GameState.STATISTICS_WAIT,
        GameState.STATISTICS_DETECTED,
        GameState.RETURN_TO_HUNT,
        GameState.COOLDOWN,
        GameState.LOCATION_SEARCH,
    ]:
        machine.transition(state)
    assert machine.state == GameState.LOCATION_SEARCH
