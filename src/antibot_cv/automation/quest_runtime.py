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
from src.antibot_cv.automation.quest_objective_runtime import ObjectiveRefreshState
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
from src.antibot_cv.automation.quest_npc_directory import QuestNpcDirectory
from src.antibot_cv.automation.world_registry import WorldRegistry
from src.antibot_cv.automation.quest_refresh_runtime import QuestRefreshRuntimeMixin


class QuestRuntimeMixin(
    QuestRefreshRuntimeMixin,
    QuestOrderedHandoffCoordinatorMixin,
    QuestChatProgressCoordinatorMixin,
    QuestTurnInCoordinatorMixin,
    QuestAcceptanceCoordinatorMixin,
):
    """Own quest observations, decisions, refreshes, and route handoff."""

    def _init_quest_runtime(self) -> None:
        self._quest_world_registry = None
        if not self.config.dry_run:
            try:
                self._quest_world_registry = WorldRegistry(self.config.world_registry_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.logger.log_event(
                    "quest_world_registry_unavailable",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    reason=str(exc),
                )
        try:
            self._quest_npc_directory = QuestNpcDirectory.from_files(
                self.config.npc_catalog_path,
                self.config.world_registry_path,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # The directory is routing enrichment, never sole authorization.
            # Runtime ID/name observations remain mandatory even if the local
            # generated catalogue is absent or corrupt.
            self._quest_npc_directory = QuestNpcDirectory()
            self.logger.log_event(
                "quest_npc_directory_unavailable",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason=str(exc),
            )
        self._init_quest_turn_in()
        self._init_quest_ordered_handoff()
        self._quest_origin_location_name: str | None = None
        self._quest_target_names: tuple[str, ...] = ()
        self._quest_target_levels: tuple[int, ...] = ()
        self._quest_target_specs: tuple[tuple[str, int], ...] = ()
        self._quest_route_locations: tuple[str, ...] = ()
        self._quest_target_routes: dict[str, tuple[str, ...]] = {}
        self._quest_route_link_label: str | None = None
        self._next_quest_refresh_cycle = (
            0
            if (
                self.config.leveling.auto_navigate_quest_targets
                or self.config.leveling.autonomous_quest_director
            )
            else max(0, int(self.config.leveling.quest_refresh_every_cycles))
        )
        self._quest_refresh_requested_monotonic: float | None = None
        self._quest_active_navigation_attempts = 0
        self._quest_active_navigation_retry_monotonic = 0.0
        self._quest_policy_intent: QuestIntent | None = None
        self._active_quest_id: str | None = None
        self._last_quest_policy_key: tuple[object, ...] | None = None
        self._quest_director = (
            QuestDirectorRuntime(
                refresh_every_completed=max(1, int(self.config.leveling.quest_refresh_every_completed)),
                catalog_max_pages=max(1, int(self.config.leveling.quest_catalog_max_pages)),
                pinned_quest_id=self.config.leveling.pinned_quest_id,
                ignored_quest_ids=self.config.leveling.ignored_quest_ids,
                prefer_active_quests=self.config.leveling.prefer_active_quests,
                chain_state_path=(
                    None
                    if self.config.dry_run
                    else _quest_chain_state_path(
                        self.config.runs_dir,
                        self.config.leveling.required_character_name,
                    )
                ),
            )
            if self.config.leveling.autonomous_quest_director
            else None
        )
        self._quest_catalog_page_requested: int | None = None
        self._quest_catalog_request_snapshot_id: str | None = None
        if self._quest_director is not None and self._quest_director.chain.pending_catalog_navigation is not None:
            restored_navigation = self._quest_director.chain.pending_catalog_navigation
            self._quest_director.begin_catalog_refresh()
            self._quest_catalog_page_requested = restored_navigation.page
            self._quest_catalog_request_snapshot_id = restored_navigation.baseline_snapshot_id
        self._quest_active_snapshot_requested = False
        self._quest_active_page_requested: int | None = None
        self._quest_active_request_snapshot_id: str | None = None
        self._quest_director_next_farm_refresh_cycle: int | None = None
        self._last_quest_director_key: tuple[object, ...] | None = None
        self._quest_intake = QuestIntakeRuntime()
        if self._quest_director is not None:
            staged_npc = (
                self._quest_director.chain.pending_npc_open
                or self._quest_director.chain.pending_npc_dialog
            )
            if staged_npc is not None:
                self._quest_intake.restore_npc_open(
                    staged_npc,
                    confirmed=self._quest_director.chain.pending_npc_dialog is not None,
                )
        self._quest_accept_area_opened_monotonic: float | None = None
        self._quest_accept_area_opened_epoch: float | None = None
        self._quest_accept_area_snapshot_id: str | None = None
        self._quest_accept_last_settle_reason: str | None = None
        self._quest_dialogue = QuestDialogueRuntime()
        self._init_quest_chat_progress()

    def _validate_route_coordinator_binding(
        self,
        recovery_kind: str | None,
        destination_name: str,
    ) -> bool:
        """Require an exact typed coordinator lease before resuming a quest route."""

        if recovery_kind not in {"quest_accept", "quest_dialogue", "quest_turn_in", "quest_ordered_handoff"}:
            if recovery_kind != "quest_location":
                return True
        binding_error = self._route_arrival_binding_error(recovery_kind, destination_name)
        if binding_error is None:
            return True
        reason = {
            "quest_accept": "quest_accept_route_binding_mismatch",
            "quest_dialogue": "quest_dialogue_route_binding_mismatch",
            "quest_turn_in": "quest_turn_in_route_binding_mismatch",
            "quest_ordered_handoff": "quest_ordered_handoff_route_binding_mismatch",
            "quest_location": "quest_location_route_binding_mismatch",
        }[recovery_kind]
        self._stop_leveling_unsafe(reason)
        return False

    def _route_arrival_binding_error(self, recovery_kind: object, target: object) -> str | None:
        evidence = route_binding_evidence(
            recovery_kind,
            target,
            director=self._quest_director,
            intake_pending=self._quest_intake.pending,
            dialogue_pending=self._quest_dialogue.pending,
            turn_in_pending=self._quest_turn_in.pending,
            active_quest_id=self._active_quest_id,
            route_link_label=self._quest_route_link_label,
            target_routes_by_monster=self._quest_target_routes,
            route_locations=self._quest_route_locations,
        )
        return validate_quest_route_binding(evidence)

    def _evaluate_quest_policy(
        self,
        state_snapshot: object,
        quest_section: dict[str, object],
        quest_data: dict[str, object],
    ) -> PolicyQuestDecision:
        self._observe_autonomous_quest_snapshot(quest_data)
        director_quest_id: str | None = None
        if self._quest_director is not None:
            mode = str(quest_data.get("mode") or "").strip().casefold()
            director_decision = self._quest_director_decision()
            if mode == "avail" or (
                mode == "started"
                and director_decision is not None
                and director_decision.intent is not QuestDirectorIntent.EXECUTE_ACTIVE
            ):
                return PolicyQuestDecision(
                    QuestIntent.START_FARM,
                    "quest_director_observation_pending",
                    snapshot_id=str(quest_data.get("snapshotId") or "") or None,
                )
            if (
                mode == "started"
                and director_decision is not None
                and director_decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
                and director_decision.quest is not None
            ):
                director_quest_id = director_decision.quest.id
                if self._quest_director.active_objective is None:
                    return PolicyQuestDecision(
                        QuestIntent.START_FARM,
                        "quest_director_non_combat_execution_pending",
                        quest_id=director_decision.quest.id,
                        quest_title=director_decision.quest.title,
                        snapshot_id=str(quest_data.get("snapshotId") or "") or None,
                    )
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        client_id = self._last_state_snapshot_client_id or self.browser_client_id or ""
        client = global_browser_injector().client_snapshot(client_id)
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        identity = (
            QuestIdentity(client_id, profile_id, int(tab_id))
            if client_id and profile_id and isinstance(tab_id, int) and not isinstance(tab_id, bool)
            else None
        )
        if identity is not None and self._bound_browser_profile_id is None and self._bound_browser_tab_id is None:
            self._bound_browser_profile_id = identity.profile_id
            self._bound_browser_tab_id = identity.tab_id
        expected_identity = (
            QuestIdentity(
                self.browser_client_id or client_id,
                self._bound_browser_profile_id,
                self._bound_browser_tab_id,
            )
            if self._bound_browser_profile_id and self._bound_browser_tab_id is not None
            else None
        )
        raw_items = quest_data.get("items")
        quests: list[PolicyQuest] = []
        active_objective = (
            self._quest_director.active_objective
            if self._quest_director is not None
            else None
        )
        if (
            director_quest_id is not None
            and active_objective is not None
            and active_objective.quest_id == director_quest_id
        ):
            quests.append(
                PolicyQuest(
                    id=active_objective.quest_id,
                    title=active_objective.quest_title,
                    target_mobs=(active_objective.monster.name,),
                    locations=(active_objective.monster.target,),
                    status=QuestStatus.ACTIVE,
                    objective_kind=QuestObjectiveKind.COMBAT,
                    progress=QuestProgress(
                        active_objective.progress,
                        active_objective.required,
                        active_objective.complete,
                        "active_catalog_objective_fingerprint",
                    ),
                )
            )
        elif isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    quests.append(PolicyQuest(None, None))
                    continue
                if director_quest_id is not None and str(item.get("id") or "").strip() != director_quest_id:
                    continue
                objective = str(item.get("objective") or "").strip()
                target_names = _extract_quest_combat_targets(
                    objective,
                    configured_names=self.config.target.allowed_names,
                )
                locations: list[str] = []
                navigation = item.get("navigation")
                if isinstance(navigation, list):
                    for entry in navigation:
                        if not isinstance(entry, dict):
                            continue
                        location_name = _clean_quest_route_label(entry.get("text") or entry.get("title"))
                        if location_name and location_name not in locations:
                            locations.append(location_name)
                status_text = str(item.get("status") or "").strip().casefold()
                try:
                    status = QuestStatus(status_text)
                except ValueError:
                    status = QuestStatus.UNKNOWN
                kind_text = str(item.get("objectiveKind") or "").strip().casefold()
                kind = (
                    QuestObjectiveKind.COMBAT
                    if target_names and kind_text == "combat"
                    else QuestObjectiveKind.UNKNOWN
                )
                quests.append(
                    PolicyQuest(
                        id=str(item.get("id") or "").strip() or None,
                        title=str(item.get("title") or "").strip() or None,
                        target_mobs=target_names,
                        locations=tuple(locations),
                        status=status,
                        objective_kind=kind,
                        progress=self._quest_progress_from_item(item),
                    )
                )
        snapshot_mapping = state_snapshot if isinstance(state_snapshot, dict) else {}
        source = quest_section.get("source")
        source_href = source.get("href") if isinstance(source, dict) else None
        observed = PolicyQuestSnapshot(
            status=str(quest_data.get("loadStatus") or ""),
            quests=tuple(quests),
            current_location=(
                str(quest_data.get("currentLocation") or "").strip()
                or self._quest_origin_location_name
            ),
            snapshot_id=str(snapshot_mapping.get("snapshotId") or quest_data.get("snapshotId") or "") or None,
            generated_at=_snapshot_epoch_seconds(
                snapshot_mapping.get("generatedAt") or quest_data.get("generatedAt")
            ),
            client_id=client_id or None,
            profile_id=profile_id or None,
            tab_id=int(tab_id) if isinstance(tab_id, int) and not isinstance(tab_id, bool) else None,
            href=str(quest_data.get("href") or source_href or "") or None,
            page_kind=str(quest_data.get("pageKind") or self.current_page_kind or "") or None,
        )
        policy = QuestPolicy(
            self._bound_character_name or self.current_character_name or "",
            require_active_quest=(
                self.config.leveling.auto_navigate_quest_targets
                or self.config.leveling.autonomous_quest_director
            ),
            require_route_location=(
                self.config.leveling.auto_navigate_quest_targets
                or self.config.leveling.autonomous_quest_director
            ),
            allow_missing_current_location_route=self.config.leveling.autonomous_quest_director,
            expected_identity=expected_identity,
            max_snapshot_age_s=max(1.0, self.config.leveling.snapshot_stale_timeout_ms / 1000),
        )
        decision = policy.decide(
            character_name=self.current_character_name,
            current_level=self.current_level,
            current_xp=self.current_xp_percent,
            goal_level=int(self.config.leveling.target_level or self.current_level + 1),
            quest_state=observed,
        )
        self._apply_quest_policy_decision(decision)
        return decision

    def _observe_autonomous_quest_snapshot(self, quest_data: dict[str, object]) -> None:
        director = self._quest_director
        if director is None:
            return
        mode = str(quest_data.get("mode") or "").strip().casefold()
        snapshot_id = str(quest_data.get("snapshotId") or "").strip()
        try:
            pending_active_navigation = director.chain.pending_active_catalog_navigation
            if pending_active_navigation is not None:
                active_evidence = self._active_catalog_snapshot_evidence(
                    quest_data, pending_active_navigation
                )
                active_settle = settle_active_catalog_snapshot(
                    pending_active_navigation, active_evidence, now=time.time()
                )
                if active_settle.status in {
                    CatalogSettleStatus.STOP_IDENTITY, CatalogSettleStatus.STOP_EXPIRED,
                }:
                    self._stop_leveling_unsafe(active_settle.reason)
                    return
                if active_settle.status is CatalogSettleStatus.WAIT:
                    return
            pending_navigation = director.chain.pending_catalog_navigation
            if pending_navigation is not None:
                evidence = self._catalog_snapshot_evidence(quest_data, pending_navigation)
                settle = settle_catalog_snapshot(
                    pending_navigation, evidence, now=time.time()
                )
                if settle.status is CatalogSettleStatus.STOP_IDENTITY:
                    self._stop_leveling_unsafe(settle.reason)
                    return
                if settle.status is CatalogSettleStatus.STOP_EXPIRED:
                    self._stop_leveling_unsafe(settle.reason)
                    return
                if settle.status is CatalogSettleStatus.WAIT:
                    return
            if mode == "avail" and director.refresh_in_progress:
                page = quest_data.get("currentPage")
                if (
                    self._quest_catalog_page_requested is None
                    or not isinstance(page, int)
                    or isinstance(page, bool)
                    or page != self._quest_catalog_page_requested
                    or not snapshot_id
                    or snapshot_id == self._quest_catalog_request_snapshot_id
                ):
                    return
                next_page = director.ingest_catalog_page(quest_data)
                if pending_navigation is not None:
                    director.chain.clear_catalog_navigation(pending_navigation)
                self._quest_catalog_page_requested = None
                self._quest_catalog_request_snapshot_id = None
                self.logger.log_event(
                    "quest_catalog_page_observed",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    page=page,
                    page_count=director.catalog.page_count,
                    collected_pages=list(director.catalog.collected_pages),
                    next_page=next_page,
                    complete=director.catalog.complete,
                    available_count=len(director.available_quests),
                    unsupported_available_count=len(director.unsupported_available_entries),
                    unsupported_available=[
                        {"quest_id": item.quest_id, "reason": item.reason}
                        for item in director.unsupported_available_entries
                    ],
                )
            elif mode == "started" and self._quest_active_snapshot_requested:
                page = quest_data.get("currentPage")
                if (
                    self._quest_active_page_requested is None
                    or not isinstance(page, int)
                    or isinstance(page, bool)
                    or page != self._quest_active_page_requested
                    or not snapshot_id
                    or snapshot_id == self._quest_active_request_snapshot_id
                ):
                    return
                next_page = director.ingest_active_page(quest_data)
                if pending_active_navigation is not None:
                    director.chain.clear_active_catalog_navigation(pending_active_navigation)
                self._quest_active_page_requested = None
                self._quest_active_request_snapshot_id = None
                self._quest_active_navigation_attempts = 0
                self._quest_active_navigation_retry_monotonic = 0.0
                if next_page is None:
                    self._quest_active_snapshot_requested = False
                    generated_at = _snapshot_epoch_seconds(quest_data.get("generatedAt"))
                    if generated_at is not None:
                        self._ordered_handoff_active_snapshot_id = snapshot_id
                        self._ordered_handoff_active_snapshot_generated_at = generated_at
                self.logger.log_event(
                    "quest_active_page_observed",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    page=page,
                    page_count=director.active_catalog.page_count,
                    collected_pages=list(director.active_catalog.collected_pages),
                    next_page=next_page,
                    complete=director.active_catalog.complete,
                    active_count=len(director.active_quests),
                )
                if next_page is None:
                    self._reconcile_quest_chat_refresh()
                    self._bound_quest_chat_step()
        except (OSError, QuestCatalogError, RuntimeError, ValueError) as exc:
            self._stop_leveling_unsafe(f"quest_director_snapshot:{exc}")

    def _quest_director_decision(self):
        director = self._quest_director
        if director is None:
            return None
        decision = director.decision(current_level_cap=self.current_level)
        if (
            decision.reason == "staged_intake_confirmed_active"
            and decision.quest is not None
            and self._quest_intake.matches_pending(decision.quest)
        ):
            self._quest_intake.recover_confirmed_active(decision.quest)
            self._quest_answer_stability = None
        key = (
            decision.intent.value,
            decision.reason,
            decision.quest.id if decision.quest else None,
            tuple(quest.id for quest in decision.intake_queue),
        )
        if key != self._last_quest_director_key:
            self._last_quest_director_key = key
            self.logger.log_event(
                "quest_director_decision",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                intent=decision.intent.value,
                reason=decision.reason,
                quest_id=decision.quest.id if decision.quest else None,
                quest_title=decision.quest.title if decision.quest else None,
                quest_location=decision.quest.location if decision.quest else None,
                quest_givers=list(decision.quest.giver_names) if decision.quest else [],
                intake_queue=[quest.id for quest in decision.intake_queue],
                active_catalog_revision=director.active_catalog.revision,
                objective_fingerprint=(
                    director.active_objective.fingerprint
                    if director.active_objective is not None
                    else None
                ),
            )
        return decision

    def _apply_quest_policy_decision(self, decision: PolicyQuestDecision) -> None:
        key = (
            decision.intent.value,
            decision.reason,
            decision.quest_id,
            decision.target_mobs,
            decision.locations,
            decision.progress,
            decision.snapshot_id,
        )
        if key != self._last_quest_policy_key:
            self._last_quest_policy_key = key
            self.logger.log_event(
                "quest_policy_decision",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                intent=decision.intent.value,
                reason=decision.reason,
                quest_id=decision.quest_id,
                quest_title=decision.quest_title,
                target_names=list(decision.target_mobs),
                route_locations=list(decision.locations),
                progress_current=decision.progress.current,
                progress_required=decision.progress.required,
                objective_complete=decision.progress.complete,
                progress_evidence=decision.progress.evidence,
                snapshot_id=decision.snapshot_id,
            )
        self._quest_policy_intent = decision.intent
        if decision.intent is QuestIntent.OBJECTIVE_COMPLETE:
            self._active_quest_id = decision.quest_id
            self._quest_target_names = ()
            self._quest_target_levels = ()
            self._quest_target_specs = ()
            self._quest_route_locations = decision.locations
            self._quest_target_routes = {}
            self._quest_route_link_label = None
            return
        if decision.intent not in {QuestIntent.SELECT_QUEST, QuestIntent.NAVIGATE}:
            self._active_quest_id = None
            self._quest_target_names = ()
            self._quest_target_levels = ()
            self._quest_target_specs = ()
            self._quest_route_locations = ()
            self._quest_target_routes = {}
            self._quest_route_link_label = None
            return
        self._active_quest_id = decision.quest_id
        self._quest_target_names = decision.target_mobs
        self._quest_route_locations = decision.locations
        self._quest_target_routes = {
            target: decision.locations
            for target in decision.target_mobs
        }
        objective = self._quest_director.active_objective if self._quest_director is not None else None
        if objective is not None and objective.quest_id == decision.quest_id:
            ordered_objectives = (objective,)
            if self._quest_director is not None:
                ordered_objectives += tuple(
                    candidate
                    for candidate in self._quest_director.supported_objectives
                    if candidate.quest_id != objective.quest_id
                )
            opportunistic_names: list[str] = list(self._quest_target_names)
            opportunistic_levels: list[int] = []
            opportunistic_specs: list[tuple[str, int]] = []
            for candidate in ordered_objectives:
                if candidate.monster.name not in opportunistic_names:
                    opportunistic_names.append(candidate.monster.name)
                if candidate.monster.level not in opportunistic_levels:
                    opportunistic_levels.append(candidate.monster.level)
                spec = (candidate.monster.name, candidate.monster.level)
                if spec not in opportunistic_specs:
                    opportunistic_specs.append(spec)
            self._quest_target_names = tuple(opportunistic_names)
            self._quest_target_levels = tuple(opportunistic_levels)
            self._quest_target_specs = tuple(opportunistic_specs)
            self._quest_route_link_label = objective.navigator_label
        else:
            self._quest_target_levels = ()
            self._quest_target_specs = ()
            self._quest_route_link_label = None

    @staticmethod
    def _quest_progress_from_item(item: dict[str, object]) -> QuestProgress:
        raw = item.get("progress")
        progress = raw if isinstance(raw, dict) else {}
        current = progress.get("current")
        required = progress.get("required")
        current_value = current if isinstance(current, int) and not isinstance(current, bool) and current >= 0 else None
        required_value = required if isinstance(required, int) and not isinstance(required, bool) and required > 0 else None
        complete = progress.get("complete") is True or (
            current_value is not None and required_value is not None and current_value >= required_value
        )
        evidence = str(progress.get("evidence") or "").strip() or None
        return QuestProgress(current_value, required_value, complete, evidence)

    def _update_quest_target_names(self, quest_data: object) -> None:
        if not isinstance(quest_data, dict):
            return
        items = quest_data.get("items")
        if not isinstance(items, list):
            return
        names: list[str] = []
        route_locations: list[str] = []
        target_routes: dict[str, tuple[str, ...]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            objective = str(item.get("objective") or "").strip()
            navigation = item.get("navigation")
            if not objective:
                continue
            item_routes: list[str] = []
            if isinstance(navigation, list):
                for entry in navigation:
                    if not isinstance(entry, dict):
                        continue
                    location = _clean_quest_route_label(entry.get("text") or entry.get("title"))
                    if not location:
                        continue
                    if location not in item_routes:
                        item_routes.append(location)
                    if location not in route_locations:
                        route_locations.append(location)
            item_targets = _extract_quest_combat_targets(
                objective,
                configured_names=self.config.target.allowed_names,
            )
            for name in item_targets:
                if name not in names:
                    names.append(name)
                target_routes[name] = tuple(item_routes)
        updated_names = tuple(names)
        updated_routes = tuple(route_locations)
        routes_changed = updated_routes != self._quest_route_locations or target_routes != self._quest_target_routes
        if updated_names != self._quest_target_names or routes_changed:
            self._quest_target_names = updated_names
            self._quest_route_locations = updated_routes
            self._quest_target_routes = target_routes
            self.logger.log_event(
                "quest_targets_observed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                target_names=list(self._quest_target_names),
                route_locations=list(self._quest_route_locations),
            )

    def _select_quest_route_target(self) -> str | None:
        configured = str(self.config.leveling.target_location_name or "").strip()
        if configured:
            matches = [
                location
                for location in self._quest_route_locations
                if _normalized_phrase_matches(location, configured)
            ]
            return matches[0] if len(matches) == 1 else None
        for target in self._quest_target_names:
            routes = self._quest_target_routes.get(target, ())
            if len(routes) == 1:
                return routes[0]
        if len(self._quest_route_locations) == 1:
            return self._quest_route_locations[0]
        return None

    def _effective_target_names(self) -> tuple[str, ...]:
        if self._quest_director is not None and self._quest_director.active_objective is not None:
            return self._quest_target_names
        configured = tuple(name for name in self.config.target.allowed_names if name)
        if configured:
            return configured
        if (
            self.config.leveling.auto_navigate_quest_targets
            or self.config.leveling.autonomous_quest_director
        ):
            return self._quest_target_names
        return ()

    def _handle_quest_refresh(self) -> None:
        if self._handle_pending_quest_turn_in():
            return
        if self._handle_pending_quest_dialogue():
            return
        if self._handle_pending_quest_acceptance():
            return
        if self._handle_pending_quest_chat_refresh(allow_active_progress=True):
            return
        started = self._quest_refresh_requested_monotonic or time.monotonic()
        if self._quest_director is not None and self._quest_active_snapshot_requested:
            if self._quest_active_page_requested is not None:
                timeout_ms = max(1000, int(self.config.leveling.quest_refresh_timeout_ms))
                if (time.monotonic() - started) * 1000 >= timeout_ms:
                    self._stop_leveling_unsafe("quest_active_refresh_timeout")
                return
            if not self._quest_director.active_catalog.complete:
                self._request_active_quest_snapshot("quest_active_next_page")
                return
        if self._maybe_begin_quest_turn_in():
            return
        if self._quest_director is not None and self._quest_director.refresh_in_progress:
            if self._quest_catalog_page_requested is not None:
                timeout_ms = max(1000, int(self.config.leveling.quest_refresh_timeout_ms))
                if (time.monotonic() - started) * 1000 >= timeout_ms:
                    self._stop_leveling_unsafe("quest_catalog_page_timeout")
                return
            page = self._quest_director.catalog.next_page
            if page is not None:
                self._request_available_quest_page(
                    "catalogue_next_page",
                    failure_reason="quest_catalog_next_page_failed",
                )
                return
        if self._quest_director is not None and (
            self._quest_director.catalog.complete
            or self._quest_director.chain.lease is not None
            or self._quest_director.active_catalog.complete
        ):
            decision = self._quest_director_decision()
            if decision is not None and decision.intent is QuestDirectorIntent.REFRESH_AVAILABLE:
                if not self._quest_director.refresh_in_progress:
                    self._quest_director.begin_catalog_refresh()
                self._request_available_quest_page(
                    decision.reason,
                    failure_reason="quest_catalog_open_failed",
                )
                return
            if decision is not None and decision.intent is QuestDirectorIntent.REFRESH_ACTIVE:
                if self._quest_active_page_requested is None:
                    self._request_active_quest_snapshot(decision.reason)
                elif (time.monotonic() - started) * 1000 >= max(
                    1000, int(self.config.leveling.quest_refresh_timeout_ms)
                ):
                    self._stop_leveling_unsafe("quest_active_refresh_timeout")
                return
            if decision is not None and decision.intent is QuestDirectorIntent.ACCEPT_QUEST:
                if decision.quest is None:
                    self._stop_leveling_unsafe("quest_accept_identity_missing")
                    return
                self._begin_quest_acceptance(decision.quest)
                return
            if (
                decision is not None
                and decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
                and self._quest_director.active_objective is not None
                and self._quest_director.objective_refresh is not None
                and self._quest_director.objective_refresh.state
                is ObjectiveRefreshState.SAME_STEP
            ):
                # One victory may yield only part of a multi-drop requirement.
                # The authoritative catalogue still exposes the same combat
                # step, so resume hunting rather than waiting on the refresh UI.
                self._finish_quest_refresh_to_hunt("quest_combat_step_still_active")
                return
            if decision is not None and decision.intent is QuestDirectorIntent.PROFIT_FARM:
                self._quest_director_next_farm_refresh_cycle = (
                    self.session.completed_cycles
                    + max(1, int(self.config.leveling.quest_refresh_every_cycles))
                )
                self._finish_quest_refresh_to_hunt("quest_catalog_empty_profit_farm")
                return
            if (
                decision is not None
                and decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
                and self._quest_director.active_objective is None
                and decision.quest is not None
            ):
                self._begin_non_combat_quest_executor(decision.quest.id)
                return
            if decision is not None and decision.intent is QuestDirectorIntent.WAIT:
                # The director owns transitional states such as an accepted
                # quest whose navigation is still being observed.  Do not let
                # the generic policy turn that expected wait into a false
                # navigation failure.
                return
            if decision is not None and decision.intent is QuestDirectorIntent.STOP_UNSAFE:
                self._stop_leveling_unsafe(f"quest_director:{decision.reason}")
                return
        if self.current_page_kind == "quests":
            if (
                self.config.leveling.auto_navigate_quest_targets
                or self.config.leveling.autonomous_quest_director
            ):
                if self._quest_policy_intent is QuestIntent.OBJECTIVE_COMPLETE:
                    self._stop_leveling_unsafe("quest_objective_complete_turn_in_pending")
                    return
                if self._quest_policy_intent is QuestIntent.SELECT_QUEST:
                    self._finish_quest_refresh_to_hunt("quest_target_already_local")
                    return
                if self._quest_policy_intent is not QuestIntent.NAVIGATE:
                    if self._quest_director is not None:
                        # The generic policy is deliberately incomplete while
                        # the director is collecting or reconciling a quest
                        # catalogue.  A later fresh snapshot decides whether
                        # to accept, execute, defer, or stop safely.
                        return
                    self._stop_leveling_unsafe("quest_policy_not_ready_for_navigation")
                    return
                target = self._select_quest_route_target()
                if not target:
                    self.logger.log_event(
                        "quest_route_skipped",
                        state=self.state_machine.state.value,
                        cycle_id=self.session.cycle_id,
                        reason="route_target_missing_or_ambiguous",
                        route_locations=list(self._quest_route_locations),
                    )
                    self._stop_leveling_unsafe("quest_route_target_missing_or_ambiguous")
                    return
                if not self._validate_route_coordinator_binding("quest_location", target):
                    return
                request = ActionRequest(
                    "open_quest_navigator",
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    dry_run=self.config.dry_run,
                    metadata={
                        "reason": "quest_location_route",
                        "target": target,
                        "quest_id": self._active_quest_id,
                        **(
                            {"link_label": self._quest_route_link_label}
                            if self._quest_route_link_label
                            else {}
                        ),
                    },
                )
                self._navigator_existing_client_ids = self._navigator_client_ids_for_parent()
                if not self.action_executor.execute(request):
                    self._stop_leveling_unsafe("quest_navigator_open_failed")
                    return
                self._navigator_target_name = target
                self._navigator_target_kind = (
                    "monster"
                    if self._quest_director is not None
                    and self._quest_director.active_objective is not None
                    else "location"
                )
                self._navigator_opened_monotonic = time.monotonic()
                self._navigator_client_id = None
                self._navigator_client_bound_monotonic = None
                self._navigator_requires_target_selection = True
                self._route_recovery_kind = "quest_location"
                self._route_started_monotonic = None
                self._route_deadline_monotonic = None
                self._route_visited_location_ids.clear()
                self._route_rehydrate_attempts = 0
                self._route_rehydrated_current_ids.clear()
                self._safe_transition(GameState.NAVIGATOR_PENDING, reason="quest_navigator_opened")
                return
            self._finish_quest_refresh_to_hunt("quest_refresh_complete")
            return
        timeout_ms = max(1000, int(self.config.leveling.quest_refresh_timeout_ms))
        if (time.monotonic() - started) * 1000 >= timeout_ms:
            self._stop_leveling_unsafe("quest_refresh_timeout")

    def _finish_quest_refresh_to_hunt(self, reason: str) -> bool:
        request = ActionRequest(
            "open_hunt",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": reason},
        )
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe("quest_refresh_return_failed")
        # A combat-drop objective must not reopen the paginated quest UI after
        # every kill.  Exact chat completion evidence provides the immediate
        # stop signal; this configured interval is only a bounded fallback for
        # objectives whose server message was unavailable.
        interval = (
            max(1, int(self.config.leveling.quest_refresh_every_completed))
            if self._quest_director is not None
            and self._quest_director.active_objective is not None
            else max(1, int(self.config.leveling.quest_refresh_every_cycles))
        )
        self._next_quest_refresh_cycle = self.session.completed_cycles + interval
        self._quest_refresh_requested_monotonic = None
        self._navigator_target_name = None
        self._navigator_target_kind = "location"
        self._navigator_opened_monotonic = None
        self._navigator_client_id = None
        self._navigator_client_bound_monotonic = None
        self._navigator_existing_client_ids.clear()
        self._navigator_requires_target_selection = False
        self._route_recovery_kind = None
        self._route_go_submitted_monotonic = None
        self._route_destination_name = None
        self._route_destination_id = None
        self._route_expected_transitions = None
        self._navigator_arrived_confirmed = False
        self._route_step_submitted_from_id = None
        self._route_step_expected_to_id = None
        self._route_step_submitted_snapshot_id = None
        self._route_step_submitted_monotonic = None
        self._route_started_monotonic = None
        self._route_deadline_monotonic = None
        self._route_visited_location_ids.clear()
        self._route_rehydrate_attempts = 0
        self._route_rehydrated_current_ids.clear()
        self._route_resume_target_name = None
        self._route_resume_recovery_kind = None
        self._reset_location_context()
        self._search_pause_until_monotonic = (
            time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
        )
        self._safe_transition(GameState.LOCATION_SEARCH, reason=reason)
        if self.state_machine.state is GameState.LOCATION_SEARCH:
            self._log_recovery_phase("hunt_opened", reason=reason)
            self._complete_death_recovery_evidence(reason)
        return True


def _quest_chain_state_path(runs_dir: str, character_name: str) -> Path | None:
    raw_identity = str(character_name or "").strip()
    if not raw_identity:
        return None
    identity = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", raw_identity)
    return Path(runs_dir) / "quest_chains" / f"{identity}.json"
