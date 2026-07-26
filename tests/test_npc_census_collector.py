from __future__ import annotations

from pathlib import Path

import pytest

from src.antibot_cv.automation.npc_census_collector import (
    CensusCollectionStatus,
    NpcCensusCollector,
)
from src.antibot_cv.automation.npc_census_store import NpcCensusStore
from src.antibot_cv.automation.npc_census_model import (
    AreaNpcEndpointObservation, NpcDialogueObservation,
)


def area(revision: int) -> dict[str, object]:
    suffix = format(revision, "x")
    return {
        "ok": True, "message": "area_npc_snapshot", "pageKind": "area",
        "snapshotId": f"area-npcs-epoch-{suffix}",
        "observationEpoch": "epoch", "observationRevision": revision,
        "generatedAt": f"2026-07-20T19:03:{revision:02d}.000Z",
        "location": {"id": "102", "name": "Городская площадь Арсы"},
        "items": [
            {"name": "Торговец Богдан", "dataId": "0", "routeRef": "398", "actionable": True},
            {"name": "Богатырь Тур", "dataId": "13", "routeRef": "228", "actionable": True},
        ], "truncated": False,
    }


def dialogue(endpoint: str, revision: int) -> dict[str, object]:
    name, instance = ("Торговец Богдан", "12") if endpoint == "0" else ("Богатырь Тур", "20")
    suffix = format(revision, "x")
    return {
        "ok": True, "message": "npc_dialog_snapshot", "pageKind": "npc",
        "snapshotId": f"npc-dialog-epoch-{suffix}",
        "observationEpoch": "epoch", "observationRevision": revision,
        "generatedAt": f"2026-07-20T19:03:{revision:02d}.500Z",
        "identityMatches": True, "npcId": endpoint, "npcInstanceId": instance,
        "resultingName": name, "matchingHeaders": [name], "truncated": False,
        "questActions": [], "dialogActions": [], "doneActions": [], "acceptActions": [],
    }


class Transport:
    def __init__(self) -> None:
        self.revision = 1
        self.inspections: list[str] = []
        self.returns = 0

    def area_snapshot(self):
        value = area(self.revision)
        self.revision += 2
        return value

    def inspect_endpoint(self, endpoint):
        self.inspections.append(endpoint.endpoint_id)
        return dialogue(endpoint.endpoint_id, endpoint.document_revision + 1)

    def open_area(self):
        self.returns += 1
        return True


def test_collects_each_endpoint_once_and_persists_restart_progress(tmp_path: Path) -> None:
    path = tmp_path / "census.json"
    first_transport = Transport()
    store = NpcCensusStore(path)
    first = NpcCensusCollector(
        actor_key="profile-tab-session", transport=first_transport, store=store,
    ).collect_current_location()
    assert first.status is CensusCollectionStatus.COMPLETE
    assert first_transport.inspections == ["0", "13"]
    assert first_transport.returns == 2

    restored = NpcCensusStore(path)
    restored.load()
    second_transport = Transport()
    second = NpcCensusCollector(
        actor_key="profile-tab-session", transport=second_transport, store=restored,
    ).collect_current_location()
    assert second.status is CensusCollectionStatus.COMPLETE
    assert second_transport.inspections == []
    assert len(restored.dialogues) == 2

    foreign_transport = Transport()
    foreign = NpcCensusCollector(
        actor_key="other-profile-tab", transport=foreign_transport, store=restored,
    ).collect_current_location()
    assert foreign.status is CensusCollectionStatus.COMPLETE
    assert foreign_transport.inspections == ["0", "13"]


def test_stops_before_next_endpoint_when_inspection_is_not_authoritative(tmp_path: Path) -> None:
    transport = Transport()
    original = transport.inspect_endpoint

    def invalid(endpoint):
        value = original(endpoint)
        value["identityMatches"] = False
        return value

    transport.inspect_endpoint = invalid
    result = NpcCensusCollector(
        actor_key="profile-tab-session", transport=transport,
        store=NpcCensusStore(tmp_path / "census.json"),
    ).collect_current_location()
    assert result.status is CensusCollectionStatus.BLOCKED
    assert transport.inspections == ["0"]
    assert transport.returns == 0


def test_stops_when_endpoint_contract_changes_after_return(tmp_path: Path) -> None:
    transport = Transport()
    original = transport.area_snapshot

    def changing_area():
        value = original()
        if transport.returns:
            value["items"][1]["routeRef"] = "999"
        return value

    transport.area_snapshot = changing_area
    result = NpcCensusCollector(
        actor_key="profile-tab-session", transport=transport,
        store=NpcCensusStore(tmp_path / "census.json"),
    ).collect_current_location()
    assert result.status is CensusCollectionStatus.BLOCKED
    assert result.reason == "area_endpoint_set_changed"
    assert transport.inspections == ["0"]


def test_noncausal_durable_dialogue_never_marks_endpoint_complete(tmp_path: Path) -> None:
    path = tmp_path / "census.json"
    store = NpcCensusStore(path)
    authority = NpcCensusCollector(
        actor_key="profile-tab-session", transport=Transport(), store=store,
    )._observe_area()[0]
    store.append_area(authority)
    noncausal = dialogue("0", authority.document_revision + 1)
    noncausal["snapshotId"] = "npc-dialog-foreign-2"
    noncausal["observationEpoch"] = "foreign"
    store.append_dialogue(NpcDialogueObservation(
        actor_key=authority.actor_key,
        document_revision=2,
        causal_area_snapshot_id=authority.snapshot_id,
        location_id=authority.location_id,
        endpoint_id=authority.endpoint_id,
        resulting_name="Торговец Богдан",
        npc_instance_id="12",
        snapshot_id="npc-dialog-foreign-2",
        generated_at="2026-07-20T19:03:02.500Z",
    ))
    transport = Transport()
    result = NpcCensusCollector(
        actor_key="profile-tab-session", transport=transport, store=store,
    ).collect_current_location()
    assert result.status is CensusCollectionStatus.COMPLETE
    assert transport.inspections == ["0", "13"]


def test_durable_models_reject_revision_above_javascript_safe_integer() -> None:
    values = dict(
        actor_key="actor", document_revision=9_007_199_254_740_992,
        location_id="102", location_name="Area", endpoint_id="0",
        endpoint_name="Npc", snapshot_id="area-npcs-epoch-1",
        generated_at="2026-07-20T00:00:00Z", route_ref="398",
    )
    with pytest.raises(ValueError, match="document_revision"):
        AreaNpcEndpointObservation(**values)


def test_durable_models_reject_numeric_identity_above_javascript_safe_integer() -> None:
    with pytest.raises(ValueError, match="location_id"):
        AreaNpcEndpointObservation(
            actor_key="actor", document_revision=1,
            location_id="9007199254740992", location_name="Area",
            endpoint_id="0", endpoint_name="Npc",
            snapshot_id="area-npcs-epoch-1",
            generated_at="2026-07-20T00:00:00Z", route_ref="398",
        )
