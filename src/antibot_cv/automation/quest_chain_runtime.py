from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import json
from pathlib import Path
import time
from typing import Mapping, Sequence

from src.antibot_cv.automation.checkpoint import checkpoint_lock, write_json_checkpoint
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_chain_state import (
    PendingTurnInCompletion,
    QuarantinedQuest,
    QuestChainLease,
)
from src.antibot_cv.automation.quest_chain_persistence import (
    restore_accepted_ref as _restore_accepted_ref,
    restore_quarantines as _restore_quarantines,
    restore_staged_ref as _restore_staged_ref,
    restore_turn_in_completion as _restore_turn_in_completion,
    serialize_quarantine as _serialize_quarantine,
    serialize_ref as _serialize_ref,
    serialize_turn_in_completion as _serialize_turn_in_completion,
    valid_capability as _valid_capability,
    valid_reason as _valid_reason,
    validate_authoritative_ref as _validate_authoritative_ref,
)
from src.antibot_cv.automation.quest_intake_quarantine import (
    IntakeQuarantine,
    intake_ref_fingerprint,
    make_intake_quarantine,
    restore_intake_quarantines,
    serialize_intake_quarantine,
)
from src.antibot_cv.automation.quest_objective_runtime import (
    ObjectiveSelectionStatus,
    QuestObjective,
    collect_monster_hunt_objectives,
    legacy_quest_step_fingerprint,
    quest_step_fingerprint,
)
from src.antibot_cv.automation.quest_objective_router import (
    ObjectiveRouteKind,
    ObjectiveRouteStatus,
    classify_objective,
)
from src.antibot_cv.automation.quest_catalog_navigation import (
    PendingCatalogNavigation,
    restore_pending_catalog_navigation,
    serialize_pending_catalog_navigation,
)
from src.antibot_cv.automation.quest_active_catalog_navigation import (
    PendingActiveCatalogNavigation,
    restore_pending_active_catalog_navigation,
    serialize_pending_active_catalog_navigation,
)
from src.antibot_cv.automation.quest_available_eligibility import (
    UnsupportedAvailableQuest,
    restore_unsupported_available_entries,
    serialize_unsupported_available,
)
from src.antibot_cv.automation.quest_npc_open_navigation import (
    PendingNpcOpen,
    LEGACY_NPC_OPEN_CAPABILITY,
    restore_pending_npc_open,
    serialize_pending_npc_open,
)
from src.antibot_cv.automation.quest_npc_action_journal import (
    NpcQuestActionKind,
    NpcQuestActionPhase,
    PendingNpcQuestAction,
    restore_pending_npc_action,
    serialize_pending_npc_action,
)
from src.antibot_cv.automation.quest_ordered_npc_handoff import (
    OrderedHandoffCursor,
    restore_ordered_handoff_cursor,
    serialize_ordered_handoff_cursor,
)


class ChainRefreshState(str, Enum):
    SAME_STEP = "same_step"
    ADVANCED = "advanced"
    EXECUTOR_REQUIRED = "executor_required"
    REMOVED_UNVERIFIED = "removed_unverified"
    LOOP_UNSAFE = "loop_unsafe"
    REGRESSED_UNSAFE = "regressed_unsafe"


class QuestChainCheckpointConflict(RuntimeError):
    """The checkpoint changed after this runtime loaded its baseline."""


@dataclass(frozen=True)
class ChainRefreshResult:
    state: ChainRefreshState
    lease: QuestChainLease
    objective: QuestObjective | None
    reason: str


def _npc_stage_matches_ref(stage: PendingNpcOpen, ref: QuestRef | None) -> bool:
    return bool(
        ref is not None
        and stage.quest_id == ref.id
        and stage.quest_title == ref.title
        and stage.quest_accept_ref == ref.accept_ref
        and stage.quest_catalog_page == ref.catalog_page
        and stage.location_name == ref.location
        and (stage.giver_name,) == ref.giver_names
        and stage.quest_fingerprint == intake_ref_fingerprint(ref)
    )


def _npc_action_matches(
    action: PendingNpcQuestAction, dialog: PendingNpcOpen | None, ref: QuestRef | None,
) -> bool:
    return bool(
        dialog is not None and ref is not None
        and action.quest_id == ref.id == dialog.quest_id
        and action.quest_title == ref.title == dialog.quest_title
        and action.quest_accept_ref == ref.accept_ref
        and action.quest_catalog_page == ref.catalog_page
        and action.quest_location == ref.location
        and action.giver_name == dialog.giver_name == ref.giver_names[0]
        and action.npc_id == dialog.npc_id
        and action.client_id == dialog.client_id
        and action.profile_id == dialog.profile_id
        and action.tab_id == dialog.tab_id
        and action.quest_opened == dialog.quest_opened
        and action.dialog_steps == dialog.dialog_steps
    )


class QuestChainRuntime:
    """Keep one quest chain pinned until explicit terminal evidence releases it."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        max_quarantines: int = 100,
        max_intake_quarantines: int = 100,
        max_unsupported_available: int = 500,
    ) -> None:
        self.lease: QuestChainLease | None = None
        self.pending_accepted_ref: QuestRef | None = None
        self.pending_turn_in_completion: PendingTurnInCompletion | None = None
        self.pending_turn_in_completion_restored = False
        self.pending_catalog_navigation: PendingCatalogNavigation | None = None
        self.pending_active_catalog_navigation: PendingActiveCatalogNavigation | None = None
        self.pending_npc_open: PendingNpcOpen | None = None
        self.pending_npc_dialog: PendingNpcOpen | None = None
        self.pending_npc_action: PendingNpcQuestAction | None = None
        self.ordered_handoff_cursor: OrderedHandoffCursor | None = None
        if not isinstance(max_unsupported_available, int) or isinstance(max_unsupported_available, bool) or max_unsupported_available <= 0:
            raise ValueError("max unsupported available must be positive")
        self.max_unsupported_available = max_unsupported_available
        self.unsupported_available_entries: tuple[UnsupportedAvailableQuest, ...] = ()
        if not isinstance(max_quarantines, int) or isinstance(max_quarantines, bool) or max_quarantines <= 0:
            raise ValueError("max quarantines must be positive")
        self.max_quarantines = max_quarantines
        self.quarantines: tuple[QuarantinedQuest, ...] = ()
        if (
            not isinstance(max_intake_quarantines, int)
            or isinstance(max_intake_quarantines, bool)
            or max_intake_quarantines <= 0
        ):
            raise ValueError("max intake quarantines must be positive")
        self.max_intake_quarantines = max_intake_quarantines
        self.intake_quarantines: tuple[IntakeQuarantine, ...] = ()
        self.state_path = Path(state_path) if state_path is not None else None
        self._disk_baseline = b""
        if self.state_path is not None:
            with checkpoint_lock(self.state_path):
                self._disk_baseline = (
                    self.state_path.read_bytes() if self.state_path.exists() else b""
                )
        if self._disk_baseline:
            try:
                payload = json.loads(self._disk_baseline)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid persisted quest chain state") from exc
            if not isinstance(payload, dict):
                raise ValueError("invalid persisted quest chain state")
            self.restore(payload)

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
            self._persist()
        return self.lease

    def pin_entry(self, entry: ActiveQuestEntry, *, revision: int) -> QuestChainLease:
        """Pin an active chain even when its current step needs another executor."""

        if not isinstance(entry, ActiveQuestEntry):
            raise ValueError("pinned quest entry is invalid")
        if self.lease is not None and self.lease.quest_id != entry.id:
            raise RuntimeError("another quest chain is already pinned")
        if self.lease is None:
            fingerprint, reason = quest_step_fingerprint(entry)
            if fingerprint is None:
                raise ValueError(reason)
            self.lease = QuestChainLease(
                entry.id,
                entry.title,
                revision,
                fingerprint,
                (fingerprint,),
            )
            self._persist()
        return self.lease

    def checkpoint(self) -> dict[str, object] | None:
        """Return a JSON-safe lease snapshot for controller checkpoints."""

        lease = self.lease
        if (
            lease is not None
            and lease.accepted_ref is None
            and self.pending_accepted_ref is not None
        ):
            raise RuntimeError("partial staged-active recovery cannot be checkpointed")
        if lease is None:
            payload: dict[str, object] = {}
            if self.pending_catalog_navigation is not None:
                payload["pending_catalog_navigation"] = serialize_pending_catalog_navigation(
                    self.pending_catalog_navigation
                )
            if self.pending_active_catalog_navigation is not None:
                payload["pending_active_catalog_navigation"] = serialize_pending_active_catalog_navigation(
                    self.pending_active_catalog_navigation
                )
            if self.pending_npc_open is not None:
                payload["pending_npc_open"] = serialize_pending_npc_open(self.pending_npc_open)
            if self.pending_npc_dialog is not None:
                payload["pending_npc_dialog"] = serialize_pending_npc_open(self.pending_npc_dialog)
            if self.pending_npc_action is not None:
                payload["pending_npc_action"] = serialize_pending_npc_action(self.pending_npc_action)
            if self.ordered_handoff_cursor is not None:
                payload["ordered_handoff_cursor"] = serialize_ordered_handoff_cursor(
                    self.ordered_handoff_cursor
                )
            if self.unsupported_available_entries:
                payload["unsupported_available_entries"] = [
                    serialize_unsupported_available(item)
                    for item in self.unsupported_available_entries
                ]
            if self.pending_accepted_ref is not None:
                payload["pending_accepted_ref"] = _serialize_ref(self.pending_accepted_ref)
            if self.quarantines:
                payload["quarantines"] = [_serialize_quarantine(item) for item in self.quarantines]
            if self.intake_quarantines:
                payload["intake_quarantines"] = [
                    serialize_intake_quarantine(item) for item in self.intake_quarantines
                ]
            return payload or None
        payload: dict[str, object] = {
            "fingerprint_schema": 2,
            "quest_id": lease.quest_id,
            "quest_title": lease.quest_title,
            "selected_revision": lease.selected_revision,
            "current_fingerprint": lease.current_fingerprint,
            "visited_fingerprints": list(lease.visited_fingerprints),
            "completed_steps": lease.completed_steps,
        }
        if self.pending_catalog_navigation is not None:
            payload["pending_catalog_navigation"] = serialize_pending_catalog_navigation(
                self.pending_catalog_navigation
            )
        if self.pending_active_catalog_navigation is not None:
            payload["pending_active_catalog_navigation"] = serialize_pending_active_catalog_navigation(
                self.pending_active_catalog_navigation
            )
        if self.pending_npc_open is not None:
            payload["pending_npc_open"] = serialize_pending_npc_open(self.pending_npc_open)
        if self.pending_npc_dialog is not None:
            payload["pending_npc_dialog"] = serialize_pending_npc_open(self.pending_npc_dialog)
        if self.pending_npc_action is not None:
            payload["pending_npc_action"] = serialize_pending_npc_action(self.pending_npc_action)
        if self.ordered_handoff_cursor is not None:
            payload["ordered_handoff_cursor"] = serialize_ordered_handoff_cursor(
                self.ordered_handoff_cursor
            )
        if self.unsupported_available_entries:
            payload["unsupported_available_entries"] = [
                serialize_unsupported_available(item)
                for item in self.unsupported_available_entries
            ]
        if lease.legacy_fingerprint_aliases:
            payload["legacy_fingerprint_aliases"] = list(
                lease.legacy_fingerprint_aliases
            )
        if lease.accepted_ref is not None:
            payload["accepted_ref"] = _serialize_ref(lease.accepted_ref)
            if lease.turn_in_ref_fingerprint is not None:
                payload["turn_in_ref_fingerprint"] = lease.turn_in_ref_fingerprint
        if lease.awaiting_executor_reason is not None:
            payload["awaiting_executor_reason"] = lease.awaiting_executor_reason
            payload["awaiting_executor_capability_version"] = (
                lease.awaiting_executor_capability_version
            )
        if self.pending_accepted_ref is not None:
            payload["pending_accepted_ref"] = _serialize_ref(self.pending_accepted_ref)
        if self.pending_turn_in_completion is not None:
            payload["pending_turn_in_completion"] = _serialize_turn_in_completion(
                self.pending_turn_in_completion
            )
        if self.quarantines:
            payload["quarantines"] = [_serialize_quarantine(item) for item in self.quarantines]
        if self.intake_quarantines:
            payload["intake_quarantines"] = [
                serialize_intake_quarantine(item) for item in self.intake_quarantines
            ]
        return payload

    def restore(self, payload: Mapping[str, object]) -> QuestChainLease | None:
        """Restore a validated lease without silently accepting partial state."""

        pending_raw = payload.get("pending_accepted_ref")
        restored_catalog_navigation = restore_pending_catalog_navigation(
            payload.get("pending_catalog_navigation")
        )
        restored_active_catalog_navigation = restore_pending_active_catalog_navigation(
            payload.get("pending_active_catalog_navigation")
        )
        restored_npc_open = restore_pending_npc_open(payload.get("pending_npc_open"))
        restored_npc_dialog = restore_pending_npc_open(payload.get("pending_npc_dialog"))
        restored_npc_action = restore_pending_npc_action(payload.get("pending_npc_action"))
        restored_ordered_handoff = restore_ordered_handoff_cursor(
            payload.get("ordered_handoff_cursor")
        )
        if restored_npc_open is not None and restored_npc_open.capability_version == LEGACY_NPC_OPEN_CAPABILITY:
            raise ValueError("legacy NPC recovery cannot be restored as pending open")
        if restored_npc_open is not None and restored_npc_dialog is not None:
            raise ValueError("NPC open and dialog stages conflict")
        if restored_npc_action is not None and (
            restored_npc_dialog is None or restored_npc_open is not None
        ):
            raise ValueError("NPC action journal requires durable dialog")
        if restored_catalog_navigation is not None and restored_active_catalog_navigation is not None:
            raise ValueError("available and active catalog navigation stages conflict")
        if (restored_catalog_navigation is not None or restored_active_catalog_navigation is not None) and (restored_npc_open or restored_npc_dialog):
            raise ValueError("catalog and NPC mutation stages conflict")
        if restored_ordered_handoff is not None and (
            restored_catalog_navigation is not None
            or restored_active_catalog_navigation is not None
            or restored_npc_open is not None
            or restored_npc_dialog is not None
            or restored_npc_action is not None
        ):
            raise ValueError("ordered handoff conflicts with another pending stage")
        restored_unsupported_available = restore_unsupported_available_entries(
            payload.get("unsupported_available_entries"),
            max_items=self.max_unsupported_available,
        )
        restored_quarantines = _restore_quarantines(
            payload.get("quarantines"),
            max_items=self.max_quarantines,
        )
        restored_intake_quarantines = restore_intake_quarantines(
            payload.get("intake_quarantines"),
            max_items=self.max_intake_quarantines,
        )
        if "quest_id" not in payload:
            if restored_ordered_handoff is not None:
                raise ValueError("ordered handoff cursor requires pinned lease")
            restored_pending_ref = _restore_staged_ref(pending_raw)
            restored_npc_stage = restored_npc_open or restored_npc_dialog
            if restored_npc_stage is not None and not _npc_stage_matches_ref(
                restored_npc_stage, restored_pending_ref,
            ):
                raise ValueError("pending NPC open conflicts with staged quest")
            if restored_npc_action is not None and not _npc_action_matches(
                restored_npc_action, restored_npc_dialog, restored_pending_ref,
            ):
                raise ValueError("pending NPC action conflicts with dialog")
            if (
                restored_pending_ref is None
                and not restored_quarantines
                and not restored_intake_quarantines
                and restored_catalog_navigation is None
                and restored_active_catalog_navigation is None
                and restored_npc_open is None
                and restored_npc_dialog is None
                and restored_npc_action is None
                and restored_ordered_handoff is None
                and not restored_unsupported_available
            ):
                raise ValueError("invalid quest chain checkpoint")
            if restored_pending_ref is not None and any(
                item.quest_id == restored_pending_ref.id
                for item in restored_quarantines
            ):
                raise ValueError("pending accepted reference conflicts with quarantine")
            if restored_pending_ref is not None and any(
                item.quest_id == restored_pending_ref.id
                for item in restored_intake_quarantines
            ):
                raise ValueError("pending accepted reference conflicts with intake quarantine")
            self.lease = None
            self.pending_accepted_ref = restored_pending_ref
            self.pending_turn_in_completion = None
            self.pending_turn_in_completion_restored = False
            self.pending_catalog_navigation = restored_catalog_navigation
            self.pending_active_catalog_navigation = restored_active_catalog_navigation
            self.pending_npc_open = restored_npc_open
            self.pending_npc_dialog = restored_npc_dialog
            self.pending_npc_action = restored_npc_action
            self.ordered_handoff_cursor = restored_ordered_handoff
            self.unsupported_available_entries = restored_unsupported_available
            self.quarantines = restored_quarantines
            self.intake_quarantines = restored_intake_quarantines
            return None

        quest_id = str(payload.get("quest_id") or "").strip()
        quest_title = str(payload.get("quest_title") or "").strip()
        current = str(payload.get("current_fingerprint") or "").strip()
        revision = payload.get("selected_revision")
        completed_steps = payload.get("completed_steps")
        visited_raw = payload.get("visited_fingerprints")
        accepted_ref = _restore_accepted_ref(payload.get("accepted_ref"), quest_id, quest_title)
        turn_in_ref_fingerprint = str(
            payload.get("turn_in_ref_fingerprint") or ""
        ).strip() or None
        awaiting_executor_reason = str(
            payload.get("awaiting_executor_reason") or ""
        ).strip() or None
        awaiting_executor_capability_version = str(
            payload.get("awaiting_executor_capability_version") or ""
        ).strip() or None
        restored_pending_ref = _restore_staged_ref(pending_raw)
        restored_npc_stage = restored_npc_open or restored_npc_dialog
        if restored_npc_stage is not None:
            raise ValueError("pending NPC acceptance conflicts with active lease")
        if restored_npc_action is not None:
            raise ValueError("pending NPC action conflicts with active lease")
        if restored_npc_stage is not None and not _npc_stage_matches_ref(
            restored_npc_stage, restored_pending_ref,
        ):
            raise ValueError("pending NPC open conflicts with staged quest")
        fingerprint_schema = payload.get("fingerprint_schema")
        aliases_raw = payload.get("legacy_fingerprint_aliases", ())
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
            or fingerprint_schema not in {None, 2}
            or not isinstance(aliases_raw, (list, tuple))
            or (
                awaiting_executor_reason is not None
                and not _valid_reason(awaiting_executor_reason)
            )
            or (awaiting_executor_reason is None) != (
                awaiting_executor_capability_version is None
            )
            or (
                awaiting_executor_capability_version is not None
                and not _valid_capability(awaiting_executor_capability_version)
            )
        ):
            raise ValueError("invalid quest chain checkpoint")
        visited = tuple(str(value or "").strip() for value in visited_raw)
        aliases = tuple(str(value or "").strip() for value in aliases_raw)
        if not visited or any(not value for value in visited) or len(set(visited)) != len(visited):
            raise ValueError("invalid quest chain fingerprint history")
        if any(not value for value in aliases) or len(set(aliases)) != len(aliases):
            raise ValueError("invalid legacy fingerprint aliases")
        if current != visited[-1]:
            raise ValueError("current quest chain fingerprint must be the latest visited step")
        if completed_steps != len(visited) - 1:
            raise ValueError("completed quest steps contradict fingerprint history")
        if turn_in_ref_fingerprint is not None and (
            accepted_ref is None or turn_in_ref_fingerprint not in visited
        ):
            raise ValueError("invalid turn-in reference fingerprint")
        pending_turn_in = _restore_turn_in_completion(
            payload.get("pending_turn_in_completion"),
            quest_id=quest_id,
            quest_title=quest_title,
        )
        if pending_turn_in is not None and pending_turn_in.completed_fingerprint != current:
            raise ValueError("pending turn-in completion does not match current step")
        new_lease = QuestChainLease(
            quest_id,
            quest_title,
            revision,
            current,
            visited,
            completed_steps,
            accepted_ref,
            turn_in_ref_fingerprint,
            awaiting_executor_reason,
            awaiting_executor_capability_version,
            aliases,
        )
        if restored_ordered_handoff is not None and (
            restored_ordered_handoff.quest_id != quest_id
            or restored_ordered_handoff.quest_title != quest_title
            or restored_ordered_handoff.fingerprint != current
            or restored_ordered_handoff.lease_fingerprint != current
            or restored_ordered_handoff.active_catalog_revision < revision
        ):
            raise ValueError("ordered handoff cursor conflicts with pinned lease")
        if restored_pending_ref is not None and restored_pending_ref != accepted_ref:
            raise ValueError("pending accepted reference conflicts with pinned lease")
        if any(
            item.quest_id == quest_id and item.fingerprint == current
            for item in restored_quarantines
        ):
            raise ValueError("quarantine conflicts with pinned lease")
        if any(item.quest_id == quest_id for item in restored_intake_quarantines):
            raise ValueError("intake quarantine conflicts with pinned lease")
        self.lease = new_lease
        self.pending_accepted_ref = restored_pending_ref
        self.pending_turn_in_completion = pending_turn_in
        self.pending_turn_in_completion_restored = pending_turn_in is not None
        self.pending_catalog_navigation = restored_catalog_navigation
        self.pending_active_catalog_navigation = restored_active_catalog_navigation
        self.pending_npc_open = restored_npc_open
        self.pending_npc_dialog = restored_npc_dialog
        self.pending_npc_action = restored_npc_action
        self.ordered_handoff_cursor = restored_ordered_handoff
        self.unsupported_available_entries = restored_unsupported_available
        self.quarantines = restored_quarantines
        self.intake_quarantines = restored_intake_quarantines
        return self.lease

    def stage_catalog_navigation(self, pending: PendingCatalogNavigation) -> None:
        """Durably stage one exact navigation intent before dispatch."""

        if self.pending_active_catalog_navigation is not None or self.pending_npc_open is not None or self.pending_npc_dialog is not None or self.pending_npc_action is not None or self.ordered_handoff_cursor is not None:
            raise RuntimeError("NPC mutation stage is already pending")
        if self.pending_catalog_navigation is not None and self.pending_catalog_navigation != pending:
            raise RuntimeError("another catalog navigation is pending")
        previous = self.pending_catalog_navigation
        self.pending_catalog_navigation = pending
        try:
            self._persist()
        except Exception:
            self.pending_catalog_navigation = previous
            raise

    def stage_active_catalog_navigation(self, pending: PendingActiveCatalogNavigation) -> None:
        """Durably stage one exact active-catalog navigation before dispatch."""

        if (
            self.pending_catalog_navigation is not None
            or self.pending_npc_open is not None or self.pending_npc_dialog is not None
            or self.pending_npc_action is not None or self.ordered_handoff_cursor is not None
        ):
            raise RuntimeError("another mutation stage is already pending")
        if self.pending_active_catalog_navigation not in {None, pending}:
            raise RuntimeError("another active catalog navigation is pending")
        previous = self.pending_active_catalog_navigation
        self.pending_active_catalog_navigation = pending
        try:
            self._persist()
        except QuestChainCheckpointConflict:
            # _persist restored the exact durable winner; do not overwrite it
            # with this runtime's stale pre-CAS value.
            raise
        except Exception:
            self.pending_active_catalog_navigation = previous
            raise

    def stage_npc_open(self, pending: PendingNpcOpen) -> None:
        if pending.capability_version == LEGACY_NPC_OPEN_CAPABILITY:
            raise RuntimeError("legacy NPC recovery cannot be staged as pending open")
        if self.pending_catalog_navigation is not None or self.pending_active_catalog_navigation is not None or self.pending_npc_dialog is not None or self.pending_npc_action is not None or self.ordered_handoff_cursor is not None:
            raise RuntimeError("another mutation stage is pending")
        if not _npc_stage_matches_ref(pending, self.pending_accepted_ref):
            raise RuntimeError("NPC open does not match staged quest")
        if self.pending_npc_open is not None and self.pending_npc_open != pending:
            raise RuntimeError("another NPC open is pending")
        previous = self.pending_npc_open
        self.pending_npc_open = pending
        try:
            self._persist()
        except Exception:
            self.pending_npc_open = previous
            raise

    def stage_ordered_handoff(self, cursor: OrderedHandoffCursor) -> None:
        """CAS-persist the route-only cursor before navigator mutation."""

        lease = self.lease
        if (
            lease is None
            or cursor.quest_id != lease.quest_id
            or cursor.quest_title != lease.quest_title
            or cursor.fingerprint != lease.current_fingerprint
            or cursor.lease_fingerprint != lease.current_fingerprint
            or self.pending_catalog_navigation is not None
            or self.pending_active_catalog_navigation is not None
            or self.pending_npc_open is not None
            or self.pending_npc_dialog is not None
            or self.pending_npc_action is not None
        ):
            raise RuntimeError("ordered handoff cursor conflicts with chain state")
        if self.ordered_handoff_cursor is not None:
            if self.ordered_handoff_cursor == cursor:
                return
            raise RuntimeError("another ordered handoff cursor is pending")
        self.ordered_handoff_cursor = cursor
        try:
            self._persist()
        except Exception:
            self.ordered_handoff_cursor = None
            raise

    def clear_npc_open(self, pending: PendingNpcOpen) -> None:
        if self.pending_npc_open != pending:
            raise RuntimeError("NPC open stage changed before clear")
        self.pending_npc_open = None
        try:
            self._persist()
        except Exception:
            self.pending_npc_open = pending
            raise

    def settle_npc_open(self, pending: PendingNpcOpen) -> None:
        """Atomically persist the causal transition into the NPC dialog phase."""

        if self.pending_npc_open != pending or self.pending_npc_dialog is not None:
            raise RuntimeError("NPC open stage changed before settle")
        self.pending_npc_open = None
        self.pending_npc_dialog = pending
        try:
            self._persist()
        except Exception:
            self.pending_npc_open = pending
            self.pending_npc_dialog = None
            raise

    def clear_npc_dialog(self, pending: PendingNpcOpen) -> None:
        if self.pending_npc_action is not None:
            raise RuntimeError("NPC action must settle before dialog clear")
        if self.pending_npc_dialog != pending:
            raise RuntimeError("NPC dialog stage changed before clear")
        self.pending_npc_dialog = None
        try:
            self._persist()
        except Exception:
            self.pending_npc_dialog = pending
            raise

    def record_npc_dialog_ambiguity(
        self, pending: PendingNpcOpen, *, snapshot_id: str, generated_at: float,
    ) -> PendingNpcOpen:
        if self.pending_npc_dialog != pending or self.pending_npc_action is not None:
            raise RuntimeError("NPC dialog changed before ambiguity watermark")
        if (
            not isinstance(snapshot_id, str) or not snapshot_id.startswith("npc-dialog-")
            or len(snapshot_id) > 120 or isinstance(generated_at, bool)
            or not isinstance(generated_at, (int, float))
            or not pending.issued_at < float(generated_at) < pending.deadline
        ):
            raise ValueError("NPC dialog ambiguity watermark is invalid")
        if pending.answer_ambiguity_generated_at is not None and generated_at <= pending.answer_ambiguity_generated_at:
            return pending
        updated = replace(
            pending, answer_ambiguity_snapshot_id=snapshot_id,
            answer_ambiguity_generated_at=float(generated_at),
        )
        self.pending_npc_dialog = updated
        try:
            self._persist()
        except Exception:
            self.pending_npc_dialog = pending
            raise
        return updated

    def stage_npc_quest_action(self, pending: PendingNpcQuestAction) -> None:
        if (
            self.pending_npc_open is not None or self.pending_catalog_navigation is not None
            or self.pending_active_catalog_navigation is not None
            or self.pending_npc_action is not None
            or pending.phase is not NpcQuestActionPhase.STAGED
            or not _npc_action_matches(pending, self.pending_npc_dialog, self.pending_accepted_ref)
        ):
            raise RuntimeError("NPC quest action conflicts with chain state")
        self.pending_npc_action = pending
        try:
            self._persist()
        except Exception:
            self.pending_npc_action = None
            raise

    def clear_npc_quest_action(self, pending: PendingNpcQuestAction) -> None:
        if self.pending_npc_action != pending or pending.phase is not NpcQuestActionPhase.STAGED:
            raise RuntimeError("NPC quest action changed before clear")
        self.pending_npc_action = None
        try:
            self._persist()
        except Exception:
            self.pending_npc_action = pending
            raise

    def mark_npc_quest_action_dispatched(self, pending: PendingNpcQuestAction) -> PendingNpcQuestAction:
        if self.pending_npc_action != pending or pending.phase is not NpcQuestActionPhase.STAGED:
            raise RuntimeError("NPC quest action changed before dispatch ACK")
        phase = (
            NpcQuestActionPhase.ACCEPT_VERIFY
            if pending.action is NpcQuestActionKind.ACCEPT else NpcQuestActionPhase.ACK_PENDING
        )
        updated = replace(pending, phase=phase)
        self.pending_npc_action = updated
        try:
            self._persist()
        except Exception:
            self.pending_npc_action = pending
            raise
        return updated

    def settle_npc_dialog_action(self, pending: PendingNpcQuestAction, *, semantic_fingerprint: str) -> PendingNpcOpen:
        dialog = self.pending_npc_dialog
        if self.pending_npc_action != pending or pending.phase is not NpcQuestActionPhase.ACK_PENDING or dialog is None or len(semantic_fingerprint) != 64:
            raise RuntimeError("NPC dialog action changed before settle")
        if pending.action is NpcQuestActionKind.OPEN:
            updated = replace(
                dialog, quest_opened=True,
                answer_ambiguity_snapshot_id=None, answer_ambiguity_generated_at=None,
            )
        elif pending.action is NpcQuestActionKind.ANSWER:
            updated = replace(
                dialog, dialog_steps=dialog.dialog_steps + 1,
                dialog_step_fingerprints=(*dialog.dialog_step_fingerprints, semantic_fingerprint),
                answer_ambiguity_snapshot_id=None, answer_ambiguity_generated_at=None,
            )
        else:
            raise RuntimeError("accept requires active-catalog verification")
        previous = dialog
        self.pending_npc_dialog = updated
        self.pending_npc_action = None
        try:
            self._persist()
        except Exception:
            self.pending_npc_dialog = previous
            self.pending_npc_action = pending
            raise
        return updated

    def recover_legacy_npc_dialog(
        self, pending: PendingNpcOpen, *, expected_disk_bytes: bytes,
    ) -> None:
        """Atomically apply one explicit operator-approved legacy proof."""

        if (
            self.lease is not None or self.pending_catalog_navigation is not None
            or self.pending_active_catalog_navigation is not None
            or self.pending_npc_open is not None or self.pending_npc_dialog is not None
            or pending.capability_version != LEGACY_NPC_OPEN_CAPABILITY
            or any(item.quest_id == pending.quest_id for item in self.intake_quarantines)
            or any(item.quest_id == pending.quest_id for item in self.quarantines)
            or not _npc_stage_matches_ref(pending, self.pending_accepted_ref)
        ):
            raise RuntimeError("legacy NPC recovery conflicts with chain state")
        if self.state_path is None:
            raise RuntimeError("legacy NPC recovery requires a persisted chain")
        with checkpoint_lock(self.state_path):
            current = self.state_path.read_bytes() if self.state_path.exists() else b""
            if current != expected_disk_bytes:
                raise QuestChainCheckpointConflict("legacy NPC recovery checkpoint changed")
            if current != self._disk_baseline:
                self._restore_disk_baseline()
                raise QuestChainCheckpointConflict("quest chain checkpoint changed")
            self.pending_npc_dialog = pending
            try:
                self._persist_or_unlink(lock_held=True)
            except Exception:
                self.pending_npc_dialog = None
                raise

    def capture_persisted_bytes(self) -> bytes:
        if self.state_path is None:
            raise RuntimeError("quest chain is not persisted")
        with checkpoint_lock(self.state_path):
            data = self.state_path.read_bytes() if self.state_path.exists() else b""
            if data != self._disk_baseline:
                raise QuestChainCheckpointConflict("quest chain checkpoint changed")
            if not data:
                raise RuntimeError("quest chain checkpoint is missing")
            try:
                disk_payload = json.loads(data)
            except json.JSONDecodeError as exc:
                raise RuntimeError("quest chain checkpoint is malformed") from exc
            if disk_payload != self.checkpoint():
                raise RuntimeError("quest chain checkpoint changed")
            return data

    def replace_unsupported_available_entries(
        self, entries: tuple[UnsupportedAvailableQuest, ...]
    ) -> None:
        if len(entries) > self.max_unsupported_available or len({item.quest_id for item in entries}) != len(entries):
            raise ValueError("invalid unsupported available quest evidence")
        previous = self.unsupported_available_entries
        self.unsupported_available_entries = entries
        try:
            self._persist()
        except Exception:
            self.unsupported_available_entries = previous
            raise

    def reconcile_unsupported_available_entries(
        self,
        entries: tuple[UnsupportedAvailableQuest, ...],
        *,
        observed_quest_ids: set[str],
    ) -> None:
        if (
            len(entries) > self.max_unsupported_available
            or len({item.quest_id for item in entries}) != len(entries)
            or any(not isinstance(value, str) or not value for value in observed_quest_ids)
            or any(item.quest_id not in observed_quest_ids for item in entries)
        ):
            raise ValueError("invalid unsupported available quest evidence")
        preserved = tuple(
            item
            for item in self.unsupported_available_entries
            if item.quest_id not in observed_quest_ids
        )
        merged = (*preserved, *entries)[-self.max_unsupported_available :]
        previous = self.unsupported_available_entries
        self.unsupported_available_entries = merged
        try:
            self._persist()
        except Exception:
            self.unsupported_available_entries = previous
            raise

    def clear_catalog_navigation(self, pending: PendingCatalogNavigation) -> None:
        """Atomically clear only the exact staged navigation."""

        if self.pending_catalog_navigation != pending:
            raise RuntimeError("catalog navigation stage changed before clear")
        self.pending_catalog_navigation = None
        try:
            self._persist()
        except Exception:
            self.pending_catalog_navigation = pending
            raise

    def clear_active_catalog_navigation(self, pending: PendingActiveCatalogNavigation) -> None:
        """CAS-clear only the exact durable active-catalog navigation."""

        if self.pending_active_catalog_navigation != pending:
            raise RuntimeError("active catalog navigation stage changed before clear")
        self.pending_active_catalog_navigation = None
        try:
            self._persist()
        except QuestChainCheckpointConflict:
            raise
        except Exception:
            self.pending_active_catalog_navigation = pending
            raise

    def stage_accepted_ref(self, quest_ref: QuestRef) -> None:
        _validate_authoritative_ref(quest_ref)
        if self.pending_accepted_ref is not None and self.pending_accepted_ref != quest_ref:
            raise RuntimeError("another accepted quest reference is pending")
        self.pending_accepted_ref = quest_ref
        self._persist()

    def quarantine_intake_ref(
        self,
        expected_ref: QuestRef,
        *,
        reason: str,
        capability_version: str,
        recorded_at: float | None = None,
    ) -> IntakeQuarantine:
        """Atomically replace one exact staged acceptance with durable evidence."""

        if self.pending_accepted_ref != expected_ref:
            raise RuntimeError("intake quarantine does not match staged acceptance")
        evidence = make_intake_quarantine(
            expected_ref,
            reason=reason,
            capability_version=capability_version,
            recorded_at=time.time() if recorded_at is None else recorded_at,
        )
        previous_pending = self.pending_accepted_ref
        previous_quarantines = self.intake_quarantines
        retained = tuple(
            item for item in self.intake_quarantines if item.quest_id != evidence.quest_id
        )
        self.intake_quarantines = (*retained, evidence)[-self.max_intake_quarantines :]
        self.pending_accepted_ref = None
        try:
            self._persist()
        except Exception:
            self.pending_accepted_ref = previous_pending
            self.intake_quarantines = previous_quarantines
            raise
        return evidence

    def is_intake_quarantined(
        self,
        quest_ref: QuestRef,
        *,
        capability_version: str,
    ) -> bool:
        """Expire only on changed authoritative identity or capability version."""

        fingerprint = intake_ref_fingerprint(quest_ref)
        matching = next(
            (item for item in self.intake_quarantines if item.quest_id == quest_ref.id),
            None,
        )
        if matching is None:
            return False
        if (
            matching.fingerprint == fingerprint
            and matching.capability_version == capability_version
        ):
            return True
        previous = self.intake_quarantines
        self.intake_quarantines = tuple(
            item for item in self.intake_quarantines if item.quest_id != quest_ref.id
        )
        try:
            self._persist_or_unlink()
        except Exception:
            self.intake_quarantines = previous
            raise
        return False

    def bind_accepted_ref(self, quest_ref: QuestRef) -> QuestChainLease:
        lease = self.lease
        if lease is None:
            raise RuntimeError("accepted quest reference requires a pinned chain")
        if (
            not isinstance(quest_ref, QuestRef)
            or quest_ref.id != lease.quest_id
            or quest_ref.title != lease.quest_title
            or not quest_ref.location
            or len(quest_ref.giver_names) != 1
            or not quest_ref.giver_names[0]
        ):
            raise ValueError("accepted quest reference is not authoritative")
        if lease.accepted_ref is not None and lease.accepted_ref != quest_ref:
            raise RuntimeError("accepted quest reference conflicts with pinned chain")
        self.lease = replace(
            lease,
            accepted_ref=quest_ref,
            turn_in_ref_fingerprint=lease.current_fingerprint,
        )
        if self.pending_accepted_ref == quest_ref:
            self.pending_accepted_ref = None
        self._persist()
        return self.lease

    def recover_staged_active_ref(
        self,
        entry: ActiveQuestEntry,
        *,
        revision: int,
        expected_ref: QuestRef,
    ) -> QuestChainLease:
        """Atomically pin and bind one restored staged ref with one durable write."""

        _validate_authoritative_ref(expected_ref)
        if (
            not isinstance(entry, ActiveQuestEntry)
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision <= 0
            or entry.id != expected_ref.id
            or entry.title != expected_ref.title
            or self.pending_accepted_ref != expected_ref
            or self.lease is not None
        ):
            raise RuntimeError("staged active recovery identity mismatch")
        fingerprint, reason = quest_step_fingerprint(entry)
        if fingerprint is None:
            raise ValueError(reason)
        previous_lease = self.lease
        previous_pending = self.pending_accepted_ref
        previous_npc_open = self.pending_npc_open
        previous_npc_dialog = self.pending_npc_dialog
        previous_npc_action = self.pending_npc_action
        npc_stage = previous_npc_open or previous_npc_dialog
        if npc_stage is not None and not _npc_stage_matches_ref(npc_stage, expected_ref):
            raise RuntimeError("staged NPC recovery identity mismatch")
        if previous_npc_action is not None and (
            previous_npc_action.action is not NpcQuestActionKind.ACCEPT
            or previous_npc_action.phase is not NpcQuestActionPhase.ACCEPT_VERIFY
            or not _npc_action_matches(previous_npc_action, previous_npc_dialog, expected_ref)
        ):
            raise RuntimeError("staged NPC accept recovery identity mismatch")
        self.lease = QuestChainLease(
            entry.id,
            entry.title,
            revision,
            fingerprint,
            (fingerprint,),
            accepted_ref=expected_ref,
            turn_in_ref_fingerprint=fingerprint,
        )
        self.pending_accepted_ref = None
        self.pending_npc_open = None
        self.pending_npc_dialog = None
        self.pending_npc_action = None
        try:
            self._persist()
        except Exception:
            self.lease = previous_lease
            self.pending_accepted_ref = previous_pending
            self.pending_npc_open = previous_npc_open
            self.pending_npc_dialog = previous_npc_dialog
            self.pending_npc_action = previous_npc_action
            raise
        return self.lease

    def stage_turn_in_completion(self, *, active_catalog_revision: int) -> None:
        lease = self.lease
        if lease is None:
            raise RuntimeError("turn-in completion requires a pinned chain")
        if (
            isinstance(active_catalog_revision, bool)
            or not isinstance(active_catalog_revision, int)
            or active_catalog_revision < 0
        ):
            raise ValueError("turn-in completion revision is invalid")
        evidence = PendingTurnInCompletion(
            lease.quest_id,
            lease.quest_title,
            lease.current_fingerprint,
            active_catalog_revision,
        )
        if self.pending_turn_in_completion not in {None, evidence}:
            raise RuntimeError("another turn-in completion is pending")
        self.pending_turn_in_completion = evidence
        self.pending_turn_in_completion_restored = False
        self._persist()

    def clear_turn_in_completion(self) -> None:
        self.pending_turn_in_completion = None
        self.pending_turn_in_completion_restored = False
        self._persist()

    def mark_awaiting_executor(
        self, reason: str, *, capability_version: str
    ) -> QuestChainLease:
        """Persist a non-terminal executor blocker without releasing the lease."""

        lease = self.lease
        normalized = str(reason or "").strip()
        normalized_capability = str(capability_version or "").strip()
        if lease is None:
            raise RuntimeError("awaiting executor requires a pinned chain")
        if not _valid_reason(normalized) or not _valid_capability(normalized_capability):
            raise ValueError("awaiting executor reason is invalid")
        if lease.awaiting_executor_reason not in {None, normalized} or (
            lease.awaiting_executor_capability_version
            not in {None, normalized_capability}
        ):
            raise RuntimeError("another executor blocker is already persisted")
        self.lease = replace(
            lease,
            awaiting_executor_reason=normalized,
            awaiting_executor_capability_version=normalized_capability,
        )
        self._persist()
        return self.lease

    def record_awaiting_executor(
        self, quest_id: str, reason: str, capability_version: str,
    ) -> QuestChainLease:
        """Compatibility-safe public entry point bound to one exact lease.

        Callers that carry a quest identity must not be able to mark whichever
        lease happens to be current.  The identity check happens before the
        existing validated/CAS-persisted state transition.
        """

        normalized_quest_id = str(quest_id or "").strip()
        lease = self.lease
        if (
            not normalized_quest_id.isdecimal()
            or int(normalized_quest_id) <= 0
        ):
            raise ValueError("awaiting executor quest identity is invalid")
        if lease is None:
            raise RuntimeError("awaiting executor requires a pinned chain")
        if lease.quest_id != normalized_quest_id:
            raise RuntimeError("awaiting executor quest does not match pinned chain")
        return self.mark_awaiting_executor(
            reason, capability_version=capability_version,
        )

    def clear_awaiting_executor(self) -> QuestChainLease:
        lease = self.lease
        if lease is None:
            raise RuntimeError("clearing executor wait requires a pinned chain")
        self.lease = replace(
            lease,
            awaiting_executor_reason=None,
            awaiting_executor_capability_version=None,
        )
        self._persist()
        return self.lease

    def quarantine_entry(
        self,
        entry: ActiveQuestEntry,
        *,
        reason: str,
        capability_version: str,
        recorded_at: float | None = None,
    ) -> QuarantinedQuest:
        """Persist an exact quest-local blocker and release only its scheduler lease."""

        fingerprint, fingerprint_reason = quest_step_fingerprint(entry)
        normalized_reason = str(reason or "").strip()
        normalized_capability = str(capability_version or "").strip()
        timestamp = time.time() if recorded_at is None else recorded_at
        if fingerprint is None:
            raise ValueError(fingerprint_reason)
        if not _valid_reason(normalized_reason) or not _valid_capability(normalized_capability):
            raise ValueError("invalid quarantine evidence")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool) or timestamp <= 0:
            raise ValueError("invalid quarantine timestamp")
        evidence = QuarantinedQuest(
            entry.id,
            entry.title,
            fingerprint,
            normalized_reason,
            normalized_capability,
            float(timestamp),
        )
        retained = tuple(item for item in self.quarantines if item.quest_id != entry.id)
        self.quarantines = (*retained, evidence)[-self.max_quarantines :]
        if self.lease is not None and self.lease.quest_id == entry.id:
            self.lease = None
            self.pending_accepted_ref = None
            self.pending_turn_in_completion = None
            self.pending_turn_in_completion_restored = False
            if (
                self.ordered_handoff_cursor is not None
                and self.ordered_handoff_cursor.quest_id == entry.id
            ):
                self.ordered_handoff_cursor = None
        self._persist()
        return evidence

    def is_quarantined(
        self, entry: ActiveQuestEntry, *, capability_version: str
    ) -> bool:
        """Pure exact-match quarantine predicate."""

        fingerprint, _ = quest_step_fingerprint(entry)
        if fingerprint is None:
            return False
        matching = next((item for item in self.quarantines if item.quest_id == entry.id), None)
        if matching is None:
            return False
        return bool(
            matching.quest_title == entry.title
            and matching.fingerprint == fingerprint
            and matching.capability_version == capability_version
        )

    def expire_changed_quarantines(
        self,
        entries: Sequence[ActiveQuestEntry],
        *,
        capability_version: str,
    ) -> None:
        """Persist expiry explicitly after a complete authoritative refresh."""

        by_id = {entry.id: entry for entry in entries}
        retained = tuple(
            item
            for item in self.quarantines
            if (
                (entry := by_id.get(item.quest_id)) is None
                or (
                    item.quest_title == entry.title
                    and item.fingerprint == quest_step_fingerprint(entry)[0]
                    and item.capability_version == capability_version
                )
            )
        )
        if retained == self.quarantines:
            return
        previous = self.quarantines
        self.quarantines = retained
        try:
            self._persist()
        except Exception:
            self.quarantines = previous
            raise

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
        legacy_fingerprint, _ = legacy_quest_step_fingerprint(entry)
        if fingerprint != lease.current_fingerprint:
            if legacy_fingerprint == lease.current_fingerprint:
                if fingerprint in lease.visited_fingerprints[:-1]:
                    return ChainRefreshResult(
                        ChainRefreshState.LOOP_UNSAFE,
                        lease,
                        None,
                        "pinned_quest_fingerprint_migration_loop",
                    )
                migrated_visited = (*lease.visited_fingerprints[:-1], fingerprint)
                migrated_turn_in = (
                    fingerprint
                    if lease.turn_in_ref_fingerprint == legacy_fingerprint
                    else lease.turn_in_ref_fingerprint
                )
                self.lease = lease = replace(
                    lease,
                    current_fingerprint=fingerprint,
                    visited_fingerprints=migrated_visited,
                    turn_in_ref_fingerprint=migrated_turn_in,
                    legacy_fingerprint_aliases=tuple(
                        dict.fromkeys(
                            (*lease.legacy_fingerprint_aliases, *lease.visited_fingerprints)
                        )
                    ),
                )
                if (
                    self.pending_turn_in_completion is not None
                    and self.pending_turn_in_completion.completed_fingerprint
                    == legacy_fingerprint
                ):
                    self.pending_turn_in_completion = replace(
                        self.pending_turn_in_completion,
                        completed_fingerprint=fingerprint,
                    )
                self._persist()
        route_plan = classify_objective(entry)
        if route_plan.status is ObjectiveRouteStatus.UNSAFE:
            return ChainRefreshResult(
                ChainRefreshState.REGRESSED_UNSAFE,
                lease,
                None,
                f"pinned_quest_route_unsafe_{route_plan.reason}",
            )
        objective: QuestObjective | None = None
        if route_plan.kind in {
            ObjectiveRouteKind.COMBAT_DROP,
            ObjectiveRouteKind.PURE_KILL,
        }:
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
        if fingerprint in lease.visited_fingerprints or (
            legacy_fingerprint is not None
            and legacy_fingerprint in lease.legacy_fingerprint_aliases
        ):
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
            awaiting_executor_reason=None,
            awaiting_executor_capability_version=None,
        )
        self.lease = advanced
        self.ordered_handoff_cursor = None
        self._persist()
        state = ChainRefreshState.ADVANCED if objective else ChainRefreshState.EXECUTOR_REQUIRED
        return ChainRefreshResult(state, advanced, objective, "pinned_quest_step_advanced")

    def release_completed(self, quest_id: str) -> None:
        if self.lease is None or self.lease.quest_id != quest_id:
            raise RuntimeError("completed quest does not match pinned chain")
        self.lease = None
        self.pending_accepted_ref = None
        self.pending_turn_in_completion = None
        self.pending_turn_in_completion_restored = False
        self.ordered_handoff_cursor = None
        self.quarantines = tuple(item for item in self.quarantines if item.quest_id != quest_id)
        self._persist_or_unlink()

    def release_deferred(self, quest_id: str) -> None:
        """Release a chain that was explicitly deferred, never count it completed."""

        if self.lease is None or self.lease.quest_id != quest_id:
            raise RuntimeError("deferred quest does not match pinned chain")
        self.lease = None
        self.pending_accepted_ref = None
        self.pending_turn_in_completion = None
        self.pending_turn_in_completion_restored = False
        self.ordered_handoff_cursor = None
        self._persist_or_unlink()

    def _persist(self) -> None:
        self._persist_or_unlink()

    def _persist_or_unlink(self, *, lock_held: bool = False) -> None:
        payload = self.checkpoint()
        if self.state_path is None:
            return
        if not lock_held:
            with checkpoint_lock(self.state_path):
                return self._persist_or_unlink(lock_held=True)
        current = self.state_path.read_bytes() if self.state_path.exists() else b""
        if current != self._disk_baseline:
            self._restore_disk_baseline()
            raise QuestChainCheckpointConflict("quest chain checkpoint changed")
        if payload is None:
            self.state_path.unlink(missing_ok=True)
            self._disk_baseline = b""
        else:
            write_json_checkpoint(self.state_path, payload, lock_held=True)
            self._disk_baseline = self.state_path.read_bytes()

    def _restore_disk_baseline(self) -> None:
        if self._disk_baseline:
            payload = json.loads(self._disk_baseline)
            if not isinstance(payload, dict):
                raise QuestChainCheckpointConflict("quest chain baseline is invalid")
            self.restore(payload)
            return
        self.lease = None
        self.pending_accepted_ref = None
        self.pending_turn_in_completion = None
        self.pending_turn_in_completion_restored = False
        self.pending_catalog_navigation = None
        self.pending_active_catalog_navigation = None
        self.pending_npc_open = None
        self.pending_npc_dialog = None
        self.pending_npc_action = None
        self.ordered_handoff_cursor = None
        self.unsupported_available_entries = ()
        self.quarantines = ()
        self.intake_quarantines = ()
