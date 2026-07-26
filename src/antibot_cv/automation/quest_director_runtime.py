"""Stateful, action-free coordinator for autonomous quest scheduling."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import re

from src.antibot_cv.automation.quest_active_catalog import (
    ActiveQuestCatalogAccumulator,
    ActiveQuestEntry,
)
from src.antibot_cv.automation.quest_available_eligibility import (
    UnsupportedAvailableQuest,
    classify_available_quest_refs,
)
from src.antibot_cv.automation.quest_catalog import QuestCatalogAccumulator
from src.antibot_cv.automation.quest_director_policy import (
    QuestDirectorDecision,
    QuestDirectorPolicy,
    QuestDirectorState,
    QuestRef,
    QuestDirectorIntent,
)
from src.antibot_cv.automation.quest_objective_runtime import (
    ObjectiveRefreshComparison,
    ObjectiveRefreshState,
    ObjectiveSelectionStatus,
    QuestObjective,
    compare_refreshed_objective,
    collect_monster_hunt_objectives,
    quest_step_fingerprint,
    select_monster_hunt_objective,
)
from src.antibot_cv.automation.quest_objective_router import (
    ObjectiveRoutePlan,
    ObjectiveRouteStatus,
    classify_objective,
    first_unmet_combat_requirement,
)
from src.antibot_cv.automation.quest_chain_runtime import (
    ChainRefreshState,
    QuestChainRuntime,
)
from src.antibot_cv.automation.quest_intake_quarantine import (
    INTAKE_CAPABILITY_VERSION,
    intake_ref_fingerprint,
)


_OBJECTIVE_CAPABILITY_VERSION = "objective_router_v3"
class QuestDirectorRuntime:
    """Maintain director observations while an orchestrator performs actions."""

    def __init__(
        self,
        *,
        refresh_every_completed: int = 5,
        catalog_max_pages: int = 20,
        max_unchanged_victories: int = 10,
        pinned_quest_id: str = "",
        ignored_quest_ids: tuple[str, ...] = (),
        prefer_active_quests: bool = False,
        chain_state_path: str | Path | None = None,
    ) -> None:
        if max_unchanged_victories <= 0:
            raise ValueError("max unchanged victories must be positive")
        self.policy = QuestDirectorPolicy(
            refresh_every_completed=refresh_every_completed,
            prefer_active_quests=prefer_active_quests,
        )
        self.catalog = QuestCatalogAccumulator(max_pages=catalog_max_pages)
        self.active_catalog = ActiveQuestCatalogAccumulator(max_pages=catalog_max_pages)
        self.discovery_initialized = False
        self.available_snapshot_fresh = False
        self.active_snapshot_fresh = False
        self.refresh_in_progress = False
        self.completed_since_refresh = 0
        self.active_quests: tuple[QuestRef, ...] = ()
        self.available_quests: tuple[QuestRef, ...] = ()
        self.intake_queue: tuple[QuestRef, ...] = ()
        self.unsupported_available_entries: tuple[UnsupportedAvailableQuest, ...] = ()
        self.pending_accept: QuestRef | None = None
        self.accepted_refs: dict[str, QuestRef] = {}
        self.active_objective: QuestObjective | None = None
        self.active_route_plan: ObjectiveRoutePlan | None = None
        self.supported_objectives: tuple[QuestObjective, ...] = ()
        self.deferred_active: dict[str, str] = {}
        preferred = str(pinned_quest_id or "").strip()
        if preferred and (not preferred.isdecimal() or int(preferred) <= 0):
            raise ValueError("pinned quest id must be a positive decimal identity")
        self.preferred_quest_id = preferred
        self.ignored_quest_ids = frozenset(str(value).strip() for value in ignored_quest_ids if str(value).strip())
        if any(not value.isdecimal() or int(value) <= 0 for value in self.ignored_quest_ids):
            raise ValueError("ignored quest ids must be positive decimal identities")
        if preferred and preferred in self.ignored_quest_ids:
            raise ValueError("pinned quest id cannot also be ignored")
        self.chain = QuestChainRuntime(state_path=chain_state_path)
        staged_ignored = self.chain.pending_accepted_ref
        if staged_ignored is not None and staged_ignored.id in self.ignored_quest_ids:
            if self.chain.pending_npc_open is not None:
                self.chain.clear_npc_open(self.chain.pending_npc_open)
            if self.chain.pending_npc_dialog is not None:
                self.chain.clear_npc_dialog(self.chain.pending_npc_dialog)
            self.chain.quarantine_intake_ref(
                staged_ignored,
                reason="quest_explicitly_ignored",
                capability_version=INTAKE_CAPABILITY_VERSION,
            )
        staged_npc = self.chain.pending_npc_open or self.chain.pending_npc_dialog
        if staged_npc is not None:
            staged_ref = self.chain.pending_accepted_ref
            if self.chain.lease is not None or staged_ref is None:
                raise ValueError("invalid restored NPC acceptance")
            self.pending_accept = staged_ref
            self.available_quests = (staged_ref,)
            self.intake_queue = (staged_ref,)
        self.unsupported_available_entries = self.chain.unsupported_available_entries
        self._intake_quarantine_wait_pending = bool(self.chain.intake_quarantines)
        self._staged_intake_reconciliation_attempted = False
        if (
            self.chain.lease is not None
            and preferred
            and self.chain.lease.quest_id != preferred
        ):
            # An explicit operator pin starts a new test/priority chain.  The
            # old lease remains non-terminal and must be released as deferred,
            # never treated as a completed quest.
            self.chain.release_deferred(self.chain.lease.quest_id)
        self.active_objective_revision: int | None = None
        self.objective_refresh: ObjectiveRefreshComparison | None = None
        self.max_unchanged_victories = max_unchanged_victories
        self.unchanged_victory_refreshes = 0
        self._active_refresh_after_victory = False
        self._current_level_cap: int | None = None
        self.turn_in_recovery_outcome: str | None = None

    def decision(self, *, current_level_cap: int | None = None) -> QuestDirectorDecision:
        """Run a bounded re-planning loop; quarantines never recurse."""

        budget = max(1, len(self.active_quests) + 1)
        for _ in range(budget):
            result = self._decision_once(current_level_cap=current_level_cap)
            if result is not None:
                return result
        return QuestDirectorDecision(
            QuestDirectorIntent.STOP_UNSAFE,
            "quest_planner_progress_budget_exhausted",
            intake_queue=self.intake_queue,
        )

    def _decision_once(
        self, *, current_level_cap: int | None = None
    ) -> QuestDirectorDecision | None:
        if current_level_cap is not None:
            self._current_level_cap = current_level_cap
        if self._intake_quarantine_wait_pending:
            self._intake_quarantine_wait_pending = False
            return QuestDirectorDecision(
                QuestDirectorIntent.WAIT,
                "quest_intake_quarantine_recorded",
                intake_queue=self.intake_queue,
            )
        if (
            self._current_level_cap is not None
            and self.active_snapshot_fresh
            and self.active_catalog.complete
            and self.chain.pending_turn_in_completion_restored
        ):
            self._recover_staged_turn_in(self.active_catalog.result)
        staged_decision = self._staged_intake_reconciliation_decision()
        if staged_decision is not None:
            return staged_decision
        if self.pending_accept is not None:
            return QuestDirectorDecision(
                QuestDirectorIntent.WAIT,
                "quest_accept_in_progress",
                quest=self.pending_accept,
                intake_queue=self.intake_queue,
            )
        if self.chain.lease is not None and not self.active_snapshot_fresh:
            return QuestDirectorDecision(
                QuestDirectorIntent.REFRESH_ACTIVE,
                "pinned_chain_active_refresh_required",
                intake_queue=self.intake_queue,
            )
        if self.preferred_quest_id:
            if not self.available_snapshot_fresh:
                return QuestDirectorDecision(
                    QuestDirectorIntent.REFRESH_AVAILABLE,
                    "preferred_quest_available_refresh_required",
                    intake_queue=self.intake_queue,
                )
            if not self.active_snapshot_fresh:
                return QuestDirectorDecision(
                    QuestDirectorIntent.REFRESH_ACTIVE,
                    "preferred_quest_active_refresh_required",
                    intake_queue=self.intake_queue,
                )
            preferred_active = next(
                (quest for quest in self.active_quests if quest.id == self.preferred_quest_id),
                None,
            )
            if preferred_active is not None:
                return QuestDirectorDecision(
                    QuestDirectorIntent.EXECUTE_ACTIVE,
                    "preferred_active_quest_ready",
                    quest=preferred_active,
                    intake_queue=self.intake_queue,
                )
            preferred = next(
                (quest for quest in self.available_quests if quest.id == self.preferred_quest_id),
                None,
            )
            if preferred is None:
                return QuestDirectorDecision(
                    QuestDirectorIntent.STOP_UNSAFE,
                    "preferred_pinned_quest_not_available_or_active",
                    intake_queue=self.intake_queue,
                )
            self.intake_queue = (preferred,)
            return QuestDirectorDecision(
                QuestDirectorIntent.ACCEPT_QUEST,
                "preferred_available_quest_waiting_for_acceptance",
                quest=preferred,
                intake_queue=self.intake_queue,
            )
        if self.active_snapshot_fresh and self.chain.lease is not None and not any(
            quest.id == self.chain.lease.quest_id for quest in self.active_quests
        ):
            return QuestDirectorDecision(
                QuestDirectorIntent.STOP_UNSAFE,
                "pinned_quest_missing_without_terminal_evidence",
                intake_queue=self.intake_queue,
            )
        result = self.policy.decide(self.snapshot())
        self.intake_queue = result.intake_queue
        if (
            result.intent is QuestDirectorIntent.PROFIT_FARM
            and self.active_quests
            and self._quarantined_active_ids()
        ):
            return QuestDirectorDecision(
                QuestDirectorIntent.WAIT,
                "quest_active_quarantined",
                intake_queue=self.intake_queue,
            )
        lease = self.chain.lease
        if (
            lease is not None
            and self.active_snapshot_fresh
            and any(quest.id == lease.quest_id for quest in self.active_quests)
            and result.intent in {
                QuestDirectorIntent.ACCEPT_QUEST,
                QuestDirectorIntent.EXECUTE_ACTIVE,
                QuestDirectorIntent.PROFIT_FARM,
                QuestDirectorIntent.REFRESH_AVAILABLE,
            }
        ):
            result = QuestDirectorDecision(
                QuestDirectorIntent.EXECUTE_ACTIVE,
                "pinned_chain_preempts_intake",
                quest=next(quest for quest in self.active_quests if quest.id == lease.quest_id),
                intake_queue=self.intake_queue,
            )
        if result.intent is QuestDirectorIntent.EXECUTE_ACTIVE:
            if current_level_cap is None:
                return QuestDirectorDecision(
                    QuestDirectorIntent.STOP_UNSAFE,
                    "quest_objective_level_cap_missing",
                    intake_queue=self.intake_queue,
                )
            if not self.active_catalog.complete:
                return QuestDirectorDecision(
                    QuestDirectorIntent.STOP_UNSAFE,
                    "quest_objective_active_catalog_incomplete",
                    intake_queue=self.intake_queue,
                )
            if (
                self.objective_refresh is not None
                and self.objective_refresh.state
                is ObjectiveRefreshState.REGRESSED_UNSAFE
            ):
                lease = self.chain.lease
                pinned_entry = (
                    next(
                        (
                            entry
                            for entry in self.active_catalog.result
                            if lease is not None
                            and entry.id == lease.quest_id
                            and entry.title == lease.quest_title
                        ),
                        None,
                    )
                    if lease is not None
                    else None
                )
                if pinned_entry is not None:
                    self._quarantine_entry(
                        pinned_entry,
                        _quarantine_reason(self.objective_refresh.reason),
                    )
                    return None
                return QuestDirectorDecision(
                    QuestDirectorIntent.STOP_UNSAFE,
                    f"quest_objective_refresh:{self.objective_refresh.reason}",
                    intake_queue=self.intake_queue,
                )
            if (
                self.objective_refresh is not None
                and self.objective_refresh.state
                is ObjectiveRefreshState.QUEST_REMOVED_UNVERIFIED
            ):
                return QuestDirectorDecision(
                    QuestDirectorIntent.STOP_UNSAFE,
                    f"quest_objective_refresh:{self.objective_refresh.reason}",
                    intake_queue=self.intake_queue,
                )
            eligible_entries = tuple(
                entry
                for entry in self.active_catalog.result
                if not self.chain.is_quarantined(
                    entry,
                    capability_version=_OBJECTIVE_CAPABILITY_VERSION,
                )
            )
            if not eligible_entries:
                return self._decision_with_blocked_guard()
            planned_entries = tuple(
                (entry, classify_objective(entry)) for entry in eligible_entries
            )
            unsafe_plans = tuple(
                (entry, plan)
                for entry, plan in planned_entries
                if plan.status is ObjectiveRouteStatus.UNSAFE
            )
            if unsafe_plans:
                quarantined = False
                for blocked_entry, blocked_plan in unsafe_plans:
                    fingerprint, _ = quest_step_fingerprint(blocked_entry)
                    # A structurally incomplete card (most commonly a folded
                    # quest without ``Текущая цель``) cannot produce the
                    # exact fingerprint required by the durable quarantine
                    # schema.  It remains action-ineligible, but must not stop
                    # unrelated, fully parsed active quests.
                    if fingerprint is None:
                        continue
                    self._quarantine_entry(blocked_entry, blocked_plan.reason)
                    quarantined = True
                if quarantined:
                    return None
                planned_entries = tuple(
                    (entry, plan)
                    for entry, plan in planned_entries
                    if plan.status is ObjectiveRouteStatus.READY
                )
                if not planned_entries:
                    return QuestDirectorDecision(
                        QuestDirectorIntent.WAIT,
                        "quest_active_structurally_incomplete",
                        intake_queue=self.intake_queue,
                    )
            plans_by_id = {entry.id: plan for entry, plan in planned_entries}
            combat_entries = tuple(
                entry
                for entry, plan in planned_entries
                if first_unmet_combat_requirement(plan) is not None
            )
            collection = collect_monster_hunt_objectives(
                combat_entries,
                current_level_cap=current_level_cap,
            )
            if collection.status is ObjectiveSelectionStatus.UNSAFE:
                match = re.fullmatch(r"quest_([1-9]\d*):([a-z0-9_]+)", collection.reason)
                if match is not None:
                    blocked_entry = next(
                        (entry for entry in eligible_entries if entry.id == match.group(1)),
                        None,
                    )
                    if blocked_entry is not None:
                        self._quarantine_entry(blocked_entry, match.group(2))
                        return None
                return QuestDirectorDecision(
                    QuestDirectorIntent.STOP_UNSAFE,
                    f"quest_objective_collection:{collection.reason}",
                    intake_queue=self.intake_queue,
                )
            self.supported_objectives = collection.objectives
            lease = self.chain.lease
            if lease is not None:
                chain_refresh = self.chain.reconcile(
                    self.active_catalog.result,
                    current_level_cap=current_level_cap,
                )
                if chain_refresh.state in {
                    ChainRefreshState.LOOP_UNSAFE,
                    ChainRefreshState.REMOVED_UNVERIFIED,
                    ChainRefreshState.REGRESSED_UNSAFE,
                }:
                    locked_entry = next(
                        (
                            entry
                            for entry in self.active_catalog.result
                            if entry.id == lease.quest_id
                        ),
                        None,
                    )
                    if locked_entry is not None:
                        self._quarantine_entry(locked_entry, chain_refresh.reason)
                        return None
                    return QuestDirectorDecision(
                        QuestDirectorIntent.STOP_UNSAFE,
                        f"quest_chain_refresh:{chain_refresh.reason}",
                        intake_queue=self.intake_queue,
                    )
                self.active_objective = next(
                    (objective for objective in collection.objectives if objective.quest_id == lease.quest_id),
                    None,
                )
                locked = next((quest for quest in self.active_quests if quest.id == lease.quest_id), None)
                if locked is None:
                    return QuestDirectorDecision(
                        QuestDirectorIntent.STOP_UNSAFE,
                        "pinned_quest_missing_without_terminal_evidence",
                        intake_queue=self.intake_queue,
                    )
                locked_entry = next(
                    (entry for entry in self.active_catalog.result if entry.id == lease.quest_id),
                    None,
                )
                if locked_entry is None:
                    return QuestDirectorDecision(
                        QuestDirectorIntent.STOP_UNSAFE,
                        "pinned_quest_planning_entry_missing",
                        intake_queue=self.intake_queue,
                    )
                self.active_route_plan = classify_objective(locked_entry)
                if self.active_route_plan.status is ObjectiveRouteStatus.UNSAFE:
                    self._quarantine_entry(locked_entry, self.active_route_plan.reason)
                    return None
                if first_unmet_combat_requirement(self.active_route_plan) is None:
                    self.active_objective = None
                if self.chain.lease is not None and self.chain.lease.awaiting_executor_reason:
                    if (
                        self.chain.lease.awaiting_executor_capability_version
                        == _OBJECTIVE_CAPABILITY_VERSION
                    ):
                        return QuestDirectorDecision(
                            QuestDirectorIntent.WAIT,
                            f"quest_awaiting_executor:{self.chain.lease.awaiting_executor_reason}",
                            quest=locked,
                            intake_queue=self.intake_queue,
                        )
                    self.chain.clear_awaiting_executor()
                if self.active_objective is None:
                    return QuestDirectorDecision(
                        QuestDirectorIntent.EXECUTE_ACTIVE,
                        "pinned_chain_requires_non_monster_executor",
                        quest=locked,
                        intake_queue=self.intake_queue,
                    )
            elif self.active_objective is None:
                selected_entry = planned_entries[0][0]
                selected_plan = plans_by_id[selected_entry.id]
                if first_unmet_combat_requirement(selected_plan) is None:
                    self.active_route_plan = selected_plan
                    self.chain.pin_entry(
                        selected_entry,
                        revision=self.active_catalog.revision,
                    )
                    self._bind_accepted_ref(selected_entry.id)
                    self.active_objective_revision = self.active_catalog.revision
                    return QuestDirectorDecision(
                        QuestDirectorIntent.EXECUTE_ACTIVE,
                        "supported_non_monster_step_ready",
                        quest=QuestRef(selected_entry.id, selected_entry.title),
                        intake_queue=self.intake_queue,
                    )
                selection = select_monster_hunt_objective(
                    (selected_entry,),
                    current_level_cap=current_level_cap,
                )
                if selection.status is not ObjectiveSelectionStatus.SELECTED or selection.objective is None:
                    self._quarantine_entry(
                        selected_entry,
                        f"objective_selection_{selection.reason}",
                    )
                    return None
                self.active_objective = selection.objective
                self.active_route_plan = selected_plan
                if self.active_route_plan.status is ObjectiveRouteStatus.UNSAFE:
                    self._quarantine_entry(
                        selected_entry,
                        self.active_route_plan.reason,
                    )
                    return None
                if first_unmet_combat_requirement(self.active_route_plan) is None:
                    self.active_objective = None
                    self.chain.pin_entry(
                        selected_entry,
                        revision=self.active_catalog.revision,
                    )
                    self._bind_accepted_ref(selected_entry.id)
                    self.active_objective_revision = self.active_catalog.revision
                    return QuestDirectorDecision(
                        QuestDirectorIntent.EXECUTE_ACTIVE,
                        "supported_non_monster_step_ready",
                        quest=QuestRef(selected_entry.id, selected_entry.title),
                        intake_queue=self.intake_queue,
                    )
                self.chain.pin(selection.objective, revision=self.active_catalog.revision)
                self._bind_accepted_ref(selection.objective.quest_id)
                self.active_objective_revision = self.active_catalog.revision
                self.unchanged_victory_refreshes = 0
            result = QuestDirectorDecision(
                QuestDirectorIntent.EXECUTE_ACTIVE,
                "supported_monster_hunt_ready",
                quest=QuestRef(self.active_objective.quest_id, self.active_objective.quest_title),
                intake_queue=self.intake_queue,
            )
        return result

    def snapshot(self) -> QuestDirectorState:
        quarantined_ids = self._quarantined_active_ids()
        return QuestDirectorState(
            discovery_initialized=self.discovery_initialized,
            available_snapshot_fresh=self.available_snapshot_fresh,
            active_snapshot_fresh=self.active_snapshot_fresh,
            refresh_in_progress=self.refresh_in_progress,
            completed_since_refresh=self.completed_since_refresh,
            active_quests=tuple(
                quest
                for quest in self.active_quests
                if quest.id not in self.deferred_active
                and quest.id not in quarantined_ids
                and quest.id not in self.ignored_quest_ids
            ),
            available_quests=tuple(quest for quest in self.available_quests if quest.id not in self.ignored_quest_ids),
            intake_queue=tuple(quest for quest in self.intake_queue if quest.id not in self.ignored_quest_ids),
            unsupported_available_count=len(self.unsupported_available_entries),
        )

    def begin_catalog_refresh(self) -> None:
        if self.pending_accept is not None:
            raise RuntimeError("cannot refresh catalogue during quest acceptance")
        self.catalog.reset()
        self.available_snapshot_fresh = False
        self.active_snapshot_fresh = False
        self.available_quests = ()
        self.intake_queue = ()
        self.unsupported_available_entries = ()
        self.refresh_in_progress = True

    def begin_completed_intake_catalog_refresh(self, quest_id: str) -> None:
        """Refresh available quests while a terminal intake action is settling.

        The active catalogue has already proved that the quest was not accepted;
        this refresh may only establish that the available card disappeared.
        """

        pending = self.pending_accept
        if pending is None or pending.id != str(quest_id or "").strip():
            raise RuntimeError("completed intake refresh identity mismatch")
        self.catalog.reset()
        self.available_snapshot_fresh = False
        self.available_quests = ()
        self.unsupported_available_entries = ()
        self.refresh_in_progress = True

    def ingest_catalog_page(self, data: object) -> int | None:
        if not self.refresh_in_progress:
            raise RuntimeError("catalogue refresh has not started")
        self.catalog.ingest(data)
        if not self.catalog.complete:
            return self.catalog.next_page
        classified = classify_available_quest_refs(
            self.catalog.director_refs,
            staged_ref=self.chain.pending_accepted_ref,
        )
        if classified.conflict_reason is not None:
            raise RuntimeError(classified.conflict_reason)
        self.chain.reconcile_unsupported_available_entries(
            classified.unsupported,
            observed_quest_ids={ref.id for ref in self.catalog.director_refs},
        )
        self.unsupported_available_entries = classified.unsupported
        retained_refs = tuple(
            quest_ref
            for quest_ref in classified.eligible
            if quest_ref.id not in self.ignored_quest_ids and not self.chain.is_intake_quarantined(
                quest_ref,
                capability_version=INTAKE_CAPABILITY_VERSION,
            )
        )
        if len(retained_refs) != len(classified.eligible):
            self._intake_quarantine_wait_pending = True
        self.available_quests = retained_refs
        self.discovery_initialized = True
        self.available_snapshot_fresh = True
        self.refresh_in_progress = False
        self.completed_since_refresh = 0
        self.intake_queue = self.policy.decide(self.snapshot()).intake_queue
        return None

    def observe_active(self, items: object) -> None:
        if not isinstance(items, list):
            raise ValueError("active quest items must be a list")
        refs: list[QuestRef] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or item.get("status") != "active":
                raise ValueError("active quest snapshot contains an invalid item")
            quest_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if (
                not quest_id
                or not quest_id.isdecimal()
                or int(quest_id) <= 0
                or not title
                or quest_id in seen
            ):
                raise ValueError("active quest identity is missing or duplicated")
            seen.add(quest_id)
            refs.append(QuestRef(quest_id, title))
        self.active_quests = tuple(refs)
        self.active_snapshot_fresh = True

    def begin_active_refresh(self, *, after_confirmed_victory: bool = False) -> None:
        self.active_catalog.reset()
        self.active_snapshot_fresh = False
        self.objective_refresh = None
        self.active_route_plan = None
        self._active_refresh_after_victory = after_confirmed_victory

    def ingest_active_page(self, data: object) -> int | None:
        result = self.active_catalog.ingest(data)
        if result is None:
            return self.active_catalog.next_page
        self.active_quests = tuple(QuestRef(entry.id, entry.title) for entry in result)
        self.chain.expire_changed_quarantines(
            result,
            capability_version=_OBJECTIVE_CAPABILITY_VERSION,
        )
        active_ids = {entry.id for entry in result}
        self.deferred_active = {
            quest_id: reason
            for quest_id, reason in self.deferred_active.items()
            if quest_id in active_ids
        }
        self.active_snapshot_fresh = True
        staged_ref = self.chain.pending_accepted_ref
        if staged_ref is not None and self.chain.pending_npc_action is None:
            confirmed_entry = next(
                (
                    entry
                    for entry in result
                    if entry.id == staged_ref.id
                    and _normalized_title(entry.title) == _normalized_title(staged_ref.title)
                ),
                None,
            )
            if confirmed_entry is not None:
                self.chain.recover_staged_active_ref(
                    confirmed_entry,
                    revision=self.active_catalog.revision,
                    expected_ref=staged_ref,
                )
                self.accepted_refs[staged_ref.id] = staged_ref
        self._recover_staged_turn_in(result)
        if self.preferred_quest_id:
            preferred = next(
                (entry for entry in result if entry.id == self.preferred_quest_id),
                None,
            )
            if preferred is not None:
                if self.chain.lease is None:
                    self.chain.pin_entry(preferred, revision=self.active_catalog.revision)
                # The explicit pin is an intake preference only.  Once its
                # accepted quest is confirmed active, the persisted chain
                # lease owns subsequent scheduling and must not trigger an
                # unnecessary available-catalogue refresh.
                self.preferred_quest_id = ""
        if self.active_objective is not None:
            previous = self.active_objective
            comparison = compare_refreshed_objective(
                previous,
                result,
                current_level_cap=self._current_level_cap or 0,
            )
            progressed = (
                comparison.state is ObjectiveRefreshState.SAME_STEP
                and comparison.refreshed is not None
                and previous.progress is not None
                and comparison.refreshed.progress is not None
                and comparison.refreshed.progress > previous.progress
            )
            if self._active_refresh_after_victory and comparison.state is ObjectiveRefreshState.SAME_STEP:
                self.unchanged_victory_refreshes = (
                    0 if progressed else self.unchanged_victory_refreshes + 1
                )
                if self.unchanged_victory_refreshes >= self.max_unchanged_victories:
                    comparison = ObjectiveRefreshComparison(
                        ObjectiveRefreshState.REGRESSED_UNSAFE,
                        comparison.refreshed,
                        "quest_progress_unchanged_after_victory_budget",
                    )
            self.objective_refresh = comparison
            if comparison.state in {
                ObjectiveRefreshState.SAME_STEP,
                ObjectiveRefreshState.STEP_CHANGED,
                ObjectiveRefreshState.STEP_COMPLETED,
            } and comparison.refreshed is not None:
                self.active_objective = comparison.refreshed
                self.active_objective_revision = self.active_catalog.revision
            elif comparison.state is ObjectiveRefreshState.STEP_CHANGED:
                self.active_objective = None
                self.active_objective_revision = self.active_catalog.revision
        if self.chain.lease is not None and self._current_level_cap is not None:
            chain_refresh = self.chain.reconcile(
                result,
                current_level_cap=self._current_level_cap,
            )
            if chain_refresh.state in {
                ChainRefreshState.LOOP_UNSAFE,
                ChainRefreshState.REMOVED_UNVERIFIED,
                ChainRefreshState.REGRESSED_UNSAFE,
            }:
                self.objective_refresh = ObjectiveRefreshComparison(
                    ObjectiveRefreshState.REGRESSED_UNSAFE,
                    chain_refresh.objective,
                    chain_refresh.reason,
                )
            self.active_objective = chain_refresh.objective
            self.active_objective_revision = self.active_catalog.revision
        self._active_refresh_after_victory = False
        return None

    def _recover_staged_turn_in(self, entries: Sequence[ActiveQuestEntry]) -> None:
        evidence = self.chain.pending_turn_in_completion
        lease = self.chain.lease
        if (
            evidence is None
            or lease is None
            or not self.chain.pending_turn_in_completion_restored
        ):
            return
        matches = [entry for entry in entries if entry.id == evidence.quest_id]
        if not matches:
            self.confirm_terminal_removal(evidence.quest_id)
            self.turn_in_recovery_outcome = "terminal_absence"
            return
        if len(matches) != 1 or matches[0].title != evidence.quest_title:
            self.turn_in_recovery_outcome = "unsafe_identity"
            return
        fingerprint, _ = quest_step_fingerprint(matches[0])
        if fingerprint is None:
            self.turn_in_recovery_outcome = "unsafe_fingerprint"
            return
        if fingerprint == evidence.completed_fingerprint:
            self.chain.clear_turn_in_completion()
            self.turn_in_recovery_outcome = "same_step_retry"
            return
        if self._current_level_cap is None:
            self.turn_in_recovery_outcome = "awaiting_level_cap"
            return
        refresh = self.chain.reconcile(
            entries,
            current_level_cap=self._current_level_cap,
        )
        if refresh.state in {
            ChainRefreshState.LOOP_UNSAFE,
            ChainRefreshState.REMOVED_UNVERIFIED,
            ChainRefreshState.REGRESSED_UNSAFE,
        }:
            self.turn_in_recovery_outcome = "unsafe_continuation"
            return
        self.chain.clear_turn_in_completion()
        self.active_objective = refresh.objective
        self.active_objective_revision = self.active_catalog.revision
        self.turn_in_recovery_outcome = "continued"

    def begin_accept(self, quest_id: str) -> QuestRef:
        if self.pending_accept is not None:
            raise RuntimeError("quest acceptance is already in progress")
        if not self.available_snapshot_fresh or not self.active_snapshot_fresh:
            raise RuntimeError("quest acceptance requires fresh snapshots")
        if not self.intake_queue or self.intake_queue[0].id != quest_id:
            raise RuntimeError("accepted quest does not match intake queue head")
        if self.chain.is_intake_quarantined(
            self.intake_queue[0],
            capability_version=INTAKE_CAPABILITY_VERSION,
        ):
            raise RuntimeError("accepted quest is quarantined")
        self.chain.stage_accepted_ref(self.intake_queue[0])
        self.pending_accept = self.intake_queue[0]
        return self.pending_accept

    def quarantine_pending_accept(
        self,
        expected_ref: QuestRef,
        reason: str,
    ) -> None:
        """Durably quarantine one exact unconfirmed intake without any action."""

        pending = self.pending_accept
        staged = self.chain.pending_accepted_ref
        eligible = tuple(
            quest_ref for quest_ref in self.available_quests if quest_ref.id == expected_ref.id
        )
        if (
            pending != expected_ref
            or staged != expected_ref
            or not self.intake_queue
            or self.intake_queue[0] != expected_ref
            or eligible != (expected_ref,)
        ):
            raise RuntimeError("intake quarantine identity mismatch")
        if (
            any(quest.id == expected_ref.id for quest in self.active_quests)
            or expected_ref.id in self.accepted_refs
            or (
                self.chain.lease is not None
                and self.chain.lease.quest_id == expected_ref.id
            )
        ):
            raise RuntimeError("confirmed quest acceptance cannot be quarantined")
        self.chain.quarantine_intake_ref(
            expected_ref,
            reason=reason,
            capability_version=INTAKE_CAPABILITY_VERSION,
        )
        self.pending_accept = None
        self.intake_queue = self.intake_queue[1:]
        self.available_quests = tuple(
            quest_ref for quest_ref in self.available_quests if quest_ref != expected_ref
        )
        self._intake_quarantine_wait_pending = True

    def quarantine_orphan_staged_accept(
        self,
        expected_ref: QuestRef,
        reason: str,
    ) -> None:
        """Quarantine an exact restored stage only after two complete absent catalogues."""

        if (
            self.pending_accept is not None
            or self.chain.pending_accepted_ref != expected_ref
            or not self.available_snapshot_fresh
            or not self.active_snapshot_fresh
            or not self.catalog.complete
            or not self.active_catalog.complete
            or any(ref.id == expected_ref.id for ref in self.available_quests)
            or any(ref.id == expected_ref.id for ref in self.active_quests)
            or expected_ref.id in self.accepted_refs
            or (self.chain.lease is not None and self.chain.lease.quest_id == expected_ref.id)
        ):
            raise RuntimeError("orphan staged intake identity mismatch")
        self.chain.quarantine_intake_ref(
            expected_ref,
            reason=reason,
            capability_version=INTAKE_CAPABILITY_VERSION,
        )
        self.intake_queue = tuple(ref for ref in self.intake_queue if ref.id != expected_ref.id)
        self._intake_quarantine_wait_pending = True

    def _staged_intake_reconciliation_decision(self) -> QuestDirectorDecision | None:
        staged = self.chain.pending_accepted_ref
        if staged is None:
            self._staged_intake_reconciliation_attempted = False
            return None
        if not (
            self.available_snapshot_fresh
            and self.active_snapshot_fresh
            and self.catalog.complete
            and self.active_catalog.complete
        ):
            return None
        active_matches = tuple(ref for ref in self.active_quests if ref.id == staged.id)
        if active_matches:
            if (
                len(active_matches) != 1
                or _normalized_title(active_matches[0].title) != _normalized_title(staged.title)
            ):
                return QuestDirectorDecision(
                    QuestDirectorIntent.STOP_UNSAFE,
                    "staged_intake_active_identity_mismatch",
                    intake_queue=self.intake_queue,
                )
            entry = next(item for item in self.active_catalog.result if item.id == staged.id)
            self.chain.recover_staged_active_ref(
                entry,
                revision=self.active_catalog.revision,
                expected_ref=staged,
            )
            self.accepted_refs[staged.id] = staged
            return QuestDirectorDecision(
                QuestDirectorIntent.WAIT,
                "staged_intake_confirmed_active",
                quest=staged,
                intake_queue=self.intake_queue,
            )
        available_matches = tuple(ref for ref in self.available_quests if ref.id == staged.id)
        if available_matches:
            if (
                len(available_matches) != 1
                or intake_ref_fingerprint(available_matches[0]) != intake_ref_fingerprint(staged)
            ):
                return QuestDirectorDecision(
                    QuestDirectorIntent.STOP_UNSAFE,
                    "staged_intake_available_identity_mismatch",
                    intake_queue=self.intake_queue,
                )
            others = tuple(ref for ref in self.intake_queue if ref.id != staged.id)
            self.available_quests = tuple(
                staged if ref.id == staged.id else ref for ref in self.available_quests
            )
            self.intake_queue = (staged, *others)
            return QuestDirectorDecision(
                QuestDirectorIntent.ACCEPT_QUEST,
                "staged_intake_available_reconstructed",
                quest=staged,
                intake_queue=self.intake_queue,
            )
        if not self._staged_intake_reconciliation_attempted:
            self._staged_intake_reconciliation_attempted = True
            return QuestDirectorDecision(
                QuestDirectorIntent.REFRESH_AVAILABLE,
                "staged_intake_reconciliation_refresh_required",
                quest=staged,
                intake_queue=(),
            )
        self.quarantine_orphan_staged_accept(staged, "orphan_staged_intake_absent")
        self._intake_quarantine_wait_pending = False
        return QuestDirectorDecision(
            QuestDirectorIntent.WAIT,
            "orphan_staged_intake_quarantined",
            intake_queue=self.intake_queue,
        )

    def invalidate_active_snapshot(self) -> None:
        self.active_snapshot_fresh = False

    def acknowledge_accept(self, quest_id: str) -> None:
        pending = self.pending_accept
        if pending is None or pending.id != quest_id:
            raise RuntimeError("accepted quest does not match pending acceptance")
        if not self.intake_queue or self.intake_queue[0].id != quest_id:
            raise RuntimeError("accepted quest does not match intake queue head")
        if not self.active_snapshot_fresh:
            raise RuntimeError("accepted quest requires fresh active snapshot")
        confirmed = next((quest for quest in self.active_quests if quest.id == quest_id), None)
        if confirmed is None or _normalized_title(confirmed.title) != _normalized_title(pending.title):
            raise RuntimeError("accepted quest is missing from active snapshot")
        self.intake_queue = self.intake_queue[1:]
        self.available_quests = tuple(quest for quest in self.available_quests if quest.id != quest_id)
        self.accepted_refs[quest_id] = pending
        active_entry = next(
            (entry for entry in self.active_catalog.result if entry.id == quest_id),
            None,
        ) if self.active_catalog.complete else None
        if self.chain.lease is None and active_entry is not None:
            self.chain.pin_entry(active_entry, revision=self.active_catalog.revision)
        self._bind_accepted_ref(quest_id)
        self.pending_accept = None

    def acknowledge_completed_intake(self, quest_id: str, staged_action) -> None:
        """Finish an available quest whose terminal button completes immediately."""

        pending = self.pending_accept
        if (
            pending is None or pending.id != quest_id
            or not self.available_snapshot_fresh or not self.catalog.complete
            or any(quest.id == quest_id for quest in self.available_quests)
        ):
            raise RuntimeError("completed intake requires fresh available-catalog absence")
        self.chain.settle_completed_intake(staged_action, expected_ref=pending)
        if self.intake_queue and self.intake_queue[0] == pending:
            self.intake_queue = self.intake_queue[1:]
        self.pending_accept = None

    def mark_quest_completed(self, quest_id: str) -> None:
        if not any(quest.id == quest_id for quest in self.active_quests):
            raise RuntimeError("completed quest was not active")
        self.active_quests = tuple(quest for quest in self.active_quests if quest.id != quest_id)
        self.deferred_active.pop(quest_id, None)
        if self.active_objective is not None and self.active_objective.quest_id == quest_id:
            self.active_objective = None
            self.active_objective_revision = None
            self.objective_refresh = None
            self.unchanged_victory_refreshes = 0
        if self.chain.lease is not None and self.chain.lease.quest_id == quest_id:
            self.chain.release_completed(quest_id)
        self.supported_objectives = tuple(
            objective for objective in self.supported_objectives if objective.quest_id != quest_id
        )
        self.completed_since_refresh += 1

    def defer_active_quest(self, quest_id: str, reason: str) -> None:
        """Record a bounded non-terminal blocker and let the scheduler continue."""

        normalized_id = str(quest_id or "").strip()
        normalized_reason = str(reason or "").strip()
        if (
            not normalized_id.isdecimal()
            or int(normalized_id) <= 0
            or not normalized_reason
            or len(normalized_reason) > 160
            or not all(char.islower() or char.isdigit() or char == "_" for char in normalized_reason)
        ):
            raise ValueError("invalid deferred quest identity or reason")
        if not any(quest.id == normalized_id for quest in self.active_quests):
            raise RuntimeError("deferred quest was not active")
        self.deferred_active[normalized_id] = normalized_reason
        if self.active_objective is not None and self.active_objective.quest_id == normalized_id:
            self.active_objective = None
            self.active_objective_revision = None
            self.objective_refresh = None
            self.unchanged_victory_refreshes = 0
        self.supported_objectives = tuple(
            objective for objective in self.supported_objectives if objective.quest_id != normalized_id
        )
        if self.chain.lease is not None and self.chain.lease.quest_id == normalized_id:
            self.chain.release_deferred(normalized_id)

    def quarantine_active_quest(self, quest_id: str, reason: str) -> None:
        """Durably skip one exact unsupported active step until it changes."""

        normalized_id = str(quest_id or "").strip()
        if not self.active_catalog.complete:
            raise RuntimeError("active quest quarantine requires a complete catalogue")
        matches = tuple(
            entry for entry in self.active_catalog.result if entry.id == normalized_id
        )
        if len(matches) != 1:
            raise RuntimeError("active quest quarantine identity is missing or ambiguous")
        if not any(quest.id == normalized_id for quest in self.active_quests):
            raise RuntimeError("quarantined quest was not active")
        self._quarantine_entry(matches[0], reason)
        self.deferred_active.pop(normalized_id, None)

    def await_active_quest_executor(self, quest_id: str, reason: str) -> None:
        """Persist a non-terminal wait while retaining the pinned chain."""

        normalized_id = str(quest_id or "").strip()
        if not any(quest.id == normalized_id for quest in self.active_quests):
            raise RuntimeError("awaiting executor quest was not active")
        if self.chain.lease is None or self.chain.lease.quest_id != normalized_id:
            raise RuntimeError("awaiting executor quest does not match pinned chain")
        self.chain.record_awaiting_executor(
            normalized_id,
            reason,
            _OBJECTIVE_CAPABILITY_VERSION,
        )

    def _quarantine_entry(self, entry: ActiveQuestEntry, reason: str) -> None:
        self.chain.quarantine_entry(
            entry,
            reason=reason,
            capability_version=_OBJECTIVE_CAPABILITY_VERSION,
        )
        self.active_objective = None
        self.active_route_plan = None
        self.active_objective_revision = None
        self.objective_refresh = None

    def _quarantined_active_ids(self) -> set[str]:
        if not self.active_catalog.complete:
            return set()
        return {
            entry.id
            for entry in self.active_catalog.result
            if self.chain.is_quarantined(
                entry,
                capability_version=_OBJECTIVE_CAPABILITY_VERSION,
            )
        }

    def _decision_after_quarantine(self) -> QuestDirectorDecision:
        # Yield one action-free scheduler turn after durable evidence is
        # written.  A later decision may accept another quest or enter profit
        # farming under the existing fresh-catalogue rules, but quarantine
        # itself can never cause an immediate page/game mutation.
        return QuestDirectorDecision(
            QuestDirectorIntent.WAIT,
            "quest_quarantine_recorded",
            intake_queue=self.intake_queue,
        )

    def _decision_with_blocked_guard(self) -> QuestDirectorDecision:
        result = self.policy.decide(self.snapshot())
        self.intake_queue = result.intake_queue
        if (
            result.intent is QuestDirectorIntent.PROFIT_FARM
            and self.active_quests
        ):
            return QuestDirectorDecision(
                QuestDirectorIntent.WAIT,
                "quest_active_quarantined",
                intake_queue=self.intake_queue,
            )
        return result

    def confirm_terminal_removal(self, quest_id: str) -> None:
        """Release a lease only after a terminal action and a fresh full absence."""

        if not self.active_snapshot_fresh or not self.active_catalog.complete:
            raise RuntimeError("terminal quest removal requires a fresh complete active catalogue")
        if any(quest.id == quest_id for quest in self.active_quests):
            raise RuntimeError("terminal quest is still active")
        if self.chain.lease is None or self.chain.lease.quest_id != quest_id:
            raise RuntimeError("terminal quest does not match pinned chain")
        self.chain.release_completed(quest_id)
        if self.active_objective is not None and self.active_objective.quest_id == quest_id:
            self.active_objective = None
        self.active_objective_revision = None
        self.objective_refresh = None
        self.supported_objectives = tuple(
            objective for objective in self.supported_objectives if objective.quest_id != quest_id
        )
        self.unchanged_victory_refreshes = 0
        self.completed_since_refresh += 1
        self.accepted_refs.pop(quest_id, None)

    def _bind_accepted_ref(self, quest_id: str) -> None:
        quest_ref = self.accepted_refs.get(quest_id)
        if quest_ref is not None and self.chain.lease is not None and self.chain.lease.quest_id == quest_id:
            self.chain.bind_accepted_ref(quest_ref)

    def expire_available_snapshot(self) -> None:
        self.available_snapshot_fresh = False


def _normalized_title(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _quarantine_reason(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return f"objective_refresh_{normalized}"[:160].rstrip("_")
