from __future__ import annotations

import json
import time

import numpy as np

from src.antibot_cv.automation.checkpoint import write_json_checkpoint
from src.antibot_cv.automation.runtime_helpers import optional_float as _optional_float
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.detection.resources import ResourceBarStatus, ResourceStatus


class ResourceRuntimeMixin:
    def _handle_post_revive_recovery(self, frame: np.ndarray) -> None:
        if self.state_machine.state is not GameState.POST_REVIVE_RECOVERY:
            return
        started = self._post_revive_recovery_started_monotonic or time.monotonic()
        self._post_revive_recovery_started_monotonic = started
        elapsed_ms = (time.monotonic() - started) * 1000
        timeout_ms = max(1000, int(self.config.leveling.post_revive_resource_timeout_ms))
        if elapsed_ms >= timeout_ms:
            self._stop_leveling_unsafe("post_revive_resource_recovery_timeout")
            return
        if not self._rest_check_due():
            return

        status = self._detect_current_resources(frame)
        missing = self._resource_missing(status)
        low = self._resource_gaps(status, recover=True)
        self._log_resource_status(
            "post_revive_resource_status",
            status,
            low_resources=low,
            missing=missing,
            force=True,
        )
        if not missing and not low:
            reason = self._post_revive_resume_reason or "post_revive_resources_ready"
            self._log_recovery_phase(
                "resources_ready",
                reason=reason,
                health_percent=status.health.percent,
                prowess_percent=status.prowess.percent,
            )
            self._resume_after_post_revive_recovery(reason)
            return

        max_attempts = max(0, int(self.config.leveling.post_revive_max_item_attempts))
        if (
            not missing
            and low
            and self.config.item_recovery.enabled
            and self._post_revive_recovery_attempts < max_attempts
        ):
            self._post_revive_recovery_attempts += 1
            self._last_item_recovery_attempt_monotonic = time.monotonic()
            succeeded = self._use_item_recovery_after_cycle(
                frame,
                reason="post_revive_resource_recovery",
                require_after_cycle=False,
                open_hunt_after=False,
                threshold_override=float(self.config.resources.recover_to_percent),
            )
            self.logger.log_event(
                "post_revive_resource_items_result",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                attempt=self._post_revive_recovery_attempts,
                max_attempts=max_attempts,
                succeeded=succeeded,
                health_percent=status.health.percent,
                prowess_percent=status.prowess.percent,
            )
            self._state_snapshot_cache = None
            self._last_state_snapshot_monotonic = None
            self._last_state_snapshot_success_monotonic = None
            return

        if not self.config.dry_run:
            self._refresh_resource_source_via_injector()

    def _detect_current_resources(self, frame: np.ndarray) -> ResourceStatus:
        if not self.config.dry_run:
            js_status = self._resource_status_via_injector()
            if js_status is not None:
                return js_status
            if self.browser_client_id:
                return ResourceStatus(
                    health=ResourceBarStatus("health", False, None, 0.0),
                    prowess=ResourceBarStatus("prowess", False, None, 0.0),
                )
        return self.resource_detector.detect(frame)

    def _resource_status_via_injector(self) -> ResourceStatus | None:
        last_success = self._last_state_snapshot_success_monotonic
        max_age_ms = max(1000, int(self.config.leveling.snapshot_stale_timeout_ms))
        if last_success is None or (time.monotonic() - last_success) * 1000 >= max_age_ms:
            self._state_snapshot_via_injector(force=True)
            last_success = self._last_state_snapshot_success_monotonic
            if last_success is None or (time.monotonic() - last_success) * 1000 >= max_age_ms:
                return None
        snapshot = self._state_snapshot_cache
        sections = snapshot.get("sections") if isinstance(snapshot, dict) else None
        player_section = sections.get("player") if isinstance(sections, dict) else None
        player = player_section.get("data") if isinstance(player_section, dict) else None
        cached_health = _optional_float(player.get("hpPercent")) if isinstance(player, dict) else None
        cached_prowess = _optional_float(player.get("prowessPercent")) if isinstance(player, dict) else None
        if cached_health is not None or cached_prowess is not None:
            return ResourceStatus(
                health=ResourceBarStatus("health", cached_health is not None, cached_health, 1.0 if cached_health is not None else 0.0),
                prowess=ResourceBarStatus("prowess", cached_prowess is not None, cached_prowess, 1.0 if cached_prowess is not None else 0.0),
            )
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "resource_snapshot",
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
        except Exception as exc:
            self.logger.log_event(
                "resource_js_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=str(exc),
                frame_hash=self._last_frame_hash,
            )
            return None
        if not result.ok:
            self.logger.log_event(
                "resource_js_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=result.message,
                injector_client_id=result.client_id,
                frame_hash=self._last_frame_hash,
            )
            return None
        try:
            payload = json.loads(result.message)
        except json.JSONDecodeError:
            self.logger.log_event(
                "resource_js_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason="invalid_json",
                injector_client_id=result.client_id,
                frame_hash=self._last_frame_hash,
            )
            return None
        health_percent = _optional_float(payload.get("healthPercent")) if isinstance(payload, dict) else None
        prowess_percent = _optional_float(payload.get("prowessPercent")) if isinstance(payload, dict) else None
        if health_percent is None and prowess_percent is None:
            return None
        return ResourceStatus(
            health=ResourceBarStatus("health", health_percent is not None, health_percent, 1.0 if health_percent is not None else 0.0),
            prowess=ResourceBarStatus("prowess", prowess_percent is not None, prowess_percent, 1.0 if prowess_percent is not None else 0.0),
        )

    def _rest_check_due(self) -> bool:
        now = time.monotonic()
        if self._last_rest_check_monotonic is not None:
            elapsed_ms = (now - self._last_rest_check_monotonic) * 1000
            if elapsed_ms < self.config.resources.rest_check_interval_ms:
                return False
        self._last_rest_check_monotonic = now
        return True

    def _enter_resting(self, status: ResourceStatus, low: list[str]) -> None:
        if self.state_machine.state != GameState.RESTING:
            self.session.mark_resource_wait()
            self._resting_since_monotonic = time.monotonic()
            self._last_rest_check_monotonic = None
            self._last_resource_refresh_monotonic = None
            self.logger.log_event(
                "resource_rest_started",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                low_resources=low,
                health_percent=status.health.percent,
                prowess_percent=status.prowess.percent,
                frame_hash=self._last_frame_hash,
            )
            self._safe_transition(GameState.RESTING, reason="resources_low")

    def _leave_resting(self) -> None:
        self._resting_since_monotonic = None
        self._last_rest_check_monotonic = None
        self._last_resource_refresh_monotonic = None
        self._safe_transition(GameState.LOCATION_SEARCH, reason="resources_ready")

    def _maybe_refresh_resource_source(self, status: ResourceStatus, *, low: list[str], missing: list[str]) -> bool:
        if self.config.dry_run:
            return False
        if self._resting_since_monotonic is None:
            return False
        refresh_after_ms = max(0, int(self.config.resources.rest_refresh_after_ms))
        refresh_interval_ms = max(0, int(self.config.resources.rest_refresh_interval_ms))
        if refresh_after_ms <= 0 or refresh_interval_ms <= 0:
            return False
        now = time.monotonic()
        resting_ms = (now - self._resting_since_monotonic) * 1000
        if resting_ms < refresh_after_ms:
            return False
        if self._last_resource_refresh_monotonic is not None:
            elapsed_ms = (now - self._last_resource_refresh_monotonic) * 1000
            if elapsed_ms < refresh_interval_ms:
                return False
        self._last_resource_refresh_monotonic = now
        ok, message = self._refresh_resource_source_via_injector()
        self.logger.log_event(
            "resource_source_refresh",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            ok=ok,
            message=message,
            resting_ms=resting_ms,
            health_percent=status.health.percent,
            prowess_percent=status.prowess.percent,
            low_resources=low,
            missing_resources=missing,
            frame_hash=self._last_frame_hash,
        )
        return ok

    def _refresh_resource_source_via_injector(self) -> tuple[bool, str]:
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "resource_refresh",
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
        except Exception as exc:
            return False, str(exc)
        return bool(result.ok), result.message

    def _resource_gaps(self, status: ResourceStatus, *, recover: bool) -> list[str]:
        health_threshold = self.config.resources.recover_to_percent if recover else self.config.resources.health_min_percent
        prowess_threshold = self.config.resources.recover_to_percent if recover else self.config.resources.prowess_min_percent
        low: list[str] = []
        if status.health.percent is not None and status.health.percent < health_threshold:
            low.append("health")
        if status.prowess.percent is not None and status.prowess.percent < prowess_threshold:
            low.append("prowess")
        return low

    def _resource_missing(self, status: ResourceStatus) -> list[str]:
        missing: list[str] = []
        if status.health.percent is None:
            missing.append("health")
        if status.prowess.percent is None:
            missing.append("prowess")
        return missing

    def _item_recovery_threshold(self, kind: str) -> float:
        config = self.config.item_recovery
        if kind == "health" and config.health_use_when_below_percent is not None:
            return float(config.health_use_when_below_percent)
        if kind == "prowess" and config.prowess_use_when_below_percent is not None:
            return float(config.prowess_use_when_below_percent)
        return float(config.use_when_below_percent)

    def _item_recovery_needed_for_status(self, status: ResourceStatus, missing: list[str]) -> bool:
        config = self.config.item_recovery
        if not config.enabled:
            return False
        if status.health.percent is not None and status.health.percent < self._item_recovery_threshold("health"):
            return True
        if status.prowess.percent is not None and status.prowess.percent < self._item_recovery_threshold("prowess"):
            return True
        return bool(missing and config.use_if_resources_missing)

    def _try_item_recovery_before_rest(
        self,
        frame: np.ndarray,
        status: ResourceStatus,
        low: list[str],
        missing: list[str],
        *,
        reason: str,
    ) -> bool:
        if self.config.dry_run or not self.config.item_recovery.enabled:
            return False
        if not self._item_recovery_needed_for_status(status, missing):
            return False
        now = time.monotonic()
        if self._last_item_recovery_attempt_monotonic is not None:
            elapsed_ms = (now - self._last_item_recovery_attempt_monotonic) * 1000
            min_interval_ms = max(1000, self.config.item_recovery.inventory_open_delay_ms + 1500)
            if elapsed_ms < min_interval_ms:
                return False
        self._last_item_recovery_attempt_monotonic = now
        self.logger.log_event(
            "recovery_items_before_search",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            reason=reason,
            health_percent=status.health.percent,
            prowess_percent=status.prowess.percent,
            low_resources=low,
            missing_resources=missing,
            health_threshold=self._item_recovery_threshold("health"),
            prowess_threshold=self._item_recovery_threshold("prowess"),
            frame_hash=self._last_frame_hash,
        )
        return self._use_item_recovery_after_cycle(frame, reason=reason, require_after_cycle=False)

    def _log_resource_status(
        self,
        event_type: str,
        status: ResourceStatus,
        *,
        low_resources: list[str] | None = None,
        missing: list[str] | None = None,
        fallback_slot: int | None = None,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and self._last_resource_log_monotonic is not None:
            if (now - self._last_resource_log_monotonic) * 1000 < self.config.resources.rest_check_interval_ms:
                return
        self._last_resource_log_monotonic = now
        self.logger.log_event(
            event_type,
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            health_percent=status.health.percent,
            prowess_percent=status.prowess.percent,
            health_detected=status.health.detected,
            prowess_detected=status.prowess.detected,
            low_resources=low_resources or [],
            missing_resources=missing or [],
            fallback_slot=fallback_slot,
            frame_hash=self._last_frame_hash,
        )

    def _search_pause_active(self) -> bool:
        if self._search_pause_until_monotonic is None:
            return False
        if time.monotonic() < self._search_pause_until_monotonic:
            return True
        self._search_pause_until_monotonic = None
        self.logger.log_event(
            "search_recovery_resumed",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            frame_hash=self._last_frame_hash,
        )
        return False

    def _recover_stuck_state(self, frame: np.ndarray) -> bool:
        if not self.config.recovery.enabled:
            return False
        state = self.state_machine.state
        elapsed_ms = self._state_elapsed_ms()
        if state == GameState.BATTLE_WAIT and elapsed_ms >= self.config.recovery.battle_wait_timeout_ms:
            if not self.config.dry_run:
                snapshot = self._visible_hunt_snapshot_via_injector()
                if self._live_snapshot_on_fight_page(snapshot):
                    self._log_live_page_wait(
                        "battle_wait_extended",
                        snapshot,
                        state_elapsed_ms=elapsed_ms,
                        reason="current_fight_page",
                    )
                    self._state_entered_monotonic = time.monotonic()
                    self._sync_battle_from_injector(frame, force_probe=True)
                    return True
                if self._live_snapshot_on_hunt_page(snapshot):
                    return self._recover_to_search("battle_wait_timeout_hunt_confirmed", frame)
                return self._stop_leveling_unsafe("battle_wait_timeout_unconfirmed_state")
            return self._recover_to_search("battle_wait_timeout", frame)
        if state == GameState.BATTLE_ACTIVE and elapsed_ms >= self.config.recovery.battle_active_timeout_ms:
            battle_end = self.battle_end_detector.detect(frame)
            if battle_end.detected:
                self._safe_transition(GameState.WAIT_BATTLE_END, reason="battle_active_timeout_battle_end_detected")
                self._handle_battle_end(frame, battle_end)
                return True
            if not self.config.dry_run:
                battle_snapshot = self._battle_snapshot_via_injector(force=True)
                if self._injector_snapshot_has_active_battle(battle_snapshot):
                    self.logger.log_event(
                        "battle_active_wait_extended",
                        state=self.state_machine.state.value,
                        cycle_id=self.session.cycle_id,
                        battle_id=self.session.battle_id,
                        state_elapsed_ms=elapsed_ms,
                        reason="active_battle_confirmed",
                        frame_hash=self._last_frame_hash,
                    )
                    self._state_entered_monotonic = time.monotonic()
                    self._continue_battle_combat(frame, None)
                    return True
                if self._injector_snapshot_has_inactive_battle(battle_snapshot):
                    self._safe_transition(GameState.WAIT_BATTLE_END, reason="battle_active_timeout_inactive_confirmed")
                    self._handle_battle_end(frame)
                    return True
                return self._stop_leveling_unsafe("battle_active_timeout_unconfirmed_state")
            return self._recover_to_search("battle_active_timeout", frame)
        if state in {GameState.WAIT_BATTLE_END, GameState.BATTLE_END_DETECTED} and elapsed_ms >= self.config.recovery.battle_end_timeout_ms:
            battle = self.battle_detector.detect(frame)
            if battle.detected:
                self.logger.log_event(
                    "battle_end_wait_extended",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    detector_confidence=battle.confidence,
                    state_elapsed_ms=elapsed_ms,
                    frame_hash=self._last_frame_hash,
                )
                self._state_entered_monotonic = time.monotonic()
                self._continue_battle_combat(frame, battle.panel_bbox)
                return True
            if not self.config.dry_run:
                battle_snapshot = self._battle_snapshot_via_injector(force=True)
                if self._injector_snapshot_has_active_battle(battle_snapshot):
                    visible_snapshot = self._visible_hunt_snapshot_via_injector()
                    self._log_live_page_wait(
                        "battle_end_wait_extended",
                        visible_snapshot,
                        state_elapsed_ms=elapsed_ms,
                        reason="current_fight_page",
                    )
                    self._state_entered_monotonic = time.monotonic()
                    self._continue_battle_combat(frame, None)
                    return True
                snapshot = self._visible_hunt_snapshot_via_injector()
                if self._live_snapshot_on_hunt_page(snapshot):
                    self._log_live_page_wait(
                        "hunt_return_confirmed",
                        snapshot,
                        reason="battle_end_js_hunt_page",
                    )
                    self._complete_or_mark_incomplete_after_hunt_return(frame, reason="battle_end_js_hunt_page")
                    return True
                if self._live_snapshot_on_fight_page(snapshot):
                    self._log_live_page_wait(
                        "battle_end_inactive_fight_page",
                        snapshot,
                        state_elapsed_ms=elapsed_ms,
                        reason="inactive_fight_page",
                    )
                    if self._exit_battle_via_injector("inactive_fight_page"):
                        return True
                return self._stop_leveling_unsafe("battle_end_timeout_unconfirmed_state")
            return self._recover_to_search("battle_end_timeout", frame)
        if state in {GameState.STATISTICS_WAIT, GameState.STATISTICS_DETECTED} and elapsed_ms >= self.config.recovery.statistics_timeout_ms:
            return self._recover_to_search("statistics_timeout", frame)
        return False

    def _state_elapsed_ms(self) -> float:
        return (time.monotonic() - self._state_entered_monotonic) * 1000

    def _recover_to_search(self, reason: str, frame: np.ndarray) -> bool:
        self.session.mark_recovery()
        self.logger.log_event(
            "state_recovered",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            reason=reason,
            state_elapsed_ms=self._state_elapsed_ms(),
            frame_hash=self._last_frame_hash,
        )
        if self.session.cycle_had_battle or self.session.cycle_had_combat_action:
            self.session.mark_incomplete_cycle()
        else:
            self.session.reset_cycle_attempt()
        self._last_attack_click_monotonic = None
        self._last_combat_click_monotonic = None
        self._combat_slot_sequence_index = 0
        self._reset_location_context()
        self._safe_transition(GameState.LOCATION_SEARCH, reason=reason)
        return True

    def write_checkpoint(self, *, force: bool = False) -> None:
        if not self.config.leveling.enabled:
            return
        now = time.monotonic()
        if not force and self._last_checkpoint_monotonic is not None:
            elapsed_ms = (now - self._last_checkpoint_monotonic) * 1000
            if elapsed_ms < max(250, int(self.config.leveling.checkpoint_interval_ms)):
                return
        payload = {
            "schema_version": 2,
            "session_id": self.session.session_id,
            "updated_at": time.time(),
            "state": self.state_machine.state.value,
            "cycle_id": self.session.cycle_id,
            "battle_id": self.session.battle_id,
            "completed_cycles": self.session.completed_cycles,
            "character_name": self.current_character_name,
            "bound_character_name": self._bound_character_name,
            "current_level": self.current_level,
            "current_xp_percent": self.current_xp_percent,
            "goal_level": self.config.leveling.target_level,
            "deaths_observed": self.deaths_observed,
            "page_kind": self.current_page_kind,
            "location_name": self.current_location_name,
            "quest_origin_location": self._quest_origin_location_name,
            "active_quest_id": self._active_quest_id,
            "quest_chain": (
                None
                if self._quest_director is None
                else self._quest_director.chain.checkpoint()
            ),
            "quest_policy_intent": None if self._quest_policy_intent is None else self._quest_policy_intent.value,
            "quest_target_names": list(self._quest_target_names),
            "quest_target_specs": [
                {"name": name, "level": level}
                for name, level in self._quest_target_specs
            ],
            "quest_route_locations": list(self._quest_route_locations),
            "death_checkpoint": None
            if self._death_checkpoint is None
            else {
                "activity": self._death_checkpoint.activity,
                "location": self._death_checkpoint.location,
                "quest": self._death_checkpoint.quest,
                "snapshot_id": self._death_checkpoint.snapshot_id,
            },
            "effective_target_levels": list(self._effective_target_levels()),
            "last_error_reason": self.last_error_reason,
            "leveling_intent": self.last_leveling_intent,
            "leveling_reason": self.last_leveling_reason,
        }
        write_json_checkpoint(self.run_dir / "checkpoint.json", payload)
        self._last_checkpoint_monotonic = now
