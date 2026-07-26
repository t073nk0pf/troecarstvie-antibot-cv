"""Pure deterministic binding of NPC census facts to canonical identities."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Iterable

from src.antibot_cv.automation.npc_census_model import (
    AreaNpcEndpointObservation,
    NpcDialogueObservation,
    NpcQuestRoleObservation,
)
from src.antibot_cv.automation.quest_npc_directory import NpcDirectoryEntry
from src.antibot_cv.automation.runtime_helpers import same_location_name


class NpcBindingStatus(str, Enum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ObservationRef:
    actor_key: str
    snapshot_id: str


@dataclass(frozen=True, slots=True)
class NpcBindingEvidence:
    endpoint: ObservationRef
    dialogue: ObservationRef
    instance_endpoint: ObservationRef | None
    instance_dialogue: ObservationRef | None


@dataclass(frozen=True, slots=True)
class VerifiedNpcBinding:
    location_id: str
    location_name: str
    endpoint_id: str
    endpoint_name: str
    resulting_name: str
    npc_instance_id: str | None
    quest_roles: tuple[NpcQuestRoleObservation, ...]
    evidence: NpcBindingEvidence


@dataclass(frozen=True, slots=True)
class NpcBindingDecision:
    status: NpcBindingStatus
    reason: str
    binding: VerifiedNpcBinding | None = None


def bind_npc_observations(
    canonical_entries: Iterable[NpcDirectoryEntry],
    area_observations: Iterable[AreaNpcEndpointObservation],
    dialogue_observations: Iterable[NpcDialogueObservation],
) -> tuple[NpcBindingDecision, ...]:
    """Bind exact location/endpoint observations; never infer through quest text."""

    entries = tuple(canonical_entries)
    areas = sorted(
        area_observations,
        key=lambda item: (item.location_id, int(item.endpoint_id), item.endpoint_name, item.snapshot_id),
    )
    dialogues = tuple(dialogue_observations)
    decisions: list[NpcBindingDecision] = []
    grouped_areas: dict[tuple[str, str], list[AreaNpcEndpointObservation]] = {}
    for area in areas:
        grouped_areas.setdefault((area.location_id, area.endpoint_id), []).append(area)
    for endpoint_key, endpoint_areas in grouped_areas.items():
        # ActorKey separates causal observation streams; reconnecting must not
        # create a semantic endpoint conflict when the observed identity agrees.
        area_contracts = {
            (item.location_name, item.endpoint_name, item.route_ref)
            for item in endpoint_areas
        }
        if len(area_contracts) != 1:
            decisions.append(NpcBindingDecision(NpcBindingStatus.CONFLICT, "area_endpoint_conflict"))
            continue
        causal_pairs = tuple(
            (area, dialogue)
            for area in endpoint_areas
            for dialogue in dialogues
            if (dialogue.location_id, dialogue.endpoint_id) == endpoint_key
            and dialogue.actor_key == area.actor_key
            and dialogue.causal_area_snapshot_id == area.snapshot_id
            and dialogue.document_revision > area.document_revision
            and _timestamp(dialogue.generated_at) > _timestamp(area.generated_at)
            and _same_observation_epoch(
                area.snapshot_id, dialogue.snapshot_id,
                area.document_revision, dialogue.document_revision,
            )
        )
        if causal_pairs:
            resulting_names = {dialogue.resulting_name for _, dialogue in causal_pairs}
            known_instance_ids = {
                dialogue.npc_instance_id
                for _, dialogue in causal_pairs
                if dialogue.npc_instance_id is not None
            }
            if len(resulting_names) != 1 or len(known_instance_ids) > 1:
                decisions.append(
                    NpcBindingDecision(NpcBindingStatus.CONFLICT, "cross_stream_identity_conflict")
                )
                continue
            resolved_instance_id = next(iter(known_instance_ids), None)
            instance_dialogue = (
                max(
                    (
                        dialogue for _, dialogue in causal_pairs
                        if dialogue.npc_instance_id == resolved_instance_id
                    ),
                    key=lambda item: (
                        _timestamp(item.generated_at), item.document_revision, item.snapshot_id,
                    ),
                )
                if resolved_instance_id is not None
                else None
            )
            instance_area = (
                next(
                    candidate for candidate, candidate_dialogue in causal_pairs
                    if candidate_dialogue == instance_dialogue
                )
                if instance_dialogue is not None
                else None
            )
            area = max(causal_pairs, key=lambda pair: (_timestamp(pair[1].generated_at), pair[1].snapshot_id))[0]
        else:
            resolved_instance_id = None
            area = max(endpoint_areas, key=lambda item: (_timestamp(item.generated_at), item.snapshot_id))
        observed_dialogues = tuple(dialogue for candidate, dialogue in causal_pairs if candidate == area)
        if not observed_dialogues:
            decisions.append(NpcBindingDecision(NpcBindingStatus.UNKNOWN, "dialogue_not_observed"))
            continue
        dialogue_names = {item.resulting_name for item in observed_dialogues}
        dialogue_instance_ids = {
            item.npc_instance_id for item in observed_dialogues if item.npc_instance_id is not None
        }
        if len(dialogue_names) != 1 or len(dialogue_instance_ids) > 1:
            decisions.append(NpcBindingDecision(NpcBindingStatus.CONFLICT, "dialogue_identity_conflict"))
            continue
        dialogue = max(
            observed_dialogues,
            key=lambda item: (_timestamp(item.generated_at), item.document_revision, item.snapshot_id),
        )
        canonical = tuple(
            entry for entry in entries
            if entry.location_id == area.location_id
            and _normalized(entry.canonical_name) == _normalized(dialogue.resulting_name)
        )
        if not canonical:
            decisions.append(NpcBindingDecision(NpcBindingStatus.UNKNOWN, "canonical_npc_not_found"))
            continue
        if len(canonical) != 1:
            decisions.append(NpcBindingDecision(NpcBindingStatus.AMBIGUOUS, "canonical_npc_ambiguous"))
            continue
        entry = canonical[0]
        if not same_location_name(entry.location_name, area.location_name) and not any(
            same_location_name(alias, area.location_name) for alias in entry.location_aliases
        ):
            decisions.append(NpcBindingDecision(NpcBindingStatus.CONFLICT, "location_identity_conflict"))
            continue
        if entry.area_object_id is not None and entry.area_object_id != area.endpoint_id:
            decisions.append(NpcBindingDecision(NpcBindingStatus.CONFLICT, "endpoint_identity_conflict"))
            continue
        if entry.npc_instance_id is not None and entry.npc_instance_id != resolved_instance_id:
            decisions.append(NpcBindingDecision(NpcBindingStatus.CONFLICT, "instance_identity_conflict"))
            continue
        roles = _sorted_roles(dialogue.quest_roles)
        decisions.append(
            NpcBindingDecision(
                NpcBindingStatus.VERIFIED,
                "exact_observed_endpoint_and_result",
                VerifiedNpcBinding(
                    location_id=area.location_id,
                    location_name=entry.location_name,
                    endpoint_id=area.endpoint_id,
                    endpoint_name=area.endpoint_name,
                    resulting_name=entry.canonical_name,
                    npc_instance_id=resolved_instance_id,
                    quest_roles=roles,
                    evidence=NpcBindingEvidence(
                        endpoint=ObservationRef(area.actor_key, area.snapshot_id),
                        dialogue=ObservationRef(dialogue.actor_key, dialogue.snapshot_id),
                        instance_endpoint=(
                            ObservationRef(instance_area.actor_key, instance_area.snapshot_id)
                            if instance_area is not None else None
                        ),
                        instance_dialogue=(
                            ObservationRef(instance_dialogue.actor_key, instance_dialogue.snapshot_id)
                            if instance_dialogue is not None else None
                        ),
                    ),
                ),
            )
        )
    duplicate_targets: dict[tuple[str, str], list[int]] = {}
    for index, decision in enumerate(decisions):
        if decision.status is NpcBindingStatus.VERIFIED and decision.binding is not None:
            duplicate_targets.setdefault(
                (decision.binding.location_id, _normalized(decision.binding.resulting_name)),
                [],
            ).append(index)
    for indexes in duplicate_targets.values():
        if len(indexes) > 1:
            for index in indexes:
                decisions[index] = NpcBindingDecision(
                    NpcBindingStatus.CONFLICT, "canonical_physical_identity_ambiguous",
                )
    return tuple(decisions)


def overlay_verified_census(
    entries: Iterable[NpcDirectoryEntry],
    decisions: Iterable[NpcBindingDecision],
) -> tuple[NpcDirectoryEntry, ...]:
    """Enrich canonical quest routing only with VERIFIED census bindings."""

    result = list(entries)
    for decision in decisions:
        if decision.status is not NpcBindingStatus.VERIFIED or decision.binding is None:
            continue
        binding = decision.binding
        matches = [
            index for index, entry in enumerate(result)
            if entry.location_id == binding.location_id
            and _normalized(entry.canonical_name) == _normalized(binding.resulting_name)
        ]
        if len(matches) != 1:
            raise ValueError("verified NPC census overlay target is ambiguous")
        index = matches[0]
        entry = result[index]
        proxies = entry.proxy_names
        if (
            _normalized(binding.endpoint_name) != _normalized(binding.resulting_name)
            and binding.endpoint_name not in proxies
        ):
            proxies = (*proxies, binding.endpoint_name)
        result[index] = replace(
            entry,
            proxy_names=proxies,
            area_object_id=binding.endpoint_id,
            npc_instance_id=binding.npc_instance_id,
        )
    return tuple(result)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def _sorted_roles(roles: tuple[NpcQuestRoleObservation, ...]) -> tuple[NpcQuestRoleObservation, ...]:
    return tuple(sorted(roles, key=lambda role: (int(role.quest_id), role.role.value)))


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _same_observation_epoch(
    area_snapshot_id: str, dialogue_snapshot_id: str,
    area_revision: int, dialogue_revision: int,
) -> bool:
    area = area_snapshot_id.split("-")
    dialogue = dialogue_snapshot_id.split("-")
    if not (
        len(area) == 4 and area[:2] == ["area", "npcs"]
        and len(dialogue) == 4 and dialogue[:2] == ["npc", "dialog"]
        and area[-2] == dialogue[-2]
        and _canonical_base36(area[-2])
        and _canonical_base36(area[-1])
        and _canonical_base36(dialogue[-1])
    ):
        return False
    return int(area[-1], 36) == area_revision and int(dialogue[-1], 36) == dialogue_revision


def _canonical_base36(value: str) -> bool:
    return (
        bool(value) and value == value.lower()
        and all(char in "0123456789abcdefghijklmnopqrstuvwxyz" for char in value)
        and (value == "0" or not value.startswith("0"))
    )
