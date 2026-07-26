from types import MappingProxyType

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_objective_runtime import (
    ObjectiveRefreshState,
    ObjectiveSelectionStatus,
    compare_refreshed_objective,
    select_monster_hunt_objective,
)


def entry(
    quest_id: str = "91",
    *,
    title: str = "Охота на рысь",
    objective: str = "Убить Белую Рысь 3/5",
    navigation: tuple[MappingProxyType[str, object], ...] | None = None,
    progress: object = MappingProxyType({"current": 3, "required": 5, "complete": False}),
) -> ActiveQuestEntry:
    routes = navigation or (
        MappingProxyType({"text": "Белая Рысь", "target": "Белая Рысь [6]"}),
        MappingProxyType({"text": "Лесная опушка", "target": "Лесная опушка"}),
    )
    data = MappingProxyType(
        {
            "id": quest_id,
            "title": title,
            "status": "active",
            "objective": objective,
            "navigation": routes,
            "progress": progress,
        }
    )
    return ActiveQuestEntry(quest_id, title, data)


def test_selects_exact_supported_monster_target_and_preserves_snapshot_fields() -> None:
    result = select_monster_hunt_objective((entry(),), current_level_cap=6)

    assert result.status is ObjectiveSelectionStatus.SELECTED
    assert result.objective is not None
    assert result.objective.quest_id == "91"
    assert result.objective.objective == "Убить Белую Рысь 3/5"
    assert result.objective.monster.target == "Белая Рысь [6]"
    assert result.objective.monster.name == "Белая Рысь"
    assert result.objective.monster.level == 6
    assert result.objective.navigator_label == "Белая Рысь"
    assert (result.objective.progress, result.objective.required) == (3, 5)
    assert result.objective.complete is False
    assert len(result.objective.fingerprint) == 64


def test_numeric_progress_is_optional_and_location_after_monster_is_allowed() -> None:
    result = select_monster_hunt_objective(
        (entry(objective="Победить рысь", progress=None),), current_level_cap=10
    )

    assert result.status is ObjectiveSelectionStatus.SELECTED
    assert result.objective is not None
    assert result.objective.progress is None
    assert result.objective.required is None


def test_does_not_skip_an_earlier_chain_step_to_reach_a_later_monster() -> None:
    chained = entry(
        navigation=(
            MappingProxyType({"text": "Дом Франка", "target": "Дом Франка"}),
            MappingProxyType({"text": "Серый Волк", "target": "Серый Волк [5]"}),
        )
    )

    result = select_monster_hunt_objective((chained,), current_level_cap=5)

    assert result.status is ObjectiveSelectionStatus.NONE_SUPPORTED
    assert result.reason == "no_supported_monster_hunt"


def test_deterministic_selection_uses_complete_catalog_order_not_level() -> None:
    first = entry("20", navigation=(MappingProxyType({"text": "Волк", "target": "Волк [8]"}),))
    second = entry("3", navigation=(MappingProxyType({"text": "Лиса", "target": "Лиса [2]"}),))

    result = select_monster_hunt_objective((first, second), current_level_cap=10)

    assert result.objective is not None
    assert result.objective.quest_id == "20"
    assert result.objective.source_order == 0


def test_fails_closed_for_duplicate_ids_ambiguity_and_level_cap() -> None:
    duplicate = select_monster_hunt_objective((entry(), entry()), current_level_cap=10)
    ambiguous = entry(
        navigation=(
            MappingProxyType({"text": "Рысь", "target": "Рысь [6]"}),
            MappingProxyType({"text": "Волк", "target": "Волк [5]"}),
        )
    )
    ambiguous_result = select_monster_hunt_objective((ambiguous,), current_level_cap=10)
    capped = select_monster_hunt_objective((entry(),), current_level_cap=5)

    assert duplicate.status is ObjectiveSelectionStatus.UNSAFE
    assert duplicate.reason == "duplicate_quest_id"
    assert ambiguous_result.status is ObjectiveSelectionStatus.UNSAFE
    assert "ambiguous_monster_target" in ambiguous_result.reason
    assert capped.status is ObjectiveSelectionStatus.UNSAFE
    assert "above_level_cap" in capped.reason


def test_skips_non_monster_quests_but_rejects_unsafe_identity() -> None:
    dialogue = entry(navigation=(MappingProxyType({"text": "Франк", "target": "Дом Франка"}),))
    unsupported = select_monster_hunt_objective((dialogue,), current_level_cap=10)
    bad = entry()
    bad = ActiveQuestEntry(bad.id, bad.title, MappingProxyType({**bad.data, "title": "Changed"}))
    unsafe = select_monster_hunt_objective((bad,), current_level_cap=10)

    assert unsupported.status is ObjectiveSelectionStatus.NONE_SUPPORTED
    assert unsafe.status is ObjectiveSelectionStatus.UNSAFE
    assert "unsafe_active_identity" in unsafe.reason


def test_refresh_comparison_handles_same_step_completion_removal_and_regression() -> None:
    selected = select_monster_hunt_objective((entry(),), current_level_cap=10).objective
    assert selected is not None

    same = compare_refreshed_objective(selected, (entry(),), current_level_cap=10)
    progressed = compare_refreshed_objective(
        selected,
        (entry(objective="Убить Белую Рысь 4/5", progress=MappingProxyType({"current": 4, "required": 5, "complete": False})),),
        current_level_cap=10,
    )
    completed = compare_refreshed_objective(
        selected,
        (entry(objective="Убить Белую Рысь 5/5", progress=MappingProxyType({"current": 5, "required": 5, "complete": True})),),
        current_level_cap=10,
    )
    removed = compare_refreshed_objective(selected, (), current_level_cap=10)
    regressed = compare_refreshed_objective(
        selected,
        (entry(objective="Убить Белую Рысь 2/5", progress=MappingProxyType({"current": 2, "required": 5, "complete": False})),),
        current_level_cap=10,
    )

    assert same.state is ObjectiveRefreshState.SAME_STEP
    assert progressed.state is ObjectiveRefreshState.SAME_STEP
    assert completed.state is ObjectiveRefreshState.STEP_COMPLETED
    assert removed.state is ObjectiveRefreshState.QUEST_REMOVED_UNVERIFIED
    assert regressed.state is ObjectiveRefreshState.REGRESSED_UNSAFE


def test_changed_supported_step_is_changed_but_changed_identity_is_unsafe() -> None:
    selected = select_monster_hunt_objective((entry(),), current_level_cap=10).objective
    assert selected is not None
    next_step = entry(
        objective="Убить Серого Волка",
        navigation=(MappingProxyType({"text": "Серый Волк", "target": "Серый Волк [7]"}),),
        progress=None,
    )
    changed = compare_refreshed_objective(selected, (next_step,), current_level_cap=10)
    changed_title = compare_refreshed_objective(
        selected, (entry(title="Другой квест"),), current_level_cap=10
    )

    assert changed.state is ObjectiveRefreshState.STEP_CHANGED
    assert changed_title.state is ObjectiveRefreshState.REGRESSED_UNSAFE
