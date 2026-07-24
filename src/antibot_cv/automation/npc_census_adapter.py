"""Fail-closed adapters from bridge observations to durable NPC census facts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from src.antibot_cv.automation.npc_census_model import (
    AreaNpcEndpointObservation,
    MAX_SAFE_REVISION,
    NpcDialogueObservation,
    NpcQuestRoleObservation,
    QuestNpcRole,
)


def parse_area_census_observations(
    snapshot: object, *, actor_key: str,
) -> tuple[AreaNpcEndpointObservation, ...]:
    raw = _snapshot(snapshot, message="area_npc_snapshot", page_kind="area")
    location = raw.get("location")
    items = raw.get("items")
    if (
        raw.get("truncated") is not False
        or not isinstance(location, Mapping)
        or not isinstance(items, list)
        or len(items) > 100
    ):
        raise ValueError("area census snapshot is incomplete")
    location_id = _identity(location.get("id"), positive=True)
    location_name = _text(location.get("name"), 220)
    snapshot_id = _text(raw.get("snapshotId"), 160)
    generated_at = _text(raw.get("generatedAt"), 64)
    epoch, revision = _snapshot_identity(raw, snapshot_id, "area-npcs")
    observations: list[AreaNpcEndpointObservation] = []
    endpoint_ids: set[str] = set()
    route_refs: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or item.get("actionable") is not True:
            raise ValueError("area census endpoint is not actionable")
        endpoint_id = _identity(item.get("dataId"), positive=False)
        route_ref = _identity(item.get("routeRef"), positive=True)
        if endpoint_id in endpoint_ids or route_ref in route_refs:
            raise ValueError("area census identity is ambiguous")
        endpoint_ids.add(endpoint_id)
        route_refs.add(route_ref)
        observations.append(AreaNpcEndpointObservation(
            actor_key=_text(actor_key, 256),
            document_revision=revision,
            location_id=location_id,
            location_name=location_name,
            endpoint_id=endpoint_id,
            endpoint_name=_text(item.get("name"), 180),
            snapshot_id=snapshot_id,
            generated_at=generated_at,
            route_ref=route_ref,
        ))
    return tuple(sorted(observations, key=lambda item: (int(item.endpoint_id), item.endpoint_name)))


def parse_dialogue_census_observation(
    snapshot: object,
    *,
    actor_key: str,
    causal_area: AreaNpcEndpointObservation,
) -> NpcDialogueObservation:
    raw = _snapshot(snapshot, message="npc_dialog_snapshot", page_kind="npc")
    if raw.get("truncated") is not False or raw.get("identityMatches") is not True:
        raise ValueError("dialogue census snapshot is incomplete")
    endpoint_id = _identity(raw.get("npcId"), positive=False)
    if _text(actor_key, 256) != causal_area.actor_key:
        raise ValueError("dialogue census actor mismatches area authority")
    if endpoint_id != causal_area.endpoint_id:
        raise ValueError("dialogue census endpoint mismatches area authority")
    snapshot_id = _text(raw.get("snapshotId"), 160)
    epoch, revision = _snapshot_identity(raw, snapshot_id, "npc-dialog")
    area_epoch = _snapshot_identity_from_id(causal_area.snapshot_id, "area-npcs")[0]
    if epoch != area_epoch:
        raise ValueError("dialogue census epoch is not causal")
    if revision <= causal_area.document_revision:
        raise ValueError("dialogue census revision is not causal")
    generated_at = _text(raw.get("generatedAt"), 64)
    if _timestamp(generated_at) <= _timestamp(causal_area.generated_at):
        raise ValueError("dialogue census timestamp is not causal")
    resulting_name = raw.get("resultingName")
    if not isinstance(resulting_name, str):
        raise ValueError("dialogue census resulting identity is ambiguous")
    roles = _quest_roles(raw)
    raw_instance = raw.get("npcInstanceId")
    instance_id = None if raw_instance in (None, "") else _identity(raw_instance, positive=True)
    return NpcDialogueObservation(
        actor_key=_text(actor_key, 256),
        document_revision=revision,
        causal_area_snapshot_id=causal_area.snapshot_id,
        location_id=causal_area.location_id,
        endpoint_id=endpoint_id,
        resulting_name=_text(resulting_name, 180),
        npc_instance_id=instance_id,
        snapshot_id=snapshot_id,
        generated_at=generated_at,
        quest_roles=roles,
    )


def census_observation_pair_is_causal(
    area: AreaNpcEndpointObservation,
    dialogue: NpcDialogueObservation,
) -> bool:
    """Validate a durable area/dialogue pair with the live adapter rules."""

    try:
        area_epoch, area_revision = _snapshot_identity_from_id(
            area.snapshot_id, "area-npcs",
        )
        dialogue_epoch, dialogue_revision = _snapshot_identity_from_id(
            dialogue.snapshot_id, "npc-dialog",
        )
        return bool(
            area.actor_key == dialogue.actor_key
            and area.location_id == dialogue.location_id
            and area.endpoint_id == dialogue.endpoint_id
            and dialogue.causal_area_snapshot_id == area.snapshot_id
            and area.document_revision == area_revision
            and dialogue.document_revision == dialogue_revision
            and 1 <= area_revision <= MAX_SAFE_REVISION
            and 1 <= dialogue_revision <= MAX_SAFE_REVISION
            and dialogue_epoch == area_epoch
            and dialogue_revision > area_revision
            and _timestamp(dialogue.generated_at) > _timestamp(area.generated_at)
        )
    except (TypeError, ValueError):
        return False


def _quest_roles(raw: Mapping[object, object]) -> tuple[NpcQuestRoleObservation, ...]:
    assignments: set[tuple[str, QuestNpcRole]] = set()
    accepted: set[str] = set()
    for item in _action_list(raw, "acceptActions"):
        quest_id = _identity(item.get("questId"), positive=True)
        accepted.add(quest_id)
        assignments.add((quest_id, QuestNpcRole.GIVER))
    for field, role in (
        ("questActions", QuestNpcRole.DIALOGUE),
        ("dialogActions", QuestNpcRole.HANDOFF),
        ("doneActions", QuestNpcRole.TURN_IN),
    ):
        for item in _action_list(raw, field):
            quest_id = _identity(item.get("questId"), positive=True)
            if role is QuestNpcRole.TURN_IN and quest_id in accepted:
                continue
            assignments.add((quest_id, role))
    return tuple(
        NpcQuestRoleObservation(quest_id, role)
        for quest_id, role in sorted(assignments, key=lambda item: (int(item[0]), item[1].value))
    )


def _action_list(raw: Mapping[object, object], field: str) -> list[Mapping[object, object]]:
    value = raw.get(field)
    if not isinstance(value, list) or len(value) > 256 or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"dialogue census {field} is invalid")
    return value


def _snapshot(value: object, *, message: str, page_kind: str) -> Mapping[object, object]:
    if (
        not isinstance(value, Mapping)
        or value.get("ok") is not True
        or value.get("message") != message
        or value.get("pageKind") != page_kind
    ):
        raise ValueError("NPC census snapshot authority is invalid")
    return value


def _snapshot_identity(
    raw: Mapping[object, object], snapshot_id: str, prefix: str,
) -> tuple[str, int]:
    epoch, revision = _snapshot_identity_from_id(snapshot_id, prefix)
    raw_revision = raw.get("observationRevision")
    if (
        raw.get("observationEpoch") != epoch
        or type(raw_revision) is not int
        or not 1 <= raw_revision <= 9_007_199_254_740_991
        or raw_revision != revision
    ):
        raise ValueError("NPC census snapshot identity fields mismatch")
    return epoch, revision


def _snapshot_identity_from_id(snapshot_id: str, prefix: str) -> tuple[str, int]:
    parts = snapshot_id.split("-")
    if len(parts) != 4 or parts[:2] != prefix.split("-"):
        raise ValueError("NPC census snapshot identity is invalid")
    epoch = parts[-2]
    revision_text = parts[-1]
    if not _canonical_base36(epoch) or not _canonical_base36(revision_text):
        raise ValueError("NPC census snapshot identity is invalid")
    try:
        revision = int(revision_text, 36)
    except ValueError as exc:
        raise ValueError("NPC census snapshot revision is invalid") from exc
    if revision <= 0:
        raise ValueError("NPC census snapshot revision is invalid")
    return epoch, revision


def _canonical_base36(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.lower()
        and all(char in "0123456789abcdefghijklmnopqrstuvwxyz" for char in value)
        and (value == "0" or not value.startswith("0"))
    )


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("NPC census timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("NPC census timestamp is invalid")
    return parsed


def _identity(value: object, *, positive: bool) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or int(value) > MAX_SAFE_REVISION
        or str(int(value)) != value
        or (int(value) <= 0 if positive else int(value) < 0)
    ):
        raise ValueError("NPC census numeric identity is invalid")
    return value


def _text(value: object, max_length: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_length
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("NPC census text is invalid")
    return value
