from __future__ import annotations

import json

from src.antibot_cv.automation.quest_npc_directory import (
    NpcResolutionStatus,
    QuestNpcDirectory,
)


def test_generated_directory_resolves_inflected_npc_in_parent_location() -> None:
    directory = QuestNpcDirectory.from_files("docs/3kingdoms/NPC_CATALOG.json")

    askord = directory.resolve("разбойнику Аскорду", location_name="Земли Пращуров")
    vilena = directory.resolve("Вилене", location_id="128")

    assert askord.status is NpcResolutionStatus.RESOLVED
    assert askord.entry is not None and askord.entry.canonical_name == "Разбойник Аскорд"
    assert askord.entry.location_id == "122"
    assert vilena.status is NpcResolutionStatus.RESOLVED
    assert vilena.entry is not None and vilena.entry.canonical_name == "Колдунья Вилена"


def test_runtime_registry_enriches_unique_proxy_without_guessing_ids(tmp_path) -> None:
    registry = tmp_path / "world.json"
    registry.write_text(json.dumps({
        "schemaVersion": 1,
        "npcs": {
            "122:0": {
                "locationId": "122", "areaObjectId": "0",
                "name": "Дом Аскорда", "npcInstanceId": "81",
            }
        },
    }), encoding="utf-8")

    directory = QuestNpcDirectory.from_files(
        "docs/3kingdoms/NPC_CATALOG.json", registry,
    )
    result = directory.resolve("Аскорду", location_id="122")

    assert result.entry is not None
    assert result.entry.proxy_names == ("Дом Аскорда",)
    assert result.entry.area_object_id == "0"
    assert result.entry.npc_instance_id == "81"


def test_directory_does_not_resolve_same_name_without_location() -> None:
    directory = QuestNpcDirectory.from_files("docs/3kingdoms/NPC_CATALOG.json")
    result = directory.resolve("Идол Перуна")
    assert result.status is NpcResolutionStatus.AMBIGUOUS


def test_location_bounded_adjacent_transposition_resolves_canonical_giver() -> None:
    directory = QuestNpcDirectory.from_files("docs/3kingdoms/NPC_CATALOG.json")

    result = directory.resolve(
        "десятнику Бертроду",
        location_name="Лагерь новобранцев",
    )

    assert result.status is NpcResolutionStatus.RESOLVED
    assert result.reason == "location_bounded_adjacent_transposition"
    assert result.entry is not None
    assert result.entry.canonical_name == "Десятник Берторд"
    assert result.entry.location_id == "276"


def test_adjacent_transposition_never_resolves_without_location_binding() -> None:
    directory = QuestNpcDirectory.from_files("docs/3kingdoms/NPC_CATALOG.json")

    result = directory.resolve("десятнику Бертроду")

    assert result.status is NpcResolutionStatus.NOT_FOUND
