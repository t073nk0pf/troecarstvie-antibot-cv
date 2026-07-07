from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable

import cv2
import numpy as np

from src.antibot_cv.automation.actions import (
    ActionExecutor,
    ActionRequest,
    BlockedActionSink,
    DryRunActionSink,
    LiveMacActionSink,
    ReplayActionSink,
)
from src.antibot_cv.automation.capture import calibrate_capture, draw_mapping_preview
from src.antibot_cv.automation.config import AutomationConfig, to_plain_dict
from src.antibot_cv.automation.preflight import run_preflight
from src.antibot_cv.automation.safety import HotkeyController, SafetyGuard
from src.antibot_cv.automation.session import SessionState
from src.antibot_cv.automation.state_machine import GameState, InvalidTransitionError, StateMachine
from src.antibot_cv.detection.attack import AttackButtonDetector
from src.antibot_cv.detection.ability import AbilityBarDetector
from src.antibot_cv.detection.battle import BattleDetector
from src.antibot_cv.detection.battle_end import BattleEndDetector
from src.antibot_cv.detection.resources import ResourceBarStatus, ResourceDetector, ResourceStatus
from src.antibot_cv.detection.statistics import StatisticsDetector
from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.entity_detection.green_labels import GreenLabelDetector
from src.antibot_cv.entity_detection.target_locator import LocatedTarget, TargetLocator
from src.antibot_cv.entity_detection.tracker import EntityTracker
from src.antibot_cv.telemetry.event_logger import EventLogger, InMemoryEventLogger, frame_hash
from src.antibot_cv.telemetry.session_summary import LatencyTracker, write_session_summary
from src.antibot_cv.viewport.coordinates import CoordinateMapper, MonitorGeometry, Point, Rect
from src.antibot_cv.viewport.direction_pad import DirectionPadNavigator
from src.antibot_cv.viewport.scrollbar import ScrollbarNavigator, ScrollDirection


DEFAULT_TEMPLATE_PATHS: dict[str, str] = {
    "attack_button": "assets/templates/location/attack_button.png",
    "battle_panel": "assets/templates/battle/battle_panel.png",
    "enemy_header": "assets/templates/battle/enemy_header.png",
    "timer_hourglass": "assets/templates/battle/timer_hourglass.png",
    "ability_bar": "assets/templates/battle/ability_bar.png",
    "victory_popup": "assets/templates/battle_end/victory_popup.png",
    "exit_button": "assets/templates/battle_end/exit_button.png",
    "statistics_header": "assets/templates/statistics/statistics_header.png",
    "team_tables": "assets/templates/statistics/team_tables.png",
    "statistics_buttons": "assets/templates/statistics/statistics_buttons.png",
    "hunt_button": "assets/templates/statistics/hunt_button.png",
    "direction_pad": "assets/templates/direction_pad/direction_pad.png",
    "steppe_jackal": "assets/templates/targets/steppe_jackal.png",
    "young_lynx": "assets/templates/targets/young_lynx.png",
}

ATTACK_RETRY_DELAY_MS = 90
HUNT_RETRY_DELAY_MS = 450
UNKNOWN_SCREEN_BACK_DELAY_MS = 1000
GAME_ENTRY_URL = "https://3kingdoms.ru/main.php"
RETURN_TO_LOCATION_POINT = (0.90, 0.20)
SCREEN_SYNC_INTERVAL_MS = 50
LIVE_APP_REACTIVATE_INTERVAL_S = 2.0
JS_VISIBLE_ATTACK_INTERVAL_MS = 450
JS_BATTLE_PROBE_INTERVAL_MS = 450


@dataclass(frozen=True)
class AutomationRunOptions:
    config_path: str | Path | None = None
    live: bool = False
    preview: bool = False
    max_cycles: int | None = None
    max_session_minutes: int | None = None
    target_allowed_levels: tuple[int, ...] | None = None
    start_delay: float | None = None
    activate_app: str | None = None
    no_activate_app: bool = False
    hotkeys: bool = False
    open_hunt_on_start: bool = False
    browser_client_id: str | None = None
    runtime_overrides: dict[str, object] | None = None


StatusCallback = Callable[[dict[str, object]], None]


class AutomationController:
    def __init__(
        self,
        config: AutomationConfig,
        *,
        sink_mode: str = "dry_run",
        logger: object | None = None,
        mapper: CoordinateMapper | None = None,
        browser_client_id: str | None = None,
    ) -> None:
        self.config = config
        self.browser_client_id = browser_client_id
        self.session = SessionState(requested_cycles=config.max_cycles)
        self.run_dir = Path(config.runs_dir) / self.session.session_id
        self.logger = logger or EventLogger(self.session.session_id, self.run_dir, dry_run=config.dry_run)
        self.guard = SafetyGuard(config)
        self.state_machine = StateMachine(self.logger)
        self.last_error_reason: str | None = None
        self.latencies = LatencyTracker()
        self.mapper = mapper or CoordinateMapper(
            MonitorGeometry(0, 0, 1280, 720, capture_width=1280, capture_height=720),
            Rect(0, 0, 1280, 720),
        )
        self.registry = TemplateRegistry.from_file(config.templates_path)
        self.target_locator = TargetLocator(
            config.target,
            GreenLabelDetector(config.target.green_label),
            recognizer=None if config.target.mode == "any_allowed_green_label" else None,
            game_field_roi=config.target.search_roi or Rect(0, 0, self.mapper.roi.width, self.mapper.roi.height),
            template_registry=self.registry,
        )
        self.tracker = EntityTracker()
        self.attack_detector = AttackButtonDetector(self.registry)
        self.battle_detector = BattleDetector(config.battle, self.registry)
        self.ability_detector = AbilityBarDetector(config.ability4, self.registry)
        self.battle_end_detector = BattleEndDetector(config.battle_end, self.registry)
        self.statistics_detector = StatisticsDetector(config.statistics, self.registry)
        self.resource_detector = ResourceDetector(config.resources)
        self.navigator = DirectionPadNavigator(config.viewport, Rect(0, 0, self.mapper.roi.width, self.mapper.roi.height))
        self.scrollbar = ScrollbarNavigator(
            roi=self._default_scrollbar_roi(),
            min_confidence=config.viewport.scrollbar_min_confidence,
        )
        self._stable_target_frames = 0
        self._current_target: LocatedTarget | None = None
        self._last_frame_hash: str | None = None
        self._scrollbar_index = 0
        self._scrollbar_moves = 0
        self._direction_moves_since_scrollbar = 0
        self._selected_target: LocatedTarget | None = None
        self._target_click_attempts = 0
        self._last_target_click_monotonic: float | None = None
        self._last_attack_click_monotonic: float | None = None
        self._last_hunt_click_monotonic: float | None = None
        self._last_combat_click_monotonic: float | None = None
        self._last_js_visible_attack_monotonic: float | None = None
        self._last_js_battle_probe_monotonic: float | None = None
        self._last_viewport_action_monotonic: float | None = None
        self._last_browser_back_monotonic: float | None = None
        self._last_screen_sync_monotonic: float | None = None
        self._resting_since_monotonic: float | None = None
        self._last_rest_check_monotonic: float | None = None
        self._last_resource_refresh_monotonic: float | None = None
        self._last_resource_log_monotonic: float | None = None
        self._last_item_recovery_attempt_monotonic: float | None = None
        self._combat_slot_sequence_index = 0
        self._state_entered_monotonic = time.monotonic()
        self._search_pause_until_monotonic: float | None = None

        sink = self._make_sink(sink_mode)
        self.action_executor = ActionExecutor(guard=self.guard, session=self.session, sink=sink, logger=self.logger)

    def _make_sink(self, sink_mode: str) -> object:
        if self.config.dry_run:
            if sink_mode == "replay":
                return ReplayActionSink(self.logger)
            return DryRunActionSink(self.logger)
        if sink_mode == "live":
            return LiveMacActionSink(self.logger)
        return BlockedActionSink(self.logger, reason="live_not_explicit")

    def start(self) -> None:
        self.logger.log_event(
            "session_started",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            dry_run=self.config.dry_run,
        )

    def process_frame(self, frame: np.ndarray) -> GameState:
        self._last_frame_hash = frame_hash(frame)
        if self.session.elapsed_s > self.config.max_session_minutes * 60:
            self.logger.log_event(
                "session_stopped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="max_session_minutes",
            )
            self._safe_transition(GameState.STOPPED, reason="max_session_minutes")
            return self.state_machine.state
        if self.guard.emergency_stopped:
            self.session.emergency_stop = True
            self._safe_transition(GameState.STOPPED, reason="emergency_stop")
            return self.state_machine.state

        if self.config.dry_run and self._sync_from_current_screen(frame):
            return self.state_machine.state

        state = self.state_machine.state
        if state in {GameState.LOCATION_SEARCH, GameState.VIEWPORT_SCAN}:
            if self._search_pause_active():
                return self.state_machine.state
            if self._recover_unknown_screen(frame):
                return self.state_machine.state
            if not self._resources_allow_search(frame):
                return self.state_machine.state
            self._handle_location_frame(frame)
        elif state == GameState.BATTLE_WAIT:
            self._handle_battle_wait(frame)
        elif state == GameState.BATTLE_ACTIVE:
            self._handle_battle_active(frame)
        elif state in {GameState.WAIT_BATTLE_END, GameState.BATTLE_END_DETECTED}:
            self._handle_battle_end(frame)
        elif state in {GameState.STATISTICS_WAIT, GameState.STATISTICS_DETECTED}:
            self._handle_statistics(frame)
        elif state == GameState.COOLDOWN:
            self._handle_cooldown(frame)
        elif state == GameState.RESTING:
            self._handle_resting(frame)
        self._recover_stuck_state(frame)
        return self.state_machine.state

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

    def _live_combat_slot_for_current_resources(self, frame: np.ndarray) -> int | None:
        if not self.config.resources.enabled:
            return self._next_live_combat_slot()
        status = self._detect_current_resources(frame)
        missing = self._resource_missing(status)
        if missing:
            self._log_resource_status("combat_resources_missing", status, missing=missing)
            return self._next_live_combat_slot()
        low = self._resource_gaps(status, recover=False)
        if not low:
            self._log_resource_status("combat_resources_ok", status)
            return self._next_live_combat_slot()
        prowess = status.prowess.percent
        fallback_threshold = max(0.0, float(self.config.combat.low_resource_fallback_percent))
        if (
            self.config.resources.wait_in_battle_when_low
            and self.config.combat.low_resource_fallback_enabled
            and prowess is not None
            and prowess <= fallback_threshold
        ):
            fallback_slot = max(0, int(self.config.combat.low_resource_fallback_slot_index))
            self._log_resource_status(
                "combat_low_resource_fallback",
                status,
                low_resources=low,
                fallback_slot=fallback_slot,
                force=True,
            )
            return fallback_slot
        self._log_resource_status("combat_low_resource_continuing", status, low_resources=low, force=True)
        return self._next_live_combat_slot()

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
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute("resource_snapshot", timeout_s=2.5)
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

            result = global_browser_injector().execute("resource_refresh", timeout_s=2.5)
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
            return self._recover_to_search("battle_wait_timeout", frame)
        if state == GameState.BATTLE_ACTIVE and elapsed_ms >= self.config.recovery.battle_active_timeout_ms:
            battle_end = self.battle_end_detector.detect(frame)
            if battle_end.detected:
                self._safe_transition(GameState.WAIT_BATTLE_END, reason="battle_active_timeout_battle_end_detected")
                self._handle_battle_end(frame, battle_end)
                return True
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
        self.session.battle_id = None
        self._last_attack_click_monotonic = None
        self._last_combat_click_monotonic = None
        self._combat_slot_sequence_index = 0
        self._reset_location_context()
        self._safe_transition(GameState.LOCATION_SEARCH, reason=reason)
        return True

    def _handle_location_frame(self, frame: np.ndarray) -> None:
        if not self.config.dry_run:
            if not self._resources_allow_search(frame):
                return
            if not self._attack_visible_target_via_injector(frame):
                if not self._handle_live_failed_visible_attack(frame):
                    self._open_hunt_from_game_shell(frame)
            return
        if self._attack_visible_target_via_injector():
            return

        targets = self.target_locator.locate(frame)
        for target in targets:
            self.logger.log_event(
                "target_candidate",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                detector_id="green_label",
                detector_confidence=target.confidence,
                target_id=target.target_id,
                x_frame=target.interaction_point.x,
                y_frame=target.interaction_point.y,
                frame_hash=self._last_frame_hash,
            )
        if targets:
            self._stable_target_frames += 1
            self._current_target = targets[0]
            tracks = self.tracker.update(targets)
            if self._stable_target_frames >= self.config.target.stable_frames:
                track = tracks[0] if tracks else None
                self.session.targets_detected += 1
                self.logger.log_event(
                    "target_acquired",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    detector_confidence=targets[0].confidence,
                    target_id=targets[0].target_id,
                    track_id=None if track is None else track.track_id,
                    frame_hash=self._last_frame_hash,
                )
                self._safe_transition(GameState.TARGET_FOUND)
                self._select_target(targets[0])
                self._stable_target_frames = 0
                self.navigator.reset()
                self._reset_scrollbar_search()
            return

        self._stable_target_frames = 0
        if self.state_machine.state == GameState.LOCATION_SEARCH:
            self._safe_transition(GameState.VIEWPORT_SCAN, reason="no_target")
        self._move_viewport(frame)

    def _select_target(self, target: LocatedTarget) -> None:
        self._selected_target = target
        self._target_click_attempts = 0
        self._last_attack_click_monotonic = None
        self._click_selected_target()

    def _attack_visible_target_via_injector(self, frame: np.ndarray | None = None) -> bool:
        if self.config.dry_run:
            return False
        if frame is not None and not self._resources_allow_search(frame):
            return False
        now = time.monotonic()
        if self._last_js_visible_attack_monotonic is not None:
            elapsed_ms = (now - self._last_js_visible_attack_monotonic) * 1000
            if elapsed_ms < JS_VISIBLE_ATTACK_INTERVAL_MS:
                return False
        self._last_js_visible_attack_monotonic = now
        metadata: dict[str, object] = {"frame_hash": self._last_frame_hash, "confirmed": 1, "margin": 35}
        if self.config.target.allowed_levels:
            metadata["allowed_levels"] = list(self.config.target.allowed_levels)
        request = ActionRequest(
            "attack_visible_target",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata=metadata,
        )
        self.logger.log_event(
            "js_visible_target_probe",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            frame_hash=self._last_frame_hash,
            allowed_levels=list(self.config.target.allowed_levels),
        )
        if not self.action_executor.execute(request):
            return False
        self.session.targets_detected += 1
        self._stable_target_frames = 0
        self._selected_target = None
        self._last_target_click_monotonic = now
        if self.state_machine.state in {GameState.LOCATION_SEARCH, GameState.VIEWPORT_SCAN}:
            self._safe_transition(GameState.TARGET_FOUND, reason="js_visible_target_attack")
            self._safe_transition(GameState.TARGET_SELECTED, reason="js_visible_target_attack")
        if self.session.battle_id is None:
            battle_id = self.session.new_battle()
            self._safe_transition(GameState.BATTLE_WAIT, battle_id=battle_id, reason="js_visible_target_attack")
        elif self.state_machine.state != GameState.BATTLE_WAIT and self.state_machine.can_transition(GameState.BATTLE_WAIT):
            self._safe_transition(GameState.BATTLE_WAIT, reason="js_visible_target_attack")
        self.navigator.reset()
        self._reset_scrollbar_search()
        return True

    def _handle_live_failed_visible_attack(self, frame: np.ndarray) -> bool:
        snapshot = self._visible_hunt_snapshot_via_injector()
        if self._live_snapshot_on_fight_page(snapshot):
            self._log_live_page_wait("live_fight_page_waiting", snapshot)
            battle_snapshot = self._battle_snapshot_via_injector(force=True)
            if self._sync_battle_from_snapshot(frame, battle_snapshot):
                return True
            if self._injector_snapshot_has_inactive_battle(battle_snapshot):
                return self._open_hunt_from_game_shell(frame, confirmed_inactive_fight_page=True)
            return True
        if self._live_snapshot_on_hunt_page(snapshot):
            self._log_live_page_wait("live_hunt_target_waiting", snapshot)
            if self._snapshot_visible_target_count(snapshot) <= 0:
                self._move_viewport(frame)
            return True
        if self._live_snapshot_on_area_error(snapshot):
            self._log_live_page_wait("live_area_error_waiting", snapshot)
            return True
        return False

    def _visible_hunt_snapshot_via_injector(self) -> dict[str, object] | None:
        if self.config.dry_run:
            return None
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "visible_hunt_targets",
                {"margin": 35, "allowedLevels": list(self.config.target.allowed_levels)},
                timeout_s=2.5,
            )
        except Exception as exc:
            self.logger.log_event(
                "js_visible_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=str(exc),
                frame_hash=self._last_frame_hash,
            )
            return None
        if not result.ok:
            self.logger.log_event(
                "js_visible_snapshot_failed",
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
            self.logger.log_event(
                "js_visible_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason="invalid_json",
                frame_hash=self._last_frame_hash,
            )
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _snapshot_visible_target_count(snapshot: dict[str, object] | None) -> int:
        targets = snapshot.get("targets") if isinstance(snapshot, dict) else None
        return len(targets) if isinstance(targets, list) else 0

    def _live_snapshot_on_fight_page(self, snapshot: dict[str, object] | None) -> bool:
        return "/fight.php" in _snapshot_main_href(snapshot)

    def _live_snapshot_on_hunt_page(self, snapshot: dict[str, object] | None) -> bool:
        if snapshot is None:
            return False
        return bool(snapshot.get("hasHunt")) or "/hunt.php" in _snapshot_main_href(snapshot)

    def _live_snapshot_on_area_error(self, snapshot: dict[str, object] | None) -> bool:
        href = _snapshot_main_href(snapshot)
        return "/area.php" in href and "error=" in href

    def _log_live_page_wait(self, event_type: str, snapshot: dict[str, object] | None, **extra: object) -> None:
        targets = snapshot.get("targets") if isinstance(snapshot, dict) else None
        display_children = snapshot.get("displayChildren") if isinstance(snapshot, dict) else None
        self.logger.log_event(
            event_type,
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            main_href="" if snapshot is None else snapshot.get("mainHref", ""),
            has_hunt=False if snapshot is None else bool(snapshot.get("hasHunt")),
            visible_target_count=len(targets) if isinstance(targets, list) else 0,
            display_child_count=len(display_children) if isinstance(display_children, list) else 0,
            frame_hash=self._last_frame_hash,
            **extra,
        )

    def _click_selected_target(self) -> bool:
        if self._selected_target is None:
            return False
        target = self._selected_target
        point = self._target_click_point(target, self._target_click_attempts)
        screen_point = self.mapper.frame_to_pynput(point)
        request = ActionRequest(
            "click_target",
            frame_point=point,
            screen_point=screen_point,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "click_count": 2 if self.config.target.double_click_to_attack else 1,
                "target_id": target.target_id,
                "frame_hash": self._last_frame_hash,
                "target_click_attempt": self._target_click_attempts,
            },
        )
        self.logger.log_event(
            "target_selected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            target_id=target.target_id,
            x_frame=point.x,
            y_frame=point.y,
            target_click_attempt=self._target_click_attempts,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._last_target_click_monotonic = time.monotonic()
            self._target_click_attempts += 1
            if self.state_machine.state == GameState.TARGET_FOUND:
                self._safe_transition(GameState.TARGET_SELECTED)
            if self.session.battle_id is None and self.state_machine.state == GameState.TARGET_SELECTED:
                battle_id = self.session.new_battle()
                self._safe_transition(GameState.BATTLE_WAIT, battle_id=battle_id)
            return True
        return False

    def _target_click_point(self, target: LocatedTarget, attempt_index: int) -> Point:
        if target.target_id == "green_sprite" or target.target_id in self.config.target.sprite_template_ids:
            sprite_offsets = (
                Point(0, 0),
                Point(10, 0),
                Point(-10, 0),
                Point(0, 10),
                Point(0, -10),
                Point(16, 8),
                Point(-16, 8),
                Point(16, -8),
                Point(-16, -8),
            )
            offset = sprite_offsets[min(attempt_index, len(sprite_offsets) - 1)]
            point = Point(target.interaction_point.x + offset.x, target.interaction_point.y + offset.y)
            return Rect(0, 0, self.mapper.roi.width, self.mapper.roi.height).clamp(point)
        if attempt_index == 0:
            return target.interaction_point
        retry_index = attempt_index - 1
        if retry_index >= len(self.config.target.click_retry_offsets):
            return target.interaction_point
        offset = self.config.target.click_retry_offsets[retry_index]
        point = Point(target.label_center.x + offset.dx, target.label_center.y + offset.dy)
        return Rect(0, 0, self.mapper.roi.width, self.mapper.roi.height).clamp(point)

    def _move_viewport(self, frame: np.ndarray) -> None:
        if self._viewport_settle_active():
            return
        self._update_direction_pad_from_frame(frame)
        if self.config.dry_run and self._interleaved_scrollbar_due() and self._move_scrollbar(frame):
            return
        move = self.navigator.next_move()
        if move is None:
            if self.config.dry_run and self._move_scrollbar(frame):
                return
            self.logger.log_event(
                "viewport_search_exhausted",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                recovery_pause_ms=self.config.recovery.viewport_exhausted_pause_ms,
                frame_hash=self._last_frame_hash,
            )
            self.session.mark_recovery()
            self.navigator.reset()
            self._reset_scrollbar_search()
            self._search_pause_until_monotonic = time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
            if self.state_machine.state != GameState.LOCATION_SEARCH:
                self._safe_transition(GameState.LOCATION_SEARCH, reason="viewport_search_exhausted")
            return
        screen_point = self.mapper.frame_to_pynput(move.point) if move.point else None
        current_moves = move.move_index - 1 + self._scrollbar_moves
        request = ActionRequest(
            "viewport_move",
            frame_point=move.point,
            screen_point=screen_point,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            is_viewport_move=True,
            viewport_moves_this_search=current_moves,
            scan_direction=move.direction.value,
            metadata={
                "frame_hash": self._last_frame_hash,
                "margin": 35,
                "use_js_hunt_direction": not self.config.dry_run,
            },
        )
        self.logger.log_event(
            "viewport_move",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            scan_direction=move.direction.value,
            x_frame=None if move.point is None else move.point.x,
            y_frame=None if move.point is None else move.point.y,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._direction_moves_since_scrollbar += 1
            self._last_viewport_action_monotonic = time.monotonic()

    def _viewport_settle_active(self) -> bool:
        if self._last_viewport_action_monotonic is None:
            return False
        elapsed_ms = (time.monotonic() - self._last_viewport_action_monotonic) * 1000
        return elapsed_ms < self.config.viewport.settle_ms

    def _update_direction_pad_from_frame(self, frame: np.ndarray) -> None:
        color_roi = _detect_direction_pad_roi(frame)
        if color_roi is not None:
            current = self.navigator.geometry.roi
            if abs(current.x - color_roi.x) > 12 or abs(current.y - color_roi.y) > 12:
                self.navigator.update_roi(color_roi)
                self.logger.log_event(
                    "direction_pad_detected",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    detector_id="direction_pad_color",
                    detector_confidence=0.8,
                    x_frame=color_roi.center.x,
                    y_frame=color_roi.center.y,
                    frame_hash=self._last_frame_hash,
                )
            return
        if self.config.viewport.direction_pad_roi is not None:
            return
        matches = self.registry.match(frame, "direction_pad")
        if not matches:
            return
        self.navigator.update_roi(matches[0].bbox)
        self.logger.log_event(
            "direction_pad_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            detector_id="direction_pad",
            detector_confidence=matches[0].confidence,
            x_frame=matches[0].center.x,
            y_frame=matches[0].center.y,
            frame_hash=self._last_frame_hash,
        )

    def _move_scrollbar(self, frame: np.ndarray) -> bool:
        if not self.config.viewport.scrollbar_enabled:
            return False
        if self._scrollbar_moves >= self.config.viewport.max_scrollbar_moves_per_search:
            return False
        if not self.config.viewport.scrollbar_sequence:
            return False

        direction_name = self.config.viewport.scrollbar_sequence[self._scrollbar_index % len(self.config.viewport.scrollbar_sequence)]
        direction = ScrollDirection[direction_name]
        move = self.scrollbar.next_drag(direction, frame, self.config.viewport.scrollbar_step_px)
        self._scrollbar_index += 1
        if move is None:
            self.logger.log_event(
                "scrollbar_not_found",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                frame_hash=self._last_frame_hash,
            )
            return False

        current_moves = self.navigator.moves + self._scrollbar_moves
        request = ActionRequest(
            "drag_scrollbar",
            frame_point=move.start,
            screen_point=self.mapper.frame_to_pynput(move.start),
            end_frame_point=move.end,
            end_screen_point=self.mapper.frame_to_pynput(move.end),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            is_viewport_move=True,
            viewport_moves_this_search=current_moves,
            scan_direction=f"SCROLL_{move.direction.value}",
            metadata={"detector_confidence": move.confidence, "frame_hash": self._last_frame_hash},
        )
        self.logger.log_event(
            "viewport_scroll",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            scan_direction=f"SCROLL_{move.direction.value}",
            detector_id="scrollbar_thumb",
            detector_confidence=move.confidence,
            x_frame=move.start.x,
            y_frame=move.start.y,
            x2_frame=move.end.x,
            y2_frame=move.end.y,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._scrollbar_moves += 1
            self._direction_moves_since_scrollbar = 0
            self._last_viewport_action_monotonic = time.monotonic()
            return True
        return False

    def _reset_scrollbar_search(self) -> None:
        self._scrollbar_index = 0
        self._scrollbar_moves = 0
        self._direction_moves_since_scrollbar = 0
        self._last_viewport_action_monotonic = None

    def _interleaved_scrollbar_due(self) -> bool:
        every = self.config.viewport.scrollbar_every_direction_moves
        return (
            self.config.viewport.scrollbar_enabled
            and every > 0
            and self._direction_moves_since_scrollbar >= every
            and self._scrollbar_moves < self.config.viewport.max_scrollbar_moves_per_search
        )

    def _default_scrollbar_roi(self) -> Rect | None:
        if self.config.viewport.scrollbar_roi is not None:
            return self.config.viewport.scrollbar_roi
        if self.config.target.search_roi is not None:
            return self.config.target.search_roi
        return None

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
        if self.config.dry_run:
            return None
        now = time.monotonic()
        if not force and self._last_js_battle_probe_monotonic is not None:
            elapsed_ms = (now - self._last_js_battle_probe_monotonic) * 1000
            if elapsed_ms < JS_BATTLE_PROBE_INTERVAL_MS:
                return None
        self._last_js_battle_probe_monotonic = now
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute("battle_snapshot", timeout_s=2.5)
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
        return parsed if isinstance(parsed, dict) else None

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
                if slot_index != fallback_slot and self._use_skill_slot_via_injector(
                    "click_ability_4", fallback_slot, "ability4_js_fallback_intended"
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

    def _next_live_combat_slot(self) -> int:
        sequence = self._configured_live_combat_slots()
        slot = sequence[self._combat_slot_sequence_index % len(sequence)]
        self._combat_slot_sequence_index += 1
        return slot

    def _use_skill_slot_via_injector(self, action_type: str, slot_index: int, event_type: str) -> bool:
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
        return self.action_executor.execute(request)

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
            if slot_index != fallback_slot and self._use_skill_slot_via_injector(
                "click_combat_slot", fallback_slot, "combat_slot_js_fallback_intended"
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
        if not self.session.can_complete_cycle():
            self.logger.log_event(
                "cycle_incomplete",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=reason,
                battles_detected=self.session.battles_detected,
                ability4_actions=self.session.ability4_actions,
                combat_actions=self.session.combat_actions,
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

    def _use_item_recovery_after_cycle(self, frame: np.ndarray, *, reason: str, require_after_cycle: bool = True) -> bool:
        config = self.config.item_recovery
        if self.config.dry_run or not config.enabled or (require_after_cycle and not config.use_after_cycle):
            return False
        request = ActionRequest(
            "use_recovery_items",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "frame_hash": self._last_frame_hash,
                "reason": reason,
                "health_names": list(config.health_names),
                "prowess_names": list(config.prowess_names),
                "use_when_below_percent": config.use_when_below_percent,
                "health_use_when_below_percent": self._item_recovery_threshold("health"),
                "prowess_use_when_below_percent": self._item_recovery_threshold("prowess"),
                "force_use": config.force_use,
                "use_if_resources_missing": config.use_if_resources_missing,
                "open_hunt_after": config.open_hunt_after,
                "timeout_s": config.timeout_s,
                "health_restore_percent": config.health_restore_percent,
                "prowess_restore_percent": config.prowess_restore_percent,
                "max_uses_per_resource": config.max_uses_per_resource,
                "inventory_open_delay_ms": config.inventory_open_delay_ms,
                "confirm_delay_ms": config.confirm_delay_ms,
                "between_items_delay_ms": config.between_items_delay_ms,
            },
        )
        self.logger.log_event(
            "recovery_items_intended",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            reason=reason,
            health_names=list(config.health_names),
            prowess_names=list(config.prowess_names),
            use_when_below_percent=config.use_when_below_percent,
            health_use_when_below_percent=self._item_recovery_threshold("health"),
            prowess_use_when_below_percent=self._item_recovery_threshold("prowess"),
            max_uses_per_resource=config.max_uses_per_resource,
            frame_hash=self._last_frame_hash,
        )
        return self.action_executor.execute(request)

    def _hunt_retry_due(self) -> bool:
        if self._last_hunt_click_monotonic is None:
            return True
        elapsed_ms = (time.monotonic() - self._last_hunt_click_monotonic) * 1000
        return elapsed_ms >= HUNT_RETRY_DELAY_MS

    def _hunt_return_confirmed(self, frame: np.ndarray) -> bool:
        return _location_map_present(frame, self.config.viewport.direction_pad_roi)

    def _safe_transition(self, target: GameState, *, battle_id: int | None = None, reason: str | None = None) -> None:
        try:
            previous = self.state_machine.state
            current = self.state_machine.transition(
                target,
                cycle_id=self.session.cycle_id,
                battle_id=battle_id if battle_id is not None else self.session.battle_id,
                reason=reason,
            )
            if current != previous:
                self._state_entered_monotonic = time.monotonic()
        except InvalidTransitionError as exc:
            self.last_error_reason = str(exc)
            self.session.errors += 1
            self.logger.log_event("error", state=self.state_machine.state.value, reason=str(exc))
            self.guard.record_error()
            if self.state_machine.state != GameState.ERROR and self.state_machine.can_transition(GameState.ERROR):
                self.state_machine.transition(GameState.ERROR, cycle_id=self.session.cycle_id, reason=str(exc))
            if self.state_machine.can_transition(GameState.STOPPED):
                self.state_machine.transition(GameState.STOPPED, cycle_id=self.session.cycle_id, reason=str(exc))

    def finish(self) -> dict[str, object]:
        if self.state_machine.state != GameState.STOPPED:
            self._safe_transition(GameState.STOPPED, reason="finish")
        summary = write_session_summary(self.run_dir / "summary.json", self.session, dry_run=self.config.dry_run, latencies=self.latencies)
        self.logger.log_event(
            "session_stopped",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            completed_cycles=self.session.completed_cycles,
        )
        return summary


def load_config(path: str | Path | None) -> AutomationConfig:
    if path is None:
        path = "config/automation.example.json"
    config_path = Path(path)
    if not config_path.exists() and config_path.name == "automation.json":
        config_path = Path("config/automation.example.json")
    return AutomationConfig.from_file(config_path)


def command_calibrate(args: argparse.Namespace) -> int:
    config = load_config(args.config).capture
    try:
        report = calibrate_capture(config)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"Calibration failed: {exc}", file=sys.stderr)
        print("Check macOS Screen & System Audio Recording permission.", file=sys.stderr)
        return 2


def command_validate_templates(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = TemplateRegistry.from_file(config.templates_path)
    issues = registry.validate()
    payload = [{"template_id": issue.template_id, "path": issue.path, "message": issue.message} for issue in issues]
    print(json.dumps({"ok": not issues, "issues": payload}, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if issues else 0


def command_inspect_resources(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.input:
        frame = cv2.imread(str(args.input), cv2.IMREAD_COLOR)
        if frame is None:
            print(f"Could not read image: {args.input}", file=sys.stderr)
            return 1
    else:
        preflight = run_preflight(require_live=False)
        if not preflight.ok:
            print(json.dumps({"preflight_ok": False, "checks": preflight.checks, "messages": preflight.messages}, indent=2), file=sys.stderr)
            return 2
        _activate_configured_app(config, args)
        from src.antibot_cv.automation.capture import ScreenCapture

        with ScreenCapture(config.capture) as capture:
            frame = capture.capture_frame()
    status = ResourceDetector(config.resources).detect(frame)
    payload = _resource_status_payload(status)
    if args.output_frame:
        output_frame = Path(args.output_frame)
        output_frame.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_frame), frame)
        payload["output_frame"] = str(output_frame)
    if args.output_overlay:
        output_overlay = Path(args.output_overlay)
        output_overlay.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_overlay), _draw_resource_overlay(frame, config.resources, status))
        payload["output_overlay"] = str(output_overlay)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _parse_target_level_args(args: argparse.Namespace) -> tuple[int, ...] | None:
    values: list[object] = []
    for attr in ("target_level", "target_levels"):
        raw = getattr(args, attr, None)
        if raw is None:
            continue
        if isinstance(raw, list):
            values.extend(raw)
        else:
            values.append(raw)
    if not values:
        return None
    levels: list[int] = []
    for value in values:
        for part in str(value).replace(",", " ").split():
            try:
                level = int(part)
            except ValueError as exc:
                raise SystemExit(f"target level must be integer: {part}") from exc
            if level <= 0:
                raise SystemExit(f"target level must be positive: {part}")
            if level not in levels:
                levels.append(level)
    return tuple(sorted(levels))


def _as_float(value: object, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _apply_runtime_overrides(config: AutomationConfig, overrides: dict[str, object] | None) -> AutomationConfig:
    if not overrides:
        return config
    data = to_plain_dict(config)
    resources = data.setdefault("resources", {})
    item_recovery = data.setdefault("item_recovery", {})
    target = data.setdefault("target", {})
    combat = data.setdefault("combat", {})

    if "targetLevels" in overrides:
        levels = []
        raw_levels = overrides.get("targetLevels")
        if isinstance(raw_levels, str):
            raw_values: Iterable[object] = raw_levels.replace(",", " ").split()
        elif isinstance(raw_levels, list):
            raw_values = raw_levels
        else:
            raw_values = ()
        for raw in raw_values:
            level = _as_int(raw, 0)
            if level > 0 and level not in levels:
                levels.append(level)
        target["allowed_levels"] = levels

    if "healthMinPercent" in overrides:
        resources["health_min_percent"] = _as_float(overrides.get("healthMinPercent"), float(resources.get("health_min_percent", 35)))
    if "prowessMinPercent" in overrides:
        resources["prowess_min_percent"] = _as_float(overrides.get("prowessMinPercent"), float(resources.get("prowess_min_percent", 35)))
    if "recoverToPercent" in overrides:
        resources["recover_to_percent"] = _as_float(overrides.get("recoverToPercent"), float(resources.get("recover_to_percent", 80)))

    if "itemRecoveryEnabled" in overrides:
        item_recovery["enabled"] = bool(overrides.get("itemRecoveryEnabled"))
    if "recoveryThreshold" in overrides:
        item_recovery["use_when_below_percent"] = _as_float(overrides.get("recoveryThreshold"), float(item_recovery.get("use_when_below_percent", 90)))
    if "recoveryHealthThreshold" in overrides:
        item_recovery["health_use_when_below_percent"] = _as_float(
            overrides.get("recoveryHealthThreshold"),
            float(item_recovery.get("health_use_when_below_percent") or item_recovery.get("use_when_below_percent", 90)),
        )
    if "recoveryProwessThreshold" in overrides:
        item_recovery["prowess_use_when_below_percent"] = _as_float(
            overrides.get("recoveryProwessThreshold"),
            float(item_recovery.get("prowess_use_when_below_percent") or item_recovery.get("use_when_below_percent", 90)),
        )
    if "maxUsesPerResource" in overrides:
        item_recovery["max_uses_per_resource"] = max(1, _as_int(overrides.get("maxUsesPerResource"), int(item_recovery.get("max_uses_per_resource", 4))))
    if "inventoryOpenDelayMs" in overrides:
        item_recovery["inventory_open_delay_ms"] = max(0, _as_int(overrides.get("inventoryOpenDelayMs"), int(item_recovery.get("inventory_open_delay_ms", 1500))))
    if "confirmDelayMs" in overrides:
        item_recovery["confirm_delay_ms"] = max(0, _as_int(overrides.get("confirmDelayMs"), int(item_recovery.get("confirm_delay_ms", 700))))
    if "betweenItemsDelayMs" in overrides:
        item_recovery["between_items_delay_ms"] = max(0, _as_int(overrides.get("betweenItemsDelayMs"), int(item_recovery.get("between_items_delay_ms", 500))))

    if "combatSlotSequence" in overrides:
        slots = []
        raw_slots = overrides.get("combatSlotSequence")
        if isinstance(raw_slots, str):
            raw_values = raw_slots.replace(",", " ").split()
        elif isinstance(raw_slots, list):
            raw_values = raw_slots
        else:
            raw_values = ()
        for raw in raw_values:
            slot = _as_int(raw, -1)
            if slot >= 0 and slot not in slots:
                slots.append(slot)
        combat["slot_sequence"] = slots
    if "combatFallbackZeroEnabled" in overrides:
        combat["low_resource_fallback_enabled"] = bool(overrides.get("combatFallbackZeroEnabled"))
    if "combatFallbackProwessPercent" in overrides:
        combat["low_resource_fallback_percent"] = max(
            0.0,
            _as_float(overrides.get("combatFallbackProwessPercent"), float(combat.get("low_resource_fallback_percent", 1))),
        )
    if "combatClickIntervalMs" in overrides:
        combat["click_interval_ms"] = max(0, _as_int(overrides.get("combatClickIntervalMs"), int(combat.get("click_interval_ms", 2500))))
    if "combatPreClickDelayMs" in overrides:
        combat["pre_click_delay_ms"] = max(0, _as_int(overrides.get("combatPreClickDelayMs"), int(combat.get("pre_click_delay_ms", 0))))
    if "combatClickHoldMs" in overrides:
        combat["click_hold_ms"] = max(0, _as_int(overrides.get("combatClickHoldMs"), int(combat.get("click_hold_ms", 0))))

    return AutomationConfig.from_dict(data)


def _controller_status(controller: AutomationController) -> dict[str, object]:
    return {
        "state": controller.state_machine.state.value,
        "cycle_id": controller.session.cycle_id,
        "completed_cycles": controller.session.completed_cycles,
        "requested_cycles": controller.config.max_cycles,
        "battles_detected": controller.session.battles_detected,
        "targets_detected": controller.session.targets_detected,
        "total_actions": controller.session.total_actions,
        "errors": controller.session.errors,
        "error_reason": controller.last_error_reason,
        "emergency_stop": controller.session.emergency_stop or controller.guard.emergency_stopped,
        "dry_run": controller.config.dry_run,
        "elapsed_s": controller.session.elapsed_s,
    }


def run_automation(
    options: AutomationRunOptions,
    *,
    stop_event: threading.Event | None = None,
    status_callback: StatusCallback | None = None,
    print_summary: bool = True,
) -> dict[str, object]:
    config = load_config(options.config_path).with_overrides(
        dry_run=not options.live,
        max_cycles=options.max_cycles,
        max_session_minutes=options.max_session_minutes,
        target_allowed_levels=options.target_allowed_levels,
    )
    config = _apply_runtime_overrides(config, options.runtime_overrides)
    preflight = run_preflight(require_live=options.live)
    if not preflight.ok:
        raise RuntimeError(json.dumps({"preflight_ok": False, "checks": preflight.checks, "messages": preflight.messages}, ensure_ascii=False))
    if options.browser_client_id:
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        global_browser_injector().set_current_client_id(options.browser_client_id)

    from src.antibot_cv.automation.capture import ScreenCapture

    activation_args = SimpleNamespace(activate_app=options.activate_app, no_activate_app=options.no_activate_app)
    _activate_configured_app(config, activation_args)
    last_app_activation_monotonic = time.monotonic()
    start_delay = options.start_delay if options.start_delay is not None else (3.0 if options.live else 0.0)
    if start_delay > 0:
        print(f"Starting in {start_delay:.1f}s. Bring Chrome/game to the front and keep it unobstructed.", file=sys.stderr)
        time.sleep(start_delay)

    preview_enabled = bool(options.preview)
    if options.live and options.preview and config.capture.roi is None:
        preview_enabled = False
        print(
            "Live preview disabled because full-screen capture would capture the preview window itself. "
            "Run live without --preview, or configure capture.roi and keep the preview window outside that ROI.",
            file=sys.stderr,
        )

    with ScreenCapture(config.capture) as capture:
        mapper = capture.mapper()
        controller = AutomationController(
            config,
            sink_mode="live" if options.live else "dry_run",
            mapper=mapper,
            browser_client_id=options.browser_client_id,
        )
        hotkeys = HotkeyController(controller.guard, controller.logger)
        if options.hotkeys:
            hotkeys.start()
        else:
            controller.logger.log_event("hotkeys_disabled", state=controller.state_machine.state.value)
        controller.start()
        if options.live and options.open_hunt_on_start:
            request = ActionRequest(
                "open_hunt",
                cycle_id=controller.session.cycle_id,
                battle_id=controller.session.battle_id,
                dry_run=controller.config.dry_run,
                metadata={"reason": "run_start_open_hunt"},
            )
            controller.action_executor.execute(request)
            controller._reset_location_context()
            controller._search_pause_until_monotonic = time.monotonic() + controller.config.recovery.viewport_exhausted_pause_ms / 1000
        if status_callback:
            status_callback(_controller_status(controller))
        frame_interval = 1.0 / max(1, config.capture_fps)
        try:
            while controller.state_machine.state != GameState.STOPPED:
                started = time.monotonic()
                if stop_event is not None and stop_event.is_set():
                    controller.guard.emergency_stop()
                    controller.state_machine.stop(cycle_id=controller.session.cycle_id, reason="api_stop_requested")
                    break
                if options.live and started - last_app_activation_monotonic >= LIVE_APP_REACTIVATE_INTERVAL_S:
                    if _activate_configured_app(config, activation_args):
                        last_app_activation_monotonic = started
                frame = capture.capture_frame()
                controller.process_frame(frame)
                if status_callback:
                    status_callback(_controller_status(controller))
                if preview_enabled:
                    _show_preview("antibot-cv-preview", frame, controller)
                sleep_s = max(0.0, frame_interval - (time.monotonic() - started))
                time.sleep(sleep_s)
        except KeyboardInterrupt:
            controller.logger.log_event(
                "session_stopped",
                state=controller.state_machine.state.value,
                cycle_id=controller.session.cycle_id,
                reason="keyboard_interrupt",
            )
        finally:
            hotkeys.stop()
            summary = controller.finish()
            if status_callback:
                status_callback({**_controller_status(controller), "summary": summary})
            if print_summary:
                print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return summary


def command_run(args: argparse.Namespace) -> int:
    try:
        run_automation(
            AutomationRunOptions(
                config_path=args.config,
                live=bool(args.live),
                preview=bool(args.preview),
                max_cycles=args.max_cycles,
                max_session_minutes=args.max_session_minutes,
                target_allowed_levels=_parse_target_level_args(args),
                start_delay=args.start_delay,
                activate_app=args.activate_app,
                no_activate_app=bool(args.no_activate_app),
                hotkeys=bool(args.hotkeys),
                open_hunt_on_start=bool(args.open_hunt_on_start),
                browser_client_id=getattr(args, "client_id", None),
            )
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def command_replay(args: argparse.Namespace) -> int:
    config = load_config(args.config).with_overrides(
        dry_run=True,
        max_cycles=args.max_cycles,
        max_session_minutes=args.max_session_minutes,
    )
    controller = AutomationController(config, sink_mode="replay")
    controller.start()
    for frame_path in _iter_frame_paths(Path(args.input)):
        frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
        if frame is None:
            continue
        controller.process_frame(frame)
        if controller.state_machine.state == GameState.STOPPED:
            break
    summary = controller.finish()
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_capture_template(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    from src.antibot_cv.automation.capture import ScreenCapture

    with ScreenCapture(config.capture) as capture:
        frame = capture.capture_frame()
    roi = cv2.selectROI("capture-template", frame, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow("capture-template")
    x, y, width, height = [int(value) for value in roi]
    if width <= 0 or height <= 0:
        print("No ROI selected.", file=sys.stderr)
        return 1
    output_path = Path(args.output or DEFAULT_TEMPLATE_PATHS.get(args.template_id, f"assets/templates/{args.template_id}.png"))
    if output_path.exists() and not args.overwrite:
        print(f"Refusing to overwrite existing template: {output_path}", file=sys.stderr)
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), frame[y : y + height, x : x + width])
    entry = {
        "template_id": args.template_id,
        "path": str(output_path),
        "threshold": args.threshold,
        "scales": [1.0],
        "search_roi": {"x": x, "y": y, "width": width, "height": height},
        "max_results": 1,
    }
    templates_config = Path(args.templates_config or config.templates_path)
    updated = False
    if args.update_config:
        _upsert_template_config(templates_config, entry)
        updated = True
    print(
        json.dumps(
            {"saved": str(output_path), "updated_config": updated, "templates_config": str(templates_config), "config_entry": entry},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_injector_status(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import CURRENT_BRIDGE_VERSION, DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    deadline = time.monotonic() + max(0.0, float(args.timeout))
    while time.monotonic() < deadline and not server.client_seen_recently(within_s=1.0):
        time.sleep(0.05)
    selected_client_id = getattr(args, "client_id", None)
    clients = server.client_snapshots(within_s=2.0)
    selected_client = server.client_snapshot(selected_client_id) if selected_client_id else None
    if selected_client is None and len([client for client in clients if client.get("client_seen") and client.get("version_ok")]) == 1:
        selected_client = [client for client in clients if client.get("client_seen") and client.get("version_ok")][0]
    client_seen = bool(selected_client.get("client_seen")) if selected_client is not None else server.client_seen_recently(within_s=2.0)
    version_ok = bool(selected_client.get("version_ok")) if selected_client is not None else server.last_client_version == CURRENT_BRIDGE_VERSION
    payload = {
        "ok": client_seen and version_ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_seen": client_seen,
        "client_id": None if selected_client is None else selected_client.get("client_id"),
        "client_version": None if selected_client is None else selected_client.get("client_version"),
        "required_version": CURRENT_BRIDGE_VERSION,
        "version_ok": version_ok,
        "clients": clients,
        "extension_path": str((Path.cwd() / "browser_injector").resolve()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if client_seen and version_ok else 1


def command_control_server(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector
    from src.antibot_cv.automation.control_server import AutomationControlApi

    server = global_browser_injector()
    server.start()
    api = AutomationControlApi(server, default_config=args.config)
    server.set_api_handler(api.handle)
    print(
        json.dumps(
            {
                "ok": True,
                "message": "control_server_started",
                "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
                "config": args.config,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        api.stop()
        server.set_api_handler(None)
        return 0


def command_injector_probe(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("probe_page", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["probe"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_inspect(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    names = [name.strip() for item in args.names for name in item.split(",") if name.strip()]
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "inspect_functions",
        {"names": names},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["inspect"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_layout(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute(
        "layout",
        {"mode": args.mode, "chatHeight": int(args.chat_height), "stretch": args.stretch},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_layout_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("layout_snapshot", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["snapshot"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_hunt_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("hunt_snapshot", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["snapshot"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_hunt_candidates(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("hunt_candidates", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["hunt"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_bot_info(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute(
        "hunt_bot_info",
        {"bot_id": int(args.bot_id)},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_visible_targets(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    names = [name.strip() for item in args.names for name in item.split(",") if name.strip()]
    allowed_levels = _parse_target_level_args(args)
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "visible_hunt_targets",
        {"margin": int(args.margin), "names": names, "allowedLevels": list(allowed_levels or ())},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["visible"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_resource_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("resource_snapshot", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            resources = json.loads(result.message)
            if isinstance(resources, dict) and not args.debug:
                resources = {
                    "ok": resources.get("ok"),
                    "healthPercent": resources.get("healthPercent"),
                    "prowessPercent": resources.get("prowessPercent"),
                    "healthCandidate": resources.get("healthCandidate"),
                    "prowessCandidate": resources.get("prowessCandidate"),
                    "candidateCount": resources.get("candidateCount"),
                    "generatedAt": resources.get("generatedAt"),
                    "bridgeVersion": resources.get("bridgeVersion"),
                }
            payload["resources"] = resources
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_inventory_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    names = [name.strip() for item in args.names for name in item.split(",") if name.strip()]
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "inventory_snapshot",
        {"names": names, "open": bool(args.open)},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["inventory"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_use_recovery_items(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    health_names = [name.strip() for item in args.health_names for name in item.split(",") if name.strip()]
    prowess_names = [name.strip() for item in args.prowess_names for name in item.split(",") if name.strip()]
    server = global_browser_injector()
    server.start()
    logger = InMemoryEventLogger(dry_run=False)
    sink = LiveMacActionSink(logger)
    ok = sink.execute(
        ActionRequest(
            "use_recovery_items",
            dry_run=False,
            metadata={
                "health_names": health_names,
                "prowess_names": prowess_names,
                "use_when_below_percent": float(args.threshold),
                "force_use": bool(args.force_use),
                "use_if_resources_missing": bool(args.use_if_missing),
                "open_hunt_after": bool(args.open_hunt_after),
                "timeout_s": max(0.1, float(args.timeout)),
                "health_restore_percent": float(args.health_restore_percent),
                "prowess_restore_percent": float(args.prowess_restore_percent),
                "max_uses_per_resource": int(args.max_uses_per_resource),
                "inventory_open_delay_ms": int(args.inventory_open_delay_ms),
                "confirm_delay_ms": int(args.confirm_delay_ms),
                "between_items_delay_ms": int(args.between_items_delay_ms),
            },
        )
    )
    payload: dict[str, object] = {
        "ok": ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": server.last_client_id,
        "events": logger.events,
    }
    if not ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


def command_injector_attack_bot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute(
        "attack_bot",
        {"bot_id": int(args.bot_id), "confirmed": 1 if args.confirmed else 0},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_attack_visible(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    names = [name.strip() for item in args.names for name in item.split(",") if name.strip()]
    allowed_levels = _parse_target_level_args(args)
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "attack_visible_bot",
        {
            "margin": int(args.margin),
            "names": names,
            "allowedLevels": list(allowed_levels or ()),
            "confirmed": 1 if args.confirmed else 0,
        },
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_battle_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute("battle_snapshot", timeout_s=max(0.1, float(args.timeout)))
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    if result.ok:
        try:
            payload["battle"] = json.loads(result.message)
        except json.JSONDecodeError:
            payload["message"] = result.message
    else:
        payload["message"] = result.message
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def command_injector_use_skill(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    server = global_browser_injector()
    server.start()
    result = server.execute(
        "use_skill_slot",
        {"slot": int(args.slot)},
        timeout_s=max(0.1, float(args.timeout)),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or server.last_client_id,
    }
    try:
        payload["result"] = json.loads(result.message)
    except json.JSONDecodeError:
        payload["message"] = result.message
    if not result.ok:
        payload["extension_path"] = str((Path.cwd() / "browser_injector").resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return max(0.0, min(100.0, number))


def _upsert_template_config(path: Path, entry: dict[str, object]) -> None:
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = {"templates": []}
    templates = raw.setdefault("templates", [])
    for index, current in enumerate(templates):
        if current.get("template_id") == entry["template_id"]:
            templates[index] = {**current, **entry}
            break
    else:
        templates.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resource_status_payload(status: ResourceStatus) -> dict[str, object]:
    def bar_payload(bar: object) -> dict[str, object]:
        bbox = getattr(bar, "bbox")
        return {
            "detected": getattr(bar, "detected"),
            "percent": getattr(bar, "percent"),
            "confidence": getattr(bar, "confidence"),
            "bbox": None
            if bbox is None
            else {"x": bbox.x, "y": bbox.y, "width": bbox.width, "height": bbox.height},
        }

    return {
        "complete": status.complete,
        "health": bar_payload(status.health),
        "prowess": bar_payload(status.prowess),
    }


def _draw_resource_overlay(frame: np.ndarray, config: object, status: ResourceStatus) -> np.ndarray:
    preview = frame.copy()

    def draw_rect(rect: Rect | None, color: tuple[int, int, int], label: str) -> None:
        if rect is None:
            return
        cv2.rectangle(preview, (rect.x, rect.y), (rect.right, rect.bottom), color, 2)
        cv2.putText(
            preview,
            label,
            (rect.x, max(12, rect.y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )

    draw_rect(getattr(config, "roi", None), (0, 255, 255), "resource search roi")
    draw_rect(getattr(config, "health_bar_roi", None), (0, 0, 255), "health roi")
    draw_rect(getattr(config, "prowess_bar_roi", None), (255, 0, 0), "prowess roi")
    draw_rect(status.health.bbox, (0, 255, 0), f"health {status.health.percent}")
    draw_rect(status.prowess.bbox, (255, 255, 0), f"prowess {status.prowess.percent}")
    return preview


def _activate_configured_app(config: AutomationConfig, args: argparse.Namespace) -> bool:
    if getattr(args, "no_activate_app", False):
        return False
    app_name = getattr(args, "activate_app", None)
    if app_name is None:
        app_name = config.activate_app
    if not app_name:
        return False
    return _activate_app(str(app_name))


def _activate_app(app_name: str) -> bool:
    safe_name = app_name.replace('"', '\\"')
    try:
        result = subprocess.run(
            ["osascript", "-e", f'tell application "{safe_name}" to activate'],
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        print(f"Could not activate {app_name}: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        print(f"Could not activate {app_name}: {message}", file=sys.stderr)
        return False
    return True


def _iter_frame_paths(input_dir: Path) -> Iterable[Path]:
    extensions = {".png", ".jpg", ".jpeg", ".bmp"}
    if not input_dir.exists():
        return []
    return sorted(path for path in input_dir.iterdir() if path.suffix.lower() in extensions)


def _show_preview(window_name: str, frame: np.ndarray, controller: AutomationController) -> None:
    preview = draw_mapping_preview(frame, controller.mapper)
    state_text = f"{controller.state_machine.state.value} cycle={controller.session.cycle_id}"
    cv2.putText(preview, state_text, (16, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    if controller._current_target is not None:
        target = controller._current_target
        cv2.rectangle(
            preview,
            (target.bbox.x, target.bbox.y),
            (target.bbox.right, target.bbox.bottom),
            (0, 255, 0),
            2,
        )
        cv2.drawMarker(
            preview,
            (int(target.interaction_point.x), int(target.interaction_point.y)),
            (0, 255, 255),
            cv2.MARKER_CROSS,
            18,
            2,
        )
    height, width = preview.shape[:2]
    scale = min(1.0, 900 / max(1, width), 650 / max(1, height))
    if scale < 1.0:
        preview = cv2.resize(preview, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.imshow(window_name, preview)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        cv2.destroyWindow(window_name)


def _attack_click_point(result: object, attempt: int) -> Point:
    center = getattr(result, "center", None)
    bbox = getattr(result, "bbox", None)
    if bbox is None:
        return center
    variants = (
        Point(bbox.x + bbox.width * 0.52, bbox.center.y),
        center,
        Point(bbox.x + bbox.width * 0.70, bbox.center.y),
        Point(bbox.x + bbox.width * 0.38, bbox.center.y),
    )
    point = variants[min(attempt, len(variants) - 1)]
    return point if point is not None else bbox.center


def _frame_has_visual_content(frame: np.ndarray) -> bool:
    if frame.size == 0:
        return False
    return float(np.count_nonzero(frame)) / float(frame.size) > 0.03


def _snapshot_main_href(snapshot: dict[str, object] | None) -> str:
    if snapshot is None:
        return ""
    return str(snapshot.get("mainHref") or "").lower()


def _location_map_present(frame: np.ndarray, direction_pad_roi: Rect | None) -> bool:
    if frame.size == 0:
        return False
    if direction_pad_roi is not None and _green_region_present(frame, direction_pad_roi, threshold=0.08):
        return True
    return _hunt_map_layout_present(frame)


def _game_shell_present(frame: np.ndarray) -> bool:
    if frame.size == 0:
        return False
    return _top_game_toolbar_present(frame) and _right_game_buttons_present(frame)


def _top_game_toolbar_present(frame: np.ndarray) -> bool:
    height, width = frame.shape[:2]
    crop = frame[int(height * 0.11) : int(height * 0.20), int(width * 0.55) : int(width * 0.98)]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    saturated = cv2.inRange(hsv, np.array([0, 55, 45]), np.array([179, 255, 255]))
    colorful_ratio = float(np.count_nonzero(saturated)) / float(saturated.size)
    return colorful_ratio > 0.22


def _right_game_buttons_present(frame: np.ndarray) -> bool:
    height, width = frame.shape[:2]
    crop = frame[int(height * 0.18) : int(height * 0.55), int(width * 0.94) : int(width * 0.995)]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, np.array([8, 60, 70]), np.array([35, 255, 255]))
    green = cv2.inRange(hsv, np.array([35, 45, 45]), np.array([95, 255, 255]))
    red = cv2.inRange(hsv, np.array([0, 60, 55]), np.array([12, 255, 255])) | cv2.inRange(
        hsv, np.array([170, 60, 55]), np.array([180, 255, 255])
    )
    active = gold | green | red
    active_ratio = float(np.count_nonzero(active)) / float(active.size)
    return active_ratio > 0.10


def _green_region_present(frame: np.ndarray, roi: Rect, *, threshold: float) -> bool:
    height, width = frame.shape[:2]
    x1 = max(0, roi.x)
    y1 = max(0, roi.y)
    x2 = min(width, roi.right)
    y2 = min(height, roi.bottom)
    if x2 <= x1 or y2 <= y1:
        return False
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 40, 30]), np.array([90, 255, 220]))
    return float(np.count_nonzero(green)) / float(green.size) > threshold


def _hunt_map_layout_present(frame: np.ndarray) -> bool:
    height, width = frame.shape[:2]
    map_crop = frame[int(height * 0.12) : int(height * 0.93), int(width * 0.18) : int(width * 0.82)]
    if map_crop.size == 0:
        return False
    hsv = cv2.cvtColor(map_crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 35, 25]), np.array([95, 255, 230]))
    green_ratio = float(np.count_nonzero(green)) / float(green.size)
    if green_ratio < 0.18:
        return False

    right_crop = frame[int(height * 0.15) : int(height * 0.93), int(width * 0.70) : int(width * 0.83)]
    if right_crop.size == 0:
        return False
    hsv_right = cv2.cvtColor(right_crop, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv_right, np.array([0, 70, 45]), np.array([12, 255, 255])) | cv2.inRange(
        hsv_right, np.array([170, 70, 45]), np.array([180, 255, 255])
    )
    red_by_column = (red > 0).mean(axis=0)
    red_column_count = int(np.count_nonzero(red_by_column > 0.1))
    max_red_column = float(red_by_column.max()) if red_by_column.size else 0.0
    return max_red_column > 0.25 and 8 <= red_column_count <= 45


def _detect_direction_pad_roi(frame: np.ndarray) -> Rect | None:
    if frame.size == 0:
        return None
    height, width = frame.shape[:2]
    x_offset = int(width * 0.12)
    y_offset = int(height * 0.08)
    crop = frame[y_offset : int(height * 0.35), x_offset : int(width * 0.35)]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 40, 30]), np.array([95, 255, 230]))
    contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, Rect]] = []
    for contour in contours:
        x, y, contour_width, contour_height = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if contour_width < 45 or contour_height < 45 or area < 1200:
            continue
        if contour_width > 135 or contour_height > 135:
            continue
        aspect = contour_width / max(1, contour_height)
        if not 0.65 <= aspect <= 1.35:
            continue
        rect = Rect(x_offset + x, y_offset + y, contour_width, contour_height)
        candidates.append((area, rect))
    if not candidates:
        return None
    _, best = max(candidates, key=lambda item: item[0])
    size = max(70, int(max(best.width, best.height) * 1.35))
    center = best.center
    return Rect(int(center.x - size / 2), int(center.y - size / 2), size, size)


def _browser_confirm_dialog_present(frame: np.ndarray) -> bool:
    return _browser_confirm_dialog_cancel_point(frame) is not None


def _browser_confirm_dialog_cancel_point(frame: np.ndarray) -> Point | None:
    if frame.size == 0:
        return None
    height, width = frame.shape[:2]
    x1 = int(width * 0.32)
    x2 = int(width * 0.70)
    y1 = int(height * 0.07)
    y2 = int(height * 0.23)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    blue = crop[:, :, 0]
    green = crop[:, :, 1]
    red = crop[:, :, 2]
    dark = (blue < 70) & (green < 70) & (red < 70)
    button_like = ((red > 85) & (blue > 75) & (green < 190) & ((red > green + 15) | (blue > green + 15))) | (
        (red > 190) & (green > 145) & (blue > 145)
    )
    dark_ratio = float(np.count_nonzero(dark)) / float(dark.size)
    button_ratio = float(np.count_nonzero(button_like)) / float(button_like.size)
    if dark_ratio <= 0.45 or button_ratio <= 0.003:
        return None

    lower = np.zeros_like(button_like, dtype=np.uint8)
    lower[int(lower.shape[0] * 0.45) :, :] = button_like[int(lower.shape[0] * 0.45) :, :].astype(np.uint8) * 255
    contours, _ = cv2.findContours(lower, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[Rect] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 18 or h < 8 or w * h < 140:
            continue
        candidates.append(Rect(x1 + x, y1 + y, w, h))
    if candidates:
        return min(candidates, key=lambda rect: rect.x).center
    return Point(width * 0.52, height * 0.164)


def _add_client_id_arg(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--client-id", default=None, help="Target browser injector client id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local bounded CV automation harness")
    parser.add_argument("--config", default=None, help="Path to automation config JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--config", default=None)
    calibrate.set_defaults(func=command_calibrate)

    run = subparsers.add_parser("run")
    run.add_argument("--config", default=None)
    run.add_argument("--preview", action="store_true")
    run.add_argument("--live", action="store_true")
    run.add_argument("--max-cycles", type=int, default=None)
    run.add_argument("--max-session-minutes", type=int, default=None)
    run.add_argument("--target-level", action="append", default=[])
    run.add_argument("--target-levels", nargs="*", default=[])
    run.add_argument("--start-delay", type=float, default=None)
    run.add_argument("--activate-app", default=None)
    run.add_argument("--no-activate-app", action="store_true")
    run.add_argument("--hotkeys", action=argparse.BooleanOptionalAction, default=False)
    run.add_argument("--open-hunt-on-start", action=argparse.BooleanOptionalAction, default=False)
    run.add_argument("--client-id", default=None, help="Target browser injector client id when several Chrome windows are connected")
    run.set_defaults(func=command_run)

    control_server = subparsers.add_parser("control-server")
    control_server.add_argument("--config", default="config/automation.local.json")
    control_server.set_defaults(func=command_control_server)

    validate = subparsers.add_parser("validate-templates")
    validate.add_argument("--config", default=None)
    validate.set_defaults(func=command_validate_templates)

    inspect_resources = subparsers.add_parser("inspect-resources")
    inspect_resources.add_argument("--config", default=None)
    inspect_resources.add_argument("--input", default=None)
    inspect_resources.add_argument("--output-frame", default=None)
    inspect_resources.add_argument("--output-overlay", default=None)
    inspect_resources.add_argument("--activate-app", default=None)
    inspect_resources.add_argument("--no-activate-app", action="store_true")
    inspect_resources.set_defaults(func=command_inspect_resources)

    capture_template = subparsers.add_parser("capture-template")
    capture_template.add_argument("--config", default=None)
    capture_template.add_argument("--template-id", required=True)
    capture_template.add_argument("--output", default=None)
    capture_template.add_argument("--templates-config", default=None)
    capture_template.add_argument("--threshold", type=float, default=0.8)
    capture_template.add_argument("--overwrite", action="store_true")
    capture_template.add_argument("--update-config", action=argparse.BooleanOptionalAction, default=True)
    capture_template.set_defaults(func=command_capture_template)

    replay = subparsers.add_parser("replay")
    replay.add_argument("--config", default=None)
    replay.add_argument("--input", required=True)
    replay.add_argument("--max-cycles", type=int, default=None)
    replay.add_argument("--max-session-minutes", type=int, default=None)
    replay.set_defaults(func=command_replay)

    injector_status = subparsers.add_parser("injector-status")
    injector_status.add_argument("--timeout", type=float, default=5.0)
    _add_client_id_arg(injector_status)
    injector_status.set_defaults(func=command_injector_status)

    injector_probe = subparsers.add_parser("injector-probe")
    injector_probe.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_probe)
    injector_probe.set_defaults(func=command_injector_probe)

    injector_inspect = subparsers.add_parser("injector-inspect")
    injector_inspect.add_argument(
        "--names",
        nargs="+",
        default=[
            "huntAttack",
            "botAttack",
            "getTarget",
            "getHuntApp",
            "useSkill",
            "fightOver",
            "fightFinished",
            "userAttack",
            "processMenu",
        ],
    )
    injector_inspect.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_inspect)
    injector_inspect.set_defaults(func=command_injector_inspect)

    injector_layout = subparsers.add_parser("injector-layout")
    injector_layout.add_argument("--mode", choices=["wide", "normal"], default="wide")
    injector_layout.add_argument("--chat-height", type=int, default=140)
    injector_layout.add_argument("--stretch", choices=["fit-width", "fit", "off"], default="fit-width")
    injector_layout.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_layout)
    injector_layout.set_defaults(func=command_injector_layout)

    injector_layout_snapshot = subparsers.add_parser("injector-layout-snapshot")
    injector_layout_snapshot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_layout_snapshot)
    injector_layout_snapshot.set_defaults(func=command_injector_layout_snapshot)

    injector_hunt_snapshot = subparsers.add_parser("injector-hunt-snapshot")
    injector_hunt_snapshot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_hunt_snapshot)
    injector_hunt_snapshot.set_defaults(func=command_injector_hunt_snapshot)

    injector_hunt_candidates = subparsers.add_parser("injector-hunt-candidates")
    injector_hunt_candidates.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_hunt_candidates)
    injector_hunt_candidates.set_defaults(func=command_injector_hunt_candidates)

    injector_bot_info = subparsers.add_parser("injector-bot-info")
    injector_bot_info.add_argument("--bot-id", type=int, required=True)
    injector_bot_info.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_bot_info)
    injector_bot_info.set_defaults(func=command_injector_bot_info)

    injector_visible_targets = subparsers.add_parser("injector-visible-targets")
    injector_visible_targets.add_argument("--margin", type=int, default=35)
    injector_visible_targets.add_argument("--names", nargs="*", default=[])
    injector_visible_targets.add_argument("--target-level", action="append", default=[])
    injector_visible_targets.add_argument("--target-levels", nargs="*", default=[])
    injector_visible_targets.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_visible_targets)
    injector_visible_targets.set_defaults(func=command_injector_visible_targets)

    injector_resource_snapshot = subparsers.add_parser("injector-resource-snapshot")
    injector_resource_snapshot.add_argument("--timeout", type=float, default=10.0)
    injector_resource_snapshot.add_argument("--debug", action="store_true")
    _add_client_id_arg(injector_resource_snapshot)
    injector_resource_snapshot.set_defaults(func=command_injector_resource_snapshot)

    injector_inventory_snapshot = subparsers.add_parser("injector-inventory-snapshot")
    injector_inventory_snapshot.add_argument("--names", nargs="*", default=["малый бурдюк жизни", "бурдюк жизни", "малый бурдюк удали", "бурдюк удали"])
    injector_inventory_snapshot.add_argument("--open", action=argparse.BooleanOptionalAction, default=True)
    injector_inventory_snapshot.add_argument("--timeout", type=float, default=8.0)
    _add_client_id_arg(injector_inventory_snapshot)
    injector_inventory_snapshot.set_defaults(func=command_injector_inventory_snapshot)

    injector_recovery_items = subparsers.add_parser("injector-use-recovery-items")
    injector_recovery_items.add_argument("--health-names", nargs="*", default=["малый бурдюк жизни", "бурдюк жизни"])
    injector_recovery_items.add_argument("--prowess-names", nargs="*", default=["малый бурдюк удали", "бурдюк удали"])
    injector_recovery_items.add_argument("--threshold", type=float, default=90.0)
    injector_recovery_items.add_argument("--force-use", action="store_true")
    injector_recovery_items.add_argument("--health-restore-percent", type=float, default=40.0)
    injector_recovery_items.add_argument("--prowess-restore-percent", type=float, default=30.0)
    injector_recovery_items.add_argument("--max-uses-per-resource", type=int, default=4)
    injector_recovery_items.add_argument("--inventory-open-delay-ms", type=int, default=1500)
    injector_recovery_items.add_argument("--confirm-delay-ms", type=int, default=700)
    injector_recovery_items.add_argument("--between-items-delay-ms", type=int, default=500)
    injector_recovery_items.add_argument("--use-if-missing", action=argparse.BooleanOptionalAction, default=True)
    injector_recovery_items.add_argument("--open-hunt-after", action=argparse.BooleanOptionalAction, default=True)
    injector_recovery_items.add_argument("--timeout", type=float, default=8.0)
    _add_client_id_arg(injector_recovery_items)
    injector_recovery_items.set_defaults(func=command_injector_use_recovery_items)

    injector_attack_bot = subparsers.add_parser("injector-attack-bot")
    injector_attack_bot.add_argument("--bot-id", type=int, required=True)
    injector_attack_bot.add_argument("--confirmed", action="store_true")
    injector_attack_bot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_attack_bot)
    injector_attack_bot.set_defaults(func=command_injector_attack_bot)

    injector_attack_visible = subparsers.add_parser("injector-attack-visible")
    injector_attack_visible.add_argument("--margin", type=int, default=35)
    injector_attack_visible.add_argument("--names", nargs="*", default=[])
    injector_attack_visible.add_argument("--target-level", action="append", default=[])
    injector_attack_visible.add_argument("--target-levels", nargs="*", default=[])
    injector_attack_visible.add_argument("--confirmed", action=argparse.BooleanOptionalAction, default=True)
    injector_attack_visible.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_attack_visible)
    injector_attack_visible.set_defaults(func=command_injector_attack_visible)

    injector_battle_snapshot = subparsers.add_parser("injector-battle-snapshot")
    injector_battle_snapshot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_battle_snapshot)
    injector_battle_snapshot.set_defaults(func=command_injector_battle_snapshot)

    injector_use_skill = subparsers.add_parser("injector-use-skill")
    injector_use_skill.add_argument("--slot", type=int, default=4)
    injector_use_skill.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_use_skill)
    injector_use_skill.set_defaults(func=command_injector_use_skill)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    client_id = getattr(args, "client_id", None)
    if client_id:
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        global_browser_injector().set_current_client_id(str(client_id))
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
