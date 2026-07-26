"""Typed, immutable identities for quest execution planning.

The objects in this module describe route authority and mutation intent only.
They deliberately do not import or invoke the action execution boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping


class RouteKind(str, Enum):
    QUEST_ACCEPT = "quest_accept"
    QUEST_DIALOGUE = "quest_dialogue"
    QUEST_LOCATION = "quest_location"
    QUEST_TURN_IN = "quest_turn_in"
    QUEST_ORDERED_HANDOFF = "quest_ordered_handoff"
    QUEST_AREA_OBJECT = "quest_area_object"


QUEST_ROUTE_KINDS = frozenset(kind.value for kind in RouteKind)


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical values cannot contain non-finite numbers")
        return value
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("canonical mapping keys must be non-empty strings")
            result[key] = _canonical_value(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _frozen_value(value: object) -> object:
    canonical = _canonical_value(value)
    if isinstance(canonical, dict):
        return MappingProxyType({key: _frozen_value(item) for key, item in canonical.items()})
    if isinstance(canonical, list):
        return tuple(_frozen_value(item) for item in canonical)
    return canonical


def canonical_json(value: object) -> str:
    """Serialize a supported value deterministically for identity checks."""

    return json.dumps(
        _canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def canonical_fingerprint(value: object, *, prefix: str = "qexec") -> str:
    prefix = _required(prefix, "prefix")
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest}"


@dataclass(frozen=True, slots=True)
class RouteLease:
    """Bounded authority to route one exact actor and quest step."""

    kind: RouteKind
    actor_id: str
    client_id: str
    profile_id: str
    tab_id: str
    quest_id: str
    step_fingerprint: str
    revision: str
    generated_at: float
    issued_at: float
    expires_at: float
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RouteKind):
            raise TypeError("kind must be RouteKind")
        for name in (
            "actor_id", "client_id", "profile_id", "tab_id", "quest_id",
            "step_fingerprint", "revision",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in ("generated_at", "issued_at", "expires_at"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version <= 0:
            raise ValueError("schema_version must be a positive integer")
        if not self.generated_at <= self.issued_at < self.expires_at:
            raise ValueError("lease timestamps must satisfy generated_at <= issued_at < expires_at")

    def canonical_data(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "actor_id": self.actor_id,
            "client_id": self.client_id,
            "profile_id": self.profile_id,
            "tab_id": self.tab_id,
            "quest_id": self.quest_id,
            "step_fingerprint": self.step_fingerprint,
            "revision": self.revision,
            "generated_at": self.generated_at,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_data(), prefix="route_lease")

    def coherence_reason(
        self,
        *,
        now: float,
        actor_id: str,
        client_id: str,
        profile_id: str,
        tab_id: str,
        quest_id: str,
        step_fingerprint: str,
        revision: str,
        max_snapshot_age_s: float,
    ) -> str | None:
        """Return ``None`` only for one fresh, exact binding."""

        try:
            now_value = _finite(now, "now")
            age_limit = _finite(max_snapshot_age_s, "max_snapshot_age_s")
        except (TypeError, ValueError):
            return "lease_freshness_invalid"
        if age_limit < 0:
            return "lease_freshness_invalid"
        expected = (
            actor_id, client_id, profile_id, tab_id, quest_id, step_fingerprint, revision
        )
        if any(not isinstance(value, str) or not value.strip() for value in expected):
            return "lease_expected_identity_invalid"
        if now_value < self.issued_at or now_value > self.expires_at:
            return "lease_expired_or_not_yet_valid"
        if now_value - self.generated_at > age_limit:
            return "lease_snapshot_stale"
        if (self.actor_id, self.client_id, self.profile_id, self.tab_id) != expected[:4]:
            return "lease_actor_identity_mismatch"
        if self.quest_id != quest_id or self.step_fingerprint != step_fingerprint:
            return "lease_step_identity_mismatch"
        if self.revision != revision:
            return "lease_revision_mismatch"
        return None

    def is_coherent(self, **expected: object) -> bool:
        return self.coherence_reason(**expected) is None  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class MutationIntent:
    """Pure mutation description to be adapted to ``ActionRequest`` later."""

    action_type: str
    requirement_id: str
    step_fingerprint: str
    idempotency_key: str
    route_lease: RouteLease | None = None
    cycle_id: int = 0
    battle_id: int | None = None
    dry_run: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("action_type", "requirement_id", "step_fingerprint", "idempotency_key"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if isinstance(self.cycle_id, bool) or not isinstance(self.cycle_id, int) or self.cycle_id < 0:
            raise ValueError("cycle_id must be a non-negative integer")
        if self.battle_id is not None and (
            isinstance(self.battle_id, bool) or not isinstance(self.battle_id, int) or self.battle_id < 0
        ):
            raise ValueError("battle_id must be a non-negative integer or None")
        if not isinstance(self.dry_run, bool):
            raise TypeError("dry_run must be boolean")
        if self.route_lease is not None and not isinstance(self.route_lease, RouteLease):
            raise TypeError("route_lease must be RouteLease or None")
        canonical_metadata = _frozen_value(self.metadata)
        if not isinstance(canonical_metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", canonical_metadata)

    def canonical_data(self) -> dict[str, object]:
        return {
            "action_type": self.action_type,
            "requirement_id": self.requirement_id,
            "step_fingerprint": self.step_fingerprint,
            "idempotency_key": self.idempotency_key,
            "route_lease": None if self.route_lease is None else self.route_lease.canonical_data(),
            "cycle_id": self.cycle_id,
            "battle_id": self.battle_id,
            "dry_run": self.dry_run,
            "metadata": self.metadata,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_data(), prefix="mutation_intent")
