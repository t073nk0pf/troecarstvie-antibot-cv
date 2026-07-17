"""Thin controller integration for the bounded quest turn-in runtime."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import replace

from src.antibot_cv.automation.actions import ActionRequest
from src.antibot_cv.automation.quest_active_catalog import json_safe_active_value
from src.antibot_cv.automation.quest_chain_runtime import ChainRefreshState
from src.antibot_cv.automation.quest_turnin_runtime import (
    QuestTurnInError,
    QuestTurnInIntent,
    QuestTurnInPhase,
    QuestTurnInRuntime,
)
from src.antibot_cv.automation.quest_turnin_reference import derive_turn_in_ref
from src.antibot_cv.automation.quest_objective_router import (
    ObjectiveRouteKind,
    ObjectiveRouteStatus,
    classify_objective,
)
from src.antibot_cv.automation.runtime_helpers import same_location_name
from src.antibot_cv.automation.state_machine import GameState


class QuestTurnInCoordinatorMixin:
    def _init_quest_turn_in(self) -> None:
        self._quest_turn_in = QuestTurnInRuntime()
        self._quest_turn_in_started_monotonic = None
        self._quest_turn_in_verify_retries = 0

    def _maybe_begin_quest_turn_in(self) -> bool:
        if self._quest_turn_in.pending is not None:
            return True
        director = self._quest_director
        if director is None or not director.active_snapshot_fresh or not director.active_catalog.complete:
            return False
        lease = director.chain.lease
        if lease is None:
            return False
        matches = [entry for entry in director.active_catalog.result if entry.id == lease.quest_id]
        if len(matches) != 1:
            return False
        progress = matches[0].data.get("progress")
        chat_evidence = getattr(self, "_quest_chat_terminal_completion_evidence", None)
        inventory_evidence = getattr(
            self, "_quest_inventory_terminal_completion_evidence", None
        )
        completion_evidence = chat_evidence or inventory_evidence
        terminal_collection_confirmed = bool(
            completion_evidence is not None
            and completion_evidence.quest_id == lease.quest_id
            and completion_evidence.quest_title == lease.quest_title
            and completion_evidence.fingerprint == lease.current_fingerprint
            and completion_evidence.terminal_collection
        )
        route_plan = classify_objective(matches[0])
        explicit_turn_in = (
            route_plan.status is ObjectiveRouteStatus.READY
            and route_plan.kind is ObjectiveRouteKind.TURN_IN
        )
        if not terminal_collection_confirmed and not explicit_turn_in and (
            not isinstance(progress, Mapping) or progress.get("complete") is not True
        ):
            return False
        if lease.accepted_ref is None or lease.turn_in_ref_fingerprint != lease.current_fingerprint:
            recovered_ref = derive_turn_in_ref(matches[0])
            if recovered_ref is None:
                self.logger.log_event(
                    "quest_turn_in_ref_missing",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    quest_id=matches[0].id,
                    quest_title=matches[0].title,
                    objective=matches[0].data.get("objective"),
                    navigation=json_safe_active_value(
                        matches[0].data.get("navigation") or ()
                    ),
                    progress=json_safe_active_value(matches[0].data.get("progress")),
                    route_kind=route_plan.kind.value,
                    route_reason=route_plan.reason,
                )
                self._stop_leveling_unsafe("quest_turn_in_step_ref_missing")
                return True
            directory = getattr(self, "_quest_npc_directory", None)
            if directory is not None:
                resolution = directory.resolve(
                    recovered_ref.giver_names[0],
                    location_name=recovered_ref.location,
                )
                canonical = resolution.entry
                if canonical is not None:
                    recovered_ref = replace(
                        recovered_ref,
                        location=canonical.location_name,
                        giver_names=(canonical.canonical_name,),
                    )
                    self.logger.log_event(
                        "quest_turn_in_npc_directory_resolved",
                        state=self.state_machine.state.value,
                        cycle_id=self.session.cycle_id,
                        quest_id=recovered_ref.id,
                        npc_name=canonical.canonical_name,
                        location_id=canonical.location_id,
                        location_name=canonical.location_name,
                        proxy_names=list(canonical.proxy_names),
                        area_object_id=canonical.area_object_id,
                        npc_instance_id=canonical.npc_instance_id,
                        reason=resolution.reason,
                    )
            try:
                lease = director.chain.bind_accepted_ref(recovered_ref)
            except (OSError, RuntimeError, ValueError) as exc:
                self._stop_leveling_unsafe(f"quest_turn_in_step_ref_bind:{exc}")
                return True
        try:
            pending = self._quest_turn_in.begin(
                matches[0],
                lease=lease,
                quest_ref=lease.accepted_ref,
                already_at_location=same_location_name(self.current_location_name, lease.accepted_ref.location),
                terminal_collection_confirmed=terminal_collection_confirmed,
            )
        except (QuestTurnInError, RuntimeError, ValueError) as exc:
            reason = exc.unsafe_reason if isinstance(exc, QuestTurnInError) else str(exc)
            self._stop_leveling_unsafe(f"quest_turn_in_begin:{reason}")
            return True
        self._quest_turn_in_started_monotonic = (
            None if pending.phase is QuestTurnInPhase.ROUTE else time.monotonic()
        )
        if terminal_collection_confirmed:
            self._quest_chat_terminal_completion_evidence = None
            self._quest_inventory_terminal_completion_evidence = None
        self._quest_refresh_requested_monotonic = time.monotonic()
        if pending.phase is QuestTurnInPhase.ROUTE:
            self._start_location_route(
                pending.objective.location,
                kind="quest_turn_in",
                reason="quest_turn_in_route",
            )
        else:
            self._open_area_for_quest_turn_in("quest_turn_in_already_at_location")
        return True

    def _open_area_for_quest_turn_in(self, reason: str) -> bool:
        request = ActionRequest(
            "open_area",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": reason},
        )
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe("quest_turn_in_area_open_failed")
        self._quest_turn_in_started_monotonic = time.monotonic()
        self._invalidate_quest_snapshot_cache()
        self._quest_refresh_requested_monotonic = time.monotonic()
        if self.state_machine.state is not GameState.QUEST_REFRESH_PENDING:
            self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason=reason)
        return True

    def _quest_turn_in_snapshot_pending(self, unsafe_reason: str) -> bool:
        if unsafe_reason not in {
            "turn_in_npc_snapshot_invalid",
            "turn_in_dialog_snapshot_invalid",
            "turn_in_action_missing",
            "turn_in_area_page_pending",
            "turn_in_step_not_advanced",
        }:
            return False
        started = self._quest_turn_in_started_monotonic or time.monotonic()
        timeout_ms = max(1000, int(self.config.leveling.quest_refresh_timeout_ms))
        return (time.monotonic() - started) * 1000 < timeout_ms

    def _on_quest_turn_in_route_arrived(self, reason: str) -> bool:
        try:
            pending = self._quest_turn_in.mark_route_arrived()
        except RuntimeError as exc:
            return self._stop_leveling_unsafe(f"quest_turn_in_route_arrival:{exc}")
        if not same_location_name(self.current_location_name, pending.objective.location):
            return self._stop_leveling_unsafe("quest_turn_in_route_arrival_mismatch")
        return self._open_area_for_quest_turn_in(reason)

    def _handle_pending_quest_turn_in(self) -> bool:
        pending = self._quest_turn_in.pending
        if pending is None:
            return False
        if pending.phase is QuestTurnInPhase.ROUTE:
            return True
        if pending.phase is QuestTurnInPhase.VERIFY_ACTIVE:
            director = self._quest_director
            if director is None:
                return self._stop_leveling_unsafe("quest_turn_in_director_missing")
            if not director.active_snapshot_fresh:
                if self._quest_active_page_requested is None:
                    return self._request_active_quest_snapshot("quest_turn_in_verify_active")
                return True
            try:
                objective = pending.objective
                matching = [
                    entry
                    for entry in director.active_catalog.result
                    if entry.id == objective.quest_id
                ]
                if matching:
                    continued = self._quest_turn_in.verify_continuation(
                        director.active_catalog.result,
                        catalog_complete=director.active_catalog.complete,
                        catalog_revision=director.active_catalog.revision,
                    )
                    chain_refresh = director.chain.reconcile(
                        director.active_catalog.result,
                        current_level_cap=max(0, self.current_level or 0),
                    )
                    if chain_refresh.state in {
                        ChainRefreshState.LOOP_UNSAFE,
                        ChainRefreshState.REMOVED_UNVERIFIED,
                        ChainRefreshState.REGRESSED_UNSAFE,
                    }:
                        raise QuestTurnInError(
                            "turn_in_continuation_unsafe",
                            chain_refresh.reason,
                        )
                    director.active_objective = chain_refresh.objective
                    director.active_objective_revision = director.active_catalog.revision
                    director.chain.clear_turn_in_completion()
                    self._quest_turn_in.confirm_continuation()
                    self.logger.log_event(
                        "quest_turn_in_advanced",
                        state=self.state_machine.state.value,
                        cycle_id=self.session.cycle_id,
                        quest_id=continued.id,
                        quest_title=continued.title,
                        active_catalog_revision=director.active_catalog.revision,
                    )
                    self._quest_turn_in_started_monotonic = None
                    self._quest_policy_intent = None
                    return True
                quest_id = self._quest_turn_in.verify_terminal(
                    director.active_catalog.result,
                    catalog_complete=director.active_catalog.complete,
                    catalog_revision=director.active_catalog.revision,
                )
                director.confirm_terminal_removal(quest_id)
            except (QuestTurnInError, RuntimeError, ValueError) as exc:
                reason = exc.unsafe_reason if isinstance(exc, QuestTurnInError) else str(exc)
                if (
                    isinstance(exc, QuestTurnInError)
                    and self._quest_turn_in_snapshot_pending(reason)
                ):
                    if reason == "turn_in_step_not_advanced":
                        if self._quest_turn_in_verify_retries >= 1:
                            return self._stop_leveling_unsafe(
                                "quest_turn_in_verify:turn_in_step_not_advanced"
                            )
                        self._quest_turn_in_verify_retries += 1
                        self._quest_turn_in_started_monotonic = time.monotonic()
                    director.invalidate_active_snapshot()
                    self._quest_active_snapshot_requested = False
                    self._quest_active_page_requested = None
                    self._quest_active_request_snapshot_id = None
                    return self._request_active_quest_snapshot(
                        "quest_turn_in_verify_active_retry"
                    )
                return self._stop_leveling_unsafe(f"quest_turn_in_verify:{reason}")
            self.logger.log_event(
                "quest_turn_in_completed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                quest_id=quest_id,
                quest_title=objective.quest_title,
                active_catalog_revision=director.active_catalog.revision,
            )
            self._quest_policy_intent = None
            self._quest_turn_in_started_monotonic = None
            return True
        if pending.phase not in {QuestTurnInPhase.NPC_LOOKUP, QuestTurnInPhase.NPC_DIALOG}:
            return self._stop_leveling_unsafe("quest_turn_in_phase_invalid")
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        if pending.phase is QuestTurnInPhase.NPC_LOOKUP:
            if self.current_page_kind != "area":
                if self._quest_turn_in_snapshot_pending("turn_in_area_page_pending"):
                    return True
                return self._stop_leveling_unsafe("quest_turn_in_area_page_timeout")
            result = global_browser_injector().execute(
                "area_npc_snapshot",
                {"expectedName": pending.objective.giver_name},
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
            try:
                snapshot = json.loads(result.message) if result.ok else None
                registry = getattr(self, "_quest_world_registry", None)
                if registry is not None and isinstance(snapshot, dict):
                    registry.observe_area_npcs(snapshot)
                decision = self._quest_turn_in.decide_area_npc(snapshot)
            except (json.JSONDecodeError, QuestTurnInError, RuntimeError, ValueError) as exc:
                reason = exc.unsafe_reason if isinstance(exc, QuestTurnInError) else str(exc)
                if isinstance(exc, QuestTurnInError) and self._quest_turn_in_snapshot_pending(reason):
                    return True
                return self._stop_leveling_unsafe(f"quest_turn_in_npc:{reason}")
        else:
            result = global_browser_injector().execute(
                "npc_dialog_snapshot",
                {
                    "expectedName": pending.objective.giver_name,
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
                decision = self._quest_turn_in.decide_dialog(snapshot)
            except (json.JSONDecodeError, QuestTurnInError, RuntimeError, ValueError) as exc:
                reason = exc.unsafe_reason if isinstance(exc, QuestTurnInError) else str(exc)
                if isinstance(exc, QuestTurnInError) and self._quest_turn_in_snapshot_pending(reason):
                    return True
                return self._stop_leveling_unsafe(f"quest_turn_in_dialog:{reason}")
        request = ActionRequest(
            decision.action_type,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata=dict(decision.action_metadata),
        )
        if decision.intent is QuestTurnInIntent.COMPLETE_QUEST:
            director = self._quest_director
            if director is None:
                return self._stop_leveling_unsafe("quest_turn_in_director_missing")
            try:
                director.chain.stage_turn_in_completion(
                    active_catalog_revision=director.active_catalog.revision,
                )
            except (RuntimeError, ValueError) as exc:
                return self._stop_leveling_unsafe(
                    f"quest_turn_in_completion_stage:{exc}"
                )
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe(decision.action_failure_reason)
        revision = self._quest_director.active_catalog.revision if self._quest_director is not None else None
        try:
            updated = self._quest_turn_in.acknowledge(
                decision,
                active_catalog_revision=revision if decision.intent is QuestTurnInIntent.COMPLETE_QUEST else None,
            )
        except (RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_turn_in_ack:{exc}")
        self._invalidate_quest_snapshot_cache()
        self._quest_refresh_requested_monotonic = time.monotonic()
        if updated.phase is QuestTurnInPhase.VERIFY_ACTIVE:
            if self._quest_director is None:
                return self._stop_leveling_unsafe("quest_turn_in_director_missing")
            self._quest_director.invalidate_active_snapshot()
            self._quest_active_snapshot_requested = False
            self._quest_active_page_requested = None
            self._quest_active_request_snapshot_id = None
            self._quest_turn_in_started_monotonic = time.monotonic()
            self._quest_turn_in_verify_retries = 0
            return self._request_active_quest_snapshot("quest_turn_in_verify_active")
        return True
