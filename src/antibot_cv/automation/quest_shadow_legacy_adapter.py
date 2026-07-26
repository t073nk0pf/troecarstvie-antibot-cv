"""Strict, action-free normalization of legacy quest decisions for shadow runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Mapping


class LegacyDecisionStatus(str, Enum):
    ACTIONABLE = "actionable"
    BLOCKED = "blocked"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"
    SATISFIED = "satisfied"


@dataclass(frozen=True, slots=True)
class LegacyDecisionEnvelope:
    quest_id: str
    status: LegacyDecisionStatus
    atom_kind: str | None = None
    target: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        quest_id = _required_text(self.quest_id, "quest_id")
        object.__setattr__(self, "quest_id", quest_id)
        if not isinstance(self.status, LegacyDecisionStatus):
            raise TypeError("status must be a LegacyDecisionStatus")
        atom_kind = _optional_text(self.atom_kind, "atom_kind")
        target = _optional_text(self.target, "target")
        reason = str(self.reason).strip()
        if self.status is LegacyDecisionStatus.ACTIONABLE:
            if atom_kind is None or target is None:
                raise ValueError("actionable legacy decision requires atom_kind and target")
        elif atom_kind is not None or target is not None:
            raise ValueError("non-actionable legacy decision cannot name an atom")
        object.__setattr__(self, "atom_kind", atom_kind.casefold() if atom_kind else None)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "reason", reason)

    def to_canonical_dict(self) -> dict[str, str | None]:
        return {
            "atom_kind": self.atom_kind,
            "quest_id": self.quest_id,
            "reason": self.reason,
            "status": self.status.value,
            "target": self.target,
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.to_canonical_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_legacy_decision(raw: Mapping[str, object]) -> LegacyDecisionEnvelope:
    """Normalize a deliberately small per-quest legacy decision map.

    The adapter does not call a legacy runtime or interpret objective text.  A
    caller must provide the decision that the existing path already produced.
    """

    if not isinstance(raw, Mapping):
        raise TypeError("legacy decision must be a mapping")
    allowed = {"quest_id", "status", "atom_kind", "target", "reason"}
    if any(not isinstance(key, str) for key in raw) or set(raw) - allowed:
        raise ValueError("legacy decision contains unsupported fields")
    try:
        status = LegacyDecisionStatus(raw.get("status"))
    except (TypeError, ValueError) as exc:
        raise ValueError("legacy decision status is invalid") from exc
    return LegacyDecisionEnvelope(
        quest_id=raw.get("quest_id", ""),
        status=status,
        atom_kind=raw.get("atom_kind"),
        target=raw.get("target"),
        reason=raw.get("reason", ""),
    )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string when supplied")
    return value.strip()


__all__ = [
    "LegacyDecisionEnvelope",
    "LegacyDecisionStatus",
    "normalize_legacy_decision",
]
