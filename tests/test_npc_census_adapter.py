from __future__ import annotations

import pytest

from src.antibot_cv.automation.npc_census_adapter import (
    parse_area_census_observations,
    parse_dialogue_census_observation,
)
from src.antibot_cv.automation.npc_census_model import QuestNpcRole


def _area() -> dict[str, object]:
    return {
        "ok": True, "message": "area_npc_snapshot", "pageKind": "area",
        "snapshotId": "area-npcs-mrtl8672-2", "generatedAt": "2026-07-20T18:58:25.502Z",
        "observationEpoch": "mrtl8672", "observationRevision": 2,
        "location": {"id": "102", "name": "Городская площадь Арсы"},
        "items": [
            {"name": "Торговец Богдан", "dataId": "0", "routeRef": "398", "actionable": True},
            {"name": "Богатырь Тур", "dataId": "13", "routeRef": "228", "actionable": True},
        ],
        "truncated": False,
    }


def _dialogue() -> dict[str, object]:
    return {
        "ok": True, "message": "npc_dialog_snapshot", "pageKind": "npc",
        "snapshotId": "npc-dialog-mrtl8672-7", "generatedAt": "2026-07-20T19:03:35.750Z",
        "observationEpoch": "mrtl8672", "observationRevision": 7,
        "identityMatches": True, "npcId": "0", "npcInstanceId": "12",
        "matchingHeaders": ["Торговец Богдан"], "truncated": False,
        "resultingName": "Торговец Богдан",
        "questActions": [{"questId": "445"}],
        "dialogActions": [{"questId": "658"}],
        "doneActions": [{"questId": "75"}, {"questId": "209"}],
        "acceptActions": [{"questId": "209"}],
    }


def test_parses_real_area_and_dialogue_into_causal_census_facts() -> None:
    areas = parse_area_census_observations(_area(), actor_key="profile-tab-session")
    assert [(item.endpoint_id, item.route_ref, item.document_revision) for item in areas] == [
        ("0", "398", 2), ("13", "228", 2),
    ]

    dialogue = parse_dialogue_census_observation(
        _dialogue(), actor_key="profile-tab-session", causal_area=areas[0],
    )
    assert dialogue.resulting_name == "Торговец Богдан"
    assert dialogue.npc_instance_id == "12"
    assert [(item.quest_id, item.role) for item in dialogue.quest_roles] == [
        ("75", QuestNpcRole.TURN_IN),
        ("209", QuestNpcRole.GIVER),
        ("445", QuestNpcRole.DIALOGUE),
        ("658", QuestNpcRole.HANDOFF),
    ]


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(truncated=True),
    lambda value: value["items"].append({"name": "X", "dataId": "14", "routeRef": "398", "actionable": True}),
    lambda value: value["items"][0].update(actionable=False),
    lambda value: value["items"][0].update(routeRef=None),
])
def test_area_adapter_fails_closed_on_incomplete_or_ambiguous_evidence(mutation) -> None:
    value = _area()
    mutation(value)
    with pytest.raises(ValueError):
        parse_area_census_observations(value, actor_key="profile-tab-session")


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(identityMatches=False),
    lambda value: value.update(truncated=True),
    lambda value: value.update(npcId="13"),
    lambda value: value.update(resultingName=None, matchingHeaders=["A", "B"]),
    lambda value: value.update(resultingName=None, matchingHeaders=["Торговец Богдан"]),
    lambda value: value.update(snapshotId="npc-dialog-mrtl8672-1", observationRevision=1),
    lambda value: value.update(questActions=[{"questId": True}]),
])
def test_dialogue_adapter_fails_closed_on_non_authoritative_evidence(mutation) -> None:
    area = parse_area_census_observations(_area(), actor_key="profile-tab-session")[0]
    value = _dialogue()
    mutation(value)
    with pytest.raises(ValueError):
        parse_dialogue_census_observation(value, actor_key="profile-tab-session", causal_area=area)


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(snapshotId="npc-dialog-mrtl8672-0007"),
    lambda value: value.update(snapshotId="npc-dialog-MRTL8672-7"),
    lambda value: value.update(snapshotId="npc-dialog-not_base36-7"),
    lambda value: value.update(observationEpoch="foreign"),
    lambda value: value.update(observationRevision=8),
    lambda value: value.update(observationRevision=True, snapshotId="npc-dialog-mrtl8672-1"),
    lambda value: value.update(observationRevision=False),
    lambda value: value.update(observationRevision=7.0),
    lambda value: value.update(observationRevision="7"),
    lambda value: value.update(observationRevision=None),
    lambda value: value.update(generatedAt="2020-01-01T00:00:00Z"),
])
def test_dialogue_adapter_rejects_identity_aliases_and_noncausal_time(mutation) -> None:
    area = parse_area_census_observations(_area(), actor_key="profile-tab-session")[0]
    value = _dialogue()
    mutation(value)
    with pytest.raises(ValueError):
        parse_dialogue_census_observation(value, actor_key="profile-tab-session", causal_area=area)


def test_dialogue_adapter_rejects_cross_actor_and_reload_epoch() -> None:
    area = parse_area_census_observations(_area(), actor_key="profile-tab-session")[0]
    with pytest.raises(ValueError):
        parse_dialogue_census_observation(
            _dialogue(), actor_key="other-profile-tab", causal_area=area,
        )
    restarted = _dialogue()
    restarted.update(
        snapshotId="npc-dialog-mrtl9999-7", observationEpoch="mrtl9999",
    )
    with pytest.raises(ValueError):
        parse_dialogue_census_observation(
            restarted, actor_key="profile-tab-session", causal_area=area,
        )
