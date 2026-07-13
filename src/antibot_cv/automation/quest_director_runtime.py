"""Stateful, action-free coordinator for autonomous quest scheduling."""

from __future__ import annotations

from pathlib import Path

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestCatalogAccumulator
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
    select_monster_hunt_objective,
)
from src.antibot_cv.automation.quest_chain_runtime import (
    ChainRefreshState,
    QuestChainRuntime,
)


class QuestDirectorRuntime:
    """Maintain director observations while an orchestrator performs actions."""

    def __init__(
        self,
        *,
        refresh_every_completed: int = 5,
        catalog_max_pages: int = 20,
        max_unchanged_victories: int = 10,
        pinned_quest_id: str = "",
        chain_state_path: str | Path | None = None,
    ) -> None:
        if max_unchanged_victories <= 0:
            raise ValueError("max unchanged victories must be positive")
        self.policy = QuestDirectorPolicy(refresh_every_completed=refresh_every_completed)
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
        self.pending_accept: QuestRef | None = None
        self.active_objective: QuestObjective | None = None
        self.supported_objectives: tuple[QuestObjective, ...] = ()
        self.deferred_active: dict[str, str] = {}
        preferred = str(pinned_quest_id or "").strip()
        if preferred and (not preferred.isdecimal() or int(preferred) <= 0):
            raise ValueError("pinned quest id must be a positive decimal identity")
        self.preferred_quest_id = preferred
        self.chain = QuestChainRuntime(state_path=chain_state_path)
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

    def decision(self, *, current_level_cap: int | None = None) -> QuestDirectorDecision:
        if current_level_cap is not None:
            self._current_level_cap = current_level_cap
        if self.pending_accept is not None:
            return QuestDirectorDecision(
                QuestDirectorIntent.WAIT,
                "quest_accept_in_progress",
                quest=self.pending_accept,
                intake_queue=self.intake_queue,
            )
        if (
            (self.chain.lease is not None or self.preferred_quest_id)
            and not self.active_snapshot_fresh
        ):
            return QuestDirectorDecision(
                QuestDirectorIntent.REFRESH_ACTIVE,
                "pinned_chain_active_refresh_required",
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
        if self.active_snapshot_fresh and self.preferred_quest_id and not any(
            quest.id == self.preferred_quest_id for quest in self.active_quests
        ):
            return QuestDirectorDecision(
                QuestDirectorIntent.STOP_UNSAFE,
                "preferred_pinned_quest_missing",
                intake_queue=self.intake_queue,
            )
        result = self.policy.decide(self.snapshot())
        self.intake_queue = result.intake_queue
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
            if self.objective_refresh is not None and self.objective_refresh.state in {
                ObjectiveRefreshState.QUEST_REMOVED_UNVERIFIED,
                ObjectiveRefreshState.REGRESSED_UNSAFE,
            }:
                return QuestDirectorDecision(
                    QuestDirectorIntent.STOP_UNSAFE,
                    f"quest_objective_refresh:{self.objective_refresh.reason}",
                    intake_queue=self.intake_queue,
                )
            collection = collect_monster_hunt_objectives(
                self.active_catalog.result,
                current_level_cap=current_level_cap,
            )
            if collection.status is ObjectiveSelectionStatus.UNSAFE:
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
                if self.active_objective is None:
                    return QuestDirectorDecision(
                        QuestDirectorIntent.EXECUTE_ACTIVE,
                        "pinned_chain_requires_non_monster_executor",
                        quest=locked,
                        intake_queue=self.intake_queue,
                    )
            elif self.active_objective is None:
                selection = select_monster_hunt_objective(
                    self.active_catalog.result,
                    current_level_cap=current_level_cap,
                )
                if selection.status is not ObjectiveSelectionStatus.SELECTED or selection.objective is None:
                    return QuestDirectorDecision(
                        QuestDirectorIntent.STOP_UNSAFE,
                        f"quest_objective_selection:{selection.reason}",
                        intake_queue=self.intake_queue,
                    )
                self.active_objective = selection.objective
                self.chain.pin(selection.objective, revision=self.active_catalog.revision)
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
        return QuestDirectorState(
            discovery_initialized=self.discovery_initialized,
            available_snapshot_fresh=self.available_snapshot_fresh,
            active_snapshot_fresh=self.active_snapshot_fresh,
            refresh_in_progress=self.refresh_in_progress,
            completed_since_refresh=self.completed_since_refresh,
            active_quests=tuple(
                quest for quest in self.active_quests if quest.id not in self.deferred_active
            ),
            available_quests=self.available_quests,
            intake_queue=self.intake_queue,
        )

    def begin_catalog_refresh(self) -> None:
        if self.pending_accept is not None:
            raise RuntimeError("cannot refresh catalogue during quest acceptance")
        self.catalog.reset()
        self.available_snapshot_fresh = False
        self.active_snapshot_fresh = False
        self.available_quests = ()
        self.intake_queue = ()
        self.refresh_in_progress = True

    def ingest_catalog_page(self, data: object) -> int | None:
        if not self.refresh_in_progress:
            raise RuntimeError("catalogue refresh has not started")
        self.catalog.ingest(data)
        if not self.catalog.complete:
            return self.catalog.next_page
        self.available_quests = self.catalog.director_refs
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
        self._active_refresh_after_victory = after_confirmed_victory

    def ingest_active_page(self, data: object) -> int | None:
        result = self.active_catalog.ingest(data)
        if result is None:
            return self.active_catalog.next_page
        self.active_quests = tuple(QuestRef(entry.id, entry.title) for entry in result)
        active_ids = {entry.id for entry in result}
        self.deferred_active = {
            quest_id: reason
            for quest_id, reason in self.deferred_active.items()
            if quest_id in active_ids
        }
        self.active_snapshot_fresh = True
        if self.chain.lease is None and self.preferred_quest_id:
            preferred = next(
                (entry for entry in result if entry.id == self.preferred_quest_id),
                None,
            )
            if preferred is None:
                self.objective_refresh = ObjectiveRefreshComparison(
                    ObjectiveRefreshState.REGRESSED_UNSAFE,
                    None,
                    "preferred_pinned_quest_missing",
                )
            else:
                self.chain.pin_entry(preferred, revision=self.active_catalog.revision)
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

    def begin_accept(self, quest_id: str) -> QuestRef:
        if self.pending_accept is not None:
            raise RuntimeError("quest acceptance is already in progress")
        if not self.available_snapshot_fresh or not self.active_snapshot_fresh:
            raise RuntimeError("quest acceptance requires fresh snapshots")
        if not self.intake_queue or self.intake_queue[0].id != quest_id:
            raise RuntimeError("accepted quest does not match intake queue head")
        self.pending_accept = self.intake_queue[0]
        return self.pending_accept

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

    def expire_available_snapshot(self) -> None:
        self.available_snapshot_fresh = False


def _normalized_title(value: str) -> str:
    return " ".join(str(value or "").casefold().split())
