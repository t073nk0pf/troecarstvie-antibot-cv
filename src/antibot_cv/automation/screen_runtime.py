from __future__ import annotations

import time

import numpy as np

from src.antibot_cv.automation.actions import ActionRequest
from src.antibot_cv.automation.runtime_constants import (
    GAME_ENTRY_URL,
    RETURN_TO_LOCATION_POINT,
    SCREEN_SYNC_INTERVAL_MS,
    UNKNOWN_SCREEN_BACK_DELAY_MS,
)
from src.antibot_cv.automation.runtime_helpers import (
    browser_confirm_dialog_cancel_point as _browser_confirm_dialog_cancel_point,
    browser_confirm_dialog_present as _browser_confirm_dialog_present,
    frame_has_visual_content as _frame_has_visual_content,
    game_shell_present as _game_shell_present,
    location_map_present as _location_map_present,
)
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.viewport.coordinates import Point


class ScreenRuntimeMixin:
    def _sync_from_current_screen(self, frame: np.ndarray) -> bool:
        if self.state_machine.state in {GameState.STOPPED, GameState.ERROR}:
            return False
        if not self._screen_sync_due():
            return False

        if self.state_machine.state in {
            GameState.LOCATION_SEARCH,
            GameState.VIEWPORT_SCAN,
            GameState.TARGET_FOUND,
            GameState.TARGET_SELECTED,
            GameState.BATTLE_WAIT,
        } and self.target_locator.locate(frame):
            return False

        statistics = self.statistics_detector.detect(frame)
        if statistics.detected and self._should_sync_screen("statistics"):
            self._ensure_battle_context("screen_sync_statistics")
            self._reset_location_context()
            self._log_screen_sync("statistics", statistics.confidence)
            if not self._sync_transition(GameState.STATISTICS_WAIT, "screen_sync_statistics"):
                return False
            self._handle_statistics(frame, statistics)
            return True

        battle_end = self.battle_end_detector.detect(frame)
        if battle_end.detected and self._should_sync_screen("battle_end"):
            self._ensure_battle_context("screen_sync_battle_end")
            self._reset_location_context()
            self._log_screen_sync("battle_end", battle_end.confidence)
            if not self._sync_transition(GameState.WAIT_BATTLE_END, "screen_sync_battle_end"):
                return False
            self._handle_battle_end(frame, battle_end)
            return True

        battle = self.battle_detector.detect(frame)
        if battle.detected and self._should_sync_screen("battle"):
            self._ensure_battle_context("screen_sync_battle")
            self._reset_location_context()
            self._log_screen_sync("battle", battle.confidence)
            self.session.mark_battle_detected()
            self.latencies.start("battle_to_ability4")
            if not self._sync_transition(GameState.BATTLE_ACTIVE, "screen_sync_battle"):
                return False
            self._handle_battle_active(frame, battle)
            return True

        attack = self.attack_detector.detect(frame)
        if attack.detected and self._should_sync_screen("attack_button"):
            if self.target_locator.locate(frame):
                return False
            self._ensure_battle_context("screen_sync_attack")
            self._reset_location_context()
            self._log_screen_sync("attack_button", attack.confidence)
            if not self._sync_transition(GameState.BATTLE_WAIT, "screen_sync_attack"):
                return False
            self._click_attack_if_available(frame)
            return True

        return False

    def _should_sync_screen(self, screen: str) -> bool:
        state = self.state_machine.state
        if screen == "statistics":
            return state not in {
                GameState.STATISTICS_WAIT,
                GameState.STATISTICS_DETECTED,
                GameState.RETURN_TO_HUNT,
                GameState.COOLDOWN,
            }
        if screen == "battle_end":
            return state not in {
                GameState.WAIT_BATTLE_END,
                GameState.BATTLE_END_DETECTED,
                GameState.EXIT_BATTLE,
                GameState.STATISTICS_WAIT,
                GameState.STATISTICS_DETECTED,
                GameState.RETURN_TO_HUNT,
                GameState.COOLDOWN,
            }
        if screen == "battle":
            return state not in {
                GameState.BATTLE_ACTIVE,
                GameState.ABILITY_4_USED,
                GameState.WAIT_BATTLE_END,
                GameState.BATTLE_END_DETECTED,
                GameState.EXIT_BATTLE,
                GameState.STATISTICS_WAIT,
                GameState.STATISTICS_DETECTED,
                GameState.RETURN_TO_HUNT,
                GameState.COOLDOWN,
            }
        if screen == "attack_button":
            return state not in {
                GameState.BATTLE_WAIT,
                GameState.BATTLE_ACTIVE,
                GameState.ABILITY_4_USED,
                GameState.WAIT_BATTLE_END,
                GameState.BATTLE_END_DETECTED,
                GameState.EXIT_BATTLE,
                GameState.STATISTICS_WAIT,
                GameState.STATISTICS_DETECTED,
                GameState.RETURN_TO_HUNT,
                GameState.COOLDOWN,
            }
        return False

    def _sync_transition(self, target: GameState, reason: str) -> bool:
        if self.state_machine.state == target:
            return True
        if self.state_machine.can_transition(target):
            self._safe_transition(target, reason=reason)
            return self.state_machine.state == target
        if self.state_machine.state != GameState.LOCATION_SEARCH and self.state_machine.can_transition(GameState.LOCATION_SEARCH):
            self._safe_transition(GameState.LOCATION_SEARCH, reason=f"{reason}_reset")
        if self.state_machine.can_transition(target):
            self._safe_transition(target, reason=reason)
            return self.state_machine.state == target
        self.logger.log_event(
            "screen_sync_blocked",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            target_state=target.value,
            reason=reason,
            frame_hash=self._last_frame_hash,
        )
        return False

    def _screen_sync_due(self) -> bool:
        now = time.monotonic()
        if self._last_screen_sync_monotonic is not None:
            elapsed_ms = (now - self._last_screen_sync_monotonic) * 1000
            if elapsed_ms < SCREEN_SYNC_INTERVAL_MS:
                return False
        self._last_screen_sync_monotonic = now
        return True

    def _ensure_battle_context(self, reason: str) -> None:
        if self.session.battle_id is not None:
            return
        battle_id = self.session.new_battle()
        self.logger.log_event(
            "battle_context_started",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=battle_id,
            reason=reason,
            frame_hash=self._last_frame_hash,
        )

    def _reset_location_context(self) -> None:
        self._stable_target_frames = 0
        self._current_target = None
        self._selected_target = None
        self._target_click_attempts = 0
        self._last_target_click_monotonic = None
        self.navigator.reset()
        self._reset_scrollbar_search()

    def _log_screen_sync(self, screen: str, confidence: float) -> None:
        self.logger.log_event(
            "screen_synced",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            screen=screen,
            detector_confidence=confidence,
            frame_hash=self._last_frame_hash,
        )

    def _recover_unknown_screen(self, frame: np.ndarray) -> bool:
        if not _frame_has_visual_content(frame):
            return False
        if _browser_confirm_dialog_present(frame):
            return self._click_confirm_dialog_cancel(frame)
        if self.target_locator.locate(frame):
            return False
        if _location_map_present(frame, self.config.viewport.direction_pad_roi):
            return False
        if _game_shell_present(frame):
            return self._open_hunt_from_game_shell(frame)
        if not self._browser_back_due():
            return True
        request = ActionRequest(
            "open_url",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"frame_hash": self._last_frame_hash, "url": GAME_ENTRY_URL},
        )
        self.logger.log_event(
            "unknown_screen_recovery",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._last_browser_back_monotonic = time.monotonic()
            self._reset_location_context()
            self._search_pause_until_monotonic = time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
            return True
        return False

    def _open_hunt_from_game_shell(self, frame: np.ndarray, *, confirmed_inactive_fight_page: bool = False) -> bool:
        if not self.config.dry_run:
            snapshot = self._visible_hunt_snapshot_via_injector()
            if self._live_snapshot_on_hunt_page(snapshot):
                self._log_live_page_wait("open_hunt_skipped_current_hunt", snapshot)
                return True
            if self._live_snapshot_on_fight_page(snapshot):
                self._log_live_page_wait("open_hunt_skipped_current_fight", snapshot)
                if not confirmed_inactive_fight_page:
                    battle_snapshot = self._battle_snapshot_via_injector(force=True)
                    if self._sync_battle_from_snapshot(frame, battle_snapshot) or self.session.battle_id is not None:
                        return True
                    if not self._injector_snapshot_has_inactive_battle(battle_snapshot):
                        return True
            if self._live_snapshot_on_area_error(snapshot):
                self._log_live_page_wait("open_hunt_skipped_area_error", snapshot)
                return True
        if not self._browser_back_due():
            return True
        request = ActionRequest(
            "open_hunt",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"frame_hash": self._last_frame_hash},
        )
        self.logger.log_event(
            "game_shell_recovery",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            frame_hash=self._last_frame_hash,
        )
        self._last_browser_back_monotonic = time.monotonic()
        if self.action_executor.execute(request):
            self._reset_location_context()
            self._search_pause_until_monotonic = time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
        else:
            self.logger.log_event(
                "game_shell_recovery_blocked",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason="open_hunt_failed",
                frame_hash=self._last_frame_hash,
            )
        return True

    def _click_confirm_dialog_cancel(self, frame: np.ndarray) -> bool:
        if not self._browser_back_due():
            return True
        point = _browser_confirm_dialog_cancel_point(frame)
        if point is None:
            return False
        request = ActionRequest(
            "click_cancel_dialog",
            frame_point=point,
            screen_point=self.mapper.frame_to_pynput(point),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"frame_hash": self._last_frame_hash},
        )
        self.logger.log_event(
            "confirm_dialog_recovery",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            x_frame=point.x,
            y_frame=point.y,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._last_browser_back_monotonic = time.monotonic()
            self._reset_location_context()
            self._search_pause_until_monotonic = time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
            return True
        return False

    def _click_return_to_location(self, frame: np.ndarray) -> bool:
        if not self._browser_back_due():
            return True
        height, width = frame.shape[:2]
        point = Point(width * RETURN_TO_LOCATION_POINT[0], height * RETURN_TO_LOCATION_POINT[1])
        request = ActionRequest(
            "click_return",
            frame_point=point,
            screen_point=self.mapper.frame_to_pynput(point),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"frame_hash": self._last_frame_hash},
        )
        self.logger.log_event(
            "return_to_location_recovery",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            x_frame=point.x,
            y_frame=point.y,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._last_browser_back_monotonic = time.monotonic()
            self._reset_location_context()
            self._search_pause_until_monotonic = time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
            return True
        return False

    def _browser_back_due(self) -> bool:
        if self._last_browser_back_monotonic is None:
            return True
        elapsed_ms = (time.monotonic() - self._last_browser_back_monotonic) * 1000
        return elapsed_ms >= UNKNOWN_SCREEN_BACK_DELAY_MS

    def _resources_allow_search(self, frame: np.ndarray) -> bool:
        if not self.config.resources.enabled:
            return True
        status = self._detect_current_resources(frame)
        low = self._resource_gaps(status, recover=self.config.resources.require_recover_before_search)
        missing = self._resource_missing(status)
        if missing and not low:
            self._log_resource_status("resources_missing", status, missing=missing)
            if self._try_item_recovery_before_rest(frame, status, low, missing, reason="resources_missing_before_search"):
                return False
            return self.config.resources.fail_open_if_missing
        if not low:
            self._log_resource_status("resources_ok", status)
            return True
        if self._try_item_recovery_before_rest(frame, status, low, missing, reason="resources_low_before_search"):
            return False
        self._enter_resting(status, low)
        return False

    def _handle_resting(self, frame: np.ndarray) -> None:
        if not self.config.dry_run and self._sync_battle_from_injector(frame):
            return
        if not self._rest_check_due():
            return
        status = self._detect_current_resources(frame)
        low = self._resource_gaps(status, recover=True)
        missing = self._resource_missing(status)
        if missing and not low and self.config.resources.fail_open_if_missing:
            self.logger.log_event(
                "resource_rest_released",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="resources_missing_fail_open",
                missing_resources=missing,
                frame_hash=self._last_frame_hash,
            )
            self._leave_resting()
            return
        if low or missing:
            self._log_resource_status("resource_rest_waiting", status, low_resources=low, missing=missing, force=True)
            if self._try_item_recovery_before_rest(frame, status, low, missing, reason="resources_low_resting"):
                return
            self._maybe_refresh_resource_source(status, low=low, missing=missing)
            if self._resting_since_monotonic is not None and self.config.resources.max_rest_minutes > 0:
                elapsed_s = time.monotonic() - self._resting_since_monotonic
                if elapsed_s > self.config.resources.max_rest_minutes * 60:
                    self.logger.log_event(
                        "session_stopped",
                        state=self.state_machine.state.value,
                        cycle_id=self.session.cycle_id,
                        reason="max_rest_minutes",
                        frame_hash=self._last_frame_hash,
                    )
                    self._safe_transition(GameState.STOPPED, reason="max_rest_minutes")
            return
        self._log_resource_status("resources_recovered", status, force=True)
        self._leave_resting()
