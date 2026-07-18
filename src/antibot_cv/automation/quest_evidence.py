"""Restart-safe typed evidence envelopes for semantic quest requirements."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import time
from typing import Any, Mapping


class EvidenceSourceKind(str, Enum):
    ACTIVE_QUEST = "active_quest"
    INVENTORY = "inventory"
    COMBAT = "combat"
    AREA_OBJECT = "area_object"
    GATHERING = "gathering"
    PURCHASE = "purchase"
    LOCATION = "location"
    NPC_DIALOG = "npc_dialog"
    TURN_IN = "turn_in"


class FactKind(str, Enum):
    COUNT_AT_LEAST = "count_at_least"
    VISITED = "visited"
    INTERACTED = "interacted"
    TURNED_IN = "turned_in"


@dataclass(frozen=True, slots=True)
class SatisfiedFact:
    kind: FactKind
    subject: str
    observed: int | bool
    required: int | bool

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FactKind):
            raise TypeError("kind must be a FactKind")
        if not str(self.subject).strip():
            raise ValueError("subject must be non-empty")
        object.__setattr__(self, "subject", str(self.subject).strip())
        if type(self.observed) is not type(self.required) or not isinstance(self.observed, (int, bool)):
            raise TypeError("observed and required must have the same int or bool type")
        if isinstance(self.observed, int) and not isinstance(self.observed, bool) and (self.observed < 0 or self.required < 0):
            raise ValueError("counts must be non-negative")

    @property
    def satisfied(self) -> bool:
        if isinstance(self.required, bool):
            return self.observed is self.required is True
        return self.observed >= self.required


def _text(value: object, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must be non-empty")
    return result


def _finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return result


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise TypeError("payload must contain only JSON-compatible finite values")


@dataclass(frozen=True, slots=True)
class EvidenceEnvelope:
    quest_id: str
    quest_title: str
    plan_fingerprint: str
    step_fingerprint: str
    requirement_id: str
    source_kind: EvidenceSourceKind
    snapshot_id: str
    revision: int
    client_id: str
    profile_id: str
    tab_id: str
    generated_at: float
    freshness_seconds: float
    complete: bool
    causal_baseline: str
    fact: SatisfiedFact
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        for name in ("quest_id", "quest_title", "plan_fingerprint", "step_fingerprint", "requirement_id", "snapshot_id", "client_id", "profile_id", "tab_id", "causal_baseline"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.source_kind, EvidenceSourceKind):
            raise TypeError("source_kind must be an EvidenceSourceKind")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        object.__setattr__(self, "generated_at", _finite(self.generated_at, "generated_at"))
        object.__setattr__(self, "freshness_seconds", _finite(self.freshness_seconds, "freshness_seconds"))
        if not isinstance(self.complete, bool):
            raise TypeError("complete must be boolean")
        if not isinstance(self.fact, SatisfiedFact):
            raise TypeError("fact must be a SatisfiedFact")
        object.__setattr__(self, "payload", _json_value(dict(self.payload)))

    def validation_errors(self, *, now: float | None = None) -> tuple[str, ...]:
        current = time.time() if now is None else _finite(now, "now")
        errors: list[str] = []
        if not self.complete:
            errors.append("evidence_incomplete")
        if not self.fact.satisfied:
            errors.append("fact_not_satisfied")
        if self.generated_at > current:
            errors.append("generated_in_future")
        elif current - self.generated_at > self.freshness_seconds:
            errors.append("evidence_stale")
        return tuple(errors)

    def is_valid(self, *, now: float | None = None) -> bool:
        return not self.validation_errors(now=now)

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "causal_baseline": self.causal_baseline, "client_id": self.client_id,
            "complete": self.complete, "fact": {"kind": self.fact.kind.value, "observed": self.fact.observed, "required": self.fact.required, "subject": self.fact.subject},
            "freshness_seconds": self.freshness_seconds, "generated_at": self.generated_at,
            "payload": self.payload, "plan_fingerprint": self.plan_fingerprint,
            "profile_id": self.profile_id, "quest_id": self.quest_id,
            "quest_title": self.quest_title, "requirement_id": self.requirement_id,
            "revision": self.revision, "snapshot_id": self.snapshot_id,
            "source_kind": self.source_kind.value, "step_fingerprint": self.step_fingerprint,
            "tab_id": self.tab_id,
        }

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_canonical_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvidenceEnvelope":
        if not isinstance(raw, Mapping):
            raise TypeError("evidence must be a mapping")
        fact = raw.get("fact")
        if not isinstance(fact, Mapping):
            raise ValueError("fact must be a mapping")
        return cls(
            quest_id=raw["quest_id"], quest_title=raw["quest_title"],
            plan_fingerprint=raw["plan_fingerprint"], step_fingerprint=raw["step_fingerprint"],
            requirement_id=raw["requirement_id"], source_kind=EvidenceSourceKind(raw["source_kind"]),
            snapshot_id=raw["snapshot_id"], revision=raw["revision"], client_id=raw["client_id"],
            profile_id=raw["profile_id"], tab_id=raw["tab_id"], generated_at=raw["generated_at"],
            freshness_seconds=raw["freshness_seconds"], complete=raw["complete"],
            causal_baseline=raw["causal_baseline"],
            fact=SatisfiedFact(FactKind(fact["kind"]), fact["subject"], fact["observed"], fact["required"]),
            payload=raw.get("payload", {}),
        )

    @classmethod
    def from_json(cls, value: str) -> "EvidenceEnvelope":
        raw = json.loads(value)
        if not isinstance(raw, dict):
            raise ValueError("evidence JSON root must be an object")
        return cls.from_dict(raw)


def validate_evidence(envelope: EvidenceEnvelope, *, now: float | None = None) -> tuple[str, ...]:
    return envelope.validation_errors(now=now)


__all__ = ["EvidenceEnvelope", "EvidenceSourceKind", "FactKind", "SatisfiedFact", "validate_evidence"]
