from __future__ import annotations

from src.antibot_cv.automation.area_object_activity import (
    AreaObjectPlanStatus,
    area_object_progress,
    parse_area_object_plan,
)
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry


def _entry(objective: str, navigation: list[dict[str, str]]) -> ActiveQuestEntry:
    return ActiveQuestEntry(
        id="304",
        title="Цветочная болезнь",
        data={
            "objective": objective,
            "navigation": navigation,
            "objectiveKind": "collect",
        },
    )


def test_parses_location_bound_area_object_requirements_in_objective_order() -> None:
    plan = parse_area_object_plan(
        _entry(
            "Убивая Непобедимых кабанов, получите 5 пузырьков крови, также найдите в Пристанище трёх ветров Светящийся мох, в Длани Рода Пятнистый гриб и 5 свежих листьев кустарника на Просторах безмолвия. Собрав необходимое, возвращайтесь к колдунье Вилене.",
            [
                {"text": "Пристанище трёх ветров", "target": "Пристанище трёх ветров"},
                {"text": "Длани Рода", "target": "Длань Рода"},
                {"text": "Просторах безмолвия", "target": "Просторы безмолвия"},
            ],
        )
    )

    assert plan.status is AreaObjectPlanStatus.READY
    assert [(item.resource_name, item.required, item.location) for item in plan.requirements] == [
        ("Светящийся мох", 1, "Пристанище трёх ветров"),
        ("Пятнистый гриб", 1, "Длань Рода"),
        ("свежих листьев кустарника", 5, "Просторы безмолвия"),
    ]


def test_rejects_unbound_free_text_location() -> None:
    plan = parse_area_object_plan(
        _entry(
            "Найдите в Пристанище трёх ветров Светящийся мох.",
            [{"text": "Длань Рода", "target": "Длань Рода"}],
        )
    )
    assert plan.status is AreaObjectPlanStatus.UNSUPPORTED
    assert plan.reason == "area_requirements_unparsed"


def test_selects_first_missing_requirement_in_objective_order() -> None:
    plan = parse_area_object_plan(
        _entry(
            "Найдите в Пристанище трёх ветров Светящийся мох, в Длани Рода Пятнистый гриб и 5 свежих листьев кустарника на Просторах безмолвия.",
            [
                {"text": "Пристанище трёх ветров", "target": "Пристанище трёх ветров"},
                {"text": "Длани Рода", "target": "Длань Рода"},
                {"text": "Просторах безмолвия", "target": "Просторы безмолвия"},
            ],
        )
    )
    progress = area_object_progress(
        plan,
        [
            {"artAltTitle": "Светящийся мох", "count": 1},
            {"artAltTitle": "Пятнистый гриб", "count": 1},
            {"artAltTitle": "Свежий лист кустарника", "count": 4},
        ],
    )
    assert progress.complete is False
    assert progress.next_requirement is not None
    assert progress.next_requirement.location == "Просторы безмолвия"
    assert progress.next_requirement.required == 5
