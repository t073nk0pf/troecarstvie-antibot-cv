from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class GameState(Enum):
    PAUSED = "PAUSED"
    LOCATION_SEARCH = "LOCATION_SEARCH"
    VIEWPORT_SCAN = "VIEWPORT_SCAN"
    TARGET_FOUND = "TARGET_FOUND"
    TARGET_SELECTED = "TARGET_SELECTED"
    BATTLE_WAIT = "BATTLE_WAIT"
    BATTLE_ACTIVE = "BATTLE_ACTIVE"
    ABILITY_4_USED = "ABILITY_4_USED"
    WAIT_BATTLE_END = "WAIT_BATTLE_END"
    BATTLE_END_DETECTED = "BATTLE_END_DETECTED"
    EXIT_BATTLE = "EXIT_BATTLE"
    STATISTICS_WAIT = "STATISTICS_WAIT"
    STATISTICS_DETECTED = "STATISTICS_DETECTED"
    RETURN_TO_HUNT = "RETURN_TO_HUNT"
    COOLDOWN = "COOLDOWN"
    RESTING = "RESTING"
    DEAD = "DEAD"
    REVIVE_PENDING = "REVIVE_PENDING"
    ROUTE_RECOVERY = "ROUTE_RECOVERY"
    QUEST_REFRESH_PENDING = "QUEST_REFRESH_PENDING"
    NAVIGATOR_PENDING = "NAVIGATOR_PENDING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


ALLOWED_TRANSITIONS: dict[GameState, frozenset[GameState]] = {
    GameState.PAUSED: frozenset({GameState.LOCATION_SEARCH, GameState.STOPPED}),
    GameState.LOCATION_SEARCH: frozenset(
        {
            GameState.TARGET_FOUND,
            GameState.VIEWPORT_SCAN,
            GameState.BATTLE_WAIT,
            GameState.BATTLE_ACTIVE,
            GameState.WAIT_BATTLE_END,
            GameState.STATISTICS_WAIT,
            GameState.RESTING,
            GameState.QUEST_REFRESH_PENDING,
            GameState.STOPPED,
            GameState.ERROR,
        }
    ),
    GameState.VIEWPORT_SCAN: frozenset(
        {
            GameState.TARGET_FOUND,
            GameState.LOCATION_SEARCH,
            GameState.VIEWPORT_SCAN,
            GameState.BATTLE_WAIT,
            GameState.BATTLE_ACTIVE,
            GameState.WAIT_BATTLE_END,
            GameState.STATISTICS_WAIT,
            GameState.RESTING,
            GameState.QUEST_REFRESH_PENDING,
            GameState.STOPPED,
            GameState.ERROR,
        }
    ),
    GameState.TARGET_FOUND: frozenset({GameState.TARGET_SELECTED, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}),
    GameState.TARGET_SELECTED: frozenset({GameState.BATTLE_WAIT, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}),
    GameState.BATTLE_WAIT: frozenset({GameState.BATTLE_ACTIVE, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}),
    GameState.BATTLE_ACTIVE: frozenset(
        {GameState.ABILITY_4_USED, GameState.WAIT_BATTLE_END, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}
    ),
    GameState.ABILITY_4_USED: frozenset({GameState.WAIT_BATTLE_END, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}),
    GameState.WAIT_BATTLE_END: frozenset(
        {GameState.BATTLE_END_DETECTED, GameState.WAIT_BATTLE_END, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}
    ),
    GameState.BATTLE_END_DETECTED: frozenset({GameState.EXIT_BATTLE, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}),
    GameState.EXIT_BATTLE: frozenset({GameState.STATISTICS_WAIT, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}),
    GameState.STATISTICS_WAIT: frozenset(
        {GameState.STATISTICS_DETECTED, GameState.STATISTICS_WAIT, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}
    ),
    GameState.STATISTICS_DETECTED: frozenset({GameState.RETURN_TO_HUNT, GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}),
    GameState.RETURN_TO_HUNT: frozenset({GameState.COOLDOWN, GameState.STOPPED, GameState.ERROR}),
    GameState.COOLDOWN: frozenset({GameState.LOCATION_SEARCH, GameState.RESTING, GameState.STOPPED, GameState.ERROR}),
    GameState.RESTING: frozenset({GameState.LOCATION_SEARCH, GameState.STOPPED, GameState.ERROR}),
    GameState.DEAD: frozenset({GameState.REVIVE_PENDING, GameState.ROUTE_RECOVERY, GameState.STOPPED, GameState.ERROR}),
    GameState.REVIVE_PENDING: frozenset(
        {GameState.REVIVE_PENDING, GameState.ROUTE_RECOVERY, GameState.DEAD, GameState.STOPPED, GameState.ERROR}
    ),
    GameState.ROUTE_RECOVERY: frozenset(
        {
            GameState.LOCATION_SEARCH,
            GameState.RESTING,
            GameState.QUEST_REFRESH_PENDING,
            GameState.NAVIGATOR_PENDING,
            GameState.DEAD,
            GameState.STOPPED,
            GameState.ERROR,
        }
    ),
    GameState.QUEST_REFRESH_PENDING: frozenset(
        {GameState.LOCATION_SEARCH, GameState.NAVIGATOR_PENDING, GameState.DEAD, GameState.STOPPED, GameState.ERROR}
    ),
    GameState.NAVIGATOR_PENDING: frozenset(
        {GameState.ROUTE_RECOVERY, GameState.LOCATION_SEARCH, GameState.DEAD, GameState.STOPPED, GameState.ERROR}
    ),
    GameState.ERROR: frozenset({GameState.STOPPED}),
    GameState.STOPPED: frozenset(),
}

for _state in tuple(ALLOWED_TRANSITIONS):
    if _state not in {
        GameState.STOPPED,
        GameState.ERROR,
        GameState.DEAD,
        GameState.REVIVE_PENDING,
        GameState.QUEST_REFRESH_PENDING,
        GameState.NAVIGATOR_PENDING,
    }:
        ALLOWED_TRANSITIONS[_state] = frozenset(set(ALLOWED_TRANSITIONS[_state]) | {GameState.DEAD})


class TransitionLogger(Protocol):
    def log_event(self, event_type: str, **fields: object) -> None:
        ...


class InvalidTransitionError(ValueError):
    pass


@dataclass
class StateMachine:
    logger: TransitionLogger | None = None
    state: GameState = GameState.LOCATION_SEARCH

    def can_transition(self, target: GameState) -> bool:
        return target in ALLOWED_TRANSITIONS[self.state]

    def transition(
        self,
        target: GameState,
        *,
        cycle_id: int = 0,
        battle_id: int | None = None,
        reason: str | None = None,
    ) -> GameState:
        if target == self.state and target in {
            GameState.VIEWPORT_SCAN,
            GameState.WAIT_BATTLE_END,
            GameState.STATISTICS_WAIT,
            GameState.REVIVE_PENDING,
        }:
            return self.state
        if not self.can_transition(target):
            raise InvalidTransitionError(f"Invalid transition {self.state.value} -> {target.value}")

        previous = self.state
        self.state = target
        if self.logger:
            self.logger.log_event(
                "state_transition",
                previous_state=previous.value,
                state=target.value,
                ts_monotonic=time.monotonic(),
                cycle_id=cycle_id,
                battle_id=battle_id,
                reason=reason,
            )
        return self.state

    def stop(self, *, cycle_id: int = 0, reason: str | None = None) -> GameState:
        if self.state == GameState.STOPPED:
            return self.state
        return self.transition(GameState.STOPPED, cycle_id=cycle_id, reason=reason)
