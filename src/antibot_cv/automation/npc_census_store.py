"""Bounded durable storage for shadow NPC census observations."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from src.antibot_cv.automation.npc_census_model import (
    AreaNpcEndpointObservation,
    NpcDialogueObservation,
    NpcQuestRoleObservation,
    QuestNpcRole,
)


class NpcCensusStore:
    def __init__(
        self,
        path: str | Path,
        *,
        max_observations: int = 5000,
        max_file_bytes: int = 16_000_000,
        max_observation_bytes: int = 131_072,
    ) -> None:
        if (
            isinstance(max_observations, bool)
            or not isinstance(max_observations, int)
            or not 1 <= max_observations <= 5000
        ):
            raise ValueError("max_observations is invalid")
        for value, name, ceiling in (
            (max_file_bytes, "max_file_bytes", 16_000_000),
            (max_observation_bytes, "max_observation_bytes", 131_072),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
                raise ValueError(f"{name} is invalid")
        self.path = Path(path)
        self.max_observations = max_observations
        self.max_file_bytes = max_file_bytes
        self.max_observation_bytes = max_observation_bytes
        self._areas: list[AreaNpcEndpointObservation] = []
        self._dialogues: list[NpcDialogueObservation] = []

    @property
    def areas(self) -> tuple[AreaNpcEndpointObservation, ...]:
        return tuple(self._areas)

    @property
    def dialogues(self) -> tuple[NpcDialogueObservation, ...]:
        return tuple(self._dialogues)

    def load(self) -> None:
        if not self.path.exists():
            return
        if self.path.stat().st_size > self.max_file_bytes:
            raise ValueError("NPC census store file exceeds hard cap")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"schemaVersion", "areas", "dialogues"}:
            raise ValueError("NPC census store schema is invalid")
        if (
            type(raw["schemaVersion"]) is not int
            or raw["schemaVersion"] not in {1, 2}
            or not isinstance(raw["areas"], list)
            or not isinstance(raw["dialogues"], list)
        ):
            raise ValueError("NPC census store schema is unsupported")
        if len(raw["areas"]) + len(raw["dialogues"]) > self.max_observations:
            raise ValueError("NPC census store exceeds hard cap")
        areas = [_decode_area(item, schema_version=raw["schemaVersion"]) for item in raw["areas"]]
        dialogues = [_decode_dialogue(item) for item in raw["dialogues"]]
        if any(
            _encoded_observation_size(item) > self.max_observation_bytes
            for item in (*areas, *dialogues)
        ):
            raise ValueError("NPC census observation exceeds hard cap")
        refs = [_observation_storage_key(item) for item in (*areas, *dialogues)]
        if len(set(refs)) != len(refs):
            raise ValueError("NPC census store contains duplicate snapshot identity")
        _validate_area_envelopes(areas)
        self._areas, self._dialogues = areas, dialogues

    def append_area(self, observation: AreaNpcEndpointObservation) -> bool:
        if not isinstance(observation, AreaNpcEndpointObservation):
            raise ValueError("area observation is invalid")
        return self._append(self._areas, observation)

    def append_dialogue(self, observation: NpcDialogueObservation) -> bool:
        if not isinstance(observation, NpcDialogueObservation):
            raise ValueError("dialogue observation is invalid")
        return self._append(self._dialogues, observation)

    def save(self) -> None:
        payload = {
            "schemaVersion": 2,
            "areas": [_encode_area(item) for item in self._areas],
            "dialogues": [_encode_dialogue(item) for item in self._dialogues],
        }
        if not self.path.parent.is_dir():
            raise ValueError("NPC census store parent directory must already exist")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if len(encoded.encode("utf-8")) > self.max_file_bytes:
            raise ValueError("NPC census store file exceeds hard cap")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _append(self, target: list, observation: object) -> bool:
        if _encoded_observation_size(observation) > self.max_observation_bytes:
            raise ValueError("NPC census observation exceeds hard cap")
        if isinstance(observation, AreaNpcEndpointObservation):
            _validate_area_envelopes([*self._areas, observation])
        key = _observation_storage_key(observation)
        for item in (*self._areas, *self._dialogues):
            if _observation_storage_key(item) == key:
                if item == observation and type(item) is type(observation):
                    return False
                raise ValueError("NPC census snapshot identity conflicts")
        if len(self._areas) + len(self._dialogues) >= self.max_observations:
            raise ValueError("NPC census store hard cap reached")
        target.append(observation)
        return True


def _encode_area(item: AreaNpcEndpointObservation) -> dict[str, object]:
    return asdict(item)


def _encoded_observation_size(item: object) -> int:
    encoded = _encode_area(item) if isinstance(item, AreaNpcEndpointObservation) else _encode_dialogue(item)
    return len(json.dumps(encoded, ensure_ascii=False).encode("utf-8"))


def _observation_storage_key(item: object) -> tuple[str, ...]:
    if isinstance(item, AreaNpcEndpointObservation):
        return ("area", item.actor_key, item.snapshot_id, item.endpoint_id)
    if isinstance(item, NpcDialogueObservation):
        return ("dialogue", item.actor_key, item.snapshot_id)
    raise ValueError("NPC census observation is invalid")


def _validate_area_envelopes(items: list[AreaNpcEndpointObservation]) -> None:
    envelopes: dict[tuple[str, str], tuple[object, ...]] = {}
    route_owners: dict[tuple[str, str, str], str] = {}
    for item in items:
        group = (item.actor_key, item.snapshot_id)
        envelope = (
            item.document_revision, item.location_id, item.location_name, item.generated_at,
        )
        existing = envelopes.setdefault(group, envelope)
        if existing != envelope:
            raise ValueError("NPC census area snapshot envelope conflicts")
        if item.route_ref is not None:
            route_key = (*group, item.route_ref)
            owner = route_owners.setdefault(route_key, item.endpoint_id)
            if owner != item.endpoint_id:
                raise ValueError("NPC census area route identity conflicts")


def _encode_dialogue(item: NpcDialogueObservation) -> dict[str, object]:
    result = asdict(item)
    result["quest_roles"] = [
        {"quest_id": role.quest_id, "role": role.role.value} for role in item.quest_roles
    ]
    return result


def _decode_area(raw: object, *, schema_version: int) -> AreaNpcEndpointObservation:
    legacy_keys = {"actor_key", "document_revision", "location_id", "location_name", "endpoint_id", "endpoint_name", "snapshot_id", "generated_at"}
    keys = {*legacy_keys, "route_ref"}
    expected = legacy_keys if schema_version == 1 else keys
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("area observation schema is invalid")
    values = dict(raw)
    values.setdefault("route_ref", None)
    return AreaNpcEndpointObservation(**values)


def _decode_dialogue(raw: object) -> NpcDialogueObservation:
    keys = {"actor_key", "document_revision", "causal_area_snapshot_id", "location_id", "endpoint_id", "resulting_name", "npc_instance_id", "snapshot_id", "generated_at", "quest_roles"}
    if (
        not isinstance(raw, dict)
        or set(raw) != keys
        or not isinstance(raw["quest_roles"], list)
        or len(raw["quest_roles"]) > 256
    ):
        raise ValueError("dialogue observation schema is invalid")
    roles = tuple(
        NpcQuestRoleObservation(item["quest_id"], QuestNpcRole(item["role"]))
        for item in raw["quest_roles"]
        if isinstance(item, dict) and set(item) == {"quest_id", "role"}
    )
    if len(roles) != len(raw["quest_roles"]):
        raise ValueError("dialogue quest role schema is invalid")
    values = dict(raw)
    values["quest_roles"] = roles
    return NpcDialogueObservation(**values)
