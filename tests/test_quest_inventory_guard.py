from src.antibot_cv.automation.quest_inventory_guard import (
    evaluate_quest_inventory,
    quest_item_requirements,
)
from src.antibot_cv.automation.quest_objective_runtime import (
    MonsterTarget,
    ObjectiveKind,
    QuestObjective,
)


def objective(text: str) -> QuestObjective:
    return QuestObjective(
        ObjectiveKind.MONSTER_HUNT,
        "280",
        "Фамильная ступка",
        text,
        "fingerprint-280",
        MonsterTarget("Свирепый кентавр [5]", "Свирепый кентавр", 5),
        "Свирепых кентавров",
        None,
        None,
        False,
        0,
    )


def snapshot(name: str, count: int) -> dict[str, object]:
    return {
        "ok": True,
        "category": "quest",
        "categoryConfirmed": True,
        "truncated": False,
        "sample": [{"artAltTitle": name, "count": count}],
    }


def test_reads_complete_items_field_beyond_legacy_sample_limit() -> None:
    current = objective(
        "Убивая Свирепых кентавров найдите ступку, после чего отнесите её травнице."
    )
    items = [
        {
            "artAltTitle": f"Посторонний предмет {index}",
            "artAltDescription": "",
            "count": 1,
        }
        for index in range(88)
    ]
    items.append(
        {
            "artAltTitle": "Фамильная ступка",
            "artAltDescription": "Используется в квесте «Фамильная ступка»",
            "count": 1,
        }
    )

    result = evaluate_quest_inventory(
        current,
        {
            "ok": True,
            "category": "quest",
            "categoryConfirmed": True,
            "truncated": False,
            "items": items,
            "sample": items[:80],
        },
    )

    assert result.confirmed is True
    assert result.complete is True
    assert result.collected == (("Фамильная ступка", 1),)


def test_extracts_singular_and_counted_item_requirements() -> None:
    singular = objective(
        "Убивая Свирепых кентавров, добудьте Пояс Кентавра-ветерана "
        "и возвращайтесь к разбойнику Аскорду."
    )
    counted = objective(
        "Убивая Кабанов-секачей, получите 10 Гурум-корней "
        "и возвращайтесь к служителю."
    )

    assert [(item.name, item.required) for item in quest_item_requirements(singular)] == [
        ("Пояс Кентавра-ветерана", 1)
    ]
    assert [(item.name, item.required) for item in quest_item_requirements(counted)] == [
        ("Гурум-корней", 10)
    ]


def test_inventory_completion_resolves_real_qualified_trophy_title() -> None:
    current = objective(
        "Убивая Кабанов-секачей, получите 10 бивней "
        "и возвращайтесь к разбойнику Аскорду."
    )
    result = evaluate_quest_inventory(
        current,
        snapshot("Бивень кабана-секача", 10),
    )
    assert result.confirmed is True
    assert result.complete is True
    assert result.collected == (("Бивень кабана-секача", 10),)


def test_inventory_completion_requires_confirmed_quest_category_and_quantity() -> None:
    current = objective(
        "Убивая Свирепых кентавров, добудьте Пояс Кентавра-ветерана "
        "и возвращайтесь к разбойнику Аскорду."
    )
    complete = evaluate_quest_inventory(
        current, snapshot("Пояс Кентавра-ветерана", 1)
    )
    missing = evaluate_quest_inventory(
        current, snapshot("Другой предмет", 1)
    )
    unconfirmed_snapshot = snapshot("Пояс Кентавра-ветерана", 1)
    unconfirmed_snapshot["categoryConfirmed"] = False
    unconfirmed = evaluate_quest_inventory(current, unconfirmed_snapshot)

    assert complete.confirmed is True and complete.complete is True
    assert missing.confirmed is True and missing.complete is False
    assert unconfirmed.confirmed is False and unconfirmed.complete is False


def test_real_q280_wording_binds_familial_mortar_through_quest_description() -> None:
    current = objective(
        "Убивая Свирепых кентавров найдите ступку, после чего отнесите её травнице."
    )
    result = evaluate_quest_inventory(
        current,
        {
            "ok": True,
            "category": "quest",
            "categoryConfirmed": True,
            "truncated": False,
            "sample": [
                {
                    "artAltTitle": "Фамильная ступка",
                    "artAltDescription": "Используется в квесте «Фамильная ступка»",
                    "count": 1,
                }
            ],
        },
    )

    assert result.confirmed is True
    assert result.complete is True
    assert [(item.name, item.required) for item in result.requirements] == [
        ("Фамильная ступка", 1)
    ]
