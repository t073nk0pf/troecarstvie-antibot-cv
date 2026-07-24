"""Typed semantic binding for an exact quest combat target."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.antibot_cv.automation.runtime_helpers import normalize_phrase


@dataclass(frozen=True, slots=True)
class QuestCombatBinding:
    target: str
    requirement_id: str
    plan_fingerprint: str

    def __post_init__(self) -> None:
        if not normalize_phrase(self.target):
            raise ValueError("combat binding target is missing")
        if not self.requirement_id.startswith("req_"):
            raise ValueError("combat binding requirement identity is invalid")
        if len(self.plan_fingerprint) != 64:
            raise ValueError("combat binding plan fingerprint is invalid")

    def metadata(self) -> dict[str, object]:
        return {
            "target": self.target,
            "requirement_id": self.requirement_id,
            "plan_fingerprint": self.plan_fingerprint,
        }


class QuestCombatAdmissionStatus(str, Enum):
    ACTIONABLE = "actionable"
    WAIT = "wait"
    BLOCKED_UNSAFE = "blocked_unsafe"


@dataclass(frozen=True, slots=True)
class QuestCombatAdmission:
    status: QuestCombatAdmissionStatus
    reason: str
    binding: QuestCombatBinding | None = None

    @property
    def attack_allowed(self) -> bool:
        return self.status is QuestCombatAdmissionStatus.ACTIONABLE and self.binding is not None


def bind_semantic_combat_target(
    target: str,
    *,
    requirement_id: str,
    plan_fingerprint: str,
    legacy_names: tuple[str, ...],
    legacy_specs: tuple[tuple[str, int], ...],
) -> QuestCombatBinding | None:
    expected = normalize_phrase(target)
    names = {normalize_phrase(name) for name in legacy_names if normalize_phrase(name)}
    specs = {normalize_phrase(name) for name, _level in legacy_specs if normalize_phrase(name)}
    if len(names) != 1 or names != {expected}:
        return None
    if specs and specs != {expected}:
        return None
    return QuestCombatBinding(target, requirement_id, plan_fingerprint)


def request_matches_combat_binding(metadata: object) -> bool:
    if not isinstance(metadata, dict) or metadata.get("semantic_authoritative") is not True:
        return False
    raw = metadata.get("semantic_binding")
    if not isinstance(raw, dict) or set(raw) != {"target", "requirement_id", "plan_fingerprint"}:
        return False
    try:
        binding = QuestCombatBinding(
            str(raw["target"]), str(raw["requirement_id"]), str(raw["plan_fingerprint"]),
        )
    except (TypeError, ValueError):
        return False
    names = metadata.get("names")
    return (
        isinstance(names, list)
        and len(names) == 1
        and normalize_phrase(names[0]) == normalize_phrase(binding.target)
    )


__all__ = [
    "QuestCombatAdmission", "QuestCombatAdmissionStatus", "QuestCombatBinding",
    "bind_semantic_combat_target", "request_matches_combat_binding",
]
