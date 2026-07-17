from __future__ import annotations

import json
import time

import numpy as np

from src.antibot_cv.automation.actions import ActionRequest, LiveMacActionSink
from src.antibot_cv.automation.combat_policy import (
    AvailableSkill as PolicySkill,
    BattleItem as PolicyBattleItem,
    BattleItemKind,
    BattleResources as PolicyBattleResources,
    BattleSnapshot as PolicyBattleSnapshot,
    CombatIntent,
    CombatPolicy,
    CombatDecision as PolicyCombatDecision,
)
from src.antibot_cv.automation.combat_skill_mutation import (
    bind_skill_mutation,
    refresh_skill_mutation_binding,
)
from src.antibot_cv.automation.runtime_constants import (
    ATTACK_RETRY_DELAY_MS,
    HUNT_RETRY_DELAY_MS,
    JS_BATTLE_PROBE_INTERVAL_MS,
)
from src.antibot_cv.automation.spellbook_combat_adapter import decide_current_spellbook_action
from src.antibot_cv.automation.runtime_helpers import (
    attack_click_point as _attack_click_point,
    game_shell_present as _game_shell_present,
    location_map_present as _location_map_present,
    normalize_phrase as _normalize_phrase,
    optional_float as _optional_float,
    optional_int as _optional_int,
    same_location_name as _same_location_name,
)
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.automation.combat_policy_runtime import CombatPolicyRuntimeMixin
from src.antibot_cv.detection.resources import ResourceStatus
from src.antibot_cv.viewport.coordinates import Rect


class CombatRuntimeMixin(CombatPolicyRuntimeMixin):
    def _handle_battle_wait(self, frame: np.ndarray) -> None:
        if not self.config.dry_run and self._sync_battle_from_injector(frame):
            return
        if not self.config.dry_run:
            self._handle_live_failed_visible_attack(frame)
            return
        result = self.battle_detector.detect(frame)
        for signal in result.signals:
            self.logger.log_event(
                "battle_signal",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                detector_id=signal.signal_id,
                detector_confidence=signal.confidence,
                frame_hash=self._last_frame_hash,
            )
        if not result.detected:
            if self._click_attack_if_available(frame):
                return
            self._retry_target_click_if_due(frame)
            return
        self.session.mark_battle_detected()
        self.logger.log_event(
            "battle_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_confidence=result.confidence,
            frame_hash=self._last_frame_hash,
        )
        self.latencies.start("battle_to_ability4")
        self._safe_transition(GameState.BATTLE_ACTIVE)
        self._selected_target = None
        self._last_attack_click_monotonic = None
        self._last_combat_click_monotonic = None
        self._combat_slot_sequence_index = 0
        self._use_ability4(frame, result.panel_bbox)

    def _click_attack_if_available(self, frame: np.ndarray) -> bool:
        if not self.session.can_click_attack():
            return False
        if self._last_attack_click_monotonic is not None:
            elapsed_ms = (time.monotonic() - self._last_attack_click_monotonic) * 1000
            if elapsed_ms < ATTACK_RETRY_DELAY_MS:
                return False
        result = self.attack_detector.detect(frame)
        if not result.detected or result.center is None:
            return False
        attack_attempt = self.session.attack_click_count()
        point = _attack_click_point(result, attack_attempt)
        request = ActionRequest(
            "click_attack",
            frame_point=point,
            screen_point=self.mapper.frame_to_pynput(point),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "attack_click_attempt": attack_attempt,
                "detector_confidence": result.confidence,
                "frame_hash": self._last_frame_hash,
            },
        )
        self.logger.log_event(
            "attack_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            attack_click_attempt=attack_attempt,
            detector_id="attack_button",
            detector_confidence=result.confidence,
            x_frame=point.x,
            y_frame=point.y,
            button_x_frame=None if result.bbox is None else result.bbox.x,
            button_y_frame=None if result.bbox is None else result.bbox.y,
            button_width=None if result.bbox is None else result.bbox.width,
            button_height=None if result.bbox is None else result.bbox.height,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self.logger.log_event(
                "attack_clicked",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                attack_click_attempt=attack_attempt,
                detector_id="attack_button",
                detector_confidence=result.confidence,
                x_frame=point.x,
                y_frame=point.y,
                frame_hash=self._last_frame_hash,
            )
            self._last_attack_click_monotonic = time.monotonic()
            return True
        return False

    def _handle_battle_active(self, frame: np.ndarray, result: object | None = None) -> None:
        if not self.config.dry_run:
            self._use_ability4(frame, None)
            return
        battle_result = result if result is not None else self.battle_detector.detect(frame)
        if not getattr(battle_result, "detected", False):
            battle_end = self.battle_end_detector.detect(frame)
            if battle_end.detected:
                self._safe_transition(GameState.WAIT_BATTLE_END, reason="battle_end_without_ability")
                self._handle_battle_end(frame, battle_end)
            return
        self._use_ability4(frame, battle_result.panel_bbox)

    def _sync_battle_from_injector(self, frame: np.ndarray, *, force_probe: bool = False) -> bool:
        snapshot = self._battle_snapshot_via_injector(force=force_probe)
        return self._sync_battle_from_snapshot(frame, snapshot)

    def _sync_battle_from_snapshot(self, frame: np.ndarray, snapshot: dict[str, object] | None) -> bool:
        if not self._injector_snapshot_has_active_battle(snapshot):
            return False
        if self.session.battle_id is None:
            self.session.new_battle()
        self.session.mark_battle_detected()
        self.logger.log_event(
            "battle_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_id="browser_injector",
            detector_confidence=1.0,
            fight_path=snapshot.get("fightPath"),
            fight_href=snapshot.get("fightHref"),
            frame_hash=self._last_frame_hash,
        )
        self.latencies.start("battle_to_ability4")
        if self.state_machine.state != GameState.BATTLE_ACTIVE:
            if not self._sync_transition(GameState.BATTLE_ACTIVE, "js_battle_detected"):
                return False
        self._selected_target = None
        self._last_attack_click_monotonic = None
        self._last_combat_click_monotonic = None
        self._combat_slot_sequence_index = 0
        self._use_ability4(frame, None)
        return True

    def _battle_snapshot_via_injector(self, *, force: bool = False) -> dict[str, object] | None:
        if (
            self.config.dry_run
            or not self.browser_client_id
            or not isinstance(self.action_executor.sink, LiveMacActionSink)
        ):
            return None
        now = time.monotonic()
        if not force and self._last_js_battle_probe_monotonic is not None:
            elapsed_ms = (now - self._last_js_battle_probe_monotonic) * 1000
            if elapsed_ms < JS_BATTLE_PROBE_INTERVAL_MS:
                return self._last_battle_snapshot_cache
        self._last_js_battle_probe_monotonic = now
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "battle_snapshot",
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
        except Exception as exc:
            self.logger.log_event(
                "js_battle_probe_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=str(exc),
                frame_hash=self._last_frame_hash,
            )
            return None
        if not result.ok:
            self.logger.log_event(
                "js_battle_probe_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=result.message,
                frame_hash=self._last_frame_hash,
            )
            return None
        try:
            parsed = json.loads(result.message)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        self._last_battle_snapshot_cache = parsed
        self._last_battle_snapshot_success_monotonic = time.monotonic()
        self._record_battle_outcome(parsed)
        return parsed

    def _record_battle_outcome(self, snapshot: dict[str, object] | None) -> None:
        if not isinstance(snapshot, dict) or snapshot.get("finished") is not True:
            return
        outcome = str(snapshot.get("outcome") or "unknown").strip().lower()
        if outcome not in {"victory", "defeat", "unknown"}:
            outcome = "unknown"
        self.session.mark_battle_outcome(outcome)
        self.logger.log_event(
            "battle_outcome_observed",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            outcome=outcome,
            evidence=snapshot.get("outcomeEvidence"),
        )

    def _injector_snapshot_has_active_battle(self, snapshot: dict[str, object] | None) -> bool:
        if not snapshot:
            return False
        abilities = snapshot.get("abilities")
        has_combat_ability = (
            isinstance(abilities, list)
            and any(isinstance(ability, dict) and isinstance(ability.get("id"), int) and ability.get("id") < 0 for ability in abilities)
        )
        return bool(snapshot.get("hasFight") and snapshot.get("useSkillAvailable") and has_combat_ability)

    def _injector_snapshot_has_inactive_battle(self, snapshot: dict[str, object] | None) -> bool:
        return isinstance(snapshot, dict) and snapshot.get("hasFight") is False

    def _retry_target_click_if_due(self, frame: np.ndarray) -> None:
        if self._selected_target is None:
            return
        max_attempts = 1 + len(self.config.target.click_retry_offsets)
        if self._target_click_attempts >= max_attempts:
            return
        if self._last_target_click_monotonic is None:
            return
        elapsed_ms = (time.monotonic() - self._last_target_click_monotonic) * 1000
        if elapsed_ms < self.config.target.click_retry_delay_ms:
            return
        if not self._refresh_selected_target(frame):
            self.logger.log_event(
                "target_reacquire_missed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                target_id=self._selected_target.target_id,
                target_click_attempt=self._target_click_attempts,
                frame_hash=self._last_frame_hash,
            )
            return
        self.logger.log_event(
            "target_click_retry",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            target_id=self._selected_target.target_id,
            target_click_attempt=self._target_click_attempts,
            frame_hash=self._last_frame_hash,
        )
        self._click_selected_target()

    def _refresh_selected_target(self, frame: np.ndarray) -> bool:
        if self._selected_target is None:
            return False
        previous = self._selected_target
        candidates = self.target_locator.locate(frame)
        if not candidates:
            return False
        tracks = self.tracker.update(candidates)
        same_target = [candidate for candidate in candidates if candidate.target_id == previous.target_id]
        candidates_to_rank = same_target or candidates
        best = min(
            candidates_to_rank,
            key=lambda candidate: (
                (candidate.label_center.x - previous.label_center.x) ** 2
                + (candidate.label_center.y - previous.label_center.y) ** 2,
                -candidate.confidence,
            ),
        )
        self._selected_target = best
        matched_track = next(
            (
                track
                for track in tracks
                if track.target_id == best.target_id
                and track.label_center.x == best.label_center.x
                and track.label_center.y == best.label_center.y
            ),
            None,
        )
        self.logger.log_event(
            "target_reacquired",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_confidence=best.confidence,
            target_id=best.target_id,
            track_id=None if matched_track is None else matched_track.track_id,
            x_frame=best.interaction_point.x,
            y_frame=best.interaction_point.y,
            target_click_attempt=self._target_click_attempts,
            frame_hash=self._last_frame_hash,
        )
        return True

    def _use_ability4(self, frame: np.ndarray, panel_bbox: Rect | None) -> None:
        if self.state_machine.state != GameState.BATTLE_ACTIVE or not self.session.can_use_ability4():
            return
        if not self.config.dry_run:
            slot_index = self._live_combat_slot_for_current_resources(frame)
            if slot_index is None:
                return
            if not self._use_skill_slot_via_injector("click_ability_4", slot_index, "ability4_js_intended"):
                fallback_slot = max(0, int(self.config.combat.low_resource_fallback_slot_index))
                if (
                    slot_index != fallback_slot
                    and self._live_zero_fallback_allowed(frame)
                    and self._use_skill_slot_via_injector(
                        "click_ability_4", fallback_slot, "ability4_js_fallback_intended"
                    )
                ):
                    slot_index = fallback_slot
                else:
                    return
            latency = self.latencies.finish("battle_to_ability4")
            self.logger.log_event(
                "latency",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                latency_type="battle_to_ability4",
                latency_ms=latency,
                reaction_latency_ms=latency,
            )
            self._safe_transition(GameState.ABILITY_4_USED)
            self._safe_transition(GameState.WAIT_BATTLE_END)
            return
        slot = self.ability_detector.slot4(frame, panel_bbox)
        if slot is None:
            self.logger.log_event("error", state=self.state_machine.state.value, reason="slot4_not_found")
            return
        ready_ok = slot.ready or self.config.dry_run or not self.config.ability4.require_ready_confirmation
        if slot.confidence < self.config.ability4.slot_confidence_threshold or not ready_ok:
            self.logger.log_event(
                "action_blocked",
                state=self.state_machine.state.value,
                action_type="click_ability_4",
                battle_id=self.session.battle_id,
                block_reason="ability4_not_ready",
            )
            return
        screen_point = self.mapper.frame_to_pynput(slot.center)
        request = ActionRequest(
            "click_ability_4",
            frame_point=slot.center,
            screen_point=screen_point,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "detector_confidence": slot.confidence,
                "frame_hash": self._last_frame_hash,
                "pre_click_delay_ms": self.config.ability4.pre_click_delay_ms,
                "click_hold_ms": self.config.ability4.click_hold_ms,
            },
        )
        self.logger.log_event(
            "ability4_intended",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            x_frame=slot.center.x,
            y_frame=slot.center.y,
            detector_confidence=slot.confidence,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            latency = self.latencies.finish("battle_to_ability4")
            self.logger.log_event(
                "latency",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                latency_type="battle_to_ability4",
                latency_ms=latency,
                reaction_latency_ms=latency,
            )
            self._safe_transition(GameState.ABILITY_4_USED)
            self._safe_transition(GameState.WAIT_BATTLE_END)

    def _configured_live_combat_slots(self) -> tuple[int, ...]:
        sequence = tuple(slot for slot in self.config.combat.slot_sequence if slot >= 0)
        if sequence:
            return sequence
        return (max(0, int(self.config.combat.slot_index or 1) - 1),)

    def _live_zero_fallback_allowed(self, frame: np.ndarray) -> bool:
        if not self.config.combat.low_resource_fallback_enabled:
            return False
        status = self._detect_current_resources(frame)
        prowess = status.prowess.percent
        threshold = max(0.0, float(self.config.combat.low_resource_fallback_percent))
        return prowess is not None and prowess <= threshold

    def _next_live_combat_slot(self) -> int:
        sequence = self._configured_live_combat_slots()
        slot = sequence[self._combat_slot_sequence_index % len(sequence)]
        self._combat_slot_sequence_index += 1
        return slot

    def _use_skill_slot_via_injector(self, action_type: str, slot_index: int, event_type: str) -> bool:
        binding = self._pending_skill_mutation_binding
        if binding is None or binding.slot != slot_index:
            self._spellbook_battle_state.reject_pending(slot_index)
            return False
        fresh_snapshot = self._battle_snapshot_via_injector(force=True)
        fresh_binding = (
            refresh_skill_mutation_binding(fresh_snapshot, binding)
            if isinstance(fresh_snapshot, dict)
            else None
        )
        if fresh_binding is None:
            self._pending_skill_mutation_binding = None
            self._spellbook_battle_state.reject_pending(slot_index)
            self.logger.log_event(
                "skill_mutation_refresh_blocked",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                skill_slot=slot_index,
            )
            return False
        binding = fresh_binding
        self._pending_skill_mutation_binding = binding
        if action_type == "click_combat_slot":
            pre_click_delay_ms = self.config.combat.pre_click_delay_ms
            click_hold_ms = self.config.combat.click_hold_ms
        else:
            pre_click_delay_ms = self.config.ability4.pre_click_delay_ms
            click_hold_ms = self.config.ability4.click_hold_ms
        request = ActionRequest(
            action_type,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "use_js_skill": True,
                "skill_slot": slot_index,
                "slot_index": slot_index,
                "frame_hash": self._last_frame_hash,
                "pre_click_delay_ms": pre_click_delay_ms,
                "click_hold_ms": click_hold_ms,
                **binding.metadata(),
            },
        )
        self.logger.log_event(
            event_type,
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            action_type=action_type,
            js_slot=slot_index,
            frame_hash=self._last_frame_hash,
        )
        confirmed = self.action_executor.execute(request)
        self._pending_skill_mutation_binding = None
        if confirmed:
            self._spellbook_battle_state.confirm_pending(slot_index, battle_id=self.session.battle_id)
            if (
                self._pending_skill_expected_damage is not None
                and self._pending_skill_expected_damage > 0
                and self._damage_boost_armed_battle_id == self.session.battle_id
            ):
                self._damage_boost_armed_battle_id = None
        else:
            self._spellbook_battle_state.reject_pending(slot_index)
        self._pending_skill_expected_damage = None
        return confirmed

    def _handle_battle_end(self, frame: np.ndarray, result: object | None = None) -> None:
        if not self.config.dry_run:
            self._handle_live_battle_end(frame)
            return
        result = result if result is not None else self.battle_end_detector.detect(frame)
        if not result.detected:
            if not self.config.dry_run:
                battle_snapshot = self._battle_snapshot_via_injector(force=True)
                if self._injector_snapshot_has_active_battle(battle_snapshot):
                    self._continue_battle_combat(frame, None)
                    return
                if self._injector_snapshot_has_inactive_battle(battle_snapshot):
                    visible_snapshot = self._visible_hunt_snapshot_via_injector()
                    if self._live_snapshot_on_hunt_page(visible_snapshot):
                        self.logger.log_event(
                            "hunt_return_confirmed",
                            state=self.state_machine.state.value,
                            cycle_id=self.session.cycle_id,
                            battle_id=self.session.battle_id,
                            reason="battle_end_js_hunt_page",
                            frame_hash=self._last_frame_hash,
                        )
                        self._complete_or_mark_incomplete_after_hunt_return(frame, reason="battle_end_js_hunt_page")
                        return
                    if self._live_snapshot_on_fight_page(visible_snapshot):
                        self._log_live_page_wait(
                            "battle_end_inactive_fight_page",
                            visible_snapshot,
                            reason="inactive_fight_page",
                        )
                        if self._exit_battle_via_injector("inactive_fight_page"):
                            return
            battle = self.battle_detector.detect(frame)
            if battle.detected:
                self._continue_battle_combat(frame, battle.panel_bbox)
                return
            if self._hunt_return_confirmed(frame):
                self.logger.log_event(
                    "hunt_return_confirmed",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    reason="battle_end_skipped",
                    frame_hash=self._last_frame_hash,
                )
                self._complete_or_mark_incomplete_after_hunt_return(frame, reason="battle_end_skipped")
            return
        self.logger.log_event(
            "battle_end_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_id="battle_end",
            detector_confidence=result.confidence,
            frame_hash=self._last_frame_hash,
        )
        if self.config.dry_run:
            self.session.mark_battle_outcome("victory")
        if self.state_machine.state == GameState.WAIT_BATTLE_END:
            self.latencies.start("battle_end_to_exit")
            self._safe_transition(GameState.BATTLE_END_DETECTED)
        if not self.session.can_click_exit():
            return
        if result.exit_button_bbox is None and self.config.dry_run:
            return
        center = None if result.exit_button_bbox is None else result.exit_button_bbox.center
        self.logger.log_event(
            "exit_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_confidence=result.confidence,
            x_frame=None if center is None else center.x,
            y_frame=None if center is None else center.y,
        )
        request = ActionRequest(
            "click_exit",
            frame_point=center,
            screen_point=None if center is None else self.mapper.frame_to_pynput(center),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "detector_confidence": result.confidence,
                "frame_hash": self._last_frame_hash,
                "use_js_open_hunt": not self.config.dry_run,
            },
        )
        if self.action_executor.execute(request):
            latency = self.latencies.finish("battle_end_to_exit")
            self.logger.log_event("latency", latency_type="battle_end_to_exit", latency_ms=latency, reaction_latency_ms=latency)
            self._safe_transition(GameState.EXIT_BATTLE)
            self._safe_transition(GameState.STATISTICS_WAIT)

    def _exit_battle_via_injector(self, reason: str) -> bool:
        if self.session.battle_id is None or not self.session.can_click_exit():
            return False
        self.logger.log_event(
            "exit_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_confidence=1.0,
            reason=reason,
            frame_hash=self._last_frame_hash,
        )
        request = ActionRequest(
            "click_exit",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "detector_confidence": 1.0,
                "frame_hash": self._last_frame_hash,
                "use_js_open_hunt": not self.config.dry_run,
                "reason": reason,
            },
        )
        if not self.action_executor.execute(request):
            return False
        self.latencies.finish("battle_end_to_exit")
        if self.state_machine.state == GameState.WAIT_BATTLE_END:
            self._safe_transition(GameState.BATTLE_END_DETECTED, reason=reason)
        if self.state_machine.state == GameState.BATTLE_END_DETECTED:
            self._safe_transition(GameState.EXIT_BATTLE, reason=reason)
        if self.state_machine.state == GameState.EXIT_BATTLE:
            self._safe_transition(GameState.STATISTICS_WAIT, reason=reason)
        return True

    def _handle_live_battle_end(self, frame: np.ndarray) -> None:
        battle_snapshot = self._battle_snapshot_via_injector(force=True)
        if self._injector_snapshot_has_active_battle(battle_snapshot):
            self._continue_battle_combat(frame, None)
            return
        visible_snapshot = self._visible_hunt_snapshot_via_injector()
        if self._live_snapshot_on_hunt_page(visible_snapshot):
            self._log_live_page_wait("hunt_return_confirmed", visible_snapshot, reason="live_battle_end_hunt_page")
            self._complete_or_mark_incomplete_after_hunt_return(frame, reason="live_battle_end_hunt_page")
            return
        if self._live_snapshot_on_fight_page(visible_snapshot):
            self._log_live_page_wait("battle_end_inactive_fight_page", visible_snapshot, reason="live_battle_end_fight_page")
            self._exit_battle_via_injector("live_battle_end_fight_page")

    def _continue_battle_combat(self, frame: np.ndarray, panel_bbox: Rect | None) -> bool:
        if not self.config.combat.enabled or self.session.battle_id is None:
            return False
        if self._last_combat_click_monotonic is not None:
            elapsed_ms = (time.monotonic() - self._last_combat_click_monotonic) * 1000
            if elapsed_ms < self.config.combat.click_interval_ms:
                return False
        slot_index = max(1, int(self.config.combat.slot_index or 4))
        if not self.config.dry_run:
            slot_index = self._live_combat_slot_for_current_resources(frame)
            if slot_index is None:
                return False
            if self._use_skill_slot_via_injector("click_combat_slot", slot_index, "combat_slot_js_intended"):
                self._last_combat_click_monotonic = time.monotonic()
                return True
            fallback_slot = max(0, int(self.config.combat.low_resource_fallback_slot_index))
            if (
                slot_index != fallback_slot
                and self._live_zero_fallback_allowed(frame)
                and self._use_skill_slot_via_injector(
                    "click_combat_slot", fallback_slot, "combat_slot_js_fallback_intended"
                )
            ):
                self._last_combat_click_monotonic = time.monotonic()
                return True
            return False
        slots = self.ability_detector.detect_slots(frame, panel_bbox)
        if len(slots) < slot_index:
            self.logger.log_event(
                "combat_slot_missing",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                slot_index=slot_index,
                frame_hash=self._last_frame_hash,
            )
            return False
        slot = slots[slot_index - 1]
        ready_ok = slot.ready or self.config.dry_run or not self.config.combat.require_ready_confirmation
        if slot.confidence < self.config.combat.slot_confidence_threshold or not ready_ok:
            self.logger.log_event(
                "action_blocked",
                state=self.state_machine.state.value,
                action_type="click_combat_slot",
                battle_id=self.session.battle_id,
                block_reason="combat_slot_not_ready",
                slot_index=slot.index,
                detector_confidence=slot.confidence,
            )
            return False
        request = ActionRequest(
            "click_combat_slot",
            frame_point=slot.center,
            screen_point=self.mapper.frame_to_pynput(slot.center),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "slot_index": slot.index,
                "detector_confidence": slot.confidence,
                "frame_hash": self._last_frame_hash,
                "pre_click_delay_ms": self.config.combat.pre_click_delay_ms,
                "click_hold_ms": self.config.combat.click_hold_ms,
            },
        )
        self.logger.log_event(
            "combat_slot_intended",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            slot_index=slot.index,
            x_frame=slot.center.x,
            y_frame=slot.center.y,
            detector_confidence=slot.confidence,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._last_combat_click_monotonic = time.monotonic()
            return True
        return False

    def _handle_statistics(self, frame: np.ndarray, result: object | None = None) -> None:
        if not self.config.dry_run:
            self._handle_live_statistics_wait(frame)
            return
        result = result if result is not None else self.statistics_detector.detect(frame)
        if not result.detected:
            if self._hunt_return_confirmed(frame):
                self.logger.log_event(
                    "hunt_return_confirmed",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    reason="statistics_skipped",
                    frame_hash=self._last_frame_hash,
                )
                self._complete_or_mark_incomplete_after_hunt_return(frame, reason="statistics_skipped")
            elif _game_shell_present(frame):
                self._open_hunt_from_game_shell(frame)
                self._state_entered_monotonic = time.monotonic()
            return
        self.logger.log_event(
            "statistics_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_id="statistics",
            detector_confidence=result.confidence,
            frame_hash=self._last_frame_hash,
        )
        if self.state_machine.state == GameState.STATISTICS_WAIT:
            self.latencies.start("statistics_to_hunt")
            self._safe_transition(GameState.STATISTICS_DETECTED)
        self._click_hunt_from_statistics(result, transition_to_cooldown=True)

    def _click_hunt_from_statistics(self, result: object, *, transition_to_cooldown: bool) -> bool:
        if not self.session.can_click_hunt():
            return False
        if result.hunt_button_bbox is None and self.config.dry_run:
            return False
        center = None if result.hunt_button_bbox is None else result.hunt_button_bbox.center
        self.logger.log_event(
            "hunt_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            detector_confidence=result.confidence,
            x_frame=None if center is None else center.x,
            y_frame=None if center is None else center.y,
        )
        request = ActionRequest(
            "click_hunt",
            frame_point=center,
            screen_point=None if center is None else self.mapper.frame_to_pynput(center),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "click_count": 2,
                "detector_confidence": result.confidence,
                "frame_hash": self._last_frame_hash,
                "use_js_open_hunt": not self.config.dry_run,
            },
        )
        if self.action_executor.execute(request):
            self._last_hunt_click_monotonic = time.monotonic()
            if transition_to_cooldown:
                latency = self.latencies.finish("statistics_to_hunt")
                self.logger.log_event("latency", latency_type="statistics_to_hunt", latency_ms=latency, reaction_latency_ms=latency)
                self._safe_transition(GameState.RETURN_TO_HUNT)
                self._safe_transition(GameState.COOLDOWN)
            return True
        return False

    def _handle_cooldown(self, frame: np.ndarray) -> None:
        if not self.config.dry_run:
            self._handle_live_cooldown(frame)
            return
        if self.battle_detector.detect(frame).detected or self.battle_end_detector.detect(frame).detected:
            return
        statistics = self.statistics_detector.detect(frame)
        if statistics.detected:
            if self._hunt_retry_due() and statistics.hunt_button_bbox is not None and self.session.can_click_hunt():
                self.logger.log_event(
                    "hunt_return_retry",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    hunt_click_attempt=self.session.hunt_click_count(),
                    detector_confidence=statistics.confidence,
                    frame_hash=self._last_frame_hash,
                )
                self._click_hunt_from_statistics(statistics, transition_to_cooldown=False)
            return
        if not self._hunt_return_confirmed(frame):
            if _game_shell_present(frame):
                self._open_hunt_from_game_shell(frame)
            else:
                self._recover_unknown_screen(frame)
            self.logger.log_event(
                "hunt_return_waiting",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                frame_hash=self._last_frame_hash,
            )
            return
        self._complete_or_mark_incomplete_after_hunt_return(frame, reason="hunt_return_confirmed")

    def _handle_live_statistics_wait(self, frame: np.ndarray) -> None:
        visible_snapshot = self._visible_hunt_snapshot_via_injector()
        if self._live_snapshot_on_hunt_page(visible_snapshot):
            self._log_live_page_wait("hunt_return_confirmed", visible_snapshot, reason="live_statistics_hunt_page")
            self._complete_or_mark_incomplete_after_hunt_return(frame, reason="live_statistics_hunt_page")
            return
        if self._live_snapshot_on_fight_page(visible_snapshot):
            battle_snapshot = self._battle_snapshot_via_injector(force=True)
            if self._injector_snapshot_has_active_battle(battle_snapshot):
                self._continue_battle_combat(frame, None)
                return
            self._log_live_page_wait("battle_end_inactive_fight_page", visible_snapshot, reason="live_statistics_fight_page")
            self._exit_battle_via_injector("live_statistics_fight_page")
            return
        if self._live_snapshot_on_area_error(visible_snapshot):
            self._log_live_page_wait("live_area_error_waiting", visible_snapshot, reason="live_statistics_area_error")
            return
        self._open_hunt_from_game_shell(frame)

    def _handle_live_cooldown(self, frame: np.ndarray) -> bool:
        visible_snapshot = self._visible_hunt_snapshot_via_injector()
        if self._live_snapshot_on_hunt_page(visible_snapshot):
            self._log_live_page_wait("hunt_return_confirmed", visible_snapshot, reason="live_cooldown_hunt_page")
            self._complete_or_mark_incomplete_after_hunt_return(frame, reason="live_cooldown_hunt_page")
            return True
        if self._live_snapshot_on_fight_page(visible_snapshot):
            battle_snapshot = self._battle_snapshot_via_injector(force=True)
            if self._injector_snapshot_has_active_battle(battle_snapshot):
                self._continue_battle_combat(frame, None)
            else:
                self._exit_battle_via_injector("live_cooldown_fight_page")
            return True
        self._open_hunt_from_game_shell(frame)
        return False

    def _complete_or_mark_incomplete_after_hunt_return(self, frame: np.ndarray, *, reason: str) -> None:
        self._last_hunt_click_monotonic = None
        if self._route_resume_target_name:
            resume_target = self._route_resume_target_name
            resume_kind = self._route_resume_recovery_kind
            self._route_resume_target_name = None
            self._route_resume_recovery_kind = None
            self.session.reset_cycle_attempt()
            self._reset_location_context()
            if not self._validate_route_coordinator_binding(resume_kind, resume_target):
                return
            if not _same_location_name(self.current_location_name, resume_target):
                if self.state_machine.state is not GameState.LOCATION_SEARCH:
                    self._safe_transition(GameState.LOCATION_SEARCH, reason="route_battle_interruption_resolved")
                self._start_location_route(
                    resume_target,
                    kind=resume_kind or "post_battle_route_resume",
                    reason="post_battle_route_resume",
                )
                return
            if resume_kind in {
                "quest_accept",
                "quest_dialogue",
                "quest_location",
                "quest_turn_in",
            }:
                if self.state_machine.state is not GameState.LOCATION_SEARCH:
                    self._safe_transition(
                        GameState.LOCATION_SEARCH,
                        reason="route_battle_destination_confirmed",
                    )
                self._route_recovery_kind = resume_kind
                self._route_destination_name = resume_target
                self._finish_route_arrival("route_battle_destination_confirmed")
                return
        if not self.session.can_complete_cycle(require_victory=not self.config.dry_run):
            self.logger.log_event(
                "cycle_incomplete",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=reason,
                battles_detected=self.session.battles_detected,
                ability4_actions=self.session.ability4_actions,
                combat_actions=self.session.combat_actions,
                battle_outcome=self.session.cycle_battle_outcome,
                frame_hash=self._last_frame_hash,
            )
            self.session.mark_incomplete_cycle()
            self.session.mark_recovery()
            self._reset_location_context()
            self._safe_transition(GameState.LOCATION_SEARCH, reason="cycle_incomplete")
            return
        completed_cycle_id = self.session.cycle_id
        if self.session.completed_cycles + 1 < self.session.requested_cycles:
            self._use_item_recovery_after_cycle(frame, reason=reason)
        self.session.complete_cycle()
        self.logger.log_event(
            "cycle_completed",
            state=self.state_machine.state.value,
            cycle_id=completed_cycle_id,
            completed_cycles=self.session.completed_cycles,
            reason=reason,
            frame_hash=self._last_frame_hash,
        )
        if self.session.completed_cycles >= self.session.requested_cycles:
            self.logger.log_event(
                "session_stopped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="max_cycles",
            )
            self._safe_transition(GameState.STOPPED, reason="max_cycles")
        elif self.state_machine.state != GameState.LOCATION_SEARCH and self.state_machine.can_transition(GameState.LOCATION_SEARCH):
            self._safe_transition(GameState.LOCATION_SEARCH, reason=reason)
            if not self._resources_allow_search(frame):
                return
        elif not self._resources_allow_search(frame):
            return
        else:
            self._safe_transition(GameState.LOCATION_SEARCH, reason=reason)


    def _hunt_retry_due(self) -> bool:
        if self._last_hunt_click_monotonic is None:
            return True
        elapsed_ms = (time.monotonic() - self._last_hunt_click_monotonic) * 1000
        return elapsed_ms >= HUNT_RETRY_DELAY_MS

    def _hunt_return_confirmed(self, frame: np.ndarray) -> bool:
        return _location_map_present(frame, self.config.viewport.direction_pad_roi)
