from __future__ import annotations

import json
from pathlib import Path
import re
import time

from src.antibot_cv.automation.actions import ActionRequest
from src.antibot_cv.automation.quest_catalog import QuestCatalogError
from src.antibot_cv.automation.quest_catalog_navigation import (
    CatalogNavigationOutcome,
    CatalogNavigationStatus,
    CatalogSettleStatus,
    CatalogSnapshotEvidence,
    make_pending_catalog_navigation,
    settle_catalog_snapshot,
    snapshot_epoch_seconds,
)
from src.antibot_cv.automation.quest_active_catalog_navigation import (
    ActiveCatalogSnapshotEvidence,
    make_pending_active_catalog_navigation,
    settle_active_catalog_snapshot,
)
from src.antibot_cv.automation.quest_acceptance_coordinator import QuestAcceptanceCoordinatorMixin
from src.antibot_cv.automation.quest_chat_progress_coordinator import (
    QuestChatProgressCoordinatorMixin,
)
from src.antibot_cv.automation.quest_ordered_handoff_coordinator import (
    QuestOrderedHandoffCoordinatorMixin,
)
from src.antibot_cv.automation.quest_ordered_npc_handoff import (
    OrderedHandoffStatus,
    parse_ordered_npc_handoff,
)
from src.antibot_cv.automation.quest_director_policy import QuestDirectorIntent
from src.antibot_cv.automation.quest_director_runtime import QuestDirectorRuntime
from src.antibot_cv.automation.quest_dialogue_runtime import (
    QuestDialogueError,
    QuestDialogueIntent,
    QuestDialoguePhase,
    QuestDialogueRuntime,
)
from src.antibot_cv.automation.quest_intake_runtime import QuestIntakeRuntime
from src.antibot_cv.automation.quest_objective_router import (
    ObjectiveRouteKind,
    ObjectiveRouteStatus,
    classify_objective,
)
from src.antibot_cv.automation.quest_route_binding import (
    route_binding_evidence,
    validate_quest_route_binding,
)
from src.antibot_cv.automation.quest_turnin_coordinator import QuestTurnInCoordinatorMixin
from src.antibot_cv.automation.quest_turnin_runtime import QuestTurnInPhase
from src.antibot_cv.automation.gathering_activity_runtime import (
    GatheringPlanStatus,
    parse_gathering_plan,
)
from src.antibot_cv.automation.quest_policy import (
    Quest as PolicyQuest,
    QuestDecision as PolicyQuestDecision,
    QuestIdentity,
    QuestIntent,
    QuestObjectiveKind,
    QuestPolicy,
    QuestProgress,
    QuestSnapshot as PolicyQuestSnapshot,
    QuestStatus,
)
from src.antibot_cv.automation.runtime_helpers import (
    clean_quest_route_label as _clean_quest_route_label,
    extract_quest_combat_targets as _extract_quest_combat_targets,
    normalized_phrase_matches as _normalized_phrase_matches,
    snapshot_epoch_seconds as _snapshot_epoch_seconds,
    same_location_name as _same_location_name,
)
from src.antibot_cv.automation.state_machine import GameState


class QuestRefreshRuntimeMixin:
    """Coordinate quest catalog refresh and non-combat objective execution."""

    def _maybe_start_quest_refresh(self) -> bool:
        config = self.config.leveling
        if not config.enabled or self.state_machine.state is GameState.RESTING:
            return False
        if self._quest_director is not None:
            pending_active_navigation = self._quest_director.chain.pending_active_catalog_navigation
            if pending_active_navigation is not None:
                self._quest_active_snapshot_requested = True
                self._quest_active_page_requested = pending_active_navigation.page
                self._quest_active_request_snapshot_id = pending_active_navigation.baseline_snapshot_id
                if time.time() >= pending_active_navigation.deadline:
                    return self._expire_active_catalog_navigation(
                        "active_catalog_navigation_settle_expired",
                        pending_active_navigation,
                    )
                return True
            pending_navigation = self._quest_director.chain.pending_catalog_navigation
            if pending_navigation is not None:
                self._quest_catalog_page_requested = pending_navigation.page
                self._quest_catalog_request_snapshot_id = pending_navigation.baseline_snapshot_id
                if time.time() >= pending_navigation.deadline:
                    return self._stop_leveling_unsafe("catalog_navigation_settle_expired")
                return True
            if self._handle_pending_quest_chat_refresh():
                return True
            if (
                self._quest_director.active_objective is not None
                and self._quest_director.active_snapshot_fresh
                and self.session.completed_cycles >= self._next_quest_refresh_cycle
            ):
                return self._request_active_quest_snapshot("quest_objective_victory_refresh")
            decision = self._quest_director_decision()
            if decision is None:
                return False
            if decision.intent is QuestDirectorIntent.STOP_UNSAFE:
                return self._stop_leveling_unsafe(f"quest_director:{decision.reason}")
            if decision.intent is QuestDirectorIntent.REFRESH_AVAILABLE:
                if not self._quest_director.refresh_in_progress:
                    self._quest_director.begin_catalog_refresh()
                if self._quest_catalog_page_requested is not None:
                    return True
                return self._request_available_quest_page(
                    decision.reason,
                    failure_reason="quest_catalog_open_failed",
                )
            if decision.intent is QuestDirectorIntent.REFRESH_ACTIVE:
                return self._request_active_quest_snapshot(decision.reason)
            if decision.intent is QuestDirectorIntent.ACCEPT_QUEST:
                if decision.quest is None:
                    return self._stop_leveling_unsafe("quest_accept_identity_missing")
                return self._begin_quest_acceptance(decision.quest)
            if decision.intent is QuestDirectorIntent.PROFIT_FARM:
                interval = max(1, int(config.quest_refresh_every_cycles))
                if self._quest_director_next_farm_refresh_cycle is None:
                    self._quest_director_next_farm_refresh_cycle = self.session.completed_cycles + interval
                elif self.session.completed_cycles >= self._quest_director_next_farm_refresh_cycle:
                    self._quest_director.expire_available_snapshot()
                    self._quest_director_next_farm_refresh_cycle = None
                    return self._maybe_start_quest_refresh()
                return False
            if decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE:
                if self.current_page_kind == "quests":
                    if (
                        self._quest_director.active_objective is None
                        and decision.quest is not None
                    ):
                        return self._begin_non_combat_quest_executor(decision.quest.id)
                    if self.state_machine.state is not GameState.QUEST_REFRESH_PENDING:
                        self._quest_refresh_requested_monotonic = time.monotonic()
                        self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason="quest_active_execution_ready")
                    return True
                return False
            if decision.intent is QuestDirectorIntent.WAIT:
                return True
        if int(config.quest_refresh_every_cycles) <= 0:
            return False
        if self.session.completed_cycles < self._next_quest_refresh_cycle:
            return False
        if self.current_page_kind == "quests":
            self._quest_refresh_requested_monotonic = time.monotonic()
            self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason="quest_page_already_open")
            return True
        if self.current_page_kind not in {"hunt", "area", "main"}:
            return False
        request = ActionRequest(
            "open_quests",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": "periodic_quest_refresh"},
        )
        if not self.action_executor.execute(request):
            if (
                self.config.leveling.auto_navigate_quest_targets
                or self.config.leveling.autonomous_quest_director
            ):
                return self._stop_leveling_unsafe("quest_refresh_open_failed")
            interval = max(1, int(config.quest_refresh_every_cycles))
            self._next_quest_refresh_cycle = self.session.completed_cycles + interval
            self.logger.log_event(
                "quest_refresh_skipped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="quest_control_missing_optional",
                next_refresh_cycle=self._next_quest_refresh_cycle,
            )
            return False
        self._quest_refresh_requested_monotonic = time.monotonic()
        self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason="periodic_quest_refresh")
        return True

    def _request_available_quest_page(
        self, reason: str, *, failure_reason: str
    ) -> bool:
        """Submit one guarded available-catalogue page request without duplicates."""

        director = self._quest_director
        if director is None:
            return self._stop_leveling_unsafe("quest_catalog_director_missing")
        if self._quest_catalog_page_requested is not None:
            return True
        page = director.catalog.next_page
        if page is None:
            return True
        try:
            pending = self._stage_catalog_navigation(page)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_catalog_stage:{exc}")
        request = ActionRequest(
            "open_quest_catalog",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": reason, "page": page},
        )
        self._quest_catalog_page_requested = page
        self._quest_catalog_request_snapshot_id = pending.baseline_snapshot_id
        self._invalidate_quest_snapshot_cache()
        sink = self.action_executor.sink
        if hasattr(sink, "last_catalog_navigation_outcome"):
            sink.last_catalog_navigation_outcome = None
        succeeded = self.action_executor.execute(request)
        outcome = getattr(sink, "last_catalog_navigation_outcome", None)
        if outcome is None and succeeded:
            outcome = CatalogNavigationOutcome(
                CatalogNavigationStatus.CONFIRMED,
                True,
                pending.destination,
                pending.client_id,
                reason="non_live_sink_confirmed",
            )
        if not succeeded or not isinstance(outcome, CatalogNavigationOutcome):
            if outcome is None or outcome.status is CatalogNavigationStatus.NOT_ISSUED:
                if not self._rollback_catalog_navigation(director, pending):
                    return True
            return self._stop_leveling_unsafe(failure_reason)
        if outcome.status is CatalogNavigationStatus.NOT_ISSUED:
            if not self._rollback_catalog_navigation(director, pending):
                return True
            return self._stop_leveling_unsafe(failure_reason)
        if not outcome.client_id or outcome.client_id != pending.client_id:
            return self._stop_leveling_unsafe("catalog_navigation_identity_mismatch")
        self._quest_refresh_requested_monotonic = time.monotonic()
        if self.state_machine.state is not GameState.QUEST_REFRESH_PENDING:
            self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason=reason)
        return True

    def _rollback_catalog_navigation(self, director, pending) -> bool:
        try:
            director.chain.clear_catalog_navigation(pending)
        except (OSError, RuntimeError, ValueError) as exc:
            self._stop_leveling_unsafe(f"quest_catalog_rollback:{exc}")
            return False
        self._quest_catalog_page_requested = None
        self._quest_catalog_request_snapshot_id = None
        return True

    def _stage_catalog_navigation(self, page: int):
        director = self._quest_director
        if director is None:
            raise RuntimeError("quest catalog director missing")
        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {
            "DryRunActionSink",
            "ReplayActionSink",
        }
        client_id = self._last_state_snapshot_client_id or self.browser_client_id or ""
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        client = global_browser_injector().client_snapshot(client_id) if client_id else {}
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        snapshot = self._state_snapshot_cache if isinstance(self._state_snapshot_cache, dict) else {}
        # NPC/dialogue mutations intentionally invalidate the parsed DOM cache.
        # Catalog navigation still needs a fresh causal baseline before
        # dispatch; acquire it read-only instead of fabricating timestamps or
        # reusing the pre-dialogue snapshot.
        if not replay and (
            not snapshot.get("snapshotId")
            or snapshot_epoch_seconds(snapshot.get("generatedAt")) is None
        ):
            fresh_snapshot = self._state_snapshot_via_injector(force=True)
            if isinstance(fresh_snapshot, dict):
                snapshot = fresh_snapshot
                self._current_state_snapshot_id = (
                    str(snapshot.get("snapshotId") or "").strip() or None
                )
        sections = snapshot.get("sections")
        quest_section = sections.get("quests") if isinstance(sections, dict) else None
        quest_data = quest_section.get("data") if isinstance(quest_section, dict) else None
        current_href = str(quest_data.get("href") or "") if isinstance(quest_data, dict) else ""
        if not current_href:
            current_href = str(client.get("href") or "")
        if replay:
            client_id = client_id or "replay-client"
            profile_id = profile_id or "replay-profile"
            tab_id = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else 0
            current_href = current_href or "https://3kingdoms.ru/main.php"
        if not client_id or not profile_id or not isinstance(tab_id, int) or isinstance(tab_id, bool):
            raise ValueError("catalog navigation browser identity missing")
        issued_at = time.time()
        baseline_generated_at = snapshot_epoch_seconds(snapshot.get("generatedAt"))
        if baseline_generated_at is not None and baseline_generated_at > issued_at:
            clock_lead = baseline_generated_at - issued_at
            if clock_lead > 1.0:
                raise ValueError("catalog navigation snapshot clock lead is unsafe")
            issued_at = baseline_generated_at
        try:
            pending = make_pending_catalog_navigation(
                client_id=client_id,
                profile_id=profile_id,
                tab_id=tab_id,
                page=page,
                current_href=current_href,
                baseline_snapshot_id=self._current_state_snapshot_id or ("replay-baseline" if replay else ""),
                baseline_generated_at=snapshot.get("generatedAt") or (issued_at - 0.001 if replay else None),
                issued_at=issued_at,
            )
        except ValueError as exc:
            raise ValueError(
                f"{exc}: client={bool(client_id)}, profile={bool(profile_id)}, "
                f"tab={isinstance(tab_id, int) and not isinstance(tab_id, bool)}, "
                f"snapshot={bool(self._current_state_snapshot_id)}, href={bool(current_href)}, "
                f"generated={baseline_generated_at is not None}"
            ) from exc
        director.chain.stage_catalog_navigation(pending)
        return pending

    def _catalog_snapshot_evidence(self, quest_data, pending):
        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {
            "DryRunActionSink",
            "ReplayActionSink",
        }
        client_id = self._last_state_snapshot_client_id or self.browser_client_id or ""
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        client = global_browser_injector().client_snapshot(client_id) if client_id else {}
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        if replay:
            client_id = client_id or pending.client_id
            profile_id = profile_id or pending.profile_id
            tab_id = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else pending.tab_id
        generated_at = snapshot_epoch_seconds(quest_data.get("generatedAt"))
        if generated_at is None and replay:
            generated_at = pending.issued_at
        return CatalogSnapshotEvidence(
            client_id=client_id,
            profile_id=profile_id,
            tab_id=tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else None,
            snapshot_id=str(quest_data.get("snapshotId") or "").strip(),
            generated_at=generated_at,
            load_status=str(quest_data.get("loadStatus") or "").strip().casefold(),
            truncated=quest_data.get("truncated") if isinstance(quest_data.get("truncated"), bool) else None,
            page_kind=str(quest_data.get("pageKind") or ("quests" if replay else "")).strip().casefold(),
            mode=str(quest_data.get("mode") or "").strip().casefold(),
            page=quest_data.get("currentPage") if isinstance(quest_data.get("currentPage"), int) and not isinstance(quest_data.get("currentPage"), bool) else None,
            href=str(quest_data.get("href") or (pending.destination if replay else "")).strip(),
        )

    def _request_active_quest_snapshot(self, reason: str) -> bool:
        director = self._quest_director
        if director is None:
            return self._stop_leveling_unsafe("quest_director_missing")
        # Resource recovery owns the browser while resting.  Defer the quest
        # refresh until RESTING releases back to LOCATION_SEARCH.
        if self.state_machine.state is GameState.RESTING:
            return False
        if self._quest_active_page_requested is not None:
            return True
        if time.monotonic() < self._quest_active_navigation_retry_monotonic:
            return True
        if self._quest_active_navigation_attempts >= 2:
            return self._expire_active_catalog_navigation(
                "active_catalog_navigation_not_issued_exhausted",
                director.chain.pending_active_catalog_navigation,
            )
        if not self._quest_active_snapshot_requested:
            director.begin_active_refresh(
                after_confirmed_victory=reason == "quest_objective_victory_refresh"
            )
            self._quest_active_snapshot_requested = True
            self._quest_active_navigation_attempts = 0
            self._quest_active_navigation_retry_monotonic = 0.0
        page = director.active_catalog.next_page
        if page is None:
            self._quest_active_snapshot_requested = False
            return True
        try:
            pending = self._stage_active_catalog_navigation(page)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_active_catalog_stage:{exc}")
        request = ActionRequest(
            "open_active_quest_page",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": reason, "page": page},
        )
        self._quest_active_page_requested = page
        self._quest_active_request_snapshot_id = pending.baseline_snapshot_id
        self._invalidate_quest_snapshot_cache()
        # Claim orchestration ownership before the browser mutation.  Doing it
        # afterwards allows a concurrent resource observation to enter RESTING
        # while the injector call is in flight, producing an invalid transition.
        if self.state_machine.state is not GameState.QUEST_REFRESH_PENDING:
            self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason=reason)
        sink = self.action_executor.sink
        if hasattr(sink, "last_active_catalog_navigation_outcome"):
            sink.last_active_catalog_navigation_outcome = None
        succeeded = self.action_executor.execute(request)
        outcome = getattr(sink, "last_active_catalog_navigation_outcome", None)
        if outcome is None and succeeded:
            outcome = CatalogNavigationOutcome(
                CatalogNavigationStatus.CONFIRMED, True, pending.destination,
                pending.client_id, reason="non_live_sink_confirmed",
            )
        if isinstance(outcome, CatalogNavigationOutcome):
            if outcome.status is CatalogNavigationStatus.NOT_ISSUED:
                try:
                    director.chain.clear_active_catalog_navigation(pending)
                except (OSError, RuntimeError, ValueError) as exc:
                    return self._stop_leveling_unsafe(f"quest_active_catalog_rollback:{exc}")
                self._quest_active_page_requested = None
                self._quest_active_request_snapshot_id = None
                self._quest_active_navigation_attempts += 1
                self._quest_active_navigation_retry_monotonic = time.monotonic() + 0.25
                self._quest_refresh_requested_monotonic = (
                    self._quest_refresh_requested_monotonic or time.monotonic()
                )
                return True
            elif not outcome.client_id or outcome.client_id != pending.client_id:
                return self._stop_leveling_unsafe("active_catalog_navigation_identity_mismatch")
        if not succeeded:
            return self._stop_leveling_unsafe("quest_active_refresh_open_failed")
        self._quest_refresh_requested_monotonic = time.monotonic()
        return True

    def _expire_active_catalog_navigation(self, reason: str, pending: object = None) -> bool:
        director = self._quest_director
        if director is not None and pending is not None:
            try:
                director.chain.clear_active_catalog_navigation(pending)
            except (OSError, RuntimeError, ValueError) as exc:
                return self._stop_leveling_unsafe(f"quest_active_catalog_expire_clear:{exc}")
        self._quest_active_snapshot_requested = False
        self._quest_active_page_requested = None
        self._quest_active_request_snapshot_id = None
        self._quest_active_navigation_attempts = 0
        self._quest_active_navigation_retry_monotonic = 0.0
        return self._stop_leveling_unsafe(reason)

    def _stage_active_catalog_navigation(self, page: int):
        director = self._quest_director
        if director is None:
            raise RuntimeError("active quest catalog director missing")
        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {
            "DryRunActionSink", "ReplayActionSink",
        }
        client_id = self._last_state_snapshot_client_id or self.browser_client_id or ""
        from src.antibot_cv.automation.browser_injector import global_browser_injector
        client = global_browser_injector().client_snapshot(client_id) if client_id else {}
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        snapshot = self._state_snapshot_cache if isinstance(self._state_snapshot_cache, dict) else {}
        if not replay and (
            not snapshot.get("snapshotId")
            or snapshot_epoch_seconds(snapshot.get("generatedAt")) is None
        ):
            fresh_snapshot = self._state_snapshot_via_injector(force=True)
            if isinstance(fresh_snapshot, dict):
                snapshot = fresh_snapshot
                self._current_state_snapshot_id = (
                    str(snapshot.get("snapshotId") or "").strip() or None
                )
        sections = snapshot.get("sections")
        quest_section = sections.get("quests") if isinstance(sections, dict) else None
        quest_data = quest_section.get("data") if isinstance(quest_section, dict) else None
        current_href = str(quest_data.get("href") or "") if isinstance(quest_data, dict) else ""
        current_href = current_href or str(client.get("href") or "")
        if replay:
            client_id = client_id or "replay-client"
            profile_id = profile_id or "replay-profile"
            tab_id = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else 0
            current_href = current_href or "https://3kingdoms.ru/main.php"
        if not client_id or not profile_id or not isinstance(tab_id, int) or isinstance(tab_id, bool):
            raise ValueError("active catalog navigation browser identity missing")
        issued_at = time.time()
        baseline_generated_at = snapshot_epoch_seconds(snapshot.get("generatedAt"))
        if baseline_generated_at is not None and baseline_generated_at > issued_at:
            if baseline_generated_at - issued_at > 1.0:
                raise ValueError("active catalog navigation snapshot clock lead is unsafe")
            issued_at = baseline_generated_at
        pending = make_pending_active_catalog_navigation(
            client_id=client_id, profile_id=profile_id, tab_id=tab_id, page=page,
            current_href=current_href,
            baseline_snapshot_id=self._current_state_snapshot_id or ("replay-baseline" if replay else ""),
            baseline_generated_at=snapshot.get("generatedAt") or (issued_at - 0.001 if replay else None),
            baseline_revision=(quest_data or {}).get("documentRevision") or (quest_data or {}).get("navigationRevision") or "",
            issued_at=issued_at,
        )
        director.chain.stage_active_catalog_navigation(pending)
        return pending

    def _active_catalog_snapshot_evidence(self, quest_data, pending):
        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {
            "DryRunActionSink", "ReplayActionSink",
        }
        client_id = self._last_state_snapshot_client_id or self.browser_client_id or ""
        from src.antibot_cv.automation.browser_injector import global_browser_injector
        client = global_browser_injector().client_snapshot(client_id) if client_id else {}
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        if replay:
            client_id = client_id or pending.client_id
            profile_id = profile_id or pending.profile_id
            tab_id = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else pending.tab_id
        generated_at = snapshot_epoch_seconds(quest_data.get("generatedAt"))
        if generated_at is None and replay:
            generated_at = pending.issued_at
        return ActiveCatalogSnapshotEvidence(
            client_id, profile_id,
            tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else None,
            str(quest_data.get("snapshotId") or "").strip(), generated_at,
            str(quest_data.get("loadStatus") or "").strip().casefold(),
            quest_data.get("truncated") if isinstance(quest_data.get("truncated"), bool) else None,
            str(quest_data.get("pageKind") or ("quests" if replay else "")).strip().casefold(),
            str(quest_data.get("mode") or "").strip().casefold(),
            quest_data.get("currentPage") if isinstance(quest_data.get("currentPage"), int) and not isinstance(quest_data.get("currentPage"), bool) else None,
            str(pending.destination if replay else quest_data.get("href") or "").strip(),
            str(quest_data.get("documentRevision") or quest_data.get("navigationRevision") or "").strip(),
        )

    def _prepare_post_revive_active_quest_refresh(self) -> None:
        """Require a complete fresh active catalogue before quest resumption."""

        director = self._quest_director
        if director is None:
            return
        director.begin_active_refresh()
        self._quest_active_snapshot_requested = True
        self._quest_active_page_requested = 0
        self._quest_active_request_snapshot_id = self._current_state_snapshot_id

    def _invalidate_quest_snapshot_cache(self) -> None:
        self._state_snapshot_cache = None
        self._state_snapshot_cache_sections = None
        self._last_state_snapshot_monotonic = None
        self._last_state_snapshot_success_monotonic = None

    def _begin_non_combat_quest_executor(self, quest_id: str) -> bool:
        """Choose a bounded executor for a non-monster quest step."""

        director = self._quest_director
        if director is None:
            return self._stop_leveling_unsafe("quest_executor_director_missing")
        entry = next(
            (candidate for candidate in director.active_catalog.result if candidate.id == quest_id),
            None,
        )
        if entry is None:
            return self._stop_leveling_unsafe("quest_executor_entry_missing")
        route_plan = director.active_route_plan
        if route_plan is None or route_plan.quest_id != quest_id:
            route_plan = classify_objective(entry)
        self.logger.log_event(
            "quest_objective_route_planned",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=quest_id,
            quest_title=entry.title,
            status=route_plan.status.value,
            kind=route_plan.kind.value,
            reason=route_plan.reason,
            unmet_requirements=[
                {"ordinal": item.ordinal, "kind": item.kind.value, "text": item.text}
                for item in route_plan.unmet_requirements
            ],
        )
        if route_plan.status is ObjectiveRouteStatus.UNSAFE:
            return self._stop_leveling_unsafe(f"quest_objective_route:{route_plan.reason}")
        ordered_handoff = parse_ordered_npc_handoff(entry)
        if ordered_handoff.status is OrderedHandoffStatus.READY:
            return self._begin_ordered_npc_handoff(entry)
        if route_plan.kind is ObjectiveRouteKind.TURN_IN:
            # The director pins a previously existing active quest only while
            # producing EXECUTE_ACTIVE.  The refresh loop's earlier turn-in
            # check therefore could not see a lease yet.  Re-enter the typed
            # turn-in coordinator now that the exact quest/fingerprint is
            # pinned; never downgrade a completed step to a dialogue route.
            if self._maybe_begin_quest_turn_in():
                return True
            return self._stop_leveling_unsafe("quest_turn_in_not_initialized_after_pin")
        if route_plan.kind in {
            ObjectiveRouteKind.NPC_PURCHASE,
            ObjectiveRouteKind.AUCTION_ACQUISITION,
            ObjectiveRouteKind.COMPOSITE,
            ObjectiveRouteKind.LOCATION_VISIT,
            ObjectiveRouteKind.UNSUPPORTED,
        }:
            # Planning evidence is not authorization.  In particular, purchase
            # requirements remain non-actionable until a typed guarded executor
            # can bind an exact NPC, item, price, quantity, and fresh snapshot.
            return self._defer_active_quest(
                quest_id,
                f"objective_route:{route_plan.kind.value}",
            )
        if route_plan.kind is not ObjectiveRouteKind.GATHER_RESOURCE:
            return self._begin_quest_dialogue(quest_id)
        plan = parse_gathering_plan(entry)
        self.logger.log_event(
            "quest_gathering_plan",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=quest_id,
            quest_title=entry.title,
            status=plan.status.value,
            activity=plan.activity.value if plan.activity is not None else None,
            requirements=[{"name": item.name, "required": item.required} for item in plan.requirements],
            reason=plan.reason,
        )
        if plan.status is GatheringPlanStatus.READY:
            return self._defer_active_quest(quest_id, "gathering_node_discovery_required")
        return self._defer_active_quest(quest_id, f"gathering_plan:{plan.reason}")

    def _defer_active_quest(self, quest_id: str, detail: str) -> bool:
        """Persist a non-terminal executor wait without performing an action."""

        director = self._quest_director
        if director is None:
            return self._stop_leveling_unsafe("quest_defer_director_missing")
        normalized_detail = str(detail or "").strip()
        if "ambiguous" in normalized_detail:
            reason = "dialogue_choice_ambiguous"
        elif normalized_detail.startswith("gathering_"):
            reason = "gathering_unavailable"
        elif normalized_detail.startswith("quest_dialogue"):
            reason = "dialogue_unavailable"
        elif normalized_detail.startswith("objective_route:"):
            reason = "objective_executor_unavailable"
        else:
            return self._stop_leveling_unsafe(f"quest_deferral_unclassified:{normalized_detail}")
        try:
            director.quarantine_active_quest(quest_id, reason)
        except (RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_defer_failed:{exc}")
        self._quest_dialogue.pending = None
        self._quest_policy_intent = None
        self.logger.log_event(
            "quest_unsupported_skipped",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=quest_id,
            reason=reason,
            detail=normalized_detail[:500],
            lease_retained=False,
        )
        return True

    def _begin_quest_dialogue(self, quest_id: str) -> bool:
        director = self._quest_director
        if director is None:
            return self._stop_leveling_unsafe("quest_dialogue_director_missing")
        if self._quest_dialogue.pending is not None:
            return True
        entry = next(
            (candidate for candidate in director.active_catalog.result if candidate.id == quest_id),
            None,
        )
        if entry is None:
            return self._stop_leveling_unsafe("quest_dialogue_entry_missing")
        already_at_location = False
        try:
            parsed = self._quest_dialogue.begin(entry, already_at_location=False)
            already_at_location = (
                _same_location_name(self.current_location_name, parsed.objective.location)
                or _same_location_name(self._last_alive_location_name, parsed.objective.location)
            )
            if already_at_location:
                self._quest_dialogue.pending = None
                parsed = self._quest_dialogue.begin(entry, already_at_location=True)
        except (QuestDialogueError, RuntimeError, ValueError) as exc:
            reason = exc.unsafe_reason if isinstance(exc, QuestDialogueError) else str(exc)
            return self._defer_active_quest(quest_id, f"quest_dialogue_begin:{reason}")
        self.logger.log_event(
            "quest_dialogue_started",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=quest_id,
            quest_title=parsed.objective.quest_title,
            npc_query=parsed.objective.npc_query,
            location=parsed.objective.location,
            already_at_location=already_at_location,
        )
        if already_at_location:
            return self._open_area_for_quest_dialogue("quest_dialogue_local_location")
        return self._start_location_route(
            parsed.objective.location,
            kind="quest_dialogue",
            reason="quest_dialogue_route",
        )

    def _open_area_for_quest_dialogue(self, reason: str) -> bool:
        request = ActionRequest(
            "open_area",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": reason},
        )
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe("quest_dialogue_area_open_failed")
        self._invalidate_quest_snapshot_cache()
        self._quest_refresh_requested_monotonic = time.monotonic()
        if self.state_machine.state is not GameState.QUEST_REFRESH_PENDING:
            self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason=reason)
        return True

    def _on_quest_dialogue_route_arrived(self, reason: str) -> bool:
        try:
            pending = self._quest_dialogue.mark_route_arrived()
        except RuntimeError as exc:
            return self._stop_leveling_unsafe(f"quest_dialogue_route_arrival:{exc}")
        if not _same_location_name(self.current_location_name, pending.objective.location):
            return self._stop_leveling_unsafe("quest_dialogue_route_arrival_mismatch")
        return self._open_area_for_quest_dialogue(reason)

    def _quest_dialogue_snapshot_pending(self, unsafe_reason: str) -> bool:
        if unsafe_reason not in {
            "dialogue_npc_snapshot_invalid",
            "dialogue_snapshot_invalid",
            "dialogue_action_missing",
            "dialogue_action_not_advanced",
            # The confirmed NPC click may be observed one controller pass
            # before the game's quest controls render.  Use the same bounded
            # causal settle window as turn-in instead of stopping on that
            # transient empty dialogue page.
            "dialogue_open_missing_or_ambiguous",
            # Immediately after an NPC mutation the old and new dialogue
            # controls can briefly coexist in the DOM.  Re-observe that
            # frame inside the existing bounded settle window; a persistent
            # branch ambiguity still expires and stops fail-closed.
            "dialogue_action_ambiguous",
        }:
            return False
        started = self._quest_refresh_requested_monotonic or time.monotonic()
        timeout_ms = max(1000, int(self.config.leveling.quest_refresh_timeout_ms))
        return (time.monotonic() - started) * 1000 < timeout_ms

    def _handle_pending_quest_dialogue(self) -> bool:
        pending = self._quest_dialogue.pending
        if pending is None:
            return False
        if pending.phase is QuestDialoguePhase.ROUTE:
            return True
        if pending.phase is QuestDialoguePhase.VERIFY_ACTIVE:
            director = self._quest_director
            if director is None:
                return self._stop_leveling_unsafe("quest_dialogue_director_missing")
            if not director.active_snapshot_fresh:
                if self._quest_active_page_requested is None:
                    return self._request_active_quest_snapshot("quest_dialogue_verify_active")
                return True
            entry = next(
                (
                    candidate
                    for candidate in director.active_catalog.result
                    if candidate.id == pending.objective.quest_id
                ),
                None,
            )
            if entry is None:
                try:
                    director.confirm_terminal_removal(pending.objective.quest_id)
                    self._quest_dialogue.finish_verified(
                        quest_id=pending.objective.quest_id,
                        previous_fingerprint=pending.objective.fingerprint,
                    )
                except RuntimeError as exc:
                    return self._stop_leveling_unsafe(
                        f"quest_dialogue_terminal_evidence_unverified:{exc}"
                    )
                self._quest_policy_intent = None
                return True
            from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint

            refreshed_fingerprint, reason = quest_step_fingerprint(entry)
            if refreshed_fingerprint is None or refreshed_fingerprint == pending.objective.fingerprint:
                return self._stop_leveling_unsafe(
                    f"quest_dialogue_step_not_advanced:{reason or 'fingerprint_unchanged'}"
                )
            self._quest_dialogue.finish_verified(
                quest_id=pending.objective.quest_id,
                previous_fingerprint=pending.objective.fingerprint,
            )
            self._quest_policy_intent = None
            return True
        if pending.phase is QuestDialoguePhase.NPC_LOOKUP:
            if self.current_page_kind != "area":
                if (
                    pending.deadline_monotonic <= 0
                    or time.monotonic() >= pending.deadline_monotonic
                ):
                    return self._defer_active_quest(
                        pending.objective.quest_id,
                        "quest_dialogue_area_wait_expired",
                    )
                return True
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "area_npc_snapshot",
                {"expectedName": pending.objective.npc_query},
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
            try:
                snapshot = json.loads(result.message) if result.ok else None
                registry = getattr(self, "_quest_world_registry", None)
                if registry is not None and isinstance(snapshot, dict):
                    registry.observe_area_npcs(snapshot)
                decision = self._quest_dialogue.decide_area_npc(snapshot)
            except (json.JSONDecodeError, QuestDialogueError, RuntimeError, ValueError) as exc:
                reason = exc.unsafe_reason if isinstance(exc, QuestDialogueError) else str(exc)
                if isinstance(exc, QuestDialogueError) and self._quest_dialogue_snapshot_pending(reason):
                    return True
                return self._stop_leveling_unsafe(f"quest_dialogue_npc:{reason}")
        elif pending.phase is QuestDialoguePhase.NPC_DIALOG:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "npc_dialog_snapshot",
                {
                    "expectedName": pending.objective.npc_query,
                    "expectedNpcId": pending.npc_id,
                },
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
            try:
                snapshot = json.loads(result.message) if result.ok else None
                registry = getattr(self, "_quest_world_registry", None)
                if registry is not None and isinstance(snapshot, dict):
                    registry.observe_npc_dialog(snapshot, location_id=pending.location_id)
                decision = self._quest_dialogue.decide_dialog(snapshot)
            except (json.JSONDecodeError, QuestDialogueError, RuntimeError, ValueError) as exc:
                reason = exc.unsafe_reason if isinstance(exc, QuestDialogueError) else str(exc)
                if isinstance(exc, QuestDialogueError) and self._quest_dialogue_snapshot_pending(reason):
                    return True
                if reason in {
                    "dialogue_puzzle_unsupported",
                }:
                    return self._defer_active_quest(
                        pending.objective.quest_id,
                        f"quest_dialogue:{reason}",
                    )
                return self._stop_leveling_unsafe(f"quest_dialogue_action:{reason}")
        else:
            return self._stop_leveling_unsafe("quest_dialogue_phase_invalid")
        request = ActionRequest(
            decision.action_type,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata=dict(decision.action_metadata),
        )
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe(decision.action_failure_reason)
        updated = self._quest_dialogue.acknowledge(decision)
        self._invalidate_quest_snapshot_cache()
        self._quest_refresh_requested_monotonic = time.monotonic()
        if decision.intent is QuestDialogueIntent.COMPLETE_STEP:
            if self._quest_director is None:
                return self._stop_leveling_unsafe("quest_dialogue_director_missing")
            self._quest_director.invalidate_active_snapshot()
            self._quest_active_snapshot_requested = False
            self._quest_active_page_requested = None
            self._quest_active_request_snapshot_id = None
            if updated.phase is not QuestDialoguePhase.VERIFY_ACTIVE:
                return self._stop_leveling_unsafe("quest_dialogue_verify_phase_missing")
            return self._request_active_quest_snapshot("quest_dialogue_verify_active")
        return True
