import pytest

from src.antibot_cv.automation.quest_work_scheduler import (
    LocalQuestTarget,
    QuestWorkScheduler,
)


def target(
    quest_id: str,
    target_id: str,
    *,
    location_id: str = "201",
    primary: bool = False,
    order: int = 0,
) -> LocalQuestTarget:
    return LocalQuestTarget(
        quest_id=quest_id,
        quest_title=f"Quest {quest_id}",
        current_location_id=location_id,
        target_id=target_id,
        target_name=f"Mob {target_id}",
        primary_chain=primary,
        source_order=order,
    )


def test_plan_keeps_primary_and_opportunistic_targets_separate_and_local() -> None:
    scheduler = QuestWorkScheduler()
    primary = target("246", "11", primary=True)
    side = target("91", "12", order=1)
    remote_side = target("92", "13", location_id="121", order=2)

    plan = scheduler.build_plan("201", (remote_side, side, primary))

    assert plan.primary_targets == (primary,)
    assert plan.opportunistic_targets == (side,)
    assert plan.selected_target == primary
    assert plan.reason == "primary_chain_local_target"
    assert plan.may_initiate_route is False
    assert remote_side not in (*plan.primary_targets, *plan.side_targets)


def test_shared_target_wins_and_reports_every_advanced_quest() -> None:
    scheduler = QuestWorkScheduler()
    primary = target("246", "11", primary=True, order=0)
    shared_first = target("91", "12", order=2)
    shared_second = target("92", "12", order=1)

    plan = scheduler.build_plan("201", (primary, shared_first, shared_second))

    assert plan.selected_target == shared_second
    assert plan.selected_quest_ids == ("92", "91")
    assert plan.multi_quest is True
    assert plan.reason == "multi_quest_local_target"


def test_shared_primary_target_is_accounted_as_primary_not_side() -> None:
    scheduler = QuestWorkScheduler(max_consecutive_side_victories=1)
    side = target("10", "20")
    scheduler.build_plan("201", (side,))
    scheduler.acknowledge_victory(progressed=True)
    shared_primary = target("246", "11", primary=True)
    shared_side = target("91", "11", order=1)

    plan = scheduler.build_plan("201", (side, shared_side, shared_primary))

    assert plan.selected_target == shared_primary
    scheduler.acknowledge_victory(progressed=True)
    assert scheduler.consecutive_side_victories == 0


def test_side_budgets_force_primary_and_never_produce_a_route() -> None:
    scheduler = QuestWorkScheduler(
        max_consecutive_side_victories=2,
        max_side_victories_per_location_visit=8,
    )
    side = target("10", "20")
    primary = target("246", "11", primary=True, order=1)
    for _ in range(2):
        plan = scheduler.build_plan("201", (side,))
        assert plan.selected_target == side
        scheduler.acknowledge_victory(progressed=True)

    side_only = scheduler.build_plan("201", (side,))
    with_primary = scheduler.build_plan("201", (side, primary))

    assert side_only.selected_target is None
    assert side_only.reason == "side_victory_budget_exhausted"
    assert side_only.may_initiate_route is False
    assert with_primary.selected_target == primary


def test_per_visit_side_budget_resets_on_new_location_visit() -> None:
    scheduler = QuestWorkScheduler(
        max_consecutive_side_victories=20,
        max_side_victories_per_location_visit=1,
    )
    first = target("10", "20")
    scheduler.build_plan("201", (first,))
    scheduler.acknowledge_victory(progressed=True)
    assert scheduler.build_plan("201", (first,)).selected_target is None

    elsewhere = target("11", "21", location_id="121")
    assert scheduler.build_plan("121", (elsewhere,)).selected_target == elsewhere


def test_no_progress_budget_quarantines_target_and_progress_resets_it() -> None:
    scheduler = QuestWorkScheduler(max_no_progress_victories=2)
    stuck = target("246", "11", primary=True)
    for _ in range(2):
        assert scheduler.build_plan("201", (stuck,)).selected_target == stuck
        scheduler.acknowledge_victory(progressed=False)

    exhausted = scheduler.build_plan("201", (stuck,))
    assert exhausted.selected_target is None
    assert exhausted.reason == "local_targets_exhausted_no_progress_budget"

    scheduler.acknowledge_progress(stuck)
    assert scheduler.build_plan("201", (stuck,)).selected_target == stuck


def test_primary_victory_resets_consecutive_side_budget() -> None:
    scheduler = QuestWorkScheduler(max_consecutive_side_victories=1)
    side = target("10", "20")
    primary = target("246", "11", primary=True)
    scheduler.build_plan("201", (side,))
    scheduler.acknowledge_victory(progressed=True)
    assert scheduler.consecutive_side_victories == 1

    scheduler.build_plan("201", (primary, side))
    scheduler.acknowledge_victory(progressed=True)

    assert scheduler.consecutive_side_victories == 0
    assert scheduler.build_plan("201", (side,)).selected_target == side


def test_order_is_deterministic_and_input_is_validated() -> None:
    scheduler = QuestWorkScheduler()
    late = target("20", "30", primary=True, order=3)
    early = target("3", "31", primary=True, order=1)

    first = scheduler.build_plan("201", (late, early))
    second = scheduler.build_plan("201", (early, late))

    assert first.selected_target == early
    assert second.selected_target == early
    with pytest.raises(ValueError):
        scheduler.build_plan("", (early,))
    with pytest.raises(ValueError):
        LocalQuestTarget("x", "Quest", "201", "11", "Mob")
