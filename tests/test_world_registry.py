from __future__ import annotations

import json

from src.antibot_cv.automation.world_registry import WorldRegistry


def test_world_registry_accumulates_structured_identities(tmp_path) -> None:
    path = tmp_path / "world.json"
    registry = WorldRegistry(path)
    assert registry.observe_route(
        {
            "location": {"id": "102", "semanticName": "Городская площадь Арсы"},
            "nextTransition": {"locId": "171", "name": "Пригород Арсы"},
        }
    )
    assert registry.observe_area_npcs(
        {
            "location": {"id": "201", "name": "Лес призраков"},
            "items": [{"dataId": "7", "name": "Заводчик Брокий"}],
        }
    )
    assert registry.observe_npc_dialog(
        {"npcId": "7", "questActions": [{"npcInstanceId": "114"}], "dialogActions": []}
    )
    assert registry.observe_instances(
        {
            "location": {"id": "123", "name": "Заброшенные копи"},
            "items": [{"id": "3", "name": "Огненный провал", "href": "/instance.php?action=enter&ref=922"}],
        }
    )
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["locations"]["171"]["name"] == "Пригород Арсы"
    assert saved["edges"]["102"]["171"]["to"] == "171"
    assert saved["npcs"]["201:7"]["npcInstanceId"] == "114"
    assert saved["instances"]["123:3"]["name"] == "Огненный провал"


def test_world_registry_finds_saved_directed_path(tmp_path) -> None:
    registry = WorldRegistry(tmp_path / "world.json")
    registry.observe_route({"currentLocationId": "1", "nextTransition": {"locId": "2", "name": "two"}})
    registry.observe_route({"currentLocationId": "2", "nextTransition": {"locId": "3", "name": "three"}})
    assert registry.shortest_path("1", "3") == ("2", "3")
    assert registry.shortest_path("3", "1") is None
