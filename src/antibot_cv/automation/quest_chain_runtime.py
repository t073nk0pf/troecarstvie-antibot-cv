from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping, Sequence

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_objective_runtime import (
    ObjectiveSelectionStatus,
    QuestObjective,
    collect_monster_hunt_objectives,
    quest_step_fingerprint,
)


class ChainRefreshState(str, Enum):
    SAME_STEP = "same_step"
    ADVANCED = "advanced"
    EXECUTOR_REQUIRED = "executor_required"
    REMOVED_UNVERIFIED = "removed_unverified"
    LOOP_UNSAFE = "loop_unsafe"
    REGRESSED_UNSAFE = "regressed_unsafe"


@dataclass(frozen=True)
class QuestChainLease:
    quest_id: str
    quest_title: str
    selected_revision: int
    current_fingerprint: str
    visited_fingerprints: tuple[str, ...]
    completed_steps: int = 0


@dataclass(frozen=True)
class ChainRefreshResult:
    state: ChainRefreshState
    lease: QuestChainLease
    objective: QuestObjective | None
    reason: str


class QuestChainRuntime:
    """Keep one quest chain pinned until explicit terminal evidence releases it."""

    def __init__(self) -> None:
        self.lease: QuestChainLease | None = None

    def pin(self, objective: QuestObjective, *, revision: int) -> QuestChainLease:
        if self.lease is not None and self.lease.quest_id != objective.quest_id:
            raise RuntimeError("another quest chain is already pinned")
        if self.lease is None:
            self.lease = QuestChainLease(
                objective.quest_id,
                objective.quest_title,
                revision,
                objective.fingerprint,
                (objective.fingerprint,),
            )
        return self.lease

    def checkpoint(self) -> dict[str, object] | None:
        """Return a JSON-safe lease snapshot for controller checkpoints."""

        lease = self.lease
        if lease is None:
            return None
        return {
            "quest_id": lease.quest_id,
            "quest_title": lease.quest_title,
            "selected_revision": lease.selected_revision,
            "current_fingerprint": lease.current_fingerprint,
            "visited_fingerprints": list(lease.visited_fingerprints),
            "completed_steps": lease.completed_steps,
        }

    def restore(self, payload: Mapping[str, object]) -> QuestChainLease:
        """Restore a validated lease without silently accepting partial state."""

        quest_id = str(payload.get("quest_id") or "").strip()
        quest_title = str(payload.get("quest_title") or "").strip()
        current = str(payload.get("current_fingerprint") or "").strip()
        revision = payload.get("selected_revision")
        completed_steps = payload.get("completed_steps")
        visited_raw = payload.get("visited_fingerprints")
        if (
            not quest_id.isdecimal()
            or int(quest_id) <= 0
            or not quest_title
            or not current
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision <= 0
            or isinstance(completed_steps, bool)
            or not isinstance(completed_steps, int)
            or completed_steps < 0
            or not isinstance(visited_raw, (list, tuple))
        ):
            raise ValueError("invalid quest chain checkpoint")
        visited = tuple(str(value or "").strip() for value in visited_raw)
        if not visited or any(not value for value in visited) or len(set(visited)) != len(visited):
            raise ValueError("invalid quest chain fingerprint history")
        if current != visited[-1]:
            raise ValueError("current quest chain fingerprint must be the latest visited step")
        self.lease = QuestChainLease(
            quest_id,
            quest_title,
            revision,
            current,
            visited,
            completed_steps,
        )
        return self.lease

    def reconcile(
        self,
        entries: Sequence[ActiveQuestEntry],
        *,
        current_level_cap: int,
    ) -> ChainRefreshResult:
        lease = self.lease
        if lease is None:
            raise RuntimeError("quest chain is not pinned")
        matches = [entry for entry in entries if entry.id == lease.quest_id]
        if len(matches) != 1:
            return ChainRefreshResult(
                ChainRefreshState.REMOVED_UNVERIFIED,
                lease,
                None,
                "pinned_quest_missing_without_terminal_evidence",
            )
        entry = matches[0]
        if entry.title != lease.quest_title:
            return ChainRefreshResult(
                ChainRefreshState.REGRESSED_UNSAFE,
                lease,
                None,
                "pinned_quest_identity_changed",
            )
        fingerprint, reason = quest_step_fingerprint(entry)
        if fingerprint is None:
            return ChainRefreshResult(ChainRefreshState.REGRESSED_UNSAFE, lease, None, reason)
        collection = collect_monster_hunt_objectives(
            (entry,), current_level_cap=current_level_cap
        )
        if collection.status is ObjectiveSelectionStatus.UNSAFE:
            return ChainRefreshResult(
                ChainRefreshState.REGRESSED_UNSAFE,
                lease,
                None,
                f"pinned_quest_objective_unsafe:{collection.reason}",
            )
        objective = collection.objectives[0] if collection.objectives else None
        if fingerprint == lease.current_fingerprint:
            state = ChainRefreshState.SAME_STEP if objective else ChainRefreshState.EXECUTOR_REQUIRED
            return ChainRefreshResult(state, lease, objective, "pinned_quest_step_unchanged")
        if fingerprint in lease.visited_fingerprints:
            return ChainRefreshResult(
                ChainRefreshState.LOOP_UNSAFE,
                lease,
                objective,
                "pinned_quest_fingerprint_loop",
            )
        advanced = replace(
            lease,
            current_fingerprint=fingerprint,
            visited_fingerprints=(*lease.visited_fingerprints, fingerprint),
            completed_steps=lease.completed_steps + 1,
        )
        self.lease = advanced
        state = ChainRefreshState.ADVANCED if objective else ChainRefreshState.EXECUTOR_REQUIRED
        return ChainRefreshResult(state, advanced, objective, "pinned_quest_step_advanced")

    def release_completed(self, quest_id: str) -> None:
        if self.lease is None or self.lease.quest_id != quest_id:
            raise RuntimeError("completed quest does not match pinned chain")
        self.lease = None
