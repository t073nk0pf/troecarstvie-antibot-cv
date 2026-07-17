from __future__ import annotations

from dataclasses import dataclass

from src.antibot_cv.automation.quest_director_policy import QuestRef


@dataclass(frozen=True)
class QuestChainLease:
    quest_id: str
    quest_title: str
    selected_revision: int
    current_fingerprint: str
    visited_fingerprints: tuple[str, ...]
    completed_steps: int = 0
    accepted_ref: QuestRef | None = None
    turn_in_ref_fingerprint: str | None = None
    awaiting_executor_reason: str | None = None
    awaiting_executor_capability_version: str | None = None
    legacy_fingerprint_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingTurnInCompletion:
    quest_id: str
    quest_title: str
    completed_fingerprint: str
    active_catalog_revision: int


@dataclass(frozen=True)
class QuarantinedQuest:
    quest_id: str
    quest_title: str
    fingerprint: str
    reason: str
    capability_version: str
    recorded_at: float
