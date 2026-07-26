"""Pure authoritative quest-inventory evidence adapter.

The adapter binds already-compiled ``Acquire`` requirements to a complete
quest-inventory observation.  It deliberately does not parse quest text and
does not import a controller, runtime, or action boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
import time
from typing import Mapping, Sequence

from .quest_evidence import (
    EvidenceEnvelope,
    EvidenceSourceKind,
    FactKind,
    SatisfiedFact,
)
from .quest_inventory_guard import QuestInventoryGuardResult
from .quest_plan_model import Acquire, QuestPlan, iter_requirements


class QuestInventoryEvidenceStatus(str, Enum):
    READY = "ready"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class QuestInventoryEvidenceResult:
    status: QuestInventoryEvidenceStatus
    reason: str
    envelopes: tuple[EvidenceEnvelope, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, QuestInventoryEvidenceStatus):
            raise TypeError("status must be a QuestInventoryEvidenceStatus")
        if not str(self.reason).strip():
            raise ValueError("reason must be non-empty")
        if self.status is QuestInventoryEvidenceStatus.UNSAFE and self.envelopes:
            raise ValueError("unsafe result cannot contain evidence")


def merge_authoritative_inventory_evidence(
    previous: tuple[EvidenceEnvelope, ...],
    observed: tuple[EvidenceEnvelope, ...],
    *,
    plan: QuestPlan,
) -> tuple[EvidenceEnvelope, ...]:
    """Merge one complete inventory observation without inventing history.

    Absence from the new complete snapshot retracts an earlier inventory fact.
    For a requirement that remains satisfied, the first causal observation is
    retained; newly satisfied requirements receive the new snapshot revision.
    """

    if not isinstance(previous, tuple) or not isinstance(observed, tuple):
        raise TypeError("inventory evidence collections must be tuples")
    if not isinstance(plan, QuestPlan):
        raise TypeError("plan must be a QuestPlan")
    allowed = {
        requirement.requirement_id
        for requirement in iter_requirements(plan.graph.root)
        if isinstance(requirement, Acquire)
    }
    for envelope in (*previous, *observed):
        if not isinstance(envelope, EvidenceEnvelope):
            raise TypeError("inventory evidence must contain EvidenceEnvelope values")
        if (
            envelope.requirement_id not in allowed
            or envelope.quest_id != plan.quest_id
            or envelope.quest_title != plan.quest_title
            or envelope.plan_fingerprint != plan.fingerprint
            or envelope.source_kind is not EvidenceSourceKind.INVENTORY
        ):
            raise ValueError("inventory evidence is foreign or unbound")
    current = {item.requirement_id: item for item in observed}
    if len(current) != len(observed):
        raise ValueError("observed inventory evidence is ambiguous")
    earlier = {item.requirement_id: item for item in previous}
    if len(earlier) != len(previous):
        raise ValueError("previous inventory evidence is ambiguous")
    merged = {
        requirement_id: earlier.get(requirement_id, envelope)
        for requirement_id, envelope in current.items()
    }
    return tuple(merged[key] for key in sorted(merged))


def build_quest_inventory_evidence(
    plan: QuestPlan,
    snapshot: object,
    guard: QuestInventoryGuardResult,
    *,
    client_id: str,
    profile_id: str,
    tab_id: str,
    causal_baseline: str,
    freshness_seconds: float = 30.0,
    now: float | None = None,
) -> QuestInventoryEvidenceResult:
    """Return requirement-scoped evidence from one authoritative snapshot."""

    if not isinstance(plan, QuestPlan):
        raise TypeError("plan must be a QuestPlan")
    if not isinstance(guard, QuestInventoryGuardResult):
        raise TypeError("guard must be a QuestInventoryGuardResult")
    identity = {
        "clientId": _required_text(client_id),
        "profileId": _required_text(profile_id),
        "tabId": _required_text(tab_id),
    }
    baseline = _required_text(causal_baseline)
    if freshness_seconds <= 0:
        raise ValueError("freshness_seconds must be positive")
    if not isinstance(snapshot, Mapping):
        return _unsafe("inventory_snapshot_invalid")
    if snapshot.get("ok") is not True:
        return _unsafe("inventory_snapshot_not_ok")
    if snapshot.get("category") != "quest" or snapshot.get("categoryConfirmed") is not True:
        return _unsafe("quest_inventory_category_unconfirmed")
    if snapshot.get("truncated") is not False:
        return _unsafe("quest_inventory_partial")
    for key, expected in identity.items():
        if _optional_text(snapshot.get(key)) != expected:
            return _unsafe("inventory_actor_identity_mismatch")
    if _optional_text(snapshot.get("causalBaseline")) != baseline:
        return _unsafe("inventory_causal_baseline_mismatch")

    snapshot_id = _optional_text(snapshot.get("snapshotId"))
    generated_at = _epoch_seconds(snapshot.get("generatedAt"))
    revision = snapshot.get("revision")
    if not snapshot_id or generated_at is None:
        return _unsafe("inventory_provenance_missing")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return _unsafe("inventory_revision_invalid")
    current = time.time() if now is None else float(now)
    if generated_at > current or current - generated_at > freshness_seconds:
        return _unsafe("inventory_snapshot_stale")
    if not guard.confirmed:
        return _unsafe("inventory_guard_unconfirmed")

    parsed_items = _inventory_items(snapshot)
    if parsed_items is None:
        return _unsafe("inventory_items_invalid")
    collected = _collected_counts(guard.collected)
    if collected is None:
        return _unsafe("inventory_guard_collected_invalid")
    for name, count in collected.items():
        if parsed_items.get(name) != count:
            return _unsafe("inventory_guard_snapshot_mismatch")

    envelopes: list[EvidenceEnvelope] = []
    for requirement in iter_requirements(plan.graph.root):
        if not isinstance(requirement, Acquire):
            continue
        candidates = tuple(
            name for name in parsed_items if _item_matches_requirement(name, requirement.item)
        )
        if len(candidates) > 1:
            return _unsafe("quest_inventory_item_binding_ambiguous")
        if not candidates:
            continue
        item_name = candidates[0]
        # The sealed snapshot is authoritative for all compiled Acquire leaves;
        # the legacy guard is an additional consistency check, not the source
        # of truth for capabilities it does not yet parse (for example leaves).
        observed = parsed_items.get(item_name)
        if observed is None or observed < requirement.count:
            continue
        envelopes.append(EvidenceEnvelope(
            quest_id=plan.quest_id,
            quest_title=plan.quest_title,
            plan_fingerprint=plan.fingerprint,
            step_fingerprint=requirement.step_id,
            requirement_id=requirement.requirement_id,
            source_kind=EvidenceSourceKind.INVENTORY,
            snapshot_id=snapshot_id,
            revision=revision,
            client_id=identity["clientId"],
            profile_id=identity["profileId"],
            tab_id=identity["tabId"],
            generated_at=generated_at,
            freshness_seconds=float(freshness_seconds),
            complete=True,
            causal_baseline=baseline,
            fact=SatisfiedFact(
                FactKind.COUNT_AT_LEAST, requirement.item, observed, requirement.count,
            ),
            payload={"category": "quest", "inventory_item": item_name},
        ))
    return QuestInventoryEvidenceResult(
        QuestInventoryEvidenceStatus.READY,
        "inventory_evidence_emitted" if envelopes else "no_satisfied_acquire_requirements",
        tuple(envelopes),
    )


def _inventory_items(snapshot: Mapping[object, object]) -> dict[str, int] | None:
    raw_items = snapshot.get("items", snapshot.get("sample"))
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        return None
    result: dict[str, int] = {}
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            return None
        name = _optional_text(raw.get("artAltTitle") or raw.get("name"))
        count = raw.get("count")
        if not name or isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None
        if name in result:
            return None
        result[name] = count
    return result


def _collected_counts(values: object) -> dict[str, int] | None:
    if not isinstance(values, tuple):
        return None
    result: dict[str, int] = {}
    for value in values:
        if not isinstance(value, tuple) or len(value) != 2:
            return None
        name, count = value
        name = _optional_text(name)
        if not name or isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None
        if name in result:
            return None
        result[name] = count
    return result


def _item_matches_requirement(item_name: str, requirement_name: str) -> bool:
    item_tokens = set(_tokens(item_name))
    requirement_tokens = {
        token for token in _tokens(requirement_name) if not token.startswith("пузыр")
    }
    if not item_tokens or not requirement_tokens:
        return False
    return all(any(_same_stem(required, item) for item in item_tokens) for required in requirement_tokens)


def quest_inventory_item_matches(item_name: str, requirement_name: str) -> bool:
    """Return the same deterministic lexical binding used by the adapter."""

    return _item_matches_requirement(item_name, requirement_name)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        _stem(token)
        for token in re.findall(r"[а-яa-z0-9]+", value.casefold().replace("ё", "е"))
        if len(token) >= 3
    )


def _stem(value: str) -> str:
    for suffix in ("иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими", "ов", "ев", "ей", "ий", "ый", "их", "ых", "ая", "яя", "ое", "ее", "ие", "ые", "а", "я", "ы", "и", "у", "ю", "ом", "ем"):
        if value.endswith(suffix) and len(value) - len(suffix) >= 3:
            return value[:-len(suffix)].rstrip("ьъ")
    return value.rstrip("ьъ")


def _same_stem(left: str, right: str) -> bool:
    return left == right


def _epoch_seconds(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _required_text(value: object) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError("identity and baseline values must be non-empty")
    return result


def _optional_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unsafe(reason: str) -> QuestInventoryEvidenceResult:
    return QuestInventoryEvidenceResult(QuestInventoryEvidenceStatus.UNSAFE, reason)


__all__ = [
    "QuestInventoryEvidenceResult",
    "QuestInventoryEvidenceStatus",
    "build_quest_inventory_evidence",
    "merge_authoritative_inventory_evidence",
    "quest_inventory_item_matches",
]
