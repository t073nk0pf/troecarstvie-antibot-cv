from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
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
from src.antibot_cv.automation.checkpoint import write_json_checkpoint
from src.antibot_cv.automation.combat_policy import (
    AvailableSkill as PolicySkill,
    BattleItem as PolicyBattleItem,
    BattleItemKind,
    BattleResources as PolicyBattleResources,
    BattleSnapshot as PolicyBattleSnapshot,
    CombatIntent,
    CombatPolicy,
)
from src.antibot_cv.automation.death_recovery import (
    DeathRecoveryPolicy,
    RecoveryCheckpoint,
    RecoveryDecision,
    RecoverySnapshot,
    ReviveOption,
)
from src.antibot_cv.automation.leveling_policy import (
    Death as LevelingDeath,
    Inventory as LevelingInventory,
    Intent as LevelingIntent,
    LevelingPolicy,
    Location as LevelingLocation,
    ObservedPlayer as LevelingPlayer,
    Quests as LevelingQuests,
)
from src.antibot_cv.automation.quest_policy import (
    Quest as PolicyQuest,
    QuestDecision as PolicyQuestDecision,
    QuestIdentity,
    QuestIntent,
    QuestObjectiveKind,
    QuestPolicy,
    QuestSnapshot as PolicyQuestSnapshot,
    QuestStatus,
)
from src.antibot_cv.automation.route_planner import RouteAction, validate_navigator_route
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
    target_allowed_names: tuple[str, ...] | None = None
    goal_level: int | None = None
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
        self._last_battle_snapshot_cache: dict[str, object] | None = None
        self._last_battle_snapshot_success_monotonic: float | None = None
        self._last_viewport_action_monotonic: float | None = None
        self._last_browser_back_monotonic: float | None = None
        self._last_screen_sync_monotonic: float | None = None
        self._resting_since_monotonic: float | None = None
        self._last_rest_check_monotonic: float | None = None
        self._last_resource_refresh_monotonic: float | None = None
        self._last_resource_log_monotonic: float | None = None
        self._last_item_recovery_attempt_monotonic: float | None = None
        self._last_battle_item_recovery_monotonic: float | None = None
        self._battle_item_recovery_counts: dict[tuple[int, str], int] = {}
        self._state_snapshot_cache: dict[str, object] | None = None
        self._last_state_snapshot_monotonic: float | None = None
        self._last_state_snapshot_success_monotonic: float | None = None
        self._last_state_snapshot_client_id: str | None = None
        self._current_state_snapshot_id: str | None = None
        self._bound_browser_profile_id: str | None = None
        self._bound_browser_tab_id: int | None = None
        self._last_valid_player_snapshot_monotonic: float | None = None
        self._leveling_started_monotonic = time.monotonic()
        self.current_character_name: str | None = None
        self._bound_character_name: str | None = str(config.leveling.required_character_name or "").strip() or None
        self.current_level: int | None = None
        self.current_xp_percent: float | None = None
        self.current_page_kind: str | None = None
        self.current_location_name: str | None = None
        self._last_alive_location_name: str | None = None
        self._quest_origin_location_name: str | None = None
        self._quest_target_names: tuple[str, ...] = ()
        self._quest_route_locations: tuple[str, ...] = ()
        self._quest_target_routes: dict[str, tuple[str, ...]] = {}
        self.deaths_observed = 0
        self._death_latched = False
        self._revive_requested_monotonic: float | None = None
        self._revive_attempted_for_current_death = False
        self._last_checkpoint_monotonic: float | None = None
        self.last_leveling_intent: str | None = None
        self.last_leveling_reason: str | None = None
        self._next_quest_refresh_cycle = (
            0
            if config.leveling.auto_navigate_quest_targets
            else max(0, int(config.leveling.quest_refresh_every_cycles))
        )
        self._quest_refresh_requested_monotonic: float | None = None
        self._quest_policy_intent: QuestIntent | None = None
        self._active_quest_id: str | None = None
        self._last_quest_policy_key: tuple[object, ...] | None = None
        self._navigator_target_name: str | None = None
        self._navigator_opened_monotonic: float | None = None
        self._navigator_client_id: str | None = None
        self._navigator_requires_target_selection = False
        self._route_recovery_kind: str | None = None
        self._route_go_submitted_monotonic: float | None = None
        self._death_checkpoint: RecoveryCheckpoint | None = None
        self._death_recovery_policy = DeathRecoveryPolicy(
            max_deaths=max(0, int(config.leveling.max_deaths_per_session)),
            free_revive_allowlist=("explicit_free_revive",),
            snapshot_max_age_seconds=max(1.0, config.leveling.snapshot_stale_timeout_ms / 1000),
            now=time.monotonic,
        )
        self._last_player_observation: tuple[object, ...] | None = None
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
            return LiveMacActionSink(self.logger, browser_client_id=self.browser_client_id)
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
        if self._observe_death_guard():
            return self.state_machine.state
        if self._observe_leveling_goal():
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
        elif state == GameState.QUEST_REFRESH_PENDING:
            self._handle_quest_refresh()
        elif state == GameState.NAVIGATOR_PENDING:
            self._handle_navigator_pending()
        elif state == GameState.ROUTE_RECOVERY:
            self._handle_route_recovery()
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
        battle_snapshot = self._last_battle_snapshot_cache
        last_battle = self._last_battle_snapshot_success_monotonic
        if battle_snapshot is None or last_battle is None or time.monotonic() - last_battle > 2.0:
            battle_snapshot = self._battle_snapshot_via_injector(force=True)
        if not isinstance(battle_snapshot, dict):
            self.logger.log_event(
                "combat_policy_skipped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason="battle_snapshot_unavailable",
            )
            return self._legacy_live_combat_slot(frame, status)
        decision = self._combat_policy_decision(status, battle_snapshot)
        self.logger.log_event(
            "combat_policy_decision",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            intent=decision.intent.value,
            reason=decision.reason,
            skill_slot=None if decision.skill is None else decision.skill.slot,
            item_name=None if decision.item is None else decision.item.name,
            item_slot=None if decision.item is None else decision.item.slot,
        )
        if decision.intent is CombatIntent.USE_ITEM and decision.item is not None:
            kind = decision.item.kind.value if isinstance(decision.item.kind, BattleItemKind) else str(decision.item.kind)
            if self._try_battle_item_recovery(
                frame,
                status,
                kind_override=kind,
                selected_slot=decision.item.slot,
                selected_name=decision.item.name,
            ):
                return None
            if self.state_machine.state == GameState.STOPPED:
                return None
            return self._next_live_combat_slot()
        if decision.intent is CombatIntent.USE_SKILL and decision.skill is not None:
            self._combat_slot_sequence_index += 1
            return decision.skill.slot
        if decision.intent is CombatIntent.STOP_UNSAFE:
            self._stop_leveling_unsafe(f"combat_policy:{decision.reason}")
        return None

    def _legacy_live_combat_slot(self, frame: np.ndarray, status: ResourceStatus) -> int | None:
        """Compatibility path; every resulting JS action still verifies its postcondition."""
        if self._try_battle_item_recovery(frame, status):
            return None
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

    def _combat_policy_decision(
        self,
        status: ResourceStatus,
        battle_snapshot: dict[str, object],
    ) -> object:
        health_percent = status.health.percent
        prowess_percent = status.prowess.percent
        if health_percent is None or prowess_percent is None:
            return CombatPolicy().decide(
                PolicyBattleSnapshot(
                    active=None,
                    finished=None,
                    turn=0,
                    resources=PolicyBattleResources(None, None, None, None),
                )
            )
        sequence = self._configured_live_combat_slots()
        start = self._combat_slot_sequence_index % len(sequence)
        rotated = sequence[start:] + sequence[:start]
        allowed_slots = list(dict.fromkeys(rotated))
        abilities: list[PolicySkill] = []
        raw_abilities = battle_snapshot.get("abilities")
        if isinstance(raw_abilities, list):
            for raw in raw_abilities:
                if not isinstance(raw, dict):
                    continue
                ability_id = _optional_int(raw.get("id"))
                if ability_id is None or ability_id >= 0:
                    continue
                slot = _optional_int(raw.get("slot"))
                if slot is None or slot not in allowed_slots:
                    continue
                raw_ready = raw.get("ready")
                raw_cooldown = _optional_float(raw.get("cooldown"))
                ready = raw_ready is True or (
                    not self.config.combat.require_ready_confirmation
                    and raw.get("disabled") is not True
                    and (raw_cooldown is None or raw_cooldown == 0)
                )
                priority = len(rotated) - rotated.index(slot)
                abilities.append(
                    PolicySkill(
                        name=str(raw.get("name") or f"slot:{slot}"),
                        slot=slot,
                        damage=float(priority),
                        ready=ready,
                        cooldown=0 if ready and raw_cooldown is None else raw_cooldown,
                    )
                )
        if not abilities and battle_snapshot.get("useSkillAvailable") is True and not self.config.combat.require_ready_confirmation:
            slot = rotated[0]
            abilities.append(PolicySkill(f"slot:{slot}", slot, 1.0, True, 0))
        fallback_threshold = max(0.0, float(self.config.combat.low_resource_fallback_percent))
        fallback_slot = max(0, int(self.config.combat.low_resource_fallback_slot_index))
        allow_zero = (
            self.config.combat.low_resource_fallback_enabled
            and prowess_percent <= fallback_threshold
        )
        if allow_zero and all(skill.slot != fallback_slot for skill in abilities):
            abilities.append(PolicySkill("zero_resource_fallback", fallback_slot, 1000.0, True, 0))
        if allow_zero and fallback_slot not in allowed_slots:
            allowed_slots.append(fallback_slot)

        item_config = self.config.battle_item_recovery
        items_enabled = item_config.enabled
        item_allow_names = {
            BattleItemKind.HEALTH: tuple(item_config.health_names) if items_enabled else (),
            BattleItemKind.PROWESS: tuple(item_config.prowess_names) if items_enabled else (),
            BattleItemKind.DAMAGE_BOOST: tuple(item_config.damage_boost_names) if items_enabled else (),
        }
        item_allow_slots = {
            BattleItemKind.HEALTH: tuple(item_config.health_slots) if items_enabled else (),
            BattleItemKind.PROWESS: tuple(item_config.prowess_slots) if items_enabled else (),
            BattleItemKind.DAMAGE_BOOST: tuple(item_config.damage_boost_slots) if items_enabled else (),
        }
        items: list[PolicyBattleItem] = []
        raw_items = battle_snapshot.get("items")
        if isinstance(raw_items, list):
            for raw in raw_items:
                if not isinstance(raw, dict):
                    continue
                slot = _optional_int(raw.get("slot"))
                name = str(raw.get("name") or "").strip()
                if slot is None or not name:
                    continue
                matched_kinds = [
                    kind
                    for kind in BattleItemKind
                    if slot in item_allow_slots[kind]
                    or any(_normalize_phrase(name) == _normalize_phrase(allowed) for allowed in item_allow_names[kind])
                ]
                if len(matched_kinds) != 1:
                    continue
                raw_quantity = _optional_int(raw.get("quantity"))
                ready = raw.get("ready") is True and raw.get("disabled") is not True
                cooldown = _optional_float(raw.get("cooldown"))
                if ready and cooldown is None:
                    cooldown = 0
                items.append(
                    PolicyBattleItem(
                        name=name,
                        slot=slot,
                        kind=matched_kinds[0],
                        count=raw_quantity if raw_quantity is not None else (1 if ready else 0),
                        ready=ready,
                        cooldown=cooldown,
                    )
                )

        hp = int(round(max(0.0, min(100.0, health_percent)) * 100))
        prowess = int(round(max(0.0, min(100.0, prowess_percent)) * 100))
        battle_id = int(self.session.battle_id or 0)
        policy = CombatPolicy(
            skill_slot_allowlist=tuple(allowed_slots),
            item_name_allowlist=item_allow_names,
            item_slot_allowlist=item_allow_slots,
            hp_threshold=max(0.0, min(1.0, item_config.health_use_when_below_percent / 100)),
            prowess_threshold=max(0.0, min(1.0, item_config.prowess_use_when_below_percent / 100)),
            damage_boost_enabled=item_config.enabled and item_config.damage_boost_enabled,
            allow_zero_prowess_slot=allow_zero,
        )
        return policy.decide(
            PolicyBattleSnapshot(
                active=battle_snapshot.get("hasFight") if isinstance(battle_snapshot.get("hasFight"), bool) else None,
                finished=battle_snapshot.get("finished") if isinstance(battle_snapshot.get("finished"), bool) else None,
                turn=1 if battle_snapshot.get("myTurn") is True else 0,
                resources=PolicyBattleResources(hp, 10000, prowess, 10000),
                skills=tuple(abilities),
                items=tuple(items),
                damage_boost_active=self._battle_item_recovery_counts.get((battle_id, "damage_boost"), 0) > 0,
            )
        )

    def _try_battle_item_recovery(
        self,
        frame: np.ndarray,
        status: ResourceStatus,
        *,
        kind_override: str | None = None,
        selected_slot: int | None = None,
        selected_name: str | None = None,
    ) -> bool:
        config = self.config.battle_item_recovery
        if self.config.dry_run or not config.enabled or self.session.battle_id is None:
            return False
        kind = kind_override or self._battle_item_recovery_kind(status)
        if kind is None:
            return False
        if kind == "health":
            slots = tuple(config.health_slots)
            names = tuple(config.health_names)
            threshold: float | None = config.health_use_when_below_percent
        elif kind == "prowess":
            slots = tuple(config.prowess_slots)
            names = tuple(config.prowess_names)
            threshold = config.prowess_use_when_below_percent
        elif kind == "damage_boost" and config.damage_boost_enabled:
            slots = tuple(config.damage_boost_slots)
            names = tuple(config.damage_boost_names)
            threshold = None
        else:
            return False
        if selected_slot is not None:
            slots = (selected_slot,)
        if selected_name:
            names = (selected_name,)
        if not slots and not names:
            self.logger.log_event(
                "battle_item_recovery_skipped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                kind=kind,
                reason="no_slots_or_names",
                health_percent=status.health.percent,
                prowess_percent=status.prowess.percent,
                frame_hash=self._last_frame_hash,
            )
            return False
        now = time.monotonic()
        cooldown_ms = max(0, int(config.cooldown_ms))
        if self._last_battle_item_recovery_monotonic is not None:
            elapsed_ms = (now - self._last_battle_item_recovery_monotonic) * 1000
            if elapsed_ms < cooldown_ms:
                return False
        key = (int(self.session.battle_id), kind)
        max_uses = max(1, int(config.max_uses_per_battle))
        if self._battle_item_recovery_counts.get(key, 0) >= max_uses:
            return False
        self.logger.log_event(
            "battle_item_recovery_intended",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            kind=kind,
            slots=list(slots),
            names=list(names),
            threshold=threshold,
            health_percent=status.health.percent,
            prowess_percent=status.prowess.percent,
            frame_hash=self._last_frame_hash,
        )
        request = ActionRequest(
            "use_battle_item",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "kind": kind,
                "slots": list(slots),
                "names": list(names),
                "threshold": threshold,
                "health_percent": status.health.percent,
                "prowess_percent": status.prowess.percent,
                "frame_hash": self._last_frame_hash,
                "pre_click_delay_ms": config.pre_click_delay_ms,
                "click_hold_ms": config.click_hold_ms,
            },
        )
        if not self.action_executor.execute(request):
            self._last_battle_item_recovery_monotonic = now
            if getattr(self.action_executor.sink, "last_ambiguous_action", None) == "use_battle_item":
                self._stop_leveling_unsafe("battle_item_result_ambiguous")
            return False
        self._battle_item_recovery_counts[key] = self._battle_item_recovery_counts.get(key, 0) + 1
        self._last_battle_item_recovery_monotonic = now
        return True

    def _battle_item_recovery_kind(self, status: ResourceStatus) -> str | None:
        config = self.config.battle_item_recovery
        if status.health.percent is not None and status.health.percent <= float(config.health_use_when_below_percent):
            return "health"
        if status.prowess.percent is not None and status.prowess.percent <= float(config.prowess_use_when_below_percent):
            return "prowess"
        return None

    def _state_snapshot_via_injector(self, *, force: bool = False) -> dict[str, object] | None:
        now = time.monotonic()
        interval_ms = max(250, int(self.config.leveling.snapshot_interval_ms))
        if not force and self._last_state_snapshot_monotonic is not None:
            elapsed_ms = (now - self._last_state_snapshot_monotonic) * 1000
            if elapsed_ms < interval_ms:
                return self._state_snapshot_cache
        self._last_state_snapshot_monotonic = now
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "state_snapshot",
                {"include": ["player", "location", "deathRevive", "battle", "hunt", "quests", "shopInventory"]},
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
        except Exception as exc:
            self.logger.log_event(
                "state_js_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=str(exc),
                frame_hash=self._last_frame_hash,
            )
            return self._state_snapshot_cache
        if not result.ok:
            self.logger.log_event(
                "state_js_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=result.message,
                injector_client_id=result.client_id,
                frame_hash=self._last_frame_hash,
            )
            return self._state_snapshot_cache
        try:
            payload = json.loads(result.message)
        except json.JSONDecodeError:
            return self._state_snapshot_cache
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            return self._state_snapshot_cache
        self._state_snapshot_cache = payload
        self._last_state_snapshot_success_monotonic = time.monotonic()
        self._last_state_snapshot_client_id = result.client_id or self.browser_client_id
        return payload

    def _observe_death_guard(self, *, force: bool = False) -> bool:
        if self.config.dry_run:
            return False
        snapshot = self._state_snapshot_via_injector(force=True) if force else self._state_snapshot_via_injector()
        if not isinstance(snapshot, dict):
            return self.state_machine.state in {GameState.DEAD, GameState.REVIVE_PENDING}
        self._current_state_snapshot_id = str(snapshot.get("snapshotId") or "") or None
        sections = snapshot.get("sections")
        if not isinstance(sections, dict):
            return self.state_machine.state in {GameState.DEAD, GameState.REVIVE_PENDING}
        player_section = sections.get("player")
        player = player_section.get("data") if isinstance(player_section, dict) else None
        if isinstance(player, dict):
            name = str(player.get("name") or "").strip()
            level = _optional_int(player.get("level"))
            xp_percent = _optional_float(player.get("xpPercent"))
            if name:
                self.current_character_name = name
                if self._bound_character_name is None:
                    self._bound_character_name = name
            if level is not None:
                self.current_level = level
            if xp_percent is not None:
                self.current_xp_percent = xp_percent
        death_section = sections.get("deathRevive")
        death_data = death_section.get("data") if isinstance(death_section, dict) else None
        location_section = sections.get("location")
        location = location_section.get("data") if isinstance(location_section, dict) else None
        self._update_location_tracking(location, death_data, player)
        if isinstance(death_data, dict) and death_data.get("resurrectionNoticeAvailable") is True:
            request = ActionRequest(
                "close_resurrection_notice",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "verify_delay_ms": 250,
                    "reason": "post_revive_confirmation",
                    "snapshot_id": self._current_state_snapshot_id,
                },
            )
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("resurrection_notice_close_failed")
            return True
        return self._handle_leveling_death(death_data)

    def _update_location_tracking(
        self,
        location: object,
        death_data: object,
        player: object,
    ) -> None:
        if not isinstance(location, dict):
            self.current_page_kind = None
            self.current_location_name = None
            return
        self.current_page_kind = str(location.get("pageKind") or "").strip() or None
        self.current_location_name = str(location.get("semanticName") or "").strip() or None
        dead = death_data.get("dead") if isinstance(death_data, dict) else None
        hp_percent = _optional_float(player.get("hpPercent")) if isinstance(player, dict) else None
        alive = dead is False or (dead is not True and hp_percent is not None and hp_percent > 0)
        recovery_active = self._death_latched or self.state_machine.state in {
            GameState.DEAD,
            GameState.REVIVE_PENDING,
        }
        if (
            alive
            and not recovery_active
            and self.current_page_kind == "area"
            and self.current_location_name
        ):
            self._last_alive_location_name = self.current_location_name
            self._quest_origin_location_name = self.current_location_name

    def _observe_leveling_goal(self) -> bool:
        leveling = self.config.leveling
        if self.config.dry_run or not leveling.enabled:
            return False
        snapshot = self._state_snapshot_via_injector()
        if isinstance(snapshot, dict):
            self._current_state_snapshot_id = str(snapshot.get("snapshotId") or "") or None
        freshness_base = self._last_state_snapshot_success_monotonic or self._leveling_started_monotonic
        snapshot_age_ms = (time.monotonic() - freshness_base) * 1000
        if snapshot_age_ms >= max(1000, int(leveling.snapshot_stale_timeout_ms)):
            return self._stop_leveling_unsafe("leveling_state_snapshot_stale")
        sections = snapshot.get("sections") if isinstance(snapshot, dict) else None
        player_section = sections.get("player") if isinstance(sections, dict) else None
        player = player_section.get("data") if isinstance(player_section, dict) else None
        quest_section = sections.get("quests") if isinstance(sections, dict) else None
        quest_data = quest_section.get("data") if isinstance(quest_section, dict) else None
        death_section = sections.get("deathRevive") if isinstance(sections, dict) else None
        death_data = death_section.get("data") if isinstance(death_section, dict) else None
        location_section = sections.get("location") if isinstance(sections, dict) else None
        location = location_section.get("data") if isinstance(location_section, dict) else None
        self._update_location_tracking(location, death_data, player)
        battle_section = sections.get("battle") if isinstance(sections, dict) else None
        battle_data = battle_section.get("data") if isinstance(battle_section, dict) else None
        level = _optional_int(player.get("level")) if isinstance(player, dict) else None
        name = str(player.get("name") or "").strip() if isinstance(player, dict) else ""
        xp_percent = _optional_float(player.get("xpPercent")) if isinstance(player, dict) else None
        if level is None or not name:
            if self._handle_leveling_death(death_data):
                return True
            last_valid = self._last_valid_player_snapshot_monotonic or self._leveling_started_monotonic
            stale_ms = (time.monotonic() - last_valid) * 1000
            if stale_ms >= max(1000, int(leveling.snapshot_stale_timeout_ms)):
                self.last_error_reason = "leveling_player_snapshot_stale"
                self.logger.log_event(
                    "session_stopped",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    reason=self.last_error_reason,
                    page_kind=self.current_page_kind,
                    stale_ms=stale_ms,
                )
                self._safe_transition(GameState.STOPPED, reason=self.last_error_reason)
                return True
            return False
        if self._last_state_snapshot_success_monotonic is not None:
            self._last_valid_player_snapshot_monotonic = self._last_state_snapshot_success_monotonic
        self.current_character_name = name
        self.current_level = level
        self.current_xp_percent = xp_percent
        observation = (name, level, xp_percent, self.current_page_kind, self.current_location_name)
        if observation != self._last_player_observation:
            self._last_player_observation = observation
            self.logger.log_event(
                "player_observed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                character_name=name,
                level=level,
                xp_percent=xp_percent,
                page_kind=self.current_page_kind,
                location_name=self.current_location_name,
            )
        if self._bound_character_name is None:
            self._bound_character_name = name
            self.logger.log_event(
                "character_bound",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                character_name=name,
                browser_client_id=self.browser_client_id,
            )
        required_name = self._bound_character_name
        if name.casefold() != required_name.casefold():
            self.last_error_reason = f"character_mismatch:{name}"
            self.logger.log_event(
                "session_stopped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="character_mismatch",
                expected_character=required_name,
                observed_character=name,
            )
            self._safe_transition(GameState.STOPPED, reason="character_mismatch")
            return True
        target_level = _optional_int(leveling.target_level)
        if target_level is not None and level >= target_level:
            self.logger.log_event(
                "goal_level_reached",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                character_name=name,
                current_level=level,
                target_level=target_level,
                xp_percent=xp_percent,
            )
            self._safe_transition(GameState.STOPPED, reason="goal_level_reached")
            return True
        if (
            isinstance(quest_section, dict)
            and isinstance(quest_data, dict)
            and quest_data.get("loadStatus") == "loaded"
        ):
            quest_decision = self._evaluate_quest_policy(snapshot, quest_section, quest_data)
            if (
                self.config.leveling.auto_navigate_quest_targets
                and quest_decision.intent is QuestIntent.STOP_UNSAFE
            ):
                return self._stop_leveling_unsafe(f"quest_policy:{quest_decision.reason}")
        if (
            (isinstance(death_data, dict) and death_data.get("dead") is True)
            or self.state_machine.state in {GameState.DEAD, GameState.REVIVE_PENDING}
        ):
            if self._handle_leveling_death(death_data):
                return True
        if self.current_page_kind == "battle" or (
            isinstance(battle_data, dict)
            and (battle_data.get("rawHasFight") is True or battle_data.get("hasFight") is True)
        ):
            reason = (
                "battle_resolution_pending"
                if isinstance(battle_data, dict) and battle_data.get("finished") is True
                else "battle_in_progress"
            )
            self._record_leveling_wait(reason)
            return False
        decision = self._record_leveling_decision(player, death_data, sections)
        if decision is not None and decision.intent is LevelingIntent.STOP_UNSAFE:
            return self._stop_leveling_unsafe(f"leveling_policy:{decision.reason}")
        return self._handle_leveling_death(death_data)

    def _record_leveling_wait(self, reason: str) -> None:
        if (self.last_leveling_intent, self.last_leveling_reason) == (LevelingIntent.WAIT.value, reason):
            return
        self.last_leveling_intent = LevelingIntent.WAIT.value
        self.last_leveling_reason = reason
        self.logger.log_event(
            "leveling_decision",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            intent=LevelingIntent.WAIT.value,
            reason=reason,
            item=None,
            character_name=self.current_character_name,
            current_level=self.current_level,
            goal_level=self.config.leveling.target_level,
        )

    def _record_leveling_decision(
        self,
        player: object,
        death_data: object,
        sections: object,
    ) -> object | None:
        if not isinstance(player, dict) or self.current_level is None or not self.current_character_name:
            return None
        hp_percent = _optional_float(player.get("hpPercent"))
        prowess_percent = _optional_float(player.get("prowessPercent"))
        dead = death_data.get("dead") if isinstance(death_data, dict) else None
        alive = False if dead is True else True if dead is False else None
        if alive is None and self.current_page_kind == "hunt" and isinstance(sections, dict):
            hunt_section = sections.get("hunt")
            hunt_data = hunt_section.get("data") if isinstance(hunt_section, dict) else None
            if isinstance(hunt_data, dict) and hunt_data.get("hasHunt") is True:
                alive = True
        target_location = str(self.config.leveling.target_location_name or "").strip()
        page_kind_known = bool(self.current_page_kind)
        location_safe: bool | None = True if page_kind_known else None
        if target_location:
            location_safe = (
                None
                if not self.current_location_name
                else self.current_location_name.casefold() == target_location.casefold()
            )
        inventory_items: dict[str, int] | None = None
        shop_section = sections.get("shopInventory") if isinstance(sections, dict) else None
        shop_data = shop_section.get("data") if isinstance(shop_section, dict) else None
        raw_items = shop_data.get("items") if isinstance(shop_data, dict) else None
        if isinstance(raw_items, list):
            inventory_items = {}
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                count = _optional_int(item.get("count")) or 0
                if title:
                    inventory_items[title] = max(inventory_items.get(title, 0), count)
        policy = LevelingPolicy(
            required_character=self._bound_character_name or self.current_character_name,
            target_level=int(self.config.leveling.target_level or self.current_level + 1),
            max_deaths=max(0, int(self.config.leveling.max_deaths_per_session)) + 1,
            hp_threshold=max(0.0, min(1.0, self.config.resources.health_min_percent / 100)),
            prowess_threshold=max(0.0, min(1.0, self.config.resources.prowess_min_percent / 100)),
            hp_item_allowlist=tuple(self.config.item_recovery.health_names),
            prowess_item_allowlist=tuple(self.config.item_recovery.prowess_names),
        )
        decision = policy.decide(
            LevelingPlayer(
                character=self.current_character_name,
                level=self.current_level,
                hp=None if hp_percent is None else int(round(hp_percent * 100)),
                max_hp=10000 if hp_percent is not None else None,
                prowess=None if prowess_percent is None else int(round(prowess_percent * 100)),
                max_prowess=10000 if prowess_percent is not None else None,
                alive=alive,
            ),
            LevelingLocation(
                name=self.current_location_name,
                safe=location_safe,
                in_hunt=None if not page_kind_known else self.current_page_kind == "hunt",
                hunt_open=None if not page_kind_known else self.current_page_kind == "hunt",
                ready=None if not page_kind_known else True,
            ),
            LevelingDeath(
                deaths=self.deaths_observed,
                can_free_revive=(
                    death_data.get("freeReviveAvailable") is True
                    if isinstance(death_data, dict)
                    else None
                ),
            ),
            LevelingQuests(complete=False),
            LevelingInventory(inventory_items),
        )
        if (decision.intent.value, decision.reason) != (self.last_leveling_intent, self.last_leveling_reason):
            self.last_leveling_intent = decision.intent.value
            self.last_leveling_reason = decision.reason
            self.logger.log_event(
                "leveling_decision",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                intent=decision.intent.value,
                reason=decision.reason,
                item=decision.item,
                character_name=self.current_character_name,
                current_level=self.current_level,
                goal_level=self.config.leveling.target_level,
            )
        return decision

    def _evaluate_quest_policy(
        self,
        state_snapshot: object,
        quest_section: dict[str, object],
        quest_data: dict[str, object],
    ) -> PolicyQuestDecision:
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
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    quests.append(PolicyQuest(None, None))
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
                kind = QuestObjectiveKind.COMBAT if target_names and kind_text == "combat" else QuestObjectiveKind.UNKNOWN
                quests.append(
                    PolicyQuest(
                        id=str(item.get("id") or "").strip() or None,
                        title=str(item.get("title") or "").strip() or None,
                        target_mobs=target_names,
                        locations=tuple(locations),
                        status=status,
                        objective_kind=kind,
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
            require_active_quest=self.config.leveling.auto_navigate_quest_targets,
            require_route_location=self.config.leveling.auto_navigate_quest_targets,
            expected_identity=expected_identity,
            max_snapshot_age_s=max(1.0, self.config.leveling.snapshot_stale_timeout_ms / 1000),
        )
        decision = policy.decide(
            character_name=self.current_character_name,
            current_level=self.current_level,
            current_xp=self.current_xp_percent,
            goal_level=self.config.leveling.target_level,
            quest_state=observed,
        )
        self._apply_quest_policy_decision(decision)
        return decision

    def _apply_quest_policy_decision(self, decision: PolicyQuestDecision) -> None:
        key = (
            decision.intent.value,
            decision.reason,
            decision.quest_id,
            decision.target_mobs,
            decision.locations,
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
                snapshot_id=decision.snapshot_id,
            )
        self._quest_policy_intent = decision.intent
        if decision.intent not in {QuestIntent.SELECT_QUEST, QuestIntent.NAVIGATE}:
            self._active_quest_id = None
            self._quest_target_names = ()
            self._quest_route_locations = ()
            self._quest_target_routes = {}
            return
        self._active_quest_id = decision.quest_id
        self._quest_target_names = decision.target_mobs
        self._quest_route_locations = decision.locations
        self._quest_target_routes = {
            target: decision.locations
            for target in decision.target_mobs
        }

    def _death_recovery_snapshot(self, death_data: dict[str, object], dead: bool | None) -> RecoverySnapshot:
        free_count = _optional_int(death_data.get("freeReviveOptionCount"))
        options: tuple[ReviveOption, ...] = ()
        if death_data.get("freeReviveAvailable") is True and free_count in {None, 1}:
            options = (ReviveOption("explicit_free_revive", free=True, safe=True, available=True),)
        state_marker = "dead" if dead is True else "alive" if dead is False else "unknown"
        snapshot_id = self._current_state_snapshot_id or (
            f"controller:{self.session.cycle_id}:{self.deaths_observed}:{state_marker}"
        )
        return RecoverySnapshot(
            snapshot_id=snapshot_id,
            captured_at=time.monotonic(),
            is_dead=dead,
            revive_options=options,
            activity=self.state_machine.state.value,
            location=(
                self._last_alive_location_name
                or self.current_location_name
                or self._quest_origin_location_name
                or self.current_page_kind
            ),
            quest=self._active_quest_id,
        )

    def _record_recovery_decision(self, decision: object) -> None:
        self.logger.log_event(
            "death_recovery_decision",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            decision=getattr(getattr(decision, "decision", None), "value", None),
            reason=getattr(decision, "reason", None),
            option_id=getattr(decision, "option_id", None),
            deaths_observed=self.deaths_observed,
        )

    def _handle_leveling_death(self, death_data: object) -> bool:
        if not isinstance(death_data, dict):
            return self.state_machine.state in {GameState.DEAD, GameState.REVIVE_PENDING}
        dead = death_data.get("dead") if isinstance(death_data.get("dead"), bool) else None
        if dead is True:
            if not self._death_latched:
                previous_activity = self.state_machine.state.value
                previous_location = (
                    self._last_alive_location_name
                    or self.current_location_name
                    or self._quest_origin_location_name
                    or self.current_page_kind
                )
                event_id = self._current_state_snapshot_id or f"death:{self.session.cycle_id}:{self.deaths_observed + 1}"
                self._death_latched = True
                self.deaths_observed += 1
                self._death_recovery_policy.record_death(event_id)
                self._death_checkpoint = RecoveryCheckpoint(
                    activity=previous_activity,
                    location=previous_location,
                    quest=self._active_quest_id,
                    snapshot_id=event_id,
                )
                self._revive_attempted_for_current_death = False
                self._revive_requested_monotonic = None
                self.session.reset_cycle_attempt()
                self.logger.log_event(
                    "character_death_observed",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    deaths_observed=self.deaths_observed,
                    max_deaths=self.config.leveling.max_deaths_per_session,
                    checkpoint_activity=previous_activity,
                    checkpoint_location=previous_location,
                    checkpoint_quest=self._active_quest_id,
                )
            recovery = self._death_recovery_policy.decide(
                self._death_recovery_snapshot(death_data, True),
                checkpoint=self._death_checkpoint,
            )
            self._record_recovery_decision(recovery)
            if recovery.decision is RecoveryDecision.STOP_UNSAFE:
                return self._stop_leveling_unsafe(f"death_recovery:{recovery.reason}")
            if self.state_machine.state not in {GameState.DEAD, GameState.REVIVE_PENDING}:
                self._safe_transition(GameState.DEAD, reason="character_death_observed")
            if self.state_machine.state == GameState.REVIVE_PENDING:
                timeout_ms = max(1000, int(self.config.leveling.revive_verify_timeout_ms))
                requested_at = self._revive_requested_monotonic or time.monotonic()
                if (time.monotonic() - requested_at) * 1000 >= timeout_ms:
                    return self._stop_leveling_unsafe("free_revive_not_confirmed")
                return True
            if self._revive_attempted_for_current_death or recovery.decision is RecoveryDecision.WAIT_CONFIRMATION:
                return True
            if recovery.decision is not RecoveryDecision.REVIVE:
                return self._stop_leveling_unsafe(f"death_recovery_unexpected:{recovery.reason}")
            if not self.config.leveling.free_revive_only:
                return self._stop_leveling_unsafe("non_free_revive_policy_forbidden")
            request = ActionRequest(
                "revive_free",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "expected_character": self.current_character_name
                    or self._bound_character_name
                    or self.config.leveling.required_character_name,
                    "verify_delay_ms": 1000,
                    "revive_option_id": recovery.option_id,
                },
            )
            self._revive_attempted_for_current_death = True
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("free_revive_action_rejected")
            self._revive_requested_monotonic = time.monotonic()
            self._safe_transition(GameState.REVIVE_PENDING, reason="free_revive_submitted")
            return True
        if self.state_machine.state == GameState.REVIVE_PENDING:
            if dead is not False:
                timeout_ms = max(1000, int(self.config.leveling.revive_verify_timeout_ms))
                requested_at = self._revive_requested_monotonic or time.monotonic()
                if (time.monotonic() - requested_at) * 1000 >= timeout_ms:
                    return self._stop_leveling_unsafe("free_revive_result_ambiguous")
                return True
            recovery = self._death_recovery_policy.decide(
                self._death_recovery_snapshot(death_data, False),
                checkpoint=self._death_checkpoint,
                revived=True,
            )
            self._record_recovery_decision(recovery)
            if recovery.decision is RecoveryDecision.STOP_UNSAFE:
                return self._stop_leveling_unsafe(f"death_recovery:{recovery.reason}")
            if recovery.decision in {RecoveryDecision.RESTORE_CHECKPOINT, RecoveryDecision.COMPLETE}:
                return self._complete_revive_recovery("free_revive_confirmed")
            return True
        if self.state_machine.state == GameState.DEAD and dead is False:
            return self._complete_revive_recovery("external_revive_confirmed")
        if dead is False:
            self._death_latched = False
        return False

    def _complete_revive_recovery(self, reason: str) -> bool:
        self._death_latched = False
        self._revive_attempted_for_current_death = False
        self._revive_requested_monotonic = None
        self._safe_transition(GameState.ROUTE_RECOVERY, reason=reason)
        checkpoint_location = (
            str(self._death_checkpoint.location or "").strip()
            if self._death_checkpoint is not None
            else ""
        )
        if (
            _is_semantic_location_name(checkpoint_location)
            and not _same_location_name(self.current_location_name, checkpoint_location)
        ):
            request = ActionRequest(
                "open_location_navigator",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "reason": "post_revive_location_route",
                    "checkpoint_location": checkpoint_location,
                },
            )
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("post_revive_location_navigator_open_failed")
            self._navigator_target_name = checkpoint_location
            self._navigator_opened_monotonic = time.monotonic()
            self._navigator_client_id = None
            self._navigator_requires_target_selection = True
            self._route_recovery_kind = "post_revive_location"
            self._route_go_submitted_monotonic = None
            self._safe_transition(GameState.NAVIGATOR_PENDING, reason="post_revive_location_navigator_opened")
            self.logger.log_event(
                "post_revive_route_recovery_started",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                deaths_observed=self.deaths_observed,
                checkpoint_activity=self._death_checkpoint.activity if self._death_checkpoint else None,
                checkpoint_location=checkpoint_location,
                checkpoint_quest=self._death_checkpoint.quest if self._death_checkpoint else None,
                route_kind=self._route_recovery_kind,
            )
            return True
        if (
            self.config.leveling.auto_navigate_quest_targets
            and self._death_checkpoint is not None
            and self._death_checkpoint.quest
        ):
            request = ActionRequest(
                "open_quests",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "reason": "post_revive_quest_route_recovery",
                    "checkpoint_quest": self._death_checkpoint.quest,
                    "checkpoint_location": self._death_checkpoint.location,
                },
            )
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("post_revive_quest_open_failed")
            self._quest_refresh_requested_monotonic = time.monotonic()
            self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason="post_revive_quest_opened")
            self.logger.log_event(
                "post_revive_route_recovery_started",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                deaths_observed=self.deaths_observed,
                checkpoint_activity=self._death_checkpoint.activity,
                checkpoint_location=self._death_checkpoint.location,
                checkpoint_quest=self._death_checkpoint.quest,
            )
            return True
        request = ActionRequest(
            "open_hunt",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": "post_revive_route_recovery"},
        )
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe("post_revive_hunt_open_failed")
        self._reset_location_context()
        self._search_pause_until_monotonic = (
            time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
        )
        self._safe_transition(GameState.LOCATION_SEARCH, reason="post_revive_hunt_opened")
        self.logger.log_event(
            "post_revive_route_recovered",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            deaths_observed=self.deaths_observed,
            reason=reason,
        )
        return True

    def _stop_leveling_unsafe(self, reason: str) -> bool:
        self.last_error_reason = reason
        self.logger.log_event(
            "session_stopped",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            reason=reason,
            character_name=self.current_character_name,
            current_level=self.current_level,
            deaths_observed=self.deaths_observed,
        )
        self._safe_transition(GameState.STOPPED, reason=reason)
        return True

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

    def _effective_target_levels(self) -> tuple[int, ...]:
        configured = tuple(int(level) for level in self.config.target.allowed_levels if int(level) > 0)
        if configured:
            return configured
        if not self.config.leveling.enabled or self.current_level is None:
            return ()
        levels = {
            self.current_level + int(offset)
            for offset in self.config.leveling.auto_target_level_offsets
            if self.current_level + int(offset) > 0
        }
        return tuple(sorted(levels))

    def _effective_target_names(self) -> tuple[str, ...]:
        configured = tuple(name for name in self.config.target.allowed_names if name)
        if configured:
            return configured
        if self.config.leveling.auto_navigate_quest_targets:
            return self._quest_target_names
        return ()

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
            "quest_policy_intent": None if self._quest_policy_intent is None else self._quest_policy_intent.value,
            "quest_target_names": list(self._quest_target_names),
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

    def _handle_location_frame(self, frame: np.ndarray) -> None:
        if not self.config.dry_run:
            if not self._resources_allow_search(frame):
                return
            if self._maybe_start_quest_refresh():
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

    def _maybe_start_quest_refresh(self) -> bool:
        config = self.config.leveling
        if not config.enabled or int(config.quest_refresh_every_cycles) <= 0:
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
            if self.config.leveling.auto_navigate_quest_targets:
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

    def _handle_quest_refresh(self) -> None:
        started = self._quest_refresh_requested_monotonic or time.monotonic()
        if self.current_page_kind == "quests":
            if self.config.leveling.auto_navigate_quest_targets:
                if self._quest_policy_intent is QuestIntent.SELECT_QUEST:
                    self._finish_quest_refresh_to_hunt("quest_target_already_local")
                    return
                if self._quest_policy_intent is not QuestIntent.NAVIGATE:
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
                request = ActionRequest(
                    "open_quest_navigator",
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    dry_run=self.config.dry_run,
                    metadata={"reason": "quest_location_route", "target": target},
                )
                if not self.action_executor.execute(request):
                    self._stop_leveling_unsafe("quest_navigator_open_failed")
                    return
                self._navigator_target_name = target
                self._navigator_opened_monotonic = time.monotonic()
                self._navigator_client_id = None
                self._navigator_requires_target_selection = False
                self._route_recovery_kind = "quest_location"
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
        interval = max(1, int(self.config.leveling.quest_refresh_every_cycles))
        self._next_quest_refresh_cycle = self.session.completed_cycles + interval
        self._quest_refresh_requested_monotonic = None
        self._navigator_target_name = None
        self._navigator_opened_monotonic = None
        self._navigator_client_id = None
        self._navigator_requires_target_selection = False
        self._route_recovery_kind = None
        self._route_go_submitted_monotonic = None
        self._reset_location_context()
        self._search_pause_until_monotonic = (
            time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
        )
        self._safe_transition(GameState.LOCATION_SEARCH, reason=reason)
        return True

    def _handle_navigator_pending(self) -> None:
        started = self._navigator_opened_monotonic or time.monotonic()
        timeout_ms = max(1000, int(self.config.leveling.navigator_timeout_ms))
        child = self._find_navigator_client()
        if child is None:
            if (time.monotonic() - started) * 1000 >= timeout_ms:
                self._stop_leveling_unsafe("navigator_child_timeout")
            return
        self._navigator_client_id = str(child.get("client_id") or "") or None
        if not self._navigator_client_id:
            return
        if self._navigator_requires_target_selection:
            request = ActionRequest(
                "navigator_select_target",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "target": self._navigator_target_name or "",
                    "navigator_client_id": self._navigator_client_id,
                    "search_delay_ms": 250,
                    "route_delay_ms": 350,
                    "reason": self._route_recovery_kind,
                },
            )
            if not self.action_executor.execute(request):
                self._stop_leveling_unsafe("navigator_target_selection_failed_or_ambiguous")
                return
            self._navigator_requires_target_selection = False
            return
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "navigator_snapshot",
                timeout_s=2.5,
                client_id=self._navigator_client_id,
            )
        except Exception as exc:
            if (time.monotonic() - started) * 1000 >= timeout_ms:
                self._stop_leveling_unsafe(f"navigator_snapshot_error:{exc}")
            return
        if not result.ok:
            if (time.monotonic() - started) * 1000 >= timeout_ms:
                self._stop_leveling_unsafe("navigator_snapshot_timeout")
            return
        try:
            snapshot = json.loads(result.message)
        except json.JSONDecodeError:
            snapshot = None
        if not isinstance(snapshot, dict):
            return
        route_decision = validate_navigator_route(
            snapshot,
            self._navigator_target_name or "",
            max_transitions=max(1, int(self.config.leveling.navigator_max_transitions)),
            max_snapshot_age_s=max(1.0, self.config.leveling.navigator_timeout_ms / 1000),
        )
        self.logger.log_event(
            "navigator_route_decision",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            action=route_decision.action.value,
            reason=route_decision.reason,
            target=route_decision.target,
            route_transitions=route_decision.route_transitions,
            snapshot_id=route_decision.snapshot_id,
            navigator_client_id=self._navigator_client_id,
        )
        if route_decision.action is RouteAction.ARRIVED:
            self._finish_quest_refresh_to_hunt("navigator_target_current_location")
            return
        if route_decision.action is RouteAction.REFRESH:
            if (time.monotonic() - started) * 1000 >= timeout_ms:
                self._stop_leveling_unsafe(f"navigator_route_refresh_timeout:{route_decision.reason}")
            return
        if route_decision.action is not RouteAction.MOVE:
            self._stop_leveling_unsafe(f"navigator_route_unsafe:{route_decision.reason}")
            return
        request = ActionRequest(
            "navigator_go",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "target": self._navigator_target_name or "",
                "navigator_client_id": self._navigator_client_id,
                "route_transitions": route_decision.route_transitions,
                "route_snapshot_id": route_decision.snapshot_id,
            },
        )
        if not self.action_executor.execute(request):
            self._stop_leveling_unsafe("navigator_go_failed_or_ambiguous")
            return
        self._route_go_submitted_monotonic = time.monotonic()
        self._safe_transition(GameState.ROUTE_RECOVERY, reason="navigator_go_submitted")

    def _find_navigator_client(self) -> dict[str, object] | None:
        if not self.browser_client_id:
            return None
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        injector = global_browser_injector()
        parent = injector.client_snapshot(self.browser_client_id)
        parent_tab_id = parent.get("tab_id")
        parent_profile = str(parent.get("profile_id") or "")
        if parent_tab_id is None or not parent_profile:
            return None
        candidates = [
            client
            for client in injector.client_snapshots(within_s=5.0)
            if client.get("client_seen")
            and client.get("version_ok")
            and client.get("opener_tab_id") == parent_tab_id
            and str(client.get("profile_id") or "") == parent_profile
            and "/navigator.php" in str(client.get("href") or "")
        ]
        return candidates[0] if candidates else None

    def _handle_route_recovery(self) -> None:
        submitted = self._route_go_submitted_monotonic
        if submitted is None:
            return
        elapsed_ms = (time.monotonic() - submitted) * 1000
        if elapsed_ms < max(500, int(self.config.leveling.route_settle_ms)):
            return
        snapshot = self._state_snapshot_via_injector(force=True)
        sections = snapshot.get("sections") if isinstance(snapshot, dict) else None
        location_section = sections.get("location") if isinstance(sections, dict) else None
        location = location_section.get("data") if isinstance(location_section, dict) else None
        page_kind = str(location.get("pageKind") or "") if isinstance(location, dict) else ""
        if page_kind in {"area", "hunt", "main"}:
            self.current_page_kind = page_kind
            self.current_location_name = str(location.get("semanticName") or "") or None
            self.logger.log_event(
                "navigator_route_confirmed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                page_kind=page_kind,
                location_name=self.current_location_name,
                target=self._navigator_target_name,
                elapsed_ms=elapsed_ms,
            )
            self._finish_quest_refresh_to_hunt("navigator_route_confirmed")
            return
        timeout_ms = max(
            int(self.config.leveling.navigator_timeout_ms),
            int(self.config.leveling.route_settle_ms) + 1000,
        )
        if elapsed_ms >= timeout_ms:
            self._stop_leveling_unsafe("navigator_route_result_unconfirmed")

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
        allowed_levels = self._effective_target_levels()
        allowed_names = self._effective_target_names()
        if self.config.leveling.enabled and not allowed_levels:
            self.logger.log_event(
                "js_visible_target_blocked",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="target_level_unknown",
                frame_hash=self._last_frame_hash,
            )
            return False
        metadata: dict[str, object] = {
            "frame_hash": self._last_frame_hash,
            "confirmed": 1,
            "margin": 35,
        }
        if allowed_levels:
            metadata["allowed_levels"] = list(allowed_levels)
        if allowed_names:
            metadata["names"] = list(allowed_names)
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
            allowed_levels=list(allowed_levels),
            allowed_names=list(allowed_names),
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

            allowed_levels = self._effective_target_levels()
            allowed_names = self._effective_target_names()
            result = global_browser_injector().execute(
                "visible_hunt_targets",
                {"margin": 35, "allowedLevels": list(allowed_levels), "names": list(allowed_names)},
                timeout_s=2.5,
                client_id=self.browser_client_id,
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
                return self._last_battle_snapshot_cache
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
        if not isinstance(parsed, dict):
            return None
        self._last_battle_snapshot_cache = parsed
        self._last_battle_snapshot_success_monotonic = time.monotonic()
        return parsed

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


def _normalize_phrase(value: object) -> str:
    return " ".join(re.findall(r"[0-9a-zа-я]+", str(value or "").casefold().replace("ё", "е")))


def _snapshot_epoch_seconds(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _normalized_phrase_matches(observed: object, expected: object) -> bool:
    observed_value = _normalize_phrase(observed)
    expected_value = _normalize_phrase(expected)
    if not observed_value or not expected_value:
        return False
    return observed_value == expected_value or observed_value in expected_value or expected_value in observed_value


def _same_location_name(observed: object, expected: object) -> bool:
    observed_value = _normalize_phrase(observed)
    expected_value = _normalize_phrase(expected)
    return bool(observed_value and expected_value and observed_value == expected_value)


def _is_semantic_location_name(value: object) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    normalized = _normalize_phrase(text)
    if len(normalized) < 3 or len(text) > 180:
        return False
    if normalized in {"area", "hunt", "main", "battle", "inventory", "quests", "navigator", "other"}:
        return False
    return not bool(re.match(r"^(?:https?://|/|[a-z_]+\.php(?:\?|$))", text, flags=re.IGNORECASE))


def _clean_quest_route_label(value: object) -> str | None:
    label = re.sub(r"проложить\s+путь", "", str(value or ""), flags=re.IGNORECASE)
    label = re.sub(r"\s+", " ", label).strip(" \t\r\n,.;:-")
    if len(_normalize_phrase(label)) < 3:
        return None
    return label[:180]


def _extract_quest_combat_targets(objective: str, *, configured_names: Iterable[str] = ()) -> tuple[str, ...]:
    text = re.sub(r"\s+", " ", str(objective or "")).strip()
    if not text:
        return ()
    normalized_objective = _normalize_phrase(text)
    targets: list[str] = []
    for configured in configured_names:
        name = str(configured or "").strip()
        if name and _normalize_phrase(name) in normalized_objective and name not in targets:
            targets.append(name)
    patterns = (
        r"\b(?:убейте|убить|уничтожьте|уничтожить|истребите|истребить|одолейте|победите)\s+(?:не\s+менее\s+)?(?:\d+\s+)?(?P<name>[a-zа-яё][^,.;:]{2,90}?)(?=\s+(?:в|на|у|и|или|после|затем|чтобы|для|из|до|вернитесь)\b|[,.;:]|$)",
        r"\b(?:нападите|напасть|охотьтесь)\s+на\s+(?:\d+\s+)?(?P<name>[a-zа-яё][^,.;:]{2,90}?)(?=\s+(?:в|на|у|и|или|после|затем|чтобы|для|из|до|вернитесь)\b|[,.;:]|$)",
    )
    generic = {"монстров", "монстра", "противников", "противника", "врагов", "врага", "существ", "существо"}
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = re.sub(r"\s+", " ", match.group("name")).strip(" \t\r\n,.;:-")
            candidate = re.sub(r"\s+\d+\s*/\s*\d+$", "", candidate).strip()
            normalized = _normalize_phrase(candidate)
            if len(normalized) < 4 or normalized in generic or candidate in targets:
                continue
            targets.append(candidate[:120])
    return tuple(targets)


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


def _parse_runtime_int_list(value: object) -> list[int]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values: Iterable[object] = value.replace(",", " ").split()
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = (value,)
    output: list[int] = []
    for raw in raw_values:
        item = _as_int(raw, -1)
        if item >= 0 and item not in output:
            output.append(item)
    return output


def _parse_runtime_name_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values: Iterable[object] = value.replace("\n", ",").split(",")
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = (value,)
    output: list[str] = []
    for raw in raw_values:
        item = str(raw).strip()
        if item and item not in output:
            output.append(item)
    return output


def _apply_runtime_overrides(config: AutomationConfig, overrides: dict[str, object] | None) -> AutomationConfig:
    if not overrides:
        return config
    data = to_plain_dict(config)
    resources = data.setdefault("resources", {})
    item_recovery = data.setdefault("item_recovery", {})
    battle_item_recovery = data.setdefault("battle_item_recovery", {})
    leveling = data.setdefault("leveling", {})
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
    if "targetNames" in overrides:
        target["allowed_names"] = _parse_runtime_name_list(overrides.get("targetNames"))

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

    if "battleItemRecoveryEnabled" in overrides:
        battle_item_recovery["enabled"] = bool(overrides.get("battleItemRecoveryEnabled"))
    if "battleHealthPotionThreshold" in overrides:
        battle_item_recovery["health_use_when_below_percent"] = max(
            0.0,
            _as_float(
                overrides.get("battleHealthPotionThreshold"),
                float(battle_item_recovery.get("health_use_when_below_percent", 35)),
            ),
        )
    if "battleProwessPotionThreshold" in overrides:
        battle_item_recovery["prowess_use_when_below_percent"] = max(
            0.0,
            _as_float(
                overrides.get("battleProwessPotionThreshold"),
                float(battle_item_recovery.get("prowess_use_when_below_percent", 15)),
            ),
        )
    if "battleHealthPotionSlots" in overrides:
        battle_item_recovery["health_slots"] = _parse_runtime_int_list(overrides.get("battleHealthPotionSlots"))
    if "battleProwessPotionSlots" in overrides:
        battle_item_recovery["prowess_slots"] = _parse_runtime_int_list(overrides.get("battleProwessPotionSlots"))
    if "battleHealthPotionNames" in overrides:
        battle_item_recovery["health_names"] = _parse_runtime_name_list(overrides.get("battleHealthPotionNames"))
    if "battleProwessPotionNames" in overrides:
        battle_item_recovery["prowess_names"] = _parse_runtime_name_list(overrides.get("battleProwessPotionNames"))
    if "battleDamageBoostEnabled" in overrides:
        battle_item_recovery["damage_boost_enabled"] = bool(overrides.get("battleDamageBoostEnabled"))
    if "battleDamageBoostSlots" in overrides:
        battle_item_recovery["damage_boost_slots"] = _parse_runtime_int_list(overrides.get("battleDamageBoostSlots"))
    if "battleDamageBoostNames" in overrides:
        battle_item_recovery["damage_boost_names"] = _parse_runtime_name_list(overrides.get("battleDamageBoostNames"))
    if "battleItemCooldownMs" in overrides:
        battle_item_recovery["cooldown_ms"] = max(
            0,
            _as_int(overrides.get("battleItemCooldownMs"), int(battle_item_recovery.get("cooldown_ms", 3000))),
        )
    if "battleItemMaxUsesPerBattle" in overrides:
        battle_item_recovery["max_uses_per_battle"] = max(
            1,
            _as_int(overrides.get("battleItemMaxUsesPerBattle"), int(battle_item_recovery.get("max_uses_per_battle", 1))),
        )

    if "goalLevel" in overrides:
        goal_level = _as_int(overrides.get("goalLevel"), 0)
        leveling["enabled"] = goal_level > 0
        leveling["target_level"] = goal_level if goal_level > 0 else None
    if "requiredCharacterName" in overrides:
        leveling["required_character_name"] = str(overrides.get("requiredCharacterName") or "").strip()
    if "maxDeathsPerSession" in overrides:
        leveling["max_deaths_per_session"] = max(
            0,
            _as_int(overrides.get("maxDeathsPerSession"), int(leveling.get("max_deaths_per_session", 3))),
        )
    if "targetLocationName" in overrides:
        leveling["target_location_name"] = str(overrides.get("targetLocationName") or "").strip()
    if "autoNavigateQuestTargets" in overrides:
        leveling["auto_navigate_quest_targets"] = bool(overrides.get("autoNavigateQuestTargets"))

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
        "character_name": controller.current_character_name,
        "bound_character_name": controller._bound_character_name,
        "current_level": controller.current_level,
        "current_xp_percent": controller.current_xp_percent,
        "goal_level": controller.config.leveling.target_level,
        "page_kind": controller.current_page_kind,
        "location_name": controller.current_location_name,
        "deaths_observed": controller.deaths_observed,
        "quest_target_names": list(controller._quest_target_names),
        "quest_route_locations": list(controller._quest_route_locations),
        "effective_target_levels": list(controller._effective_target_levels()),
        "leveling_intent": controller.last_leveling_intent,
        "leveling_reason": controller.last_leveling_reason,
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
        target_allowed_names=options.target_allowed_names,
        goal_level=options.goal_level,
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
        startup_death_blocked = controller._observe_death_guard(force=True) if options.live else False
        if options.live and options.open_hunt_on_start and not startup_death_blocked:
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
                controller.write_checkpoint()
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
            controller.write_checkpoint(force=True)
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
                target_allowed_names=tuple(getattr(args, "target_name", None) or ()) or None,
                goal_level=getattr(args, "goal_level", None),
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
    api = AutomationControlApi(server, default_config=args.config, allow_live=bool(args.live))
    server.set_api_handler(api.handle)
    print(
        json.dumps(
            {
                "ok": True,
                "message": "control_server_started",
                "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
                "config": args.config,
                "live_allowed": bool(args.live),
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


def command_injector_state_snapshot(args: argparse.Namespace) -> int:
    from src.antibot_cv.automation.browser_injector import DEFAULT_INJECTOR_HOST, global_browser_injector

    include = [value.strip() for item in args.include for value in str(item).split(",") if value.strip()]
    server = global_browser_injector()
    server.start()
    result = server.execute(
        "state_snapshot",
        {"include": include},
        timeout_s=max(0.1, float(args.timeout)),
        client_id=getattr(args, "client_id", None),
    )
    payload: dict[str, object] = {
        "ok": result.ok,
        "server_url": f"http://{DEFAULT_INJECTOR_HOST}:{server.port}",
        "client_id": result.client_id or getattr(args, "client_id", None) or server.last_client_id,
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


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    run.add_argument("--target-name", action="append", default=[], help="Allowed mob name; repeat for several names")
    run.add_argument("--goal-level", type=int, default=None, help="Stop after the character reaches this level")
    run.add_argument("--start-delay", type=float, default=None)
    run.add_argument("--activate-app", default=None)
    run.add_argument("--no-activate-app", action="store_true")
    run.add_argument("--hotkeys", action=argparse.BooleanOptionalAction, default=False)
    run.add_argument("--open-hunt-on-start", action=argparse.BooleanOptionalAction, default=False)
    run.add_argument("--client-id", default=None, help="Target browser injector client id when several Chrome windows are connected")
    run.set_defaults(func=command_run)

    control_server = subparsers.add_parser("control-server")
    control_server.add_argument("--config", default="config/automation.local.json")
    control_server.add_argument("--live", action="store_true", help="Explicitly allow live runs requested by the extension")
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

    injector_state_snapshot = subparsers.add_parser("injector-state-snapshot")
    injector_state_snapshot.add_argument(
        "--include",
        nargs="*",
        default=["player", "location", "deathRevive", "battle", "hunt", "quests", "shopInventory"],
    )
    injector_state_snapshot.add_argument("--timeout", type=float, default=10.0)
    _add_client_id_arg(injector_state_snapshot)
    injector_state_snapshot.set_defaults(func=command_injector_state_snapshot)

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
