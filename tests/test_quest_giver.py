from __future__ import annotations

import pytest

from src.antibot_cv.automation.quest_giver import resolve_unique_giver


def npc(npc_id: str, name: str) -> dict[str, object]:
    return {"dataId": npc_id, "name": name, "actionable": True}


def test_resolves_catalogue_case_form_to_exact_area_npc() -> None:
    assert resolve_unique_giver("богатыря Тура", [npc("7", "Богатырь Тур")])["dataId"] == "7"
    assert resolve_unique_giver("Хранителя леса Франка", [npc("9", "Хранитель леса Франк")])["dataId"] == "9"


def test_resolves_unique_location_proxy_by_proper_name_stem() -> None:
    items = [npc("7", "Заводчик Брокий"), npc("3", "Дом Франка")]

    assert resolve_unique_giver("Хранителя леса Франка", items)["dataId"] == "3"


def test_rejects_non_unique_morphological_match() -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_unique_giver(
            "богатыря Тура",
            [npc("7", "Богатырь Тур"), npc("8", "Богатырь Тур")],
        )


def test_rejects_ambiguous_location_proxy() -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_unique_giver(
            "Хранителя леса Франка",
            [npc("3", "Дом Франка"), npc("4", "Лавка Франка")],
        )
