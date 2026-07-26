"""Application coordinator for bounded, snapshot-bound quest acceptance."""

from __future__ import annotations

import json
import time

from src.antibot_cv.automation.actions import ActionRequest
from src.antibot_cv.automation.quest_acceptance_settle import (
    AcceptanceSettleIntent,
    acceptance_client_matches,
    assess_acceptance_area_snapshot,
)
from src.antibot_cv.automation.quest_intake_runtime import (
    QuestAcceptPhase,
    QuestIntakeDecisionError,
    QuestIntakeIntent,
)
from src.antibot_cv.automation.quest_npc_open_navigation import (
    NpcOpenSettleStatus,
    NpcOpenStatus,
    make_pending_npc_open,
    settle_npc_open_snapshot,
)
from src.antibot_cv.automation.quest_catalog_navigation import exact_game_origin_href, snapshot_epoch_seconds
from src.antibot_cv.automation.quest_dialog_answer_stability import (
    AnswerStabilityStatus,
    answer_snapshot_can_wait,
    assess_answer_stability,
)
from src.antibot_cv.automation.quest_dialogue_recipe import dialogue_recipe_timeout_s
from src.antibot_cv.automation.quest_npc_action_journal import (
    NpcQuestActionKind,
    NpcQuestActionPhase,
    NpcQuestActionSettle,
    NpcQuestActionStatus,
    make_pending_npc_quest_action,
    dialog_semantic_fingerprint,
    settle_accept_action,
    settle_dialog_action,
)
from src.antibot_cv.automation.runtime_helpers import same_location_name
from src.antibot_cv.automation.state_machine import GameState


_LOCAL_INTAKE_QUARANTINE_REASONS = frozenset({
    "quest_accept_giver_not_observed",
    "quest_accept_giver_ambiguous",
    "quest_accept_open_action_missing_or_ambiguous",
    "quest_accept_dialog_action_missing_or_ambiguous",
    "quest_accept_dialog_action_ambiguous",
    "quest_accept_terminal_evidence_missing",
    "quest_accept_submit_action_missing_or_ambiguous",
    "quest_accept_dialog_step_limit_exceeded",
    "quest_accept_exact_npc_has_no_quest_action",
    "quest_accept_navigation_unavailable",
})


class QuestAcceptanceCoordinatorMixin:
    """Coordinate actions while intake and settle modules own all decisions."""

    def _begin_quest_acceptance(self, quest) -> bool:
        director = self._quest_director
        if director is None:
            return self._stop_leveling_unsafe("quest_director_missing")
        giver_names = tuple(getattr(quest, "giver_names", ()))
        if len(giver_names) != 1 or not str(giver_names[0] or "").strip():
            return self._stop_leveling_unsafe("quest_accept_giver_missing_or_ambiguous")
        try:
            director.begin_accept(quest.id)
            already_at_location = (
                same_location_name(self.current_location_name, quest.location)
                or same_location_name(self._last_alive_location_name, quest.location)
            )
            pending = self._quest_intake.begin(quest, already_at_location=already_at_location)
        except (RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_accept_begin:{exc}")
        self.logger.log_event(
            "quest_accept_started",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=pending.quest_id,
            quest_title=pending.title,
            quest_location=pending.location,
            giver_name=pending.giver_name,
            already_at_location=already_at_location,
        )
        if not already_at_location:
            return self._start_location_route(
                pending.location,
                kind="quest_accept",
                reason="quest_accept_route",
            )
        return self._open_area_for_quest_accept("quest_accept_local_location")

    def _open_area_for_quest_accept(self, reason: str) -> bool:
        request = ActionRequest(
            "open_area",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": reason},
        )
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe("quest_accept_area_open_failed")
        self._invalidate_quest_snapshot_cache()
        self._quest_accept_area_opened_monotonic = time.monotonic()
        self._quest_accept_area_opened_epoch = time.time()
        self._quest_accept_area_snapshot_id = None
        self._quest_accept_last_settle_reason = None
        self._quest_refresh_requested_monotonic = self._quest_accept_area_opened_monotonic
        if self.state_machine.state is not GameState.QUEST_REFRESH_PENDING:
            self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason=reason)
        return True

    def _on_quest_accept_route_arrived(self, reason: str) -> bool:
        try:
            pending = self._quest_intake.mark_route_arrived()
        except RuntimeError as exc:
            return self._stop_leveling_unsafe(f"quest_accept_route_arrival:{exc}")
        if not same_location_name(self.current_location_name, pending.location):
            return self._stop_leveling_unsafe("quest_accept_route_arrival_mismatch")
        return self._open_area_for_quest_accept(reason)

    def _abandon_quest_accept_navigation(self, detail: str) -> bool:
        """Release one pre-NPC route failure without stopping the session."""

        self._clear_location_route_tracking()
        if not self._quarantine_pending_quest_accept(
            "quest_accept_navigation_unavailable"
        ):
            return False
        self.logger.log_event(
            "quest_accept_navigation_abandoned",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            reason=detail,
        )
        if self.state_machine.state is GameState.NAVIGATOR_PENDING:
            self._safe_transition(
                GameState.LOCATION_SEARCH,
                reason="quest_accept_navigation_abandoned",
            )
        return self._open_area_for_quest_accept(
            "quest_accept_navigation_return_to_location"
        )

    def _handle_pending_quest_acceptance(self) -> bool:
        pending = self._quest_intake.pending
        if pending is None:
            return False
        if self._quest_director is not None and self._quest_director.chain.pending_npc_action is not None:
            return self._settle_pending_npc_quest_action(pending)
        if (
            self._quest_director is not None
            and self._quest_director.chain.pending_npc_open is not None
        ):
            return self._settle_pending_npc_open(pending)
        if pending.phase is QuestAcceptPhase.ROUTE:
            return True
        if pending.phase is QuestAcceptPhase.NPC_LOOKUP:
            return self._handle_quest_accept_npc_lookup(pending)
        if pending.phase is QuestAcceptPhase.NPC_DIALOG:
            return self._handle_quest_accept_npc_dialog(pending)
        if pending.phase is QuestAcceptPhase.VERIFY_STARTED:
            return self._handle_quest_accept_verification(pending)
        return True

    def _handle_quest_accept_npc_lookup(self, pending) -> bool:
        if self.current_page_kind != "area":
            return self._wait_for_quest_accept_snapshot(
                pending, "quest_accept_area_page_pending", None,
            )
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        result = global_browser_injector().execute(
            "area_npc_snapshot", {"expectedName": pending.giver_name},
            timeout_s=2.5, client_id=self.browser_client_id,
        )
        if not acceptance_client_matches(
            self.browser_client_id, result.client_id, dry_run=self.config.dry_run,
        ):
            return self._stop_leveling_unsafe("quest_accept_npc_client_mismatch")
        try:
            snapshot = json.loads(result.message) if result.ok else None
        except json.JSONDecodeError:
            snapshot = None
        settle = assess_acceptance_area_snapshot(
            snapshot,
            expected_location=pending.location,
            expected_giver=pending.giver_name,
            previous_snapshot_id=self._quest_accept_area_snapshot_id,
            area_opened_epoch=self._quest_accept_area_opened_epoch or float("inf"),
        )
        if settle.snapshot_id:
            self._quest_accept_area_snapshot_id = settle.snapshot_id
        if settle.intent is AcceptanceSettleIntent.WAIT:
            return self._wait_for_quest_accept_snapshot(
                pending, settle.reason, settle.snapshot_id,
            )
        if settle.intent is AcceptanceSettleIntent.STOP_UNSAFE:
            if settle.reason == "quest_accept_giver_ambiguous":
                return self._quarantine_pending_quest_accept(settle.reason)
            return self._stop_leveling_unsafe(settle.reason)
        try:
            decision = self._quest_intake.decide_area_npc(snapshot)
        except QuestIntakeDecisionError as exc:
            return self._stop_leveling_unsafe(exc.unsafe_reason)
        except RuntimeError as exc:
            return self._stop_leveling_unsafe(f"quest_accept_npc_snapshot:{exc}")
        pending = self._quest_intake.pending
        if pending is None:
            return self._stop_leveling_unsafe("quest_accept_npc_pending_missing")
        try:
            staged = self._stage_quest_accept_npc_open(pending, result.client_id)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_accept_npc_stage:{exc}")
        request = ActionRequest(
            decision.action_type,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata=dict(decision.action_metadata),
        )
        if not self.action_executor.execute(request):
            try:
                self._quest_director.chain.clear_npc_open(staged)
            except (OSError, RuntimeError, ValueError) as exc:
                return self._stop_leveling_unsafe(f"quest_accept_npc_rollback:{exc}")
            return self._stop_leveling_unsafe(decision.action_failure_reason)
        outcome = getattr(self.action_executor.sink, "last_npc_open_outcome", None)
        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {
            "DryRunActionSink", "ReplayActionSink",
        }
        if not replay and (
            outcome is None or outcome.status is NpcOpenStatus.NOT_ISSUED
            or outcome.client_id != staged.client_id
        ):
            return self._stop_leveling_unsafe("quest_accept_npc_dispatch_identity_missing")
        return True

    def _wait_for_quest_accept_snapshot(
        self, pending, reason: str, snapshot_id: str | None,
    ) -> bool:
        started = self._quest_accept_area_opened_monotonic
        timeout_ms = max(1000, int(self.config.leveling.quest_refresh_timeout_ms))
        elapsed_ms = None if started is None else (time.monotonic() - started) * 1000
        self._quest_accept_last_settle_reason = reason
        self.logger.log_event(
            "quest_accept_npc_settle_wait",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=pending.quest_id,
            reason=reason,
            snapshot_id=snapshot_id,
            elapsed_ms=elapsed_ms,
            timeout_ms=timeout_ms,
        )
        if elapsed_ms is not None and elapsed_ms < timeout_ms:
            return True
        last_reason = self._quest_accept_last_settle_reason or reason
        if last_reason == "quest_accept_giver_not_observed":
            return self._quarantine_pending_quest_accept(last_reason)
        return self._stop_leveling_unsafe(
            f"quest_accept_npc_snapshot_settle_timeout:{last_reason}"
        )

    def _handle_quest_accept_npc_dialog(self, pending) -> bool:
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        injector = global_browser_injector()
        result = injector.execute(
            "npc_dialog_snapshot",
            {"expectedName": pending.giver_name, "expectedNpcId": pending.npc_id},
            timeout_s=2.5,
            client_id=self.browser_client_id,
        )
        if not acceptance_client_matches(
            self.browser_client_id, result.client_id, dry_run=self.config.dry_run,
        ):
            return self._stop_leveling_unsafe("quest_accept_dialog_client_mismatch")
        try:
            snapshot = json.loads(result.message) if result.ok else None
        except json.JSONDecodeError:
            snapshot = None
        client = injector.client_snapshot(result.client_id or "") if hasattr(injector, "client_snapshot") else {}
        profile_id = str(client.get("profile_id") or "")
        tab_raw = client.get("tab_id")
        tab_id = tab_raw if isinstance(tab_raw, int) and not isinstance(tab_raw, bool) else None
        if str(client.get("client_id") or "") != str(result.client_id or ""):
            return self._stop_leveling_unsafe("quest_accept_dialog_client_snapshot_mismatch")
        try:
            decision = self._quest_intake.decide_dialog(snapshot)
        except QuestIntakeDecisionError as exc:
            if self._quest_accept_dialog_snapshot_pending(exc.unsafe_reason):
                self.logger.log_event(
                    "quest_accept_dialog_snapshot_wait",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    quest_id=pending.quest_id,
                    reason=exc.unsafe_reason,
                )
                return True
            dialog_is_durable = bool(
                self._quest_director is not None
                and self._quest_director.chain.pending_npc_dialog is not None
            )
            if exc.unsafe_reason in _LOCAL_INTAKE_QUARANTINE_REASONS and not dialog_is_durable:
                return self._quarantine_pending_quest_accept(exc.unsafe_reason)
            if (
                exc.unsafe_reason == "quest_accept_dialog_action_ambiguous"
                and dialog_is_durable and pending.quest_opened and pending.dialog_steps < 20
            ):
                stage = self._quest_director.chain.pending_npc_dialog
                now = time.time()
                generated = snapshot_epoch_seconds(snapshot.get("generatedAt")) if isinstance(snapshot, dict) else None
                snapshot_id = str((snapshot or {}).get("snapshotId") or "") if isinstance(snapshot, dict) else ""
                if (
                    stage is not None
                    and stage.answer_ambiguity_generated_at is not None
                    and generated is not None
                    and generated > stage.answer_ambiguity_generated_at
                    and snapshot_id != stage.answer_ambiguity_snapshot_id
                ):
                    self._quest_answer_stability = None
                    return self._quarantine_pending_quest_accept(exc.unsafe_reason)
                if stage is not None and answer_snapshot_can_wait(
                    stage, snapshot, client_id=result.client_id or "",
                    profile_id=profile_id, tab_id=tab_id, now=now,
                ):
                    assert generated is not None
                    try:
                        stage = self._quest_director.chain.record_npc_dialog_ambiguity(
                            stage, snapshot_id=str(snapshot.get("snapshotId") or ""),
                            generated_at=generated,
                        )
                    except (OSError, RuntimeError, TypeError, ValueError) as error:
                        return self._stop_leveling_unsafe(f"quest_accept_dialog_ambiguity_stage:{error}")
                    self._quest_answer_stability = None
                    self.logger.log_event(
                        "quest_accept_dialog_stability_wait",
                        state=self.state_machine.state.value,
                        cycle_id=self.session.cycle_id,
                        quest_id=pending.quest_id,
                        reason="ambiguous_transient_dialog_actions",
                        snapshot_id=str((snapshot or {}).get("snapshotId") or ""),
                    )
                    return True
            return self._stop_leveling_unsafe(exc.unsafe_reason)
        except RuntimeError as exc:
            return self._stop_leveling_unsafe(f"quest_accept_dialog:{exc}")
        self.logger.log_event(
            "quest_accept_dialog_observed",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=pending.quest_id,
            giver_name=pending.giver_name,
            headers=list(decision.observed_headers),
            actions=list(decision.observed_actions),
            intake_intent=decision.intent.value,
        )
        if decision.intent is QuestIntakeIntent.ANSWER_DIALOG:
            stage = self._quest_director.chain.pending_npc_dialog
            status, candidate = assess_answer_stability(
                getattr(self, "_quest_answer_stability", None), stage, snapshot, decision,
                client_id=result.client_id or "", profile_id=profile_id,
                tab_id=tab_id, now=time.time(),
            ) if stage is not None else (AnswerStabilityStatus.STOP, None)
            if status is AnswerStabilityStatus.STOP:
                self._quest_answer_stability = None
                return self._stop_leveling_unsafe("quest_accept_dialog_stability_unsafe")
            self._quest_answer_stability = candidate
            if status is AnswerStabilityStatus.WAIT:
                self.logger.log_event(
                    "quest_accept_dialog_stability_wait",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    quest_id=pending.quest_id,
                    reason="stable_unique_answer_successor_required",
                    snapshot_id=decision.snapshot_id,
                )
                return True
            self._quest_answer_stability = None
        request = ActionRequest(
            decision.action_type,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata=dict(decision.action_metadata),
        )
        try:
            staged_action = self._stage_npc_quest_action(pending, decision, result.client_id)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_accept_action_stage:{exc}")
        if not self.action_executor.execute(request):
            try:
                self._quest_director.chain.clear_npc_quest_action(staged_action)
            except (OSError, RuntimeError, ValueError) as exc:
                return self._stop_leveling_unsafe(f"quest_accept_action_rollback:{exc}")
            return self._stop_leveling_unsafe(decision.action_failure_reason)
        try:
            outcome = getattr(self.action_executor.sink, "last_npc_quest_action_outcome", None)
            replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {"DryRunActionSink", "ReplayActionSink"}
            if not replay and (
                outcome is None or outcome.status is NpcQuestActionStatus.NOT_ISSUED
                or outcome.client_id != staged_action.client_id
            ):
                raise RuntimeError("NPC action dispatch identity missing")
            updated_action = self._quest_director.chain.mark_npc_quest_action_dispatched(staged_action)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_accept_action:{exc}")
        self._invalidate_quest_snapshot_cache()
        if updated_action.action is NpcQuestActionKind.ACCEPT:
            self._quest_director.invalidate_active_snapshot()
            self._quest_active_snapshot_requested = False
            self._quest_active_page_requested = None
            self._quest_active_request_snapshot_id = None
            return self._request_active_quest_snapshot("quest_accept_action_verify_active")
        if updated_action.action is NpcQuestActionKind.DONE:
            self._quest_director.expire_available_snapshot()
            self._quest_director.invalidate_active_snapshot()
            self._quest_active_snapshot_requested = False
            self._quest_active_page_requested = None
            self._quest_active_request_snapshot_id = None
            return self._request_active_quest_snapshot(
                "quest_done_action_verify_active"
            )
        # The game replaces dialogue controls asynchronously after an answer.
        # Bound transient invalid observations to this mutation, rather than to
        # the much older moment when the NPC page was first opened.
        self._quest_refresh_requested_monotonic = time.monotonic()
        return True

    def _quest_accept_dialog_snapshot_pending(self, unsafe_reason: str) -> bool:
        """Allow only a bounded re-observation of a transitional dialog frame."""

        if unsafe_reason != "quest_accept_dialog_snapshot_invalid":
            return False
        started = self._quest_refresh_requested_monotonic
        if started is None:
            return False
        timeout_ms = max(1000, int(self.config.leveling.quest_refresh_timeout_ms))
        return (time.monotonic() - started) * 1000 < timeout_ms

    def _stage_quest_accept_npc_open(self, pending, observed_client_id):
        director = self._quest_director
        if director is None:
            raise RuntimeError("quest director missing")
        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {
            "DryRunActionSink", "ReplayActionSink",
        }
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        client_id = str(observed_client_id or "")
        injector = global_browser_injector()
        client = injector.client_snapshot(client_id) if client_id and hasattr(injector, "client_snapshot") else {}
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        issued_at = time.time()
        if replay:
            client_id = client_id or self.browser_client_id or "replay-client"
            profile_id = profile_id or "replay-profile"
            tab_id = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else 0
        elif not exact_game_origin_href(client.get("href")):
            raise ValueError("NPC open browser origin missing")
        quest_ref = director.chain.pending_accepted_ref
        if quest_ref is None or quest_ref.id != pending.quest_id:
            raise ValueError("pending quest reference missing")
        staged = make_pending_npc_open(
            client_id=client_id, profile_id=profile_id, tab_id=tab_id,
            quest_id=pending.quest_id, quest_title=pending.title,
            quest_accept_ref=quest_ref.accept_ref,
            quest_catalog_page=quest_ref.catalog_page,
            giver_name=pending.giver_name, npc_id=pending.npc_id,
            route_ref=pending.route_ref,
            npc_name=pending.npc_name,
            location_id=pending.location_id, location_name=pending.location,
            area_snapshot_id=pending.area_snapshot_id,
            area_generated_at=pending.area_generated_at,
            issued_at=issued_at,
            settle_timeout_s=dialogue_recipe_timeout_s(pending.quest_id, pending.title),
        )
        director.chain.stage_npc_open(staged)
        return staged

    def _stage_npc_quest_action(self, pending, decision, observed_client_id):
        director = self._quest_director
        if director is None or director.chain.pending_accepted_ref is None:
            raise RuntimeError("quest acceptance reference missing")
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {"DryRunActionSink", "ReplayActionSink"}
        client_id = str(observed_client_id or "")
        injector = global_browser_injector()
        client = injector.client_snapshot(client_id) if client_id and hasattr(injector, "client_snapshot") else {}
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        if replay:
            client_id = client_id or self.browser_client_id or "replay-client"
            profile_id = profile_id or "replay-profile"
            tab_id = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else 0
        elif not exact_game_origin_href(client.get("href")):
            raise ValueError("NPC action browser origin missing")
        metadata = dict(decision.action_metadata)
        staged = make_pending_npc_quest_action(
            ref=director.chain.pending_accepted_ref, client_id=client_id,
            profile_id=profile_id, tab_id=tab_id, giver_name=pending.giver_name,
            npc_id=pending.npc_id, action=metadata.get("action"),
            expected_snapshot_id=metadata.get("expected_snapshot_id"),
            expected_generated_at=metadata.get("expected_generated_at"),
            source_semantic_fingerprint=metadata.get("source_semantic_fingerprint"),
            expected_ref=metadata.get("expected_ref"), expected_point_id=metadata.get("expected_point_id"),
            expected_text=metadata.get("expected_text"), expected_title=metadata.get("expected_title"),
            quest_opened=pending.quest_opened, dialog_steps=pending.dialog_steps,
            issued_at=time.time(),
        )
        director.chain.stage_npc_quest_action(staged)
        return staged

    def _settle_pending_npc_quest_action(self, pending) -> bool:
        director = self._quest_director
        if director is None or director.chain.pending_npc_action is None:
            return self._stop_leveling_unsafe("quest_accept_action_stage_missing")
        staged = director.chain.pending_npc_action
        if staged.phase is NpcQuestActionPhase.STAGED:
            try:
                staged = director.chain.mark_npc_quest_action_dispatched(staged)
            except (OSError, RuntimeError, ValueError) as exc:
                return self._stop_leveling_unsafe(f"quest_accept_action_restart:{exc}")
        if staged.action is NpcQuestActionKind.ACCEPT:
            catalog_complete = director.active_snapshot_fresh and director.active_catalog.complete
            settle = settle_accept_action(
                staged, director.active_catalog.result if catalog_complete else (),
                complete=catalog_complete,
                now=time.time(),
            )
            if settle is NpcQuestActionSettle.WAIT:
                if self._quest_active_page_requested is None:
                    return self._request_active_quest_snapshot("quest_accept_action_verify_active")
                return True
            if settle is NpcQuestActionSettle.STOP:
                return self._stop_leveling_unsafe("quest_accept_action_active_proof_missing")
            matches = [entry for entry in director.active_catalog.result if entry.id == staged.quest_id and entry.title == staged.quest_title]
            try:
                director.chain.recover_staged_active_ref(
                    matches[0], revision=director.active_catalog.revision,
                    expected_ref=director.pending_accept,
                )
                self._quest_intake.mark_accept_submitted()
                director.acknowledge_accept(staged.quest_id)
                self._quest_intake.finish(staged.quest_id)
            except (OSError, RuntimeError, ValueError) as exc:
                return self._stop_leveling_unsafe(f"quest_accept_action_active_settle:{exc}")
            return False
        if staged.action is NpcQuestActionKind.DONE:
            if not director.active_snapshot_fresh:
                if self._quest_active_page_requested is None:
                    return self._request_active_quest_snapshot(
                        "quest_done_action_verify_active"
                    )
                return True
            active_matches = [
                entry for entry in director.active_catalog.result
                if entry.id == staged.quest_id and entry.title == staged.quest_title
            ] if director.active_catalog.complete else []
            if active_matches:
                try:
                    director.chain.recover_staged_active_ref(
                        active_matches[0],
                        revision=director.active_catalog.revision,
                        expected_ref=director.pending_accept,
                    )
                    self._quest_intake.mark_accept_submitted()
                    director.acknowledge_accept(staged.quest_id)
                    self._quest_intake.finish(staged.quest_id)
                except (OSError, RuntimeError, ValueError) as exc:
                    return self._stop_leveling_unsafe(
                        f"quest_done_action_active_settle:{exc}"
                    )
                return False
            if not director.active_catalog.complete:
                return True
            if not director.available_snapshot_fresh:
                if not director.refresh_in_progress:
                    director.begin_completed_intake_catalog_refresh(staged.quest_id)
                if self._quest_catalog_page_requested is None:
                    return self._request_available_quest_page(
                        "quest_complete_verify_available",
                        failure_reason="quest_complete_catalog_open_failed",
                    )
                return True
            if not director.catalog.complete or any(
                ref.id == staged.quest_id for ref in director.available_quests
            ):
                return self._stop_leveling_unsafe("quest_complete_available_proof_missing")
            try:
                director.acknowledge_completed_intake(staged.quest_id, staged)
                self._quest_intake.mark_accept_submitted()
                self._quest_intake.finish(staged.quest_id)
            except (OSError, RuntimeError, ValueError) as exc:
                return self._stop_leveling_unsafe(f"quest_complete_settle:{exc}")
            self.logger.log_event(
                "quest_intake_completed", quest_id=staged.quest_id,
                quest_title=staged.quest_title,
            )
            return False
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        injector = global_browser_injector()
        result = injector.execute(
            "npc_dialog_snapshot", {"expectedName": staged.giver_name, "expectedNpcId": staged.npc_id},
            timeout_s=2.5, client_id=staged.client_id,
        )
        try:
            snapshot = json.loads(result.message) if result.ok else None
        except json.JSONDecodeError:
            snapshot = None
        client = injector.client_snapshot(result.client_id or staged.client_id) if hasattr(injector, "client_snapshot") else {}
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {"DryRunActionSink", "ReplayActionSink"}
        if replay:
            profile_id = profile_id or staged.profile_id
            tab_id = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else staged.tab_id
        settle = settle_dialog_action(
            staged, snapshot, client_id=result.client_id or "", profile_id=profile_id,
            tab_id=tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else None,
            now=time.time(),
        )
        if settle is NpcQuestActionSettle.WAIT:
            return True
        if settle is NpcQuestActionSettle.STOP:
            return self._stop_leveling_unsafe("quest_accept_action_settle_unsafe")
        fingerprint = dialog_semantic_fingerprint(snapshot)
        try:
            director.chain.settle_npc_dialog_action(staged, semantic_fingerprint=fingerprint)
            if staged.action is NpcQuestActionKind.OPEN:
                self._quest_intake.mark_quest_opened()
            else:
                self._quest_intake.mark_dialog_answer_settled()
        except (OSError, RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_accept_action_settle:{exc}")
        self._invalidate_quest_snapshot_cache()
        return True

    def _settle_pending_npc_open(self, pending) -> bool:
        director = self._quest_director
        if director is None or director.chain.pending_npc_open is None:
            return self._stop_leveling_unsafe("quest_accept_npc_stage_missing")
        staged = director.chain.pending_npc_open
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        injector = global_browser_injector()
        result = injector.execute(
            "npc_dialog_snapshot",
            {"expectedName": staged.giver_name, "expectedNpcId": staged.npc_id},
            timeout_s=2.5, client_id=staged.client_id,
        )
        try:
            snapshot = json.loads(result.message) if result.ok else None
        except json.JSONDecodeError:
            snapshot = None
        client = (
            injector.client_snapshot(result.client_id or staged.client_id)
            if hasattr(injector, "client_snapshot") else {}
        )
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {
            "DryRunActionSink", "ReplayActionSink",
        }
        if replay:
            profile_id = profile_id or staged.profile_id
            tab_id = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else staged.tab_id
        settle = settle_npc_open_snapshot(
            staged, snapshot,
            client_id=result.client_id or "",
            profile_id=profile_id,
            tab_id=tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else None,
            now=time.time(),
        )
        if settle.status is NpcOpenSettleStatus.WAIT:
            self.logger.log_event(
                "quest_accept_npc_open_settle_wait", quest_id=pending.quest_id,
                reason=settle.reason, deadline=staged.deadline,
            )
            return True
        if settle.status is NpcOpenSettleStatus.EMPTY:
            try:
                director.chain.clear_npc_open(staged)
            except (OSError, RuntimeError, ValueError) as exc:
                return self._stop_leveling_unsafe(
                    f"quest_accept_empty_npc_rollback:{exc}"
                )
            if not self._quarantine_pending_quest_accept(
                "quest_accept_exact_npc_has_no_quest_action"
            ):
                return False
            return self._open_area_for_quest_accept(
                "quest_accept_empty_npc_return_to_location"
            )
        if settle.status is not NpcOpenSettleStatus.ACCEPT:
            return self._stop_leveling_unsafe(settle.reason)
        quest_already_open = settle.reason == "npc_open_exact_dialog_confirmed"
        try:
            director.chain.settle_npc_open(staged, quest_opened=quest_already_open)
            self._quest_intake.mark_npc_opened()
            if quest_already_open:
                self._quest_intake.mark_quest_opened()
        except (OSError, RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"quest_accept_npc_settle:{exc}")
        self._invalidate_quest_snapshot_cache()
        return True

    def _handle_quest_accept_verification(self, pending) -> bool:
        director = self._quest_director
        if director is None:
            return self._stop_leveling_unsafe("quest_director_missing")
        if not director.active_snapshot_fresh:
            if self._quest_active_page_requested is None:
                return self._request_active_quest_snapshot("quest_accept_verify_started")
            return True
        try:
            dialog_stage = director.chain.pending_npc_dialog
            if dialog_stage is not None:
                director.chain.clear_npc_dialog(dialog_stage)
            director.acknowledge_accept(pending.quest_id)
            self._quest_intake.finish(pending.quest_id)
        except RuntimeError as exc:
            return self._stop_leveling_unsafe(f"quest_accept_verify:{exc}")
        self.logger.log_event(
            "quest_accept_confirmed",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=pending.quest_id,
            quest_title=pending.title,
            active_count=len(director.active_quests),
        )
        return False

    def _quarantine_pending_quest_accept(self, reason: str) -> bool:
        """Durably quarantine one exact pre-mutation blocker, then abandon locally."""

        if reason not in _LOCAL_INTAKE_QUARANTINE_REASONS:
            return self._stop_leveling_unsafe("quest_accept_quarantine_reason_not_allowed")
        director = self._quest_director
        expected = None if director is None else director.pending_accept
        eligible = () if director is None or expected is None else tuple(
            ref for ref in director.available_quests if ref.id == expected.id
        )
        if (
            director is None
            or expected is None
            or not self._quest_intake.matches_pending(expected)
            or director.chain.pending_accepted_ref != expected
            or not director.intake_queue
            or director.intake_queue[0] != expected
            or eligible != (expected,)
        ):
            return self._stop_leveling_unsafe("quest_accept_quarantine_binding_mismatch")
        try:
            director.quarantine_pending_accept(expected, reason)
            self._quest_intake.abandon(expected)
        except RuntimeError:
            return self._stop_leveling_unsafe("quest_accept_quarantine_binding_mismatch")
        self._quest_accept_area_opened_monotonic = None
        self._quest_accept_area_opened_epoch = None
        self._quest_accept_area_snapshot_id = None
        self._quest_accept_last_settle_reason = None
        self.logger.log_event(
            "quest_accept_quarantined",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=expected.id,
            quest_title=expected.title,
            reason=reason,
        )
        return True
