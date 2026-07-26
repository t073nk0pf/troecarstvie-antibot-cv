import json

import pytest

from src.antibot_cv.automation.npc_census_model import AreaNpcEndpointObservation
from src.antibot_cv.automation.npc_census_runner import (
    CensusCursor, CensusEndpoint, CensusIntentKind, area_observed, dialogue_observed,
    endpoint_opened, next_census_intent,
)
from src.antibot_cv.automation.npc_census_store import NpcCensusStore


def area(snapshot: str = "area-1") -> AreaNpcEndpointObservation:
    return AreaNpcEndpointObservation(
        actor_key="actor", document_revision=1, location_id="127",
        location_name="Пристанище трёх ветров", endpoint_id="0",
        endpoint_name="Дом Василисы", snapshot_id=snapshot,
        generated_at="2026-07-20T13:00:00Z",
        route_ref="398",
    )


def test_store_round_trip_deduplicates_and_preserves_zero_endpoint(tmp_path) -> None:
    path = tmp_path / "census.json"
    store = NpcCensusStore(path, max_observations=2)
    assert store.append_area(area()) is True
    assert store.append_area(area()) is False
    store.save()

    restored = NpcCensusStore(path, max_observations=2)
    restored.load()
    assert restored.areas == (area(),)
    assert json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == 2


def test_store_migrates_schema_one_area_without_route_authority(tmp_path) -> None:
    path = tmp_path / "census.json"
    raw = {
        "actor_key": "actor", "document_revision": 1, "location_id": "127",
        "location_name": "Пристанище трёх ветров", "endpoint_id": "0",
        "endpoint_name": "Дом Василисы", "snapshot_id": "area-legacy",
        "generated_at": "2026-07-20T13:00:00Z",
    }
    path.write_text(json.dumps({"schemaVersion": 1, "areas": [raw], "dialogues": []}), encoding="utf-8")
    store = NpcCensusStore(path)
    store.load()
    assert store.areas[0].route_ref is None


def test_store_accepts_multiple_endpoints_from_one_area_snapshot(tmp_path) -> None:
    path = tmp_path / "census.json"
    store = NpcCensusStore(path)
    first = area()
    second = AreaNpcEndpointObservation(
        actor_key=first.actor_key, document_revision=first.document_revision,
        location_id=first.location_id, location_name=first.location_name,
        endpoint_id="3", endpoint_name="Дом Торвара",
        snapshot_id=first.snapshot_id, generated_at=first.generated_at,
    )

    assert store.append_area(first) is True
    assert store.append_area(second) is True
    store.save()
    restored = NpcCensusStore(path)
    restored.load()
    assert restored.areas == (first, second)


def test_store_rejects_two_endpoints_with_same_physical_route(tmp_path) -> None:
    store = NpcCensusStore(tmp_path / "census.json")
    first = area()
    second = AreaNpcEndpointObservation(
        actor_key=first.actor_key, document_revision=first.document_revision,
        location_id=first.location_id, location_name=first.location_name,
        endpoint_id="3", endpoint_name="Дом Торвара", route_ref=first.route_ref,
        snapshot_id=first.snapshot_id, generated_at=first.generated_at,
    )
    store.append_area(first)
    with pytest.raises(ValueError, match="route identity conflicts"):
        store.append_area(second)


def test_store_rejects_conflicting_area_snapshot_envelope(tmp_path) -> None:
    store = NpcCensusStore(tmp_path / "census.json")
    first = area()
    store.append_area(first)
    conflicting = AreaNpcEndpointObservation(
        actor_key=first.actor_key, document_revision=2,
        location_id="999", location_name="Чужая локация", endpoint_id="3",
        endpoint_name="Чужой NPC", snapshot_id=first.snapshot_id,
        generated_at="2026-07-20T13:01:00Z",
    )
    with pytest.raises(ValueError, match="envelope conflicts"):
        store.append_area(conflicting)


def test_store_load_rejects_conflicting_area_snapshot_envelope(tmp_path) -> None:
    path = tmp_path / "census.json"
    first = {
        "actor_key": "actor", "document_revision": 1, "location_id": "127",
        "location_name": "Пристанище трёх ветров", "endpoint_id": "0",
        "endpoint_name": "Дом Василисы", "snapshot_id": "area",
        "generated_at": "2026-07-20T13:00:00Z",
    }
    second = dict(
        first, document_revision=2, location_id="999", location_name="Чужая локация",
        endpoint_id="3", endpoint_name="Чужой NPC", generated_at="2026-07-20T13:01:00Z",
    )
    path.write_text(json.dumps({"schemaVersion": 1, "areas": [first, second], "dialogues": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="envelope conflicts"):
        NpcCensusStore(path).load()


def test_store_hard_cap_is_fail_closed(tmp_path) -> None:
    store = NpcCensusStore(tmp_path / "census.json", max_observations=1)
    store.append_area(area())
    try:
        store.append_area(area("area-2"))
    except ValueError as exc:
        assert "hard cap" in str(exc)
    else:
        raise AssertionError("hard cap did not fail closed")


@pytest.mark.parametrize("limit", [True, 1.5, "2", None])
def test_store_limit_is_strict_positive_integer(tmp_path, limit) -> None:
    with pytest.raises(ValueError, match="max_observations"):
        NpcCensusStore(tmp_path / "census.json", max_observations=limit)


def test_store_rejects_limit_above_absolute_cap(tmp_path) -> None:
    with pytest.raises(ValueError, match="max_observations"):
        NpcCensusStore(tmp_path / "census.json", max_observations=5001)


def test_store_rejects_oversized_file_before_json_decode(tmp_path) -> None:
    path = tmp_path / "census.json"
    path.write_bytes(b"x" * 101)
    with pytest.raises(ValueError, match="file exceeds hard cap"):
        NpcCensusStore(path, max_file_bytes=100).load()


def test_store_load_enforces_per_observation_byte_cap(tmp_path) -> None:
    path = tmp_path / "census.json"
    writer = NpcCensusStore(path)
    writer.append_area(area())
    writer.save()
    with pytest.raises(ValueError, match="observation exceeds hard cap"):
        NpcCensusStore(path, max_observation_bytes=10).load()


def test_model_rejects_oversized_single_observation() -> None:
    with pytest.raises(ValueError, match="endpoint_name"):
        AreaNpcEndpointObservation(
            actor_key="actor", document_revision=1, location_id="127",
            location_name="Пристанище трёх ветров", endpoint_id="0",
            endpoint_name="x" * 181, snapshot_id="area",
            generated_at="2026-07-20T13:00:00Z",
        )


def test_model_and_runner_reject_control_characters() -> None:
    with pytest.raises(ValueError, match="resulting_name"):
        from src.antibot_cv.automation.npc_census_model import NpcDialogueObservation
        NpcDialogueObservation(
            actor_key="actor", document_revision=1, causal_area_snapshot_id="area",
            location_id="127", endpoint_id="0", resulting_name="Крестьянка\nВасилиса",
            npc_instance_id=None, snapshot_id="dialog", generated_at="2026-07-20T13:00:00Z",
        )
    with pytest.raises(ValueError, match="endpoint name"):
        CensusEndpoint("127", "0", "Дом\x00Василисы", "398")


def test_store_requires_existing_parent_directory(tmp_path) -> None:
    store = NpcCensusStore(tmp_path / "missing" / "census.json")
    store.append_area(area())
    with pytest.raises(ValueError, match="parent directory"):
        store.save()


def test_store_rejects_conflicting_duplicate_snapshot(tmp_path) -> None:
    store = NpcCensusStore(tmp_path / "census.json")
    store.append_area(area())
    conflicting = AreaNpcEndpointObservation(
        actor_key="actor", document_revision=1, location_id="127",
        location_name="Пристанище трёх ветров", endpoint_id="0",
        endpoint_name="Дом Торвара", snapshot_id="area-1",
        generated_at="2026-07-20T13:00:00Z",
    )
    with pytest.raises(ValueError, match="conflicts"):
        store.append_area(conflicting)


def test_store_rejects_numeric_persisted_quest_id(tmp_path) -> None:
    path = tmp_path / "census.json"
    path.write_text(json.dumps({
        "schemaVersion": 1, "areas": [], "dialogues": [{
            "actor_key": "actor", "document_revision": 1,
            "causal_area_snapshot_id": "area", "location_id": "127",
            "endpoint_id": "0", "resulting_name": "Крестьянка Василиса",
            "npc_instance_id": None, "snapshot_id": "dialog",
            "generated_at": "2026-07-20T13:00:00Z",
            "quest_roles": [{"quest_id": 267, "role": "giver"}],
        }],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="quest_id"):
        NpcCensusStore(path).load()


def test_store_load_rejects_duplicate_snapshot_identity(tmp_path) -> None:
    path = tmp_path / "census.json"
    first = {
        "actor_key": "actor", "document_revision": 1, "location_id": "127",
        "location_name": "Пристанище трёх ветров", "endpoint_id": "0",
        "endpoint_name": "Дом Василисы", "snapshot_id": "same",
        "generated_at": "2026-07-20T13:00:00Z",
    }
    second = dict(first, endpoint_name="Другой дом")
    path.write_text(json.dumps({"schemaVersion": 1, "areas": [first, second], "dialogues": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate snapshot"):
        NpcCensusStore(path).load()


def test_store_rejects_raw_count_before_decoding(tmp_path, monkeypatch) -> None:
    path = tmp_path / "census.json"
    path.write_text(json.dumps({"schemaVersion": 1, "areas": [{}, {}], "dialogues": []}), encoding="utf-8")
    import src.antibot_cv.automation.npc_census_store as module
    monkeypatch.setattr(module, "_decode_area", lambda raw: (_ for _ in ()).throw(AssertionError("decoded")))
    with pytest.raises(ValueError, match="exceeds hard cap"):
        NpcCensusStore(path, max_observations=1).load()


@pytest.mark.parametrize("version", [True, 1.0, "1", None])
def test_store_schema_version_requires_exact_integer(tmp_path, version) -> None:
    path = tmp_path / "census.json"
    path.write_text(json.dumps({"schemaVersion": version, "areas": [], "dialogues": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        NpcCensusStore(path).load()


def test_runner_is_bounded_and_resumable_one_intent_at_a_time() -> None:
    cursor = CensusCursor("127")
    assert next_census_intent(cursor).kind is CensusIntentKind.OBSERVE_AREA
    cursor = area_observed(
        cursor,
        (
            CensusEndpoint("127", "3", "Дом Торвара", "403"),
            CensusEndpoint("127", "0", "Дом Василисы", "398"),
        ),
    )
    assert next_census_intent(cursor).endpoint.endpoint_id == "0"
    cursor = endpoint_opened(cursor)
    assert next_census_intent(cursor).kind is CensusIntentKind.OBSERVE_DIALOGUE
    cursor = dialogue_observed(cursor)
    assert next_census_intent(cursor).endpoint.endpoint_id == "3"
    cursor = dialogue_observed(endpoint_opened(cursor))
    assert next_census_intent(cursor).kind is CensusIntentKind.COMPLETE


def test_runner_rejects_duplicate_physical_route() -> None:
    endpoints = (
        CensusEndpoint("127", "0", "Дом Василисы", "398"),
        CensusEndpoint("127", "3", "Дом Торвара", "398"),
    )
    with pytest.raises(ValueError, match="routes are ambiguous"):
        area_observed(CensusCursor("127"), endpoints)


def test_runner_rejects_oversized_or_inconsistent_state() -> None:
    endpoints = tuple(CensusEndpoint("127", str(index), f"NPC {index}", str(398 + index)) for index in range(3))
    with pytest.raises(ValueError, match="hard cap"):
        area_observed(CensusCursor("127"), endpoints, max_endpoints=2)
    with pytest.raises(ValueError, match="phase is inconsistent"):
        CensusCursor("127", (CensusEndpoint("127", "0", "Дом", "398"),), 0, CensusIntentKind.COMPLETE)
    with pytest.raises(ValueError, match="endpoint identity"):
        CensusEndpoint("127", "bad", "Дом", "398")
    for endpoint_id in ("00", "01"):
        with pytest.raises(ValueError, match="endpoint identity"):
            CensusEndpoint("127", endpoint_id, "Дом", "398")
    with pytest.raises(ValueError, match="location mismatches"):
        CensusCursor(
            "127", (CensusEndpoint("999", "0", "Чужой", "398"),), 0,
            CensusIntentKind.OPEN_ENDPOINT,
        )
    with pytest.raises(ValueError, match="hard cap"):
        CensusCursor(
            "127",
            tuple(CensusEndpoint("127", str(index), f"NPC {index}", str(398 + index)) for index in range(257)),
            0,
            CensusIntentKind.OPEN_ENDPOINT,
        )
