"""Pure CPU scheduling policy for the controller capture loop."""

from __future__ import annotations

from src.antibot_cv.automation.state_machine import GameState


LOW_RATE_STATES = frozenset(
    {
        GameState.PAUSED,
        GameState.RESTING,
        GameState.ROUTE_RECOVERY,
        GameState.QUEST_REFRESH_PENDING,
        GameState.NAVIGATOR_PENDING,
        GameState.REVIVE_PENDING,
        GameState.POST_REVIVE_RECOVERY,
    }
)

CV_BURST_STATES = frozenset(
    {
        GameState.TARGET_FOUND,
        GameState.TARGET_SELECTED,
        GameState.BATTLE_WAIT,
        GameState.BATTLE_ACTIVE,
        GameState.ABILITY_4_USED,
        GameState.WAIT_BATTLE_END,
        GameState.BATTLE_END_DETECTED,
        GameState.STATISTICS_WAIT,
    }
)


def capture_fps_for_state(state: GameState, configured_fps: int) -> int:
    """Return a bounded state-aware FPS without changing configured semantics."""

    cap = max(1, int(configured_fps))
    if state in LOW_RATE_STATES:
        return min(cap, 2)
    if state in CV_BURST_STATES:
        return min(cap, 30)
    return min(cap, 5)


def evidence_hash_view(frame: object, *, stride: int = 4) -> object:
    """Use a cheap spatial sample; callers make it contiguous only on demand."""

    try:
        return frame[::stride, ::stride, :3]  # type: ignore[index]
    except (IndexError, TypeError):
        return frame
