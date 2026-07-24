from types import SimpleNamespace

from src.antibot_cv.automation.quest_use_item_plan import parse_quest_use_item_plan


def test_parse_exact_use_item_then_turn_in_clause() -> None:
    plan = parse_quest_use_item_plan(SimpleNamespace(
        id="108", title="Печаль Героя",
        objective="Используя точильный камень, наточите топор Рокоша. Наточив топор, верните точильный камень ремесленнику Сулемайту на Прокалённое плато.",
    ))
    assert plan is not None
    assert plan.item_name == "точильный камень"
    assert "наточите топор Рокоша" in plan.expected_result
    assert plan.result_item_name == "Наточенный топор Рокоша"


def test_reject_unbound_use_item_text() -> None:
    assert parse_quest_use_item_plan(SimpleNamespace(
        id="108", title="x", objective="Используйте какой-нибудь предмет.",
    )) is None
