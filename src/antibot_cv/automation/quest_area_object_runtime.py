"""Bounded coordinator state for illustrated quest objects on area pages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from src.antibot_cv.automation.area_object_activity import (
    AreaObjectPlan,
    AreaObjectProgress,
    AreaObjectRequirement,
    area_object_progress,
)
from src.antibot_cv.automation.runtime_helpers import same_location_name


class AreaObjectPhase(str, Enum):
    ROUTE = "route"
    OBSERVE = "observe"
    INSPECT = "inspect"


@dataclass
class PendingAreaObjectQuest:
    plan: AreaObjectPlan
    requirement: AreaObjectRequirement
    phase: AreaObjectPhase
    candidates: tuple[dict[str, object], ...] = ()
    candidate_index: int = 0


class QuestAreaObjectRuntime:
    """Keeps one bounded, exact object-search pass in controller memory.

    Every click is followed by a fresh quest-inventory observation.  The
    runtime never guesses success from popup text or from a click result.
    """

    def __init__(self) -> None:
        self.pending: PendingAreaObjectQuest | None = None

    def begin(self, plan: AreaObjectPlan, inventory_items: object) -> PendingAreaObjectQuest | None:
        progress = area_object_progress(plan, inventory_items)
        if progress.complete or progress.next_requirement is None:
            self.pending = None
            return None
        self.pending = PendingAreaObjectQuest(plan, progress.next_requirement, AreaObjectPhase.ROUTE)
        return self.pending

    def mark_route_arrived(self, location: object) -> PendingAreaObjectQuest:
        pending = self._require_pending()
        if not same_location_name(str(location or ""), pending.requirement.location):
            raise RuntimeError("area_object_route_arrival_mismatch")
        pending.phase = AreaObjectPhase.OBSERVE
        return pending

    def accept_snapshot(self, snapshot: object) -> PendingAreaObjectQuest:
        pending = self._require_pending()
        if pending.phase is not AreaObjectPhase.OBSERVE:
            raise RuntimeError("area_object_snapshot_phase_invalid")
        if not isinstance(snapshot, Mapping) or snapshot.get("ok") is not True:
            raise RuntimeError("area_object_snapshot_invalid")
        raw = snapshot.get("candidates")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise RuntimeError("area_object_candidates_invalid")
        candidates: list[dict[str, object]] = []
        for item in raw:
            if not isinstance(item, Mapping) or item.get("requiresConfirmation") is True:
                continue
            candidate_id = item.get("candidateId")
            fingerprint = item.get("fingerprint")
            if not isinstance(candidate_id, str) or not candidate_id or not isinstance(fingerprint, str) or not fingerprint:
                continue
            candidates.append({"candidateId": candidate_id, "fingerprint": fingerprint})
        if not candidates:
            raise RuntimeError("area_object_no_safe_candidates")
        pending.candidates = tuple(candidates[:24])
        pending.candidate_index = 0
        pending.phase = AreaObjectPhase.INSPECT
        return pending

    def next_click_payload(self, snapshot_id: object) -> dict[str, str]:
        pending = self._require_pending()
        if pending.phase is not AreaObjectPhase.INSPECT or pending.candidate_index >= len(pending.candidates):
            raise RuntimeError("area_object_candidates_exhausted")
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise RuntimeError("area_object_snapshot_id_invalid")
        candidate = pending.candidates[pending.candidate_index]
        return {
            "expectedSnapshotId": snapshot_id.strip(),
            "candidateId": str(candidate["candidateId"]),
            "expectedFingerprint": str(candidate["fingerprint"]),
        }

    def reconcile_inventory(self, items: object) -> AreaObjectProgress:
        pending = self._require_pending()
        progress = area_object_progress(pending.plan, items)
        if progress.complete:
            self.pending = None
            return progress
        if progress.next_requirement is None:
            raise RuntimeError("area_object_progress_invalid")
        if progress.next_requirement.location != pending.requirement.location:
            self.pending = PendingAreaObjectQuest(pending.plan, progress.next_requirement, AreaObjectPhase.ROUTE)
            return progress
        pending.requirement = progress.next_requirement
        pending.candidate_index += 1
        if pending.candidate_index >= len(pending.candidates):
            raise RuntimeError("area_object_candidates_exhausted")
        pending.phase = AreaObjectPhase.OBSERVE
        return progress

    def _require_pending(self) -> PendingAreaObjectQuest:
        if self.pending is None:
            raise RuntimeError("area_object_pending_missing")
        return self.pending
