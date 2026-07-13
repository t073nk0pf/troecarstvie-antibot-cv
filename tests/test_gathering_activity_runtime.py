from types import MappingProxyType

from src.antibot_cv.automation.gathering_activity_runtime import (
    GatheringPlanStatus,
    gathering_progress,
    parse_gathering_plan,
)
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry


def entry(objective: str) -> ActiveQuestEntry:
    return ActiveQuestEntry(
        "246",
        "Хворь скакунов",
        MappingProxyType(
            {
                "id": "246",
                "title": "Хворь скакунов",
                "objectiveKind": "collect",
                "objective": objective,
            }
        ),
    )


def test_parses_multi_resource_collection_and_sums_case_inflected_inventory_stacks() -> None:
    plan = parse_gathering_plan(
        entry("Добудьте 100 Тисса и 100 Вьюнка узколистного, затем отнесите растения Филониду.")
    )

    progress = gathering_progress(
        plan,
        (
            MappingProxyType({"name": "Тисс", "count": 30}),
            MappingProxyType({"name": "Тисс", "count": 70}),
            MappingProxyType({"name": "Вьюнок узколистный", "count": 100}),
        ),
    )

    assert plan.status is GatheringPlanStatus.READY
    assert tuple((item.name, item.required) for item in plan.requirements) == (
        ("Тисса", 100),
        ("Вьюнка узколистного", 100),
    )
    assert progress.complete is True
    assert progress.missing == ()


def test_rejects_ambiguous_case_inflection_instead_of_crediting_the_quest() -> None:
    plan = parse_gathering_plan(entry("Добудьте 5 Тисса."))

    progress = gathering_progress(
        plan,
        (
            MappingProxyType({"name": "Тисс", "count": 5}),
            MappingProxyType({"name": "Тиссовый экстракт", "count": 5}),
        ),
    )

    assert progress.complete is False
    assert progress.missing[0].required == 5
