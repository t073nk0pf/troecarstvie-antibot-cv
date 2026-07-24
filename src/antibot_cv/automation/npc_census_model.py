"""Immutable facts collected by the shadow-only NPC census.

The census stores transport observations without granting mutation authority.
Binding those observations to canonical NPCs is a separate pure operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


MAX_SAFE_REVISION = 9_007_199_254_740_991


class QuestNpcRole(str, Enum):
    GIVER = "giver"
    TURN_IN = "turn_in"
    DIALOGUE = "dialogue"
    HANDOFF = "handoff"


@dataclass(frozen=True, slots=True)
class AreaNpcEndpointObservation:
    actor_key: str
    document_revision: int
    location_id: str
    location_name: str
    endpoint_id: str
    endpoint_name: str
    snapshot_id: str
    generated_at: str
    route_ref: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.actor_key, "actor_key", 256)
        if (
            isinstance(self.document_revision, bool)
            or not isinstance(self.document_revision, int)
            or not 1 <= self.document_revision <= MAX_SAFE_REVISION
        ):
            raise ValueError("document_revision is invalid")
        _require_identity(self.location_id, "location_id", positive=True)
        _require_identity(self.endpoint_id, "endpoint_id", positive=False)
        if self.route_ref is not None:
            _require_identity(self.route_ref, "route_ref", positive=True)
        _require_text(self.location_name, "location_name", 220)
        _require_text(self.endpoint_name, "endpoint_name", 180)
        _require_text(self.snapshot_id, "snapshot_id", 160)
        _require_text(self.generated_at, "generated_at", 64)
        _parse_timestamp(self.generated_at)


@dataclass(frozen=True, slots=True)
class NpcQuestRoleObservation:
    quest_id: str
    role: QuestNpcRole

    def __post_init__(self) -> None:
        _require_identity(self.quest_id, "quest_id", positive=True)
        if not isinstance(self.role, QuestNpcRole):
            raise ValueError("role is invalid")


@dataclass(frozen=True, slots=True)
class NpcDialogueObservation:
    actor_key: str
    document_revision: int
    causal_area_snapshot_id: str
    location_id: str
    endpoint_id: str
    resulting_name: str
    npc_instance_id: str | None
    snapshot_id: str
    generated_at: str
    quest_roles: tuple[NpcQuestRoleObservation, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.actor_key, "actor_key", 256)
        if (
            isinstance(self.document_revision, bool)
            or not isinstance(self.document_revision, int)
            or not 1 <= self.document_revision <= MAX_SAFE_REVISION
        ):
            raise ValueError("document_revision is invalid")
        _require_text(self.causal_area_snapshot_id, "causal_area_snapshot_id", 160)
        _require_identity(self.location_id, "location_id", positive=True)
        _require_identity(self.endpoint_id, "endpoint_id", positive=False)
        _require_text(self.resulting_name, "resulting_name", 180)
        if self.npc_instance_id is not None:
            _require_identity(self.npc_instance_id, "npc_instance_id", positive=True)
        _require_text(self.snapshot_id, "snapshot_id", 160)
        _require_text(self.generated_at, "generated_at", 64)
        _parse_timestamp(self.generated_at)
        if not isinstance(self.quest_roles, tuple) or any(
            not isinstance(role, NpcQuestRoleObservation) for role in self.quest_roles
        ):
            raise ValueError("quest_roles are invalid")
        if len(self.quest_roles) > 256:
            raise ValueError("quest_roles exceed hard cap")
        if len(set(self.quest_roles)) != len(self.quest_roles):
            raise ValueError("quest_roles contain duplicates")


def _require_identity(value: object, field: str, *, positive: bool) -> None:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or len(value) > 40
        or int(value) > MAX_SAFE_REVISION
        or str(int(value)) != value
        or (int(value) <= 0 if positive else int(value) < 0)
    ):
        raise ValueError(f"{field} is invalid")


def _require_text(value: object, field: str, max_length: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_length
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{field} is invalid")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("generated_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    return parsed
