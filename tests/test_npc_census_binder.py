from __future__ import annotations

import pytest
from dataclasses import replace

from src.antibot_cv.automation.npc_census_binder import (
    NpcBindingStatus,
    bind_npc_observations,
    overlay_verified_census,
)
from src.antibot_cv.automation.npc_census_model import (
    AreaNpcEndpointObservation,
    NpcDialogueObservation,
    NpcQuestRoleObservation,
    QuestNpcRole,
)
from src.antibot_cv.automation.quest_npc_directory import NpcDirectoryEntry


def vasilisa_area() -> AreaNpcEndpointObservation:
    return AreaNpcEndpointObservation(
        actor_key="profile-a/tab-17/epoch-1",
        document_revision=10,
        location_id="127",
        location_name="Пристанище трёх ветров",
        endpoint_id="0",
        endpoint_name="Дом Василисы",
        snapshot_id="area-npcs-epoch-a",
        generated_at="2026-07-20T12:59:56.420Z",
    )


def vasilisa_dialogue(
    *,
    resulting_name: str = "Крестьянка Василиса",
    actor_key: str = "profile-a/tab-17/epoch-1",
    causal_area_snapshot_id: str = "area-npcs-epoch-a",
    generated_at: str = "2026-07-20T13:00:01.000Z",
    quest_roles: tuple[NpcQuestRoleObservation, ...] | None = None,
) -> NpcDialogueObservation:
    return NpcDialogueObservation(
        actor_key=actor_key,
        document_revision=11,
        causal_area_snapshot_id=causal_area_snapshot_id,
        location_id="127",
        endpoint_id="0",
        resulting_name=resulting_name,
        npc_instance_id="267",
        snapshot_id="npc-dialog-epoch-b",
        generated_at=generated_at,
        quest_roles=quest_roles or (NpcQuestRoleObservation("267", QuestNpcRole.GIVER),),
    )


def canonical_vasilisa() -> NpcDirectoryEntry:
    return NpcDirectoryEntry(
        canonical_name="Крестьянка Василиса",
        location_id="127",
        location_name="Пристанище трёх ветров",
    )


def test_live_vasilisa_proxy_binds_to_canonical_npc_and_quest_role() -> None:
    decisions = bind_npc_observations(
        (canonical_vasilisa(),), (vasilisa_area(),), (vasilisa_dialogue(),),
    )

    assert decisions[0].status is NpcBindingStatus.VERIFIED
    assert decisions[0].binding is not None
    assert decisions[0].binding.endpoint_id == "0"
    assert decisions[0].binding.endpoint_name == "Дом Василисы"
    assert decisions[0].binding.resulting_name == "Крестьянка Василиса"
    assert decisions[0].binding.quest_roles == (
        NpcQuestRoleObservation("267", QuestNpcRole.GIVER),
    )

    overlaid = overlay_verified_census((canonical_vasilisa(),), decisions)
    assert overlaid[0].area_object_id == "0"
    assert overlaid[0].npc_instance_id == "267"
    assert overlaid[0].proxy_names == ("Дом Василисы",)


def test_nonverified_census_never_enriches_quest_directory() -> None:
    decisions = bind_npc_observations(
        (canonical_vasilisa(),), (vasilisa_area(),), (),
    )
    assert decisions[0].status is NpcBindingStatus.UNKNOWN
    assert overlay_verified_census((canonical_vasilisa(),), decisions) == (
        canonical_vasilisa(),
    )


def test_binding_is_deterministic_for_reordered_input() -> None:
    second = AreaNpcEndpointObservation(
        actor_key="profile-a/tab-17/epoch-1", document_revision=10,
        location_id="127", location_name="Пристанище трёх ветров",
        endpoint_id="3", endpoint_name="Дом Торвара", snapshot_id="area-torvar",
        generated_at="2026-07-20T13:00:00Z",
    )
    args = ((canonical_vasilisa(),), (vasilisa_area(), second), (vasilisa_dialogue(),))
    forward = bind_npc_observations(*args)
    reverse = bind_npc_observations(args[0], reversed(args[1]), reversed(args[2]))

    assert forward == reverse


def test_conflicting_dialogue_identity_fails_closed() -> None:
    decisions = bind_npc_observations(
        (canonical_vasilisa(),),
        (vasilisa_area(),),
        (vasilisa_dialogue(), vasilisa_dialogue(resulting_name="Самозванка Василиса")),
    )

    assert decisions[0].status is NpcBindingStatus.CONFLICT
    assert decisions[0].binding is None


def test_stale_or_foreign_dialogue_cannot_satisfy_area_observation() -> None:
    for dialogue in (
        vasilisa_dialogue(generated_at="2020-01-01T00:00:00Z"),
        vasilisa_dialogue(actor_key="profile-a/tab-99/epoch-1"),
        vasilisa_dialogue(causal_area_snapshot_id="area-npcs-old"),
    ):
        decision = bind_npc_observations(
            (canonical_vasilisa(),), (vasilisa_area(),), (dialogue,),
        )[0]
        assert decision.status is NpcBindingStatus.UNKNOWN
        assert decision.binding is None


def test_location_or_proxy_contract_conflict_fails_closed() -> None:
    wrong_location = AreaNpcEndpointObservation(
        actor_key="profile-a/tab-17/epoch-1", document_revision=10,
        location_id="127", location_name="Чужая локация", endpoint_id="0",
        endpoint_name="Дом Василисы", snapshot_id="area-npcs-epoch-a",
        generated_at="2026-07-20T12:59:56.420Z",
    )
    conflicting_proxy = AreaNpcEndpointObservation(
        actor_key="profile-a/tab-17/epoch-1", document_revision=9,
        location_id="127", location_name="Пристанище трёх ветров", endpoint_id="0",
        endpoint_name="Другой дом", snapshot_id="area-npcs-old",
        generated_at="2026-07-20T12:59:50.000Z",
    )

    wrong = bind_npc_observations(
        (canonical_vasilisa(),), (wrong_location,), (vasilisa_dialogue(),),
    )[0]
    proxy_conflict = bind_npc_observations(
        (canonical_vasilisa(),),
        (vasilisa_area(), conflicting_proxy),
        (vasilisa_dialogue(),),
    )[0]

    assert wrong.status is NpcBindingStatus.CONFLICT
    assert proxy_conflict.status is NpcBindingStatus.CONFLICT


def test_quest_role_order_does_not_create_false_conflict() -> None:
    roles = (
        NpcQuestRoleObservation("267", QuestNpcRole.GIVER),
        NpcQuestRoleObservation("268", QuestNpcRole.TURN_IN),
    )
    reverse_roles = tuple(reversed(roles))
    first = vasilisa_dialogue(quest_roles=roles)
    second = NpcDialogueObservation(
        actor_key=first.actor_key, document_revision=12,
        causal_area_snapshot_id=first.causal_area_snapshot_id,
        location_id=first.location_id, endpoint_id=first.endpoint_id,
        resulting_name=first.resulting_name, npc_instance_id=first.npc_instance_id,
        snapshot_id="npc-dialog-epoch-c", generated_at="2026-07-20T13:00:02Z",
        quest_roles=reverse_roles,
    )

    decision = bind_npc_observations(
        (canonical_vasilisa(),), (vasilisa_area(),), (first, second),
    )[0]

    assert decision.status is NpcBindingStatus.VERIFIED
    assert decision.binding is not None
    assert decision.binding.quest_roles == roles


def test_reconnect_with_same_semantic_endpoint_uses_latest_complete_causal_stream() -> None:
    reconnected_area = AreaNpcEndpointObservation(
        actor_key="profile-a/tab-17/epoch-2", document_revision=1,
        location_id="127", location_name="Пристанище трёх ветров", endpoint_id="0",
        endpoint_name="Дом Василисы", snapshot_id="area-npcs-epoch2-1",
        generated_at="2026-07-20T13:01:00Z",
    )
    reconnected_dialogue = NpcDialogueObservation(
        actor_key=reconnected_area.actor_key, document_revision=2,
        causal_area_snapshot_id=reconnected_area.snapshot_id,
        location_id="127", endpoint_id="0", resulting_name="Крестьянка Василиса",
        npc_instance_id="267", snapshot_id="npc-dialog-epoch2-2",
        generated_at="2026-07-20T13:01:01Z",
        quest_roles=(NpcQuestRoleObservation("267", QuestNpcRole.GIVER),),
    )

    decision = bind_npc_observations(
        (canonical_vasilisa(),),
        (vasilisa_area(), reconnected_area),
        (vasilisa_dialogue(), reconnected_dialogue),
    )[0]

    assert decision.status is NpcBindingStatus.VERIFIED
    assert decision.binding is not None
    assert decision.binding.evidence.endpoint.snapshot_id == "area-npcs-epoch2-1"
    assert decision.binding.evidence.dialogue.snapshot_id == "npc-dialog-epoch2-2"
    assert decision.binding.evidence.instance_endpoint is not None
    assert decision.binding.evidence.instance_endpoint.snapshot_id == "area-npcs-epoch2-1"
    assert decision.binding.evidence.instance_dialogue is not None
    assert decision.binding.evidence.instance_dialogue.snapshot_id == "npc-dialog-epoch2-2"


def test_reconnect_cannot_overwrite_conflicting_verified_identity() -> None:
    reconnected_area = AreaNpcEndpointObservation(
        actor_key="profile-a/tab-17/epoch-2", document_revision=1,
        location_id="127", location_name="Пристанище трёх ветров", endpoint_id="0",
        endpoint_name="Дом Василисы", snapshot_id="area-npcs-epoch2-1",
        generated_at="2026-07-20T13:01:00Z",
    )
    conflicting_dialogue = NpcDialogueObservation(
        actor_key=reconnected_area.actor_key, document_revision=2,
        causal_area_snapshot_id=reconnected_area.snapshot_id,
        location_id="127", endpoint_id="0", resulting_name="Самозванка Василиса",
        npc_instance_id="999", snapshot_id="npc-dialog-epoch2-2",
        generated_at="2026-07-20T13:01:01Z",
        quest_roles=(NpcQuestRoleObservation("267", QuestNpcRole.GIVER),),
    )

    decision = bind_npc_observations(
        (
            canonical_vasilisa(),
            NpcDirectoryEntry("Самозванка Василиса", "127", "Пристанище трёх ветров"),
        ),
        (vasilisa_area(), reconnected_area),
        (vasilisa_dialogue(), conflicting_dialogue),
    )[0]

    assert decision.status is NpcBindingStatus.CONFLICT
    assert decision.reason == "cross_stream_identity_conflict"
    assert decision.binding is None


def test_dynamic_quest_roles_do_not_invalidate_stable_npc_identity() -> None:
    reconnected_area = AreaNpcEndpointObservation(
        actor_key="profile-a/tab-17/epoch-2", document_revision=1,
        location_id="127", location_name="Пристанище трёх ветров", endpoint_id="0",
        endpoint_name="Дом Василисы", snapshot_id="area-npcs-epoch2-1",
        generated_at="2026-07-20T13:01:00Z",
    )
    after_accept = NpcDialogueObservation(
        actor_key=reconnected_area.actor_key, document_revision=2,
        causal_area_snapshot_id=reconnected_area.snapshot_id,
        location_id="127", endpoint_id="0", resulting_name="Крестьянка Василиса",
        npc_instance_id="267", snapshot_id="npc-dialog-epoch2-2",
        generated_at="2026-07-20T13:01:01Z", quest_roles=(),
    )

    decision = bind_npc_observations(
        (canonical_vasilisa(),),
        (vasilisa_area(), reconnected_area),
        (vasilisa_dialogue(), after_accept),
    )[0]

    assert decision.status is NpcBindingStatus.VERIFIED
    assert decision.binding is not None
    assert decision.binding.quest_roles == ()


def test_latest_dialogue_time_and_revision_select_dynamic_roles_not_snapshot_name() -> None:
    old = vasilisa_dialogue()
    old = NpcDialogueObservation(
        actor_key=old.actor_key, document_revision=11,
        causal_area_snapshot_id=old.causal_area_snapshot_id,
        location_id=old.location_id, endpoint_id=old.endpoint_id,
        resulting_name=old.resulting_name, npc_instance_id=old.npc_instance_id,
        snapshot_id="npc-dialog-epoch-b", generated_at="2026-07-20T13:00:01Z",
        quest_roles=(NpcQuestRoleObservation("267", QuestNpcRole.GIVER),),
    )
    new = NpcDialogueObservation(
        actor_key=old.actor_key, document_revision=12,
        causal_area_snapshot_id=old.causal_area_snapshot_id,
        location_id=old.location_id, endpoint_id=old.endpoint_id,
        resulting_name=old.resulting_name, npc_instance_id=old.npc_instance_id,
        snapshot_id="npc-dialog-epoch-c", generated_at="2026-07-20T13:00:02Z",
        quest_roles=(),
    )

    decision = bind_npc_observations(
        (canonical_vasilisa(),), (vasilisa_area(),), (old, new),
    )[0]

    assert decision.status is NpcBindingStatus.VERIFIED
    assert decision.binding is not None
    assert decision.binding.quest_roles == ()
    assert decision.binding.evidence.dialogue.snapshot_id == "npc-dialog-epoch-c"


@pytest.mark.parametrize("revision", [True, 1.5, "1", None])
def test_document_revision_is_strict_integer(revision: object) -> None:
    with pytest.raises(ValueError, match="document_revision is invalid"):
        AreaNpcEndpointObservation(
            actor_key="actor", document_revision=revision,  # type: ignore[arg-type]
            location_id="127", location_name="Пристанище трёх ветров",
            endpoint_id="0", endpoint_name="Дом Василисы", snapshot_id="area",
            generated_at="2026-07-20T13:00:00Z",
        )


def test_timestamp_without_timezone_is_rejected_at_model_boundary() -> None:
    with pytest.raises(ValueError, match="must include a timezone"):
        AreaNpcEndpointObservation(
            actor_key="actor", document_revision=1,
            location_id="127", location_name="Пристанище трёх ветров",
            endpoint_id="0", endpoint_name="Дом Василисы", snapshot_id="area",
            generated_at="2026-07-20T13:00:00",
        )


def test_quest_role_requires_enum_variant() -> None:
    with pytest.raises(ValueError, match="role is invalid"):
        NpcQuestRoleObservation("267", "giver")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "roles",
    [
        [NpcQuestRoleObservation("267", QuestNpcRole.GIVER)],
        (object(),),
    ],
)
def test_dialogue_roles_require_immutable_typed_tuple(roles: object) -> None:
    with pytest.raises(ValueError, match="quest_roles are invalid"):
        NpcDialogueObservation(
            actor_key="actor", document_revision=1, causal_area_snapshot_id="area",
            location_id="127", endpoint_id="0", resulting_name="Крестьянка Василиса",
            npc_instance_id=None, snapshot_id="dialog", generated_at="2026-07-20T13:00:00Z",
            quest_roles=roles,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("first_instance,second_instance", [(None, "267"), ("267", None)])
def test_unknown_instance_is_refined_without_erasing_known_identity(
    first_instance: str | None, second_instance: str | None,
) -> None:
    area2 = AreaNpcEndpointObservation(
        actor_key="actor-2", document_revision=1, location_id="127",
        location_name="Пристанище трёх ветров", endpoint_id="0",
        endpoint_name="Дом Василисы", snapshot_id="area-npcs-epoch2-1",
        generated_at="2026-07-20T13:01:00Z",
    )
    first = NpcDialogueObservation(
        actor_key=vasilisa_area().actor_key, document_revision=11,
        causal_area_snapshot_id=vasilisa_area().snapshot_id,
        location_id="127", endpoint_id="0", resulting_name="Крестьянка Василиса",
        npc_instance_id=first_instance, snapshot_id="npc-dialog-epoch-b",
        generated_at="2026-07-20T13:00:01Z",
    )
    second = NpcDialogueObservation(
        actor_key="actor-2", document_revision=2, causal_area_snapshot_id="area-npcs-epoch2-1",
        location_id="127", endpoint_id="0", resulting_name="Крестьянка Василиса",
        npc_instance_id=second_instance, snapshot_id="npc-dialog-epoch2-2",
        generated_at="2026-07-20T13:01:01Z",
    )

    decision = bind_npc_observations(
        (canonical_vasilisa(),), (vasilisa_area(), area2), (first, second),
    )[0]

    assert decision.status is NpcBindingStatus.VERIFIED
    assert decision.binding is not None
    assert decision.binding.npc_instance_id == "267"
    expected_instance_snapshot = "npc-dialog-epoch-b" if first_instance == "267" else "npc-dialog-epoch2-2"
    assert decision.binding.evidence.instance_dialogue is not None
    assert decision.binding.evidence.instance_dialogue.snapshot_id == expected_instance_snapshot
    assert decision.binding.evidence.instance_endpoint is not None

    without_instance_evidence = tuple(
        item for item in (first, second) if item.snapshot_id != expected_instance_snapshot
    )
    replay = bind_npc_observations(
        (canonical_vasilisa(),), (vasilisa_area(), area2), without_instance_evidence,
    )
    verified_ids = tuple(
        item.binding.npc_instance_id
        for item in replay
        if item.status is NpcBindingStatus.VERIFIED and item.binding is not None
    )
    assert "267" not in verified_ids

    refs = {
        (decision.binding.evidence.endpoint.actor_key, decision.binding.evidence.endpoint.snapshot_id),
        (decision.binding.evidence.dialogue.actor_key, decision.binding.evidence.dialogue.snapshot_id),
        (
            decision.binding.evidence.instance_endpoint.actor_key,
            decision.binding.evidence.instance_endpoint.snapshot_id,
        ),
        (
            decision.binding.evidence.instance_dialogue.actor_key,
            decision.binding.evidence.instance_dialogue.snapshot_id,
        ),
    }
    replay_areas = tuple(
        item for item in (vasilisa_area(), area2) if (item.actor_key, item.snapshot_id) in refs
    )
    replay_dialogues = tuple(
        item for item in (first, second) if (item.actor_key, item.snapshot_id) in refs
    )
    replay_from_evidence = bind_npc_observations(
        (canonical_vasilisa(),), reversed(replay_areas), reversed(replay_dialogues),
    )[0]
    assert replay_from_evidence == decision


def test_different_known_instance_ids_conflict() -> None:
    area2 = AreaNpcEndpointObservation(
        actor_key="actor-2", document_revision=1, location_id="127",
        location_name="Пристанище трёх ветров", endpoint_id="0",
        endpoint_name="Дом Василисы", snapshot_id="area-npcs-epoch2-1",
        generated_at="2026-07-20T13:01:00Z",
    )
    second = NpcDialogueObservation(
        actor_key="actor-2", document_revision=2, causal_area_snapshot_id="area-npcs-epoch2-1",
        location_id="127", endpoint_id="0", resulting_name="Крестьянка Василиса",
        npc_instance_id="999", snapshot_id="npc-dialog-epoch2-2",
        generated_at="2026-07-20T13:01:01Z",
    )

    decision = bind_npc_observations(
        (canonical_vasilisa(),), (vasilisa_area(), area2), (vasilisa_dialogue(), second),
    )[0]

    assert decision.status is NpcBindingStatus.CONFLICT


def test_historical_route_change_for_same_endpoint_conflicts() -> None:
    second_area = AreaNpcEndpointObservation(
        actor_key="actor-2", document_revision=1, location_id="127",
        location_name="Пристанище трёх ветров", endpoint_id="0",
        endpoint_name="Дом Василисы", snapshot_id="area-npcs-epoch2-1",
        generated_at="2026-07-20T13:01:00Z", route_ref="999",
    )
    first_area = replace(vasilisa_area(), route_ref="398")
    second_dialogue = NpcDialogueObservation(
        actor_key="actor-2", document_revision=2,
        causal_area_snapshot_id=second_area.snapshot_id,
        location_id="127", endpoint_id="0", resulting_name="Крестьянка Василиса",
        npc_instance_id="267", snapshot_id="npc-dialog-epoch2-2",
        generated_at="2026-07-20T13:01:01Z",
    )
    decision = bind_npc_observations(
        (canonical_vasilisa(),), (first_area, second_area),
        (vasilisa_dialogue(), second_dialogue),
    )[0]
    assert decision.status is NpcBindingStatus.CONFLICT


def test_two_endpoints_for_same_canonical_npc_both_conflict() -> None:
    areas = (
        AreaNpcEndpointObservation(
            actor_key="actor", document_revision=1, location_id="127",
            location_name="Пристанище трёх ветров", endpoint_id=endpoint,
            endpoint_name=name, snapshot_id=f"area-npcs-{epoch}-1",
            generated_at="2026-07-20T13:00:00Z", route_ref=route,
        )
        for endpoint, name, epoch, route in (
            ("0", "Дом Василисы", "e1", "398"),
            ("1", "Двор Василисы", "e2", "399"),
        )
    )
    areas = tuple(areas)
    dialogues = tuple(
        NpcDialogueObservation(
            actor_key=item.actor_key, document_revision=2,
            causal_area_snapshot_id=item.snapshot_id, location_id="127",
            endpoint_id=item.endpoint_id, resulting_name="Крестьянка Василиса",
            npc_instance_id="267", snapshot_id=f"npc-dialog-{item.snapshot_id.split('-')[-2]}-2",
            generated_at="2026-07-20T13:00:01Z",
        )
        for item in areas
    )
    decisions = bind_npc_observations((canonical_vasilisa(),), areas, dialogues)
    assert [item.status for item in decisions] == [
        NpcBindingStatus.CONFLICT, NpcBindingStatus.CONFLICT,
    ]


@pytest.mark.parametrize("value", [True, -1, "-1", "", " 0", "00", "01"])
def test_endpoint_identity_rejects_malformed_values(value: object) -> None:
    with pytest.raises(ValueError, match="endpoint_id is invalid"):
        AreaNpcEndpointObservation(
            actor_key="profile-a/tab-17/epoch-1", document_revision=10,
            location_id="127", location_name="Пристанище трёх ветров",
            endpoint_id=value,  # type: ignore[arg-type]
            endpoint_name="Дом Василисы", snapshot_id="area", generated_at="now",
        )
