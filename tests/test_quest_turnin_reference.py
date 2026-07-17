from __future__ import annotations

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_turnin_reference import derive_turn_in_ref


def _entry(objective: str, navigation: list[dict[str, object]]) -> ActiveQuestEntry:
    return ActiveQuestEntry("198", "Яд для сильнейших", {
        "objective": objective,
        "navigation": navigation,
    })


def test_derives_turn_in_from_body_and_unique_non_monster_route() -> None:
    entry = _entry(
        "Убивая Шершней-мстителей, получите 5 жал и возвращайтесь "
        "к воеводе Асенарду на Прокалённое плато.",
        [
            {"text": "Шершней", "target": "Шершень-мститель [3]"},
            {"text": "Прокалённое плато", "target": "Прокалённое плато"},
        ],
    )

    ref = derive_turn_in_ref(entry)

    assert ref is not None
    assert ref.location == "Прокалённое плато"
    assert ref.giver_names == ("воеводе Асенарду",)


def test_derives_location_first_return_wording() -> None:
    entry = _entry(
        "Получите 3 клыка и возращайтесь в Лагерь новобранцев к десятнику Бертроду.",
        [{"target": "Лагерь новобранцев"}],
    )

    ref = derive_turn_in_ref(entry)

    assert ref is not None
    assert ref.giver_names == ("десятнику Бертроду",)


def test_derives_deliver_to_npc_turn_in_wording() -> None:
    entry = _entry(
        "Убивая Свирепых кентавров найдите ступку, "
        "после чего отнесите её ведунье Ильмет в Курганы Бренности.",
        [
            {"text": "Свирепый кентавр", "target": "Свирепый кентавр [6]"},
            {"text": "Курганы Бренности", "target": "Курганы Бренности"},
        ],
    )

    ref = derive_turn_in_ref(entry)

    assert ref is not None
    assert ref.location == "Курганы Бренности"
    assert ref.giver_names == ("ведунье Ильмет",)


def test_rejects_missing_or_ambiguous_non_monster_route() -> None:
    objective = "Получите 5 жал и возвращайтесь к воеводе Асенарду на Прокалённое плато."
    assert derive_turn_in_ref(_entry(objective, [])) is None
    assert derive_turn_in_ref(_entry(objective, [
        {"target": "Прокалённое плато"},
        {"target": "Город Арса"},
    ])) is None


def test_reuses_strict_dialogue_shape_for_confirmed_turn_in_travel_wording() -> None:
    ref = derive_turn_in_ref(ActiveQuestEntry("280", "Фамильная ступка", {
        "id": "280",
        "title": "Фамильная ступка",
        "status": "active",
        "objective": "Отправляйтесь к разбойнику Аскорду в Земли Пращуров.",
        "navigation": ({"text": "Земли Пращуров", "target": "Земли Пращуров"},),
        "progress": None,
    }))

    assert ref is not None
    assert ref.location == "Земли Пращуров"
    assert ref.giver_names == ("разбойнику Аскорду",)
