from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.automation.state_snapshot_profiles import sections_for_state


def test_general_heartbeat_excludes_expensive_sections() -> None:
    assert sections_for_state(GameState.RESTING, "main") == (
        "player",
        "location",
        "deathRevive",
    )


def test_profiles_add_only_state_relevant_sections() -> None:
    assert sections_for_state(GameState.BATTLE_ACTIVE, "battle")[-2:] == (
        "battle",
        "questChatProgress",
    )
    assert sections_for_state(GameState.LOCATION_SEARCH, "hunt")[-2:] == (
        "hunt",
        "questChatProgress",
    )
    assert sections_for_state(GameState.QUEST_REFRESH_PENDING, "quests")[-2:] == (
        "quests",
        "questChatProgress",
    )
    assert sections_for_state(GameState.RESTING, "inventory")[-1] == "shopInventory"


def test_route_recovery_observes_battle_without_scanning_unrelated_domains() -> None:
    sections = sections_for_state(GameState.ROUTE_RECOVERY, "area")
    assert "battle" in sections
    assert "hunt" not in sections
    assert "quests" not in sections
    assert "questChatProgress" in sections
    assert "shopInventory" not in sections
