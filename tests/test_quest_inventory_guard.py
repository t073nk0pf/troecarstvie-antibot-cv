from src.antibot_cv.automation.quest_inventory_guard import (
    evaluate_quest_inventory,
    quest_item_requirements,
)
from src.antibot_cv.automation.quest_objective_runtime import (
    MonsterTarget,
    ObjectiveKind,
    QuestObjective,
)


def objective(
    text: str,
    *,
    quest_id: str = "280",
    quest_title: str = "Фамильная ступка",
    monster_name: str = "Свирепый кентавр",
) -> QuestObjective:
    return QuestObjective(
        ObjectiveKind.MONSTER_HUNT,
        quest_id,
        quest_title,
        text,
        "fingerprint-280",
        MonsterTarget(f"{monster_name} [5]", monster_name, 5),
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


def test_real_q271_typo_extracts_only_acquisition_clause() -> None:
    current = objective(
        "Убивая Ядовитых Випер, получите 3 Клыка и возращайтесь "
        "в Лагерь новобранцев к десятнику Бертроду.",
        quest_id="271",
        quest_title="Ядовитые Виперы",
        monster_name="Ядовитая Випера",
    )

    assert [(item.name, item.required) for item in quest_item_requirements(current)] == [
        ("Клыка", 3)
    ]


def test_real_q271_inventory_quantity_drives_complete_and_missing() -> None:
    current = objective(
        "Убивая Ядовитых Випер, получите 3 Клыка и возращайтесь "
        "в Лагерь новобранцев к десятнику Бертроду.",
        quest_id="271",
        quest_title="Ядовитые Виперы",
        monster_name="Ядовитая Випера",
    )

    complete = evaluate_quest_inventory(current, snapshot("Клык Ядовитой Виперы", 3))
    missing = evaluate_quest_inventory(current, snapshot("Клык Ядовитой Виперы", 2))

    assert complete.confirmed is True
    assert complete.complete is True
    assert complete.requirements[0].name == "Клык Ядовитой Виперы"
    assert complete.collected == (("Клык Ядовитой Виперы", 3),)
    assert missing.confirmed is True
    assert missing.complete is False
    assert missing.reason == "quest_items_missing"


def test_captured_navigation_clause_fails_closed_instead_of_missing() -> None:
    current = objective(
        "Получите 3 Клыка а затем возращайтесь в Лагерь новобранцев.",
        monster_name="Ядовитая Випера",
    )

    result = evaluate_quest_inventory(current, snapshot("Клык Ядовитой Виперы", 2))

    assert result.confirmed is False
    assert result.complete is False
    assert result.reason == "quest_item_requirement_contains_navigation_clause"


def test_captured_return_address_without_known_verb_fails_closed() -> None:
    current = objective(
        "Получите 3 Клыка и в Лагерь новобранцев к десятнику Бертроду.",
        monster_name="Ядовитая Випера",
    )

    result = evaluate_quest_inventory(current, snapshot("Клык Ядовитой Виперы", 2))

    assert result.confirmed is False
    assert result.reason == "quest_item_requirement_contains_navigation_clause"


def test_unknown_navigation_imperative_does_not_become_item_name() -> None:
    current = objective(
        "Получите 3 Клыка и двигайтесь в Лагерь новобранцев.",
        monster_name="Ядовитая Випера",
    )

    result = evaluate_quest_inventory(current, snapshot("Клык Ядовитой Виперы", 2))

    assert result.confirmed is False
    assert result.reason == "quest_item_requirement_contains_navigation_clause"


def test_unknown_navigation_imperative_without_connector_fails_closed() -> None:
    for separator in (" ", " — "):
        current = objective(
            f"Получите 3 Клыка{separator}двигайтесь в Лагерь новобранцев.",
            monster_name="Ядовитая Випера",
        )

        result = evaluate_quest_inventory(
            current, snapshot("Клык Ядовитой Виперы", 2)
        )

        assert result.confirmed is False
        assert result.reason == "quest_item_requirement_contains_navigation_clause"


def test_preserves_repeated_verb_multi_item_clauses() -> None:
    current = objective(
        "Получите 3 Клыка, соберите 4 Когтя и возвращайтесь к охотнику."
    )

    assert [(item.name, item.required) for item in quest_item_requirements(current)] == [
        ("Клыка", 3),
        ("Когтя", 4),
    ]


def test_splits_counted_multi_item_shorthand_without_contamination() -> None:
    current = objective(
        "Получите 3 Клыка и 4 Когтя, затем возвращайтесь к охотнику."
    )

    assert [(item.name, item.required) for item in quest_item_requirements(current)] == [
        ("Клыка", 3),
        ("Когтя", 4),
    ]


def test_splits_comma_counted_shorthand_without_false_complete() -> None:
    current = objective(
        "Получите 3 Клыка, 4 Когтя и возвращайтесь к охотнику."
    )

    assert [(item.name, item.required) for item in quest_item_requirements(current)] == [
        ("Клыка", 3),
        ("Когтя", 4),
    ]
    result = evaluate_quest_inventory(current, snapshot("Клык", 3))
    assert result.confirmed is True
    assert result.complete is False
    assert result.reason == "quest_items_missing"


def test_malformed_comma_counted_tail_cannot_false_complete() -> None:
    for text in (
        "Получите 3 Клыка, 4.",
        "Получите 3 Клыка, 4",
        "Получите 3 Клыка, 0 Когтей и возвращайтесь к охотнику.",
    ):
        result = evaluate_quest_inventory(objective(text), snapshot("Клык", 3))

        assert result.confirmed is False
        assert result.complete is False
        assert result.reason == "quest_item_requirement_contains_navigation_clause"


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


def test_inventory_completion_resolves_short_inflected_trophy_to_lynx_ear() -> None:
    current = objective(
        "Убивая Молодых рысей, получите 3 уха и возвращайтесь к охотнику."
    )
    result = evaluate_quest_inventory(current, snapshot("Ухо рыси", 3))

    assert result.confirmed is True
    assert result.complete is True
    assert result.requirements[0].name == "Ухо рыси"
    assert result.collected == (("Ухо рыси", 3),)


def test_short_inflected_trophy_does_not_guess_between_two_sources() -> None:
    current = objective(
        "Убивая Молодых рысей, получите 3 уха и возвращайтесь к охотнику."
    )
    result = evaluate_quest_inventory(
        current,
        {
            "ok": True,
            "category": "quest",
            "categoryConfirmed": True,
            "truncated": False,
            "items": [
                {"artAltTitle": "Ухо рыси", "count": 3},
                {"artAltTitle": "Ухо волка", "count": 3},
            ],
        },
    )

    assert result.confirmed is True
    assert result.complete is False


def test_real_q304_counted_blood_is_not_replaced_by_description_bound_moss() -> None:
    current = objective(
        "Убивая Непобедимых кабанов, получите 5 пузырьков крови, также найдите "
        "в Пристанище трёх ветров Светящийся мох, в Длани Рода Пятнистый гриб и 5 свежих "
        "листьев кустарника на Просторах безмолвия. Собрав необходимое, возвращайтесь к колдунье Вилене.",
        quest_id="304",
        quest_title="Цветочная болезнь",
        monster_name="Непобедимый кабан",
    )
    result = evaluate_quest_inventory(
        current,
        {
            "ok": True,
            "category": "quest",
            "categoryConfirmed": True,
            "truncated": False,
            "items": [
                {"artAltTitle": "Кровь Непобедимого кабана", "count": 5},
                {
                    "artAltTitle": "Светящийся мох",
                    "artAltDescription": "Используется в квесте «Цветочная болезнь»",
                    "count": 1,
                },
            ],
        },
    )

    assert result.complete is True
    assert [(item.name, item.required) for item in result.requirements] == [
        ("Кровь Непобедимого кабана", 5)
    ]


def test_q304_does_not_bind_single_blood_trophy_from_wrong_monster() -> None:
    current = objective(
        "Убивая Непобедимых кабанов, получите 5 пузырьков крови и возвращайтесь к колдунье.",
        quest_id="304",
        quest_title="Цветочная болезнь",
        monster_name="Непобедимый кабан",
    )
    result = evaluate_quest_inventory(
        current, snapshot("Кровь Свирепого кабана", 5)
    )

    assert result.confirmed is True
    assert result.complete is False
    assert [(item.name, item.required) for item in result.requirements] == [
        ("пузырьков крови", 5)
    ]


def test_q304_does_not_bind_blood_trophy_missing_monster_qualifier() -> None:
    current = objective(
        "Убивая Непобедимых кабанов, получите 5 пузырьков крови и возвращайтесь к колдунье.",
        quest_id="304",
        quest_title="Цветочная болезнь",
        monster_name="Непобедимый кабан",
    )

    result = evaluate_quest_inventory(current, snapshot("Кровь кабана", 5))

    assert result.complete is False


def test_q304_does_not_take_blood_qualifier_from_area_object_wording() -> None:
    current = objective(
        "Убивая Непобедимых кабанов, получите 5 пузырьков крови, также найдите "
        "Светящийся мох и 5 свежих листьев кустарника. Собрав необходимое, возвращайтесь к колдунье.",
        quest_id="304",
        quest_title="Цветочная болезнь",
        monster_name="Непобедимый кабан",
    )

    result = evaluate_quest_inventory(
        current, snapshot("Кровь Свежего кабана", 5)
    )

    assert result.complete is False


def test_generic_blood_requirement_does_not_guess_between_qualified_trophies() -> None:
    current = objective(
        "Убивая кабанов, получите 5 пузырьков крови и возвращайтесь к колдунье."
    )
    result = evaluate_quest_inventory(
        current,
        {
            "ok": True,
            "category": "quest",
            "categoryConfirmed": True,
            "truncated": False,
            "items": [
                {"artAltTitle": "Кровь Непобедимого кабана", "count": 5},
                {"artAltTitle": "Кровь Свирепого кабана", "count": 5},
            ],
        },
    )

    assert result.complete is False
    assert [(item.name, item.required) for item in result.requirements] == [
        ("пузырьков крови", 5)
    ]


def test_inventory_binding_covers_common_case_and_number_forms() -> None:
    cases = (
        ("получите 3 уха", "Ухо рыси", 3),
        ("получите 5 рогов", "Рог кентавра", 5),
        ("получите 4 когтей", "Коготь рыси", 4),
        ("получите 2 щетины", "Щетина кабана-секача", 2),
    )
    for wording, item_name, count in cases:
        current = objective(f"Убивая монстров, {wording} и возвращайтесь к охотнику.")
        result = evaluate_quest_inventory(current, snapshot(item_name, count))
        assert result.complete is True, (wording, item_name, result)


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
