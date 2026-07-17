from __future__ import annotations

from src.antibot_cv.automation.state_machine import GameState


BASE_SECTIONS = ("player", "location", "deathRevive")

_COMBAT_STATES = frozenset(
    {
        GameState.BATTLE_WAIT,
        GameState.BATTLE_ACTIVE,
        GameState.ABILITY_4_USED,
        GameState.WAIT_BATTLE_END,
        GameState.BATTLE_END_DETECTED,
        GameState.EXIT_BATTLE,
        GameState.ROUTE_RECOVERY,
    }
)

_HUNT_STATES = frozenset(
    {
        GameState.LOCATION_SEARCH,
        GameState.VIEWPORT_SCAN,
        GameState.TARGET_FOUND,
        GameState.TARGET_SELECTED,
        GameState.RETURN_TO_HUNT,
        GameState.COOLDOWN,
    }
)


def sections_for_state(state: GameState, page_kind: str | None) -> tuple[str, ...]:
    """Return the smallest operational snapshot required by current intent.

    Expensive catalogue and inventory sections are page/state scoped.  The
    bounded incremental chat observer accompanies combat/hunt heartbeats so a
    server collection-complete line can trigger authoritative quest refresh.
    """

    page = str(page_kind or "").strip().casefold()
    sections = list(BASE_SECTIONS)
    if state in _COMBAT_STATES or page == "battle":
        sections.append("battle")
    if state in _HUNT_STATES or page == "hunt":
        sections.append("hunt")
    if state in _COMBAT_STATES or state in _HUNT_STATES or page in {"battle", "hunt"}:
        sections.append("questChatProgress")
    if state is GameState.QUEST_REFRESH_PENDING or page == "quests":
        sections.append("quests")
        if "questChatProgress" not in sections:
            sections.append("questChatProgress")
    if page in {"inventory", "shop"}:
        sections.append("shopInventory")
    return tuple(sections)
