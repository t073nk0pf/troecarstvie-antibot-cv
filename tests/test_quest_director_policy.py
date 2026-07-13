from __future__ import annotations

from src.antibot_cv.automation.quest_director_policy import (
    QuestDirectorIntent,
    QuestDirectorPolicy,
    QuestDirectorState,
    QuestRef,
)


def quest(number: int) -> QuestRef:
    return QuestRef(f"q{number}", f"Quest {number}", f"accept-{number}")


def decide(**overrides: object):
    values = {
        "discovery_initialized": True,
        "available_snapshot_fresh": True,
        "active_snapshot_fresh": True,
    }
    values.update(overrides)
    return QuestDirectorPolicy().decide(QuestDirectorState(**values))


def test_initial_observation_always_refreshes_global_available_catalogue() -> None:
    result = QuestDirectorPolicy().decide(QuestDirectorState())

    assert result.intent is QuestDirectorIntent.REFRESH_AVAILABLE
    assert result.reason == "initial_available_refresh_required"


def test_refresh_in_progress_waits_without_starting_other_work() -> None:
    result = decide(refresh_in_progress=True, active_quests=(quest(1),))

    assert result.intent is QuestDirectorIntent.WAIT
    assert result.reason == "available_refresh_in_progress"


def test_refresh_is_due_after_every_five_completed_quests() -> None:
    before = decide(completed_since_refresh=4, active_quests=(quest(1),))
    due = decide(completed_since_refresh=5, active_quests=(quest(1),))
    overdue = decide(completed_since_refresh=8, active_quests=(quest(1),))

    assert before.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert due.intent is QuestDirectorIntent.REFRESH_AVAILABLE
    assert overdue.intent is QuestDirectorIntent.REFRESH_AVAILABLE
    assert due.reason == "completed_quest_refresh_interval_reached"


def test_all_available_quests_are_queued_before_active_execution() -> None:
    result = decide(
        active_quests=(quest(9),),
        available_quests=(quest(1), quest(2), quest(3)),
    )

    assert result.intent is QuestDirectorIntent.ACCEPT_QUEST
    assert result.quest == quest(1)
    assert result.intake_queue == (quest(1), quest(2), quest(3))


def test_existing_queue_order_is_stable_and_new_available_items_append() -> None:
    result = decide(
        intake_queue=(quest(2), quest(1)),
        available_quests=(quest(1), quest(3)),
    )

    assert result.quest == quest(2)
    assert result.intake_queue == (quest(2), quest(1), quest(3))


def test_active_or_duplicate_available_quest_is_not_reaccepted() -> None:
    result = decide(
        active_quests=(quest(1),),
        available_quests=(quest(1), quest(2), quest(2)),
    )

    assert result.intent is QuestDirectorIntent.ACCEPT_QUEST
    assert result.quest == quest(2)
    assert result.intake_queue == (quest(2),)


def test_stale_available_observations_are_not_added_to_intake() -> None:
    result = decide(
        available_snapshot_fresh=False,
        active_quests=(quest(9),),
        available_quests=(quest(1),),
    )

    assert result.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert result.quest == quest(9)
    assert result.intake_queue == ()


def test_stale_snapshot_discards_previously_queued_intake() -> None:
    result = decide(available_snapshot_fresh=False, intake_queue=(quest(1),))

    assert result.intent is QuestDirectorIntent.REFRESH_AVAILABLE
    assert result.intake_queue == ()


def test_active_quest_executes_after_acceptance_queue_is_empty() -> None:
    result = decide(active_quests=(quest(4), quest(5)))

    assert result.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert result.quest == quest(4)


def test_empty_active_set_refreshes_when_available_snapshot_is_not_fresh() -> None:
    result = decide(available_snapshot_fresh=False)

    assert result.intent is QuestDirectorIntent.REFRESH_AVAILABLE
    assert result.reason == "active_queue_empty_refresh_required"


def test_active_snapshot_must_be_refreshed_before_intake_or_profit_farm() -> None:
    result = decide(active_snapshot_fresh=False, available_quests=(quest(1),))

    assert result.intent is QuestDirectorIntent.REFRESH_ACTIVE
    assert result.intake_queue == ()


def test_profit_farm_requires_fresh_proof_that_active_and_available_are_empty() -> None:
    result = decide()

    assert result.intent is QuestDirectorIntent.PROFIT_FARM
    assert result.reason == "fresh_catalogue_and_active_queue_empty"


def test_invalid_inputs_fail_closed() -> None:
    invalid_state = QuestDirectorPolicy().decide(object())
    invalid_interval = QuestDirectorPolicy(refresh_every_completed=0).decide(QuestDirectorState())
    invalid_count = QuestDirectorPolicy().decide(QuestDirectorState(completed_since_refresh=-1))
    invalid_ref = QuestDirectorPolicy().decide(
        QuestDirectorState(active_quests=(QuestRef("", "Untitled"),))
    )
    duplicate_active = QuestDirectorPolicy().decide(
        QuestDirectorState(active_quests=(quest(1), quest(1)))
    )

    assert invalid_state.reason == "invalid_director_state"
    assert invalid_interval.intent is QuestDirectorIntent.STOP_UNSAFE
    assert invalid_count.reason == "invalid_completed_quest_count"
    assert invalid_ref.reason == "invalid_quest_reference"
    assert duplicate_active.reason == "duplicate_active_quest"
