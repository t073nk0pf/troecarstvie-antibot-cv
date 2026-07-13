from __future__ import annotations

from src.antibot_cv.automation.quest_policy import (
    Intent,
    Quest,
    QuestIdentity,
    QuestIntent,
    QuestObjectiveKind,
    QuestPolicy,
    QuestProgress,
    QuestSnapshot,
    QuestStatus,
)


NOW = 1_800_000_000.0
IDENTITY = QuestIdentity("client-a", "profile-a", 17)


def policy(**overrides: object) -> QuestPolicy:
    values = {
        "require_active_quest": True,
        "require_route_location": True,
        "expected_identity": IDENTITY,
        "max_snapshot_age_s": 15.0,
        "now": lambda: NOW,
    }
    values.update(overrides)
    return QuestPolicy("  Герой  ", **values)


def snapshot(*quests: Quest, **overrides: object) -> QuestSnapshot:
    values = {
        "status": "loaded",
        "quests": tuple(quests),
        "current_location": "Лес",
        "snapshot_id": "snapshot-1",
        "generated_at": NOW - 1,
        "client_id": "client-a",
        "profile_id": "profile-a",
        "tab_id": 17,
        "href": "https://3kingdoms.ru/user_quest.php?mode=started",
        "page_kind": "quests",
    }
    values.update(overrides)
    return QuestSnapshot(**values)


def combat_quest(**overrides: object) -> Quest:
    values = {
        "id": "q1",
        "title": "Охота",
        "target_mobs": ("Волк",),
        "locations": ("Лес",),
        "status": QuestStatus.ACTIVE,
        "objective_kind": QuestObjectiveKind.COMBAT,
    }
    values.update(overrides)
    return Quest(**values)


def decide(*, quest_state: QuestSnapshot | dict | None = None, **overrides: object):
    values = {
        "character_name": "герой",
        "current_level": 4,
        "current_xp": 50.0,
        "goal_level": 5,
        "quest_state": quest_state if quest_state is not None else snapshot(combat_quest()),
    }
    values.update(overrides)
    return policy().decide(**values)


def test_one_fresh_active_combat_quest_is_selected() -> None:
    result = decide(character_name="  ГЕРОЙ ")
    assert result.intent is QuestIntent.SELECT_QUEST
    assert result.reason == "active_combat_quest_confirmed"
    assert result.target_mobs == ("Волк",)
    assert result.locations == ("Лес",)
    assert result.snapshot_id == "snapshot-1"
    assert Intent is QuestIntent


def test_available_completed_and_unknown_quests_do_not_become_active_targets() -> None:
    for status in (QuestStatus.AVAILABLE, QuestStatus.COMPLETED, QuestStatus.UNKNOWN):
        result = decide(quest_state=snapshot(combat_quest(status=status)))
        assert result.intent is QuestIntent.STOP_UNSAFE
        assert result.reason == "no_active_combat_quest"


def test_quest_optional_mode_can_start_regular_farm() -> None:
    result = policy(require_active_quest=False).decide(
        character_name="герой",
        current_level=4,
        current_xp=1,
        goal_level=5,
        quest_state=snapshot(),
    )
    assert result.intent is QuestIntent.START_FARM
    assert result.reason == "quest_optional"


def test_combat_target_and_route_must_be_explicit_and_unambiguous() -> None:
    cases = (
        (combat_quest(target_mobs=()), "combat_target_missing"),
        (combat_quest(target_mobs=("Волк", "Рысь")), "ambiguous_combat_targets"),
        (combat_quest(locations=()), "quest_route_missing"),
        (combat_quest(locations=("Лес", "Поле")), "ambiguous_quest_route"),
    )
    for quest, reason in cases:
        result = decide(quest_state=snapshot(quest))
        assert result.intent is QuestIntent.STOP_UNSAFE
        assert result.reason == reason


def test_different_explicit_location_requests_navigation() -> None:
    result = decide(quest_state=snapshot(combat_quest(locations=("Город",))))
    assert result.intent is QuestIntent.NAVIGATE
    assert result.locations == ("Город",)
    assert result.target_mobs == ("Волк",)


def test_confirmed_objective_progress_stops_farming_for_turn_in() -> None:
    result = decide(
        quest_state=snapshot(
            combat_quest(progress=QuestProgress(current=5, required=5, complete=True, evidence="objective_ratio"))
        )
    )
    assert result.intent is QuestIntent.OBJECTIVE_COMPLETE
    assert result.reason == "quest_objective_complete"
    assert result.progress.current == 5
    assert result.progress.required == 5


def test_multiple_active_combat_quests_are_ambiguous() -> None:
    result = decide(quest_state=snapshot(combat_quest(), combat_quest(id="q2", title="Другая охота")))
    assert result.intent is QuestIntent.STOP_UNSAFE
    assert result.reason == "ambiguous_active_combat_quests"


def test_snapshot_must_be_fresh_loaded_and_bound_to_logical_tab() -> None:
    cases = (
        (snapshot(combat_quest(), status="partial"), "quest_snapshot_unavailable"),
        (snapshot(combat_quest(), stale=True), "quest_snapshot_unavailable"),
        (snapshot(combat_quest(), generated_at=NOW - 20), "quest_snapshot_stale"),
        (snapshot(combat_quest(), snapshot_id=None), "quest_snapshot_identity_missing"),
        (snapshot(combat_quest(), profile_id="other"), "quest_snapshot_identity_mismatch"),
        (snapshot(combat_quest(), tab_id=18), "quest_snapshot_identity_mismatch"),
        (snapshot(combat_quest(), page_kind="hunt"), "quest_snapshot_identity_missing"),
    )
    for state, reason in cases:
        result = decide(quest_state=state)
        assert result.intent is QuestIntent.STOP_UNSAFE
        assert result.reason == reason


def test_mapping_adapter_rejects_string_booleans_and_preserves_field_types() -> None:
    state = {
        "status": "available",
        "snapshotId": "snapshot-raw",
        "generatedAt": "2027-01-15T08:00:00Z",
        "clientId": "client-a",
        "profileId": "profile-a",
        "tabId": 17,
        "href": "https://3kingdoms.ru/user_quest.php?mode=started",
        "pageKind": "quests",
        "data": {
            "loadStatus": "loaded",
            "currentLocation": "Лес",
            "items": [
                {
                    "id": "q1",
                    "title": "Охота",
                    "status": "active",
                    "objectiveKind": "combat",
                    "targetMobs": ["Лесной жук"],
                    "locations": ["Волчья поляна"],
                    "suitable": "false",
                }
            ],
        },
    }
    result = policy(now=lambda: 1_800_000_000.0).decide(
        character_name="герой", current_level=4, current_xp=5, goal_level=5, quest_state=state
    )
    assert result.intent is QuestIntent.STOP_UNSAFE
    assert result.reason == "no_active_combat_quest"


def test_current_location_is_required_for_route_quest() -> None:
    result = decide(quest_state=snapshot(combat_quest(), current_location=None))
    assert result.intent is QuestIntent.STOP_UNSAFE
    assert result.reason == "current_location_unknown"


def test_goal_and_player_observation_are_strict() -> None:
    assert decide(current_level=5, quest_state=None).intent is QuestIntent.COMPLETE
    for values in (
        {"character_name": "другой"},
        {"current_level": -1},
        {"current_xp": float("inf")},
        {"goal_level": 0},
    ):
        assert decide(**values).intent is QuestIntent.STOP_UNSAFE


def test_repeated_snapshot_is_deterministic_and_does_not_mutate_input() -> None:
    state = snapshot(combat_quest())
    first = decide(quest_state=state)
    second = decide(quest_state=state)
    assert first == second
    assert state.quests[0].target_mobs == ("Волк",)
