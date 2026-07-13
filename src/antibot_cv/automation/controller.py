from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable

import numpy as np

from src.antibot_cv.automation.actions import (
    ActionExecutor,
    ActionRequest,
    BlockedActionSink,
    DryRunActionSink,
    LiveMacActionSink,
    ReplayActionSink,
)
from src.antibot_cv.automation.combat_runtime import CombatRuntimeMixin
from src.antibot_cv.automation.config import AutomationConfig, to_plain_dict
from src.antibot_cv.automation.death_recovery import DeathRecoveryPolicy, RecoveryCheckpoint
from src.antibot_cv.automation.desktop_runtime import (
    activate_configured_app as _activate_configured_app,
    show_preview as _show_preview,
)
from src.antibot_cv.automation.leveling_runtime import LevelingRuntimeMixin
from src.antibot_cv.automation.navigation_runtime import NavigationRuntimeMixin
from src.antibot_cv.automation.preflight import run_preflight
from src.antibot_cv.automation.quest_runtime import QuestRuntimeMixin
from src.antibot_cv.automation.recovery_items_runtime import RecoveryItemsRuntimeMixin
from src.antibot_cv.automation.resource_runtime import ResourceRuntimeMixin
from src.antibot_cv.automation.safety import HotkeyController, SafetyGuard
from src.antibot_cv.automation.screen_runtime import ScreenRuntimeMixin
from src.antibot_cv.automation.session import SessionState
from src.antibot_cv.automation.state_machine import GameState, InvalidTransitionError, StateMachine
from src.antibot_cv.detection.ability import AbilityBarDetector
from src.antibot_cv.detection.attack import AttackButtonDetector
from src.antibot_cv.detection.battle import BattleDetector
from src.antibot_cv.detection.battle_end import BattleEndDetector
from src.antibot_cv.detection.resources import ResourceDetector
from src.antibot_cv.detection.statistics import StatisticsDetector
from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.entity_detection.green_labels import GreenLabelDetector
from src.antibot_cv.entity_detection.target_locator import LocatedTarget, TargetLocator
from src.antibot_cv.entity_detection.tracker import EntityTracker
from src.antibot_cv.telemetry.event_logger import EventLogger, frame_hash
from src.antibot_cv.telemetry.session_summary import LatencyTracker, write_session_summary
from src.antibot_cv.viewport.coordinates import CoordinateMapper, MonitorGeometry, Rect
from src.antibot_cv.viewport.direction_pad import DirectionPadNavigator
from src.antibot_cv.viewport.scrollbar import ScrollbarNavigator


LIVE_APP_REACTIVATE_INTERVAL_S = 2.0


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


class AutomationController(
    ScreenRuntimeMixin,
    CombatRuntimeMixin,
    QuestRuntimeMixin,
    LevelingRuntimeMixin,
    RecoveryItemsRuntimeMixin,
    ResourceRuntimeMixin,
    NavigationRuntimeMixin,
):
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
        self._init_quest_runtime()
        self.deaths_observed = 0
        self._death_latched = False
        self._active_recovery_id: str | None = None
        self._recovery_phase_events: list[str] = []
        self._revive_requested_monotonic: float | None = None
        self._revive_attempted_for_current_death = False
        self._post_revive_recovery_started_monotonic: float | None = None
        self._post_revive_recovery_attempts = 0
        self._post_revive_resume_reason: str | None = None
        self._last_checkpoint_monotonic: float | None = None
        self.last_leveling_intent: str | None = None
        self.last_leveling_reason: str | None = None
        self._navigator_target_name: str | None = None
        self._navigator_target_kind = "location"
        self._navigator_opened_monotonic: float | None = None
        self._navigator_client_id: str | None = None
        self._navigator_existing_client_ids: set[str] = set()
        self._navigator_requires_target_selection = False
        self._route_recovery_kind: str | None = None
        self._route_go_submitted_monotonic: float | None = None
        self._route_destination_name: str | None = None
        self._route_destination_id: str | None = None
        self._route_expected_transitions: int | None = None
        self._route_step_submitted_from_id: str | None = None
        self._route_step_submitted_monotonic: float | None = None
        self._route_resume_target_name: str | None = None
        self._configured_route_completed_target: str | None = None
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
        elif state == GameState.POST_REVIVE_RECOVERY:
            self._handle_post_revive_recovery(frame)
        elif state == GameState.QUEST_REFRESH_PENDING:
            self._handle_quest_refresh()
        elif state == GameState.NAVIGATOR_PENDING:
            self._handle_navigator_pending()
        elif state == GameState.ROUTE_RECOVERY:
            self._handle_route_recovery()
        self._recover_stuck_state(frame)
        return self.state_machine.state


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


def build_parser() -> argparse.ArgumentParser:
    from src.antibot_cv.automation.controller_cli import build_parser as cli_build_parser

    return cli_build_parser()


def main(argv: list[str] | None = None) -> int:
    from src.antibot_cv.automation.controller_cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
