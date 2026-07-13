from __future__ import annotations

import json
import subprocess
import sys
import time

import cv2

from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.automation.controller import AutomationController, _apply_runtime_overrides
from src.antibot_cv.automation.actions import DryRunActionSink
from src.antibot_cv.automation.death_recovery import RecoveryCheckpoint
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.detection.attack import AttackButtonDetection
from src.antibot_cv.detection.battle_end import BattleEndDetection
from src.antibot_cv.detection.resources import ResourceBarStatus, ResourceStatus
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger
from src.antibot_cv.viewport.coordinates import Point, Rect
from tests.conftest import (
    battle_end_frame,
    battle_frame,
    blank_frame,
    location_map_frame,
    resource_frame,
    statistics_frame,
    statistics_red_frame,
    target_frame,
)


def _game_shell_frame():
    frame = blank_frame(1280, 720)
    frame[:, :] = (205, 185, 150)
    for idx in range(9):
        x1 = 715 + idx * 55
        cv2.rectangle(frame, (x1, 88), (x1 + 45, 132), (40 + idx * 15, 180, 220), -1)
    for idx in range(6):
        y = 140 + idx * 48
        cv2.circle(frame, (1242, y), 18, (0, 180, 230), -1)
        cv2.circle(frame, (1242, y), 10, (0, 80, 220), -1)
    return frame


def _complete_post_revive_resource_gate(controller: AutomationController) -> None:
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 100.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 100.0, 1.0),
    )
    controller._last_rest_check_monotonic = None
    controller._handle_post_revive_recovery(blank_frame())


def test_dry_run_full_synthetic_cycle(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    controller.start()
    for frame in [target_frame(), battle_frame(), battle_end_frame(), statistics_frame(), location_map_frame()]:
        controller.process_frame(frame)
    assert controller.session.completed_cycles == 1
    assert controller.session.ability4_actions == 1
    assert controller.session.exit_actions == 1
    assert controller.session.hunt_actions == 1
    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert any(event["event_type"] == "cycle_completed" for event in logger.events)


def test_direct_map_return_after_exit_completes_cycle_without_statistics(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    for frame in [target_frame(), battle_frame(), battle_end_frame(), location_map_frame()]:
        controller.process_frame(frame)

    assert controller.session.completed_cycles == 1
    assert controller.session.hunt_actions == 0
    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert any(
        event["event_type"] == "cycle_completed" and event["reason"] == "statistics_skipped"
        for event in logger.events
    )


def test_live_cycle_requires_confirmed_victory(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    config = AutomationConfig.from_dict(data)

    unknown = AutomationController(config, sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    unknown.state_machine.state = GameState.COOLDOWN
    unknown.session.new_battle()
    unknown.session.mark_battle_detected()
    unknown.session.mark_combat()
    unknown.session.mark_battle_outcome("unknown")
    unknown._complete_or_mark_incomplete_after_hunt_return(blank_frame(), reason="live_result")

    assert unknown.session.completed_cycles == 0
    assert unknown.session.incomplete_cycles == 1

    victory = AutomationController(config, sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    victory.state_machine.state = GameState.COOLDOWN
    victory.session.new_battle()
    victory.session.mark_battle_detected()
    victory.session.mark_combat()
    victory.session.mark_battle_outcome("victory")
    victory._complete_or_mark_incomplete_after_hunt_return(blank_frame(), reason="live_result")

    assert victory.session.completed_cycles == 1
    assert victory.session.incomplete_cycles == 0


def test_battle_end_target_like_label_does_not_confirm_hunt_return(test_config: AutomationConfig) -> None:
    import cv2

    frame = battle_end_frame()
    cv2.rectangle(frame, (75, 150), (185, 165), (0, 0, 220), -1)
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    controller.session.new_battle()
    controller.session.mark_battle_detected()
    controller.session.mark_ability4()
    controller._safe_transition(GameState.BATTLE_ACTIVE, reason="test")
    controller._safe_transition(GameState.WAIT_BATTLE_END, reason="test")

    controller.process_frame(frame)
    controller.process_frame(frame)

    assert controller.session.completed_cycles == 0
    assert not any(event["event_type"] == "cycle_completed" for event in logger.events)


def test_direct_map_return_from_battle_end_marks_incomplete_without_statistics(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    for frame in [battle_end_frame(), location_map_frame()]:
        controller.process_frame(frame)

    assert controller.session.completed_cycles == 0
    assert controller.session.incomplete_cycles == 1
    assert controller.session.hunt_actions == 0
    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert any(
        event["event_type"] == "cycle_incomplete" and event["reason"] == "statistics_skipped"
        for event in logger.events
    )


def test_game_shell_return_after_exit_opens_hunt_without_losing_cycle(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    for frame in [target_frame(), battle_frame(), battle_end_frame()]:
        controller.process_frame(frame)
    assert controller.state_machine.state == GameState.STATISTICS_WAIT
    assert controller.session.can_complete_cycle() is True

    controller.process_frame(_game_shell_frame())

    assert controller.state_machine.state == GameState.STATISTICS_WAIT
    assert controller.session.can_complete_cycle() is True
    assert any(event.get("action_type") == "open_hunt" for event in logger.events)

    controller.process_frame(location_map_frame())

    assert controller.session.completed_cycles == 1
    assert controller.state_machine.state == GameState.LOCATION_SEARCH


def test_stop_after_three_cycles(test_config: AutomationConfig) -> None:
    controller = AutomationController(test_config, sink_mode="replay", logger=InMemoryEventLogger())
    for _ in range(3):
        for frame in [target_frame(), battle_frame(), battle_end_frame(), statistics_frame(), location_map_frame()]:
            controller.process_frame(frame)
    assert controller.session.completed_cycles == 3
    assert controller.state_machine.state == GameState.STOPPED


def test_start_from_battle_screen_continues_cycle(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    for frame in [battle_frame(), battle_end_frame(), statistics_frame(), location_map_frame()]:
        controller.process_frame(frame)

    assert controller.session.completed_cycles == 1
    assert controller.session.targets_detected == 0
    assert controller.session.ability4_actions == 1
    assert controller.session.exit_actions == 1
    assert controller.session.hunt_actions == 1
    assert any(event["event_type"] == "screen_synced" and event["screen"] == "battle" for event in logger.events)


def test_start_from_battle_end_screen_continues_cycle(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    for frame in [battle_end_frame(), statistics_frame(), location_map_frame()]:
        controller.process_frame(frame)

    assert controller.session.completed_cycles == 0
    assert controller.session.incomplete_cycles == 1
    assert controller.session.ability4_actions == 0
    assert controller.session.exit_actions == 1
    assert controller.session.hunt_actions == 1
    assert any(event["event_type"] == "screen_synced" and event["screen"] == "battle_end" for event in logger.events)


def test_start_from_statistics_screen_returns_to_hunt(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    for frame in [statistics_frame(), location_map_frame()]:
        controller.process_frame(frame)

    assert controller.session.completed_cycles == 0
    assert controller.session.incomplete_cycles == 1
    assert controller.session.exit_actions == 0
    assert controller.session.hunt_actions == 1
    assert any(event["event_type"] == "screen_synced" and event["screen"] == "statistics" for event in logger.events)
    assert any(
        event["event_type"] == "replay_intended_action"
        and event["action_type"] == "click_hunt"
        and event.get("click_count") == 2
        for event in logger.events
    )


def test_start_from_red_statistics_screen_returns_to_hunt(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    for frame in [statistics_red_frame(), location_map_frame()]:
        controller.process_frame(frame)

    assert controller.session.completed_cycles == 0
    assert controller.session.incomplete_cycles == 1
    assert controller.session.hunt_actions == 1
    assert any(event["event_type"] == "screen_synced" and event["screen"] == "statistics" for event in logger.events)


def test_cooldown_waits_for_statistics_to_disappear(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    controller.process_frame(statistics_red_frame())
    assert controller.state_machine.state == GameState.COOLDOWN
    assert controller.session.completed_cycles == 0
    assert controller.session.hunt_actions == 1

    controller.process_frame(statistics_red_frame())
    assert controller.state_machine.state == GameState.COOLDOWN
    assert controller.session.completed_cycles == 0
    assert controller.session.hunt_actions == 1

    controller.process_frame(blank_frame())
    assert controller.session.completed_cycles == 0
    assert controller.session.incomplete_cycles == 0
    assert controller.state_machine.state == GameState.COOLDOWN
    assert any(event["event_type"] == "hunt_return_waiting" for event in logger.events)

    controller.process_frame(location_map_frame())
    assert controller.session.completed_cycles == 0
    assert controller.session.incomplete_cycles == 1
    assert controller.state_machine.state == GameState.LOCATION_SEARCH


def test_start_from_attack_button_clicks_attack(test_config: AutomationConfig) -> None:
    class StubAttackDetector:
        def detect(self, frame):
            return AttackButtonDetection(True, 0.95, Rect(10, 10, 100, 24), Point(25, 22))

    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    controller.attack_detector = StubAttackDetector()

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.BATTLE_WAIT
    assert controller.session.attack_actions == 1
    attack_click = next(event for event in logger.events if event.get("action_type") == "click_attack")
    assert attack_click["x_frame"] == 62.0
    assert any(event["event_type"] == "screen_synced" and event["screen"] == "attack_button" for event in logger.events)


def test_visible_target_takes_precedence_over_attack_false_positive(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    frame = target_frame()
    cv2.rectangle(frame, (170, 30), (260, 62), (0, 0, 190), -1)

    controller.process_frame(frame)

    assert controller.state_machine.state == GameState.BATTLE_WAIT
    assert controller.session.targets_detected == 1
    assert any(event.get("action_type") == "click_target" for event in logger.events)
    assert not any(event.get("action_type") == "click_attack" for event in logger.events)


def test_visible_target_takes_precedence_over_battle_end_false_positive(test_config: AutomationConfig) -> None:
    class StubBattleEndDetector:
        def detect(self, frame):
            return BattleEndDetection(True, 1.0, 1, Rect(20, 20, 80, 40), Rect(80, 160, 100, 35))

    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    controller.battle_end_detector = StubBattleEndDetector()

    controller.process_frame(target_frame())

    assert controller.state_machine.state == GameState.BATTLE_WAIT
    assert controller.session.targets_detected == 1
    assert any(event.get("action_type") == "click_target" for event in logger.events)
    assert not any(event.get("action_type") == "click_exit" for event in logger.events)
    assert not any(event.get("screen") == "battle_end" for event in logger.events)


def test_target_click_uses_double_click_to_trigger_canvas_attack(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    controller.process_frame(target_frame())

    target_clicks = [event for event in logger.events if event.get("action_type") == "click_target"]
    assert target_clicks
    assert target_clicks[0]["click_count"] == 2


def test_mid_state_statistics_screen_resyncs_to_hunt(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    controller.process_frame(target_frame())
    assert controller.state_machine.state == GameState.BATTLE_WAIT
    controller._last_screen_sync_monotonic = None

    controller.process_frame(statistics_frame())

    assert controller.state_machine.state == GameState.COOLDOWN
    assert controller.session.hunt_actions == 1
    assert controller.session.errors == 0
    assert any(event["event_type"] == "screen_synced" and event["screen"] == "statistics" for event in logger.events)
    assert any(event.get("action_type") == "click_hunt" for event in logger.events)


def test_mid_state_battle_end_screen_resyncs_and_exits(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    controller.process_frame(target_frame())
    assert controller.state_machine.state == GameState.BATTLE_WAIT
    controller._last_screen_sync_monotonic = None

    controller.process_frame(battle_end_frame())

    assert controller.state_machine.state == GameState.STATISTICS_WAIT
    assert controller.session.exit_actions == 1
    assert controller.session.errors == 0
    assert any(event["event_type"] == "screen_synced" and event["screen"] == "battle_end" for event in logger.events)
    assert any(event.get("action_type") == "click_exit" for event in logger.events)


def test_resting_state_battle_screen_resyncs_to_combat(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["resources"] = {
        "enabled": True,
        "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
        "health_min_percent": 35,
        "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
        "prowess_min_percent": 35,
        "recover_to_percent": 80,
        "rest_check_interval_ms": 0,
        "fail_open_if_missing": False,
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(resource_frame(health=20, prowess=90, include_target=True))
    assert controller.state_machine.state == GameState.RESTING
    controller._last_screen_sync_monotonic = None

    controller.process_frame(battle_frame())

    assert controller.state_machine.state == GameState.WAIT_BATTLE_END
    assert controller.session.ability4_actions == 1
    assert controller.session.errors == 0
    assert any(event["event_type"] == "screen_synced" and event["screen"] == "battle" for event in logger.events)


def test_unknown_screen_opens_game_main(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    frame = blank_frame()
    frame[:, :] = (60, 60, 60)

    controller.process_frame(frame)

    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert any(event.get("action_type") == "open_url" for event in logger.events)
    assert any(event.get("url") == "https://3kingdoms.ru/main.php" for event in logger.events)
    assert any(event["event_type"] == "unknown_screen_recovery" for event in logger.events)


def test_non_game_shell_page_opens_game_main(test_config: AutomationConfig) -> None:
    class CompleteResourceDetector:
        def detect(self, frame):
            class Status:
                complete = True

            return Status()

    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    controller.resource_detector = CompleteResourceDetector()
    frame = blank_frame()
    frame[:, :] = (210, 190, 160)

    controller.process_frame(frame)

    assert any(event.get("action_type") == "open_url" for event in logger.events)
    assert any(event.get("url") == "https://3kingdoms.ru/main.php" for event in logger.events)
    assert not any(event.get("action_type") == "click_return" for event in logger.events)


def test_game_shell_page_opens_hunt_frame_instead_of_reloading_main(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    frame = blank_frame(1280, 720)
    frame[:, :] = (205, 185, 150)
    for idx in range(9):
        x1 = 715 + idx * 55
        cv2.rectangle(frame, (x1, 88), (x1 + 45, 132), (40 + idx * 15, 180, 220), -1)
    for idx in range(6):
        y = 140 + idx * 48
        cv2.circle(frame, (1242, y), 18, (0, 180, 230), -1)
        cv2.circle(frame, (1242, y), 10, (0, 80, 220), -1)

    controller.process_frame(frame)

    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert any(event["event_type"] == "game_shell_recovery" for event in logger.events)
    assert any(event.get("action_type") == "open_hunt" for event in logger.events)
    assert not any(event.get("action_type") == "open_url" for event in logger.events)


def test_standalone_hunt_map_does_not_reopen_hunt(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    frame = blank_frame(640, 400)
    cv2.rectangle(frame, (150, 70), (510, 360), (45, 135, 45), -1)
    cv2.rectangle(frame, (500, 95), (508, 345), (0, 0, 180), -1)

    controller.process_frame(frame)

    assert not any(event.get("action_type") == "open_url" for event in logger.events)
    assert any(event["event_type"] == "viewport_move" for event in logger.events)


def test_hunt_map_with_visible_target_does_not_reopen_hunt(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    frame = blank_frame(640, 400)
    frame[:, :] = (190, 170, 140)
    cv2.rectangle(frame, (220, 170), (390, 186), (0, 255, 0), -1)

    controller.process_frame(frame)

    assert controller.state_machine.state == GameState.BATTLE_WAIT
    assert controller.session.targets_detected == 1
    assert any(event.get("action_type") == "click_target" for event in logger.events)
    assert not any(event.get("action_type") == "open_url" for event in logger.events)


def test_live_hunt_page_without_visible_target_does_not_reopen_hunt(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    class FailingActionSink:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            return False

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["target"] = {**data["target"], "allowed_levels": [3]}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    controller._resources_allow_search = lambda frame: True
    sink = FailingActionSink()
    controller.action_executor.sink = sink
    controller._visible_hunt_snapshot_via_injector = lambda: {
        "mainHref": "https://3kingdoms.ru/hunt.php",
        "hasHunt": True,
        "targets": [],
        "displayChildren": [],
    }

    controller._handle_location_frame(location_map_frame())

    assert [request.action_type for request in sink.requests] == ["attack_visible_target", "viewport_move"]
    assert sink.requests[-1].scan_direction == "NORTH"
    assert sink.requests[-1].metadata["use_js_hunt_direction"] is True
    assert not any(event.get("action_type") == "open_hunt" for event in logger.events)
    assert any(event["event_type"] == "live_hunt_target_waiting" for event in logger.events)


def test_live_attack_fails_closed_without_target_filter(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["target"] = {**data["target"], "allowed_levels": [], "allowed_names": []}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller._resources_allow_search = lambda frame: True

    assert controller._attack_visible_target_via_injector(location_map_frame()) is False
    assert sink.requests == []
    assert any(
        event["event_type"] == "js_visible_target_blocked"
        and event["reason"] == "target_filter_missing"
        for event in controller.logger.events
    )


def test_live_fight_page_without_visible_target_does_not_reopen_hunt(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    class FailingActionSink:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            return False

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["target"] = {**data["target"], "allowed_levels": [3]}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    controller._resources_allow_search = lambda frame: True
    sink = FailingActionSink()
    controller.action_executor.sink = sink
    controller._visible_hunt_snapshot_via_injector = lambda: {
        "mainHref": "https://3kingdoms.ru/fight.php?abc",
        "hasHunt": False,
        "targets": [],
        "displayChildren": [],
    }
    battle_probe_calls = []
    controller._battle_snapshot_via_injector = lambda force=False: battle_probe_calls.append(force) or None

    controller._handle_location_frame(location_map_frame())

    assert [request.action_type for request in sink.requests] == ["attack_visible_target"]
    assert battle_probe_calls == [True]
    assert not any(event.get("action_type") == "open_hunt" for event in logger.events)
    assert any(event["event_type"] == "live_fight_page_waiting" for event in logger.events)


def test_live_inactive_fight_page_without_visible_target_reopens_hunt(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    class FailingThenSuccessfulActionSink:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            return request.action_type == "open_hunt"

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["target"] = {**data["target"], "allowed_levels": [3]}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    controller._resources_allow_search = lambda frame: True
    sink = FailingThenSuccessfulActionSink()
    controller.action_executor.sink = sink
    controller._visible_hunt_snapshot_via_injector = lambda: {
        "mainHref": "https://3kingdoms.ru/fight.php?done",
        "hasHunt": False,
        "targets": [],
        "displayChildren": [],
    }
    controller._battle_snapshot_via_injector = lambda force=False: {
        "hasFight": False,
        "useSkillAvailable": False,
        "fightPath": "top.frames[1].frames[2]",
        "fightHref": "https://3kingdoms.ru/fight.php?done",
        "abilities": [],
    }

    controller._handle_location_frame(location_map_frame())

    assert [request.action_type for request in sink.requests] == ["attack_visible_target", "open_hunt"]
    assert any(event["event_type"] == "game_shell_recovery" for event in logger.events)


def test_live_area_error_does_not_reopen_hunt(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    class FailingActionSink:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            return False

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["target"] = {**data["target"], "allowed_levels": [3]}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    controller._resources_allow_search = lambda frame: True
    sink = FailingActionSink()
    controller.action_executor.sink = sink
    controller._visible_hunt_snapshot_via_injector = lambda: {
        "mainHref": "https://3kingdoms.ru/area.php?error=overloaded",
        "hasHunt": False,
        "targets": [],
        "displayChildren": [],
    }

    controller._handle_location_frame(location_map_frame())

    assert [request.action_type for request in sink.requests] == ["attack_visible_target"]
    assert not any(event.get("action_type") == "open_hunt" for event in logger.events)
    assert any(event["event_type"] == "live_area_error_waiting" for event in logger.events)


def test_standalone_hunt_map_updates_stale_direction_pad_roi(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["viewport"] = {**data["viewport"], "direction_pad_roi": {"x": 400, "y": 300, "width": 90, "height": 90}}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)
    frame = blank_frame(640, 400)
    cv2.rectangle(frame, (150, 70), (510, 360), (45, 135, 45), -1)
    cv2.rectangle(frame, (500, 95), (508, 345), (0, 0, 180), -1)
    cv2.circle(frame, (140, 80), 32, (55, 140, 55), -1)

    controller.process_frame(frame)

    moves = [event for event in logger.events if event["event_type"] == "viewport_move"]
    assert moves
    assert moves[0]["x_frame"] < 180
    assert moves[0]["y_frame"] < 80
    assert any(event.get("detector_id") == "direction_pad_color" for event in logger.events)


def test_confirm_dialog_clicks_cancel_before_recovery_url(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)
    frame = blank_frame(640, 400)
    frame[:, :] = (95, 80, 70)
    cv2.rectangle(frame, (220, 45), (430, 105), (26, 24, 31), -1)
    cv2.rectangle(frame, (330, 82), (370, 98), (160, 80, 180), -1)
    cv2.rectangle(frame, (376, 80), (420, 100), (210, 170, 230), -1)

    controller.process_frame(frame)

    assert any(event["event_type"] == "confirm_dialog_recovery" for event in logger.events)
    assert any(event.get("action_type") == "click_cancel_dialog" for event in logger.events)
    assert not any(event.get("action_type") == "open_url" for event in logger.events)


def test_wait_battle_end_continues_combat_clicks(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["combat"] = {**data["combat"], "click_interval_ms": 0, "slot_index": 4}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(target_frame())
    controller.process_frame(battle_frame())
    assert controller.state_machine.state == GameState.WAIT_BATTLE_END

    controller.process_frame(battle_frame())

    assert controller.session.combat_actions == 1
    assert any(event.get("action_type") == "click_combat_slot" for event in logger.events)
    assert any(event["event_type"] == "combat_slot_intended" for event in logger.events)


def test_live_js_combat_uses_configured_slot_sequence(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "click_interval_ms": 0, "slot_sequence": [2, 3]}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 90.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 90.0, 1.0),
    )
    controller.session.new_battle()
    controller._safe_transition(GameState.BATTLE_ACTIVE, reason="test")

    controller._use_ability4(blank_frame(), None)
    controller._continue_battle_combat(blank_frame(), None)

    assert [request.metadata["skill_slot"] for request in sink.requests] == [2, 3]


def test_live_battle_item_recovery_precedes_skills_and_honors_cooldown_and_max_uses(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "click_interval_ms": 0, "slot_sequence": [2, 3]}
    data["resources"] = {
        **data["resources"],
        "enabled": True,
        "wait_in_battle_when_low": True,
        "fail_open_if_missing": False,
    }
    data["battle_item_recovery"] = {
        **data["battle_item_recovery"],
        "enabled": True,
        "health_use_when_below_percent": 35,
        "prowess_use_when_below_percent": 15,
        "health_slots": [5],
        "prowess_names": ["Малый бурдюк удали"],
        "cooldown_ms": 30_000,
        "max_uses_per_battle": 1,
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    controller.session.new_battle()
    controller._safe_transition(GameState.BATTLE_ACTIVE, reason="test")

    low_health = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 20.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 90.0, 1.0),
    )
    controller._resource_status_via_injector = low_health

    assert controller._continue_battle_combat(blank_frame(), None) is False
    assert [request.action_type for request in sink.requests] == ["use_battle_item"]
    assert sink.requests[0].metadata["kind"] == "health"
    assert sink.requests[0].metadata["slots"] == [5]

    # The cooldown prevents another item request, while combat can continue with a skill.
    assert controller._continue_battle_combat(blank_frame(), None) is True
    assert [request.action_type for request in sink.requests] == ["use_battle_item", "click_combat_slot"]
    assert sink.requests[-1].metadata["skill_slot"] == 2

    # Even after the cooldown has elapsed, max_uses_per_battle prevents a second item use.
    controller._last_battle_item_recovery_monotonic = time.monotonic() - 60
    assert controller._continue_battle_combat(blank_frame(), None) is True
    assert [request.action_type for request in sink.requests] == [
        "use_battle_item",
        "click_combat_slot",
        "click_combat_slot",
    ]
    assert [request.metadata["skill_slot"] for request in sink.requests[1:]] == [2, 3]


def test_live_combat_policy_uses_allowlisted_damage_boost_once_then_skill(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "click_interval_ms": 0, "slot_sequence": [2, 3]}
    data["battle_item_recovery"] = {
        **data["battle_item_recovery"],
        "enabled": True,
        "damage_boost_enabled": True,
        "damage_boost_slots": [6],
        "damage_boost_names": ["Эликсир силы"],
        "cooldown_ms": 0,
        "max_uses_per_battle": 1,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.session.new_battle()
    controller._safe_transition(GameState.BATTLE_ACTIVE, reason="test")
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 90.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 90.0, 1.0),
    )
    controller._last_battle_snapshot_cache = {
        "hasFight": True,
        "finished": False,
        "myTurn": True,
        "useSkillAvailable": True,
        "abilities": [{"slot": 2, "name": "Сильный удар", "ready": True, "cooldown": 0}],
        "items": [
            {
                "slot": 6,
                "name": "Эликсир силы",
                "ready": True,
                "disabled": False,
                "cooldown": 0,
                "quantity": 2,
            }
        ],
    }
    controller._last_battle_snapshot_success_monotonic = time.monotonic()

    assert controller._continue_battle_combat(blank_frame(), None) is False
    assert sink.requests[-1].action_type == "use_battle_item"
    assert sink.requests[-1].metadata["kind"] == "damage_boost"
    assert sink.requests[-1].metadata["slots"] == [6]

    assert controller._continue_battle_combat(blank_frame(), None) is True
    assert sink.requests[-1].action_type == "click_combat_slot"
    assert sink.requests[-1].metadata["skill_slot"] == 2


def test_live_combat_policy_ignores_unallowlisted_item_and_waits_on_opponent_turn(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "click_interval_ms": 0, "slot_sequence": [2]}
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.session.new_battle()
    controller._safe_transition(GameState.BATTLE_ACTIVE, reason="test")
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 10.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 90.0, 1.0),
    )
    controller._last_battle_snapshot_cache = {
        "hasFight": True,
        "finished": False,
        "myTurn": False,
        "useSkillAvailable": True,
        "abilities": [{"slot": 2, "name": "Удар", "ready": True, "cooldown": 0}],
        "items": [{"slot": 9, "name": "Неизвестная банка", "ready": True, "cooldown": 0, "quantity": 9}],
    }
    controller._last_battle_snapshot_success_monotonic = time.monotonic()

    assert controller._continue_battle_combat(blank_frame(), None) is False
    assert sink.requests == []


def test_live_combat_policy_ignores_positive_item_ids_in_ability_array(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.combat_policy import CombatIntent
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "slot_sequence": [2]}
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    status = ResourceStatus(
        health=ResourceBarStatus("health", True, 90.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 90.0, 1.0),
    )

    decision = controller._combat_policy_decision(
        status,
        {
            "hasFight": True,
            "finished": False,
            "myTurn": True,
            "useSkillAvailable": True,
            "abilities": [
                {"id": 438, "slot": 2, "name": "Малый бурдюк жизни", "ready": True, "cooldown": 0},
                {"id": -4626, "slot": 2, "name": "Возмездие I", "ready": True, "cooldown": 0},
            ],
            "items": [],
        },
    )

    assert decision.intent is CombatIntent.USE_SKILL
    assert decision.skill is not None
    assert decision.skill.name == "Возмездие I"


def test_live_js_battle_sync_creates_battle_context_before_skills(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "click_interval_ms": 0, "slot_sequence": [2, 3]}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 90.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 90.0, 1.0),
    )
    controller._battle_snapshot_via_injector = lambda force=False: {
        "hasFight": True,
        "finished": False,
        "myTurn": True,
        "useSkillAvailable": True,
        "fightPath": "top.frames[1].frames[2]",
        "fightHref": "https://3kingdoms.ru/fight.php?1",
        "abilities": [{"id": -4626, "slot": 2, "name": "Возмездие I", "ready": True, "cooldown": 0}],
    }

    assert controller.session.battle_id is None
    assert controller._sync_battle_from_injector(blank_frame(), force_probe=True) is True

    assert controller.session.battle_id == 1
    assert controller.session.ability4_actions == 1
    assert [request.metadata["skill_slot"] for request in sink.requests] == [2]


def test_battle_wait_timeout_extends_on_live_fight_page(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["recovery"] = {**data["recovery"], "battle_wait_timeout_ms": 1}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    battle_id = controller.session.new_battle()
    controller._safe_transition(GameState.BATTLE_WAIT, battle_id=battle_id, reason="test")
    controller._state_entered_monotonic -= 1
    controller._battle_snapshot_via_injector = lambda force=False: None
    controller._visible_hunt_snapshot_via_injector = lambda: {
        "mainHref": "https://3kingdoms.ru/fight.php?loading",
        "hasHunt": False,
        "targets": [],
        "displayChildren": [],
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.BATTLE_WAIT
    assert controller.session.battle_id == battle_id
    assert controller.session.recoveries == 0
    assert any(event["event_type"] == "battle_wait_extended" for event in logger.events)


def test_live_battle_active_timeout_stops_when_bridge_state_is_ambiguous(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["recovery"] = {**data["recovery"], "battle_active_timeout_ms": 1}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    controller.session.new_battle()
    controller.session.mark_battle_detected()
    controller._safe_transition(GameState.BATTLE_ACTIVE, reason="test")
    controller._state_entered_monotonic -= 1
    controller._battle_snapshot_via_injector = lambda force=False: None

    assert controller._recover_stuck_state(blank_frame()) is True

    assert controller.state_machine.state == GameState.STOPPED
    assert controller.last_error_reason == "battle_active_timeout_unconfirmed_state"
    assert controller.session.recoveries == 0


def test_battle_end_timeout_extends_when_battle_still_active(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["combat"] = {**data["combat"], "click_interval_ms": 0}
    data["recovery"] = {**data["recovery"], "battle_end_timeout_ms": 1000}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(target_frame())
    controller.process_frame(battle_frame())
    controller._state_entered_monotonic -= 2
    controller.process_frame(battle_frame())

    assert controller.state_machine.state == GameState.WAIT_BATTLE_END
    assert controller.session.recoveries == 0
    assert controller.session.combat_actions >= 1
    assert any(event["event_type"] == "battle_end_wait_extended" for event in logger.events)


def test_cycle_completes_when_battle_returns_directly_to_hunt(test_config: AutomationConfig) -> None:
    logger = InMemoryEventLogger()
    controller = AutomationController(test_config, sink_mode="replay", logger=logger)

    controller.process_frame(target_frame())
    controller.process_frame(battle_frame())
    assert controller.state_machine.state == GameState.WAIT_BATTLE_END

    controller.process_frame(location_map_frame())

    assert controller.session.completed_cycles == 1
    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert any(
        event["event_type"] == "hunt_return_confirmed" and event["reason"] == "battle_end_skipped"
        for event in logger.events
    )


def test_battle_end_timeout_extends_on_live_fight_page(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "click_interval_ms": 0}
    data["recovery"] = {**data["recovery"], "battle_end_timeout_ms": 1}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 90.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 0.0, 1.0),
    )
    controller.session.new_battle()
    controller.session.mark_battle_detected()
    controller.session.mark_combat()
    controller._safe_transition(GameState.WAIT_BATTLE_END, reason="test")
    controller._state_entered_monotonic -= 1
    controller._battle_snapshot_via_injector = lambda force=False: {
        "hasFight": True,
        "finished": False,
        "myTurn": True,
        "useSkillAvailable": True,
        "fightPath": "top.frames[1].frames[2]",
        "fightHref": "https://3kingdoms.ru/fight.php?abc",
        "abilities": [{"id": -4626, "slot": 2, "name": "Возмездие I", "ready": True, "cooldown": 0}],
    }
    controller._visible_hunt_snapshot_via_injector = lambda: {
        "mainHref": "https://3kingdoms.ru/fight.php?abc",
        "hasHunt": False,
        "targets": [],
        "displayChildren": [],
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.WAIT_BATTLE_END
    assert controller.session.recoveries == 0
    assert any(
        event["event_type"] == "battle_end_wait_extended" and event["reason"] == "current_fight_page"
        for event in logger.events
    )


def test_battle_end_exits_inactive_live_fight_page_without_timeout(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["recovery"] = {**data["recovery"], "battle_end_timeout_ms": 30000}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    battle_id = controller.session.new_battle()
    controller.session.mark_battle_detected()
    controller.session.mark_combat()
    controller._safe_transition(GameState.WAIT_BATTLE_END, battle_id=battle_id, reason="test")
    controller._battle_snapshot_via_injector = lambda force=False: {
        "hasFight": False,
        "useSkillAvailable": False,
        "fightPath": "top.frames[1].frames[2]",
        "fightHref": "https://3kingdoms.ru/fight.php?done",
        "abilities": [],
    }
    controller._visible_hunt_snapshot_via_injector = lambda: {
        "mainHref": "https://3kingdoms.ru/fight.php?done",
        "hasHunt": False,
        "targets": [],
        "displayChildren": [],
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.STATISTICS_WAIT
    assert controller.session.exit_actions == 1
    assert [request.action_type for request in sink.requests] == ["click_exit"]
    assert any(event["event_type"] == "battle_end_inactive_fight_page" for event in logger.events)


def test_battle_end_timeout_exits_inactive_live_fight_page(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["recovery"] = {**data["recovery"], "battle_end_timeout_ms": 1}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    battle_id = controller.session.new_battle()
    controller.session.mark_battle_detected()
    controller.session.mark_combat()
    controller._safe_transition(GameState.WAIT_BATTLE_END, battle_id=battle_id, reason="test")
    controller._state_entered_monotonic -= 1
    controller._battle_snapshot_via_injector = lambda force=False: {
        "hasFight": False,
        "useSkillAvailable": False,
        "fightPath": "top.frames[1].frames[2]",
        "fightHref": "https://3kingdoms.ru/fight.php?done",
        "abilities": [],
    }
    controller._visible_hunt_snapshot_via_injector = lambda: {
        "mainHref": "https://3kingdoms.ru/fight.php?done",
        "hasHunt": False,
        "targets": [],
        "displayChildren": [],
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.STATISTICS_WAIT
    assert controller.session.exit_actions == 1
    assert [request.action_type for request in sink.requests] == ["click_exit"]
    assert any(event["event_type"] == "battle_end_inactive_fight_page" for event in logger.events)


def test_low_resources_rest_before_target_click(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["resources"] = {
        "enabled": True,
        "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
        "health_min_percent": 35,
        "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
        "prowess_min_percent": 35,
        "recover_to_percent": 80,
        "rest_check_interval_ms": 0,
        "fail_open_if_missing": False,
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(resource_frame(health=25, prowess=90, include_target=True))
    assert controller.state_machine.state == GameState.RESTING
    assert controller.session.resource_waits == 1
    assert not any(event.get("action_type") == "click_target" for event in logger.events)

    controller.process_frame(resource_frame(health=90, prowess=90, include_target=True))
    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    controller.process_frame(resource_frame(health=90, prowess=90, include_target=True))
    assert controller.state_machine.state == GameState.BATTLE_WAIT
    assert any(event.get("action_type") == "click_target" for event in logger.events)


def test_resting_syncs_to_active_battle_from_js_snapshot(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="live", logger=logger)
    used_abilities = []
    controller.state_machine.transition(GameState.RESTING, reason="test_resting")
    controller._battle_snapshot_via_injector = lambda force=False: {
        "hasFight": True,
        "useSkillAvailable": True,
        "fightPath": "top.main_frame.main",
        "fightHref": "https://3kingdoms.ru/fight.php?1",
        "abilities": [{"id": -4626, "slot": 2, "name": "skill"}],
    }
    controller._use_ability4 = lambda frame, panel_bbox: used_abilities.append(True)

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.BATTLE_ACTIVE
    assert controller.session.battle_id is not None
    assert used_abilities == [True]


def test_live_low_js_resources_block_visible_attack(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    class RecordingActionSink:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            return True

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "slot_sequence": [2, 3]}
    data["resources"] = {
        "enabled": True,
        "health_min_percent": 35,
        "prowess_min_percent": 35,
        "recover_to_percent": 80,
        "require_recover_before_search": True,
        "rest_check_interval_ms": 0,
        "fail_open_if_missing": False,
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = RecordingActionSink()
    controller.action_executor.sink = sink
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 20.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 90.0, 1.0),
    )

    controller._handle_location_frame(location_map_frame())

    assert controller.state_machine.state == GameState.RESTING
    assert sink.requests == []
    assert controller.session.resource_waits == 1


def test_live_combat_zero_fallback_only_at_configured_prowess_threshold(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {
        **data["combat"],
        "slot_sequence": [2, 3],
        "low_resource_fallback_enabled": True,
        "low_resource_fallback_slot_index": 0,
        "low_resource_fallback_percent": 1,
    }
    data["resources"] = {
        "enabled": True,
        "health_min_percent": 35,
        "prowess_min_percent": 90,
        "wait_in_battle_when_low": True,
        "fail_open_if_missing": False,
    }
    config = AutomationConfig.from_dict(data)
    controller = AutomationController(config, sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 90.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 50.0, 1.0),
    )

    assert controller._live_combat_slot_for_current_resources(blank_frame()) == 2

    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 90.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 0.0, 1.0),
    )

    assert controller._live_combat_slot_for_current_resources(blank_frame()) == 0


def test_failed_skill_does_not_trigger_zero_attack_with_high_prowess(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {
        **data["combat"],
        "slot_sequence": [2],
        "low_resource_fallback_enabled": True,
        "low_resource_fallback_slot_index": 0,
        "low_resource_fallback_percent": 1,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )

    class FailPrimarySink:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request) -> bool:
            self.requests.append(request)
            return False

    sink = FailPrimarySink()
    controller.action_executor.sink = sink
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 100.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 80.0, 1.0),
    )
    controller.session.new_battle()
    controller._safe_transition(GameState.BATTLE_ACTIVE, reason="test")

    controller._use_ability4(blank_frame(), None)

    assert [request.metadata["skill_slot"] for request in sink.requests] == [2]
    assert controller.state_machine.state is GameState.BATTLE_ACTIVE


def test_live_low_resources_trigger_item_recovery_before_search(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    class RecordingActionSink:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            return True

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["resources"] = {
        "enabled": True,
        "health_min_percent": 90,
        "prowess_min_percent": 90,
        "fail_open_if_missing": False,
    }
    data["item_recovery"] = {
        **data["item_recovery"],
        "enabled": True,
        "use_after_cycle": False,
        "health_use_when_below_percent": 90,
        "prowess_use_when_below_percent": 90,
    }
    config = AutomationConfig.from_dict(data)
    controller = AutomationController(config, sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    sink = RecordingActionSink()
    controller.action_executor.sink = sink
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 100.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 55.0, 1.0),
    )

    assert controller._resources_allow_search(blank_frame()) is False
    assert [request.action_type for request in sink.requests] == ["use_recovery_items"]
    assert sink.requests[0].metadata["prowess_use_when_below_percent"] == 90


def test_recover_threshold_required_before_new_search(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["resources"] = {
        "enabled": True,
        "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
        "health_min_percent": 35,
        "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
        "prowess_min_percent": 35,
        "recover_to_percent": 80,
        "require_recover_before_search": True,
        "rest_check_interval_ms": 0,
        "fail_open_if_missing": False,
    }
    config = AutomationConfig.from_dict(data)
    controller = AutomationController(config, sink_mode="replay", logger=InMemoryEventLogger())

    controller.process_frame(resource_frame(health=90, prowess=55, include_target=True))

    assert controller.state_machine.state == GameState.RESTING
    assert controller.session.targets_detected == 0


def test_live_resting_refreshes_stale_resource_source(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "slot_sequence": [2, 3]}
    data["resources"] = {
        "enabled": True,
        "health_min_percent": 35,
        "prowess_min_percent": 35,
        "recover_to_percent": 80,
        "require_recover_before_search": True,
        "rest_check_interval_ms": 0,
        "rest_refresh_after_ms": 1000,
        "rest_refresh_interval_ms": 1000,
        "fail_open_if_missing": False,
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    refresh_calls = []
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 20.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 20.0, 1.0),
    )
    controller._battle_snapshot_via_injector = lambda force=False: None
    controller._refresh_resource_source_via_injector = lambda: refresh_calls.append(True) or (True, "main_frame_reload_scheduled")

    controller.process_frame(location_map_frame())
    controller._resting_since_monotonic = time.monotonic() - 2
    controller.process_frame(location_map_frame())

    assert controller.state_machine.state == GameState.RESTING
    assert refresh_calls == [True]
    assert any(event["event_type"] == "resource_source_refresh" and event["ok"] is True for event in logger.events)


def test_live_js_combat_falls_back_to_slot_zero_when_prowess_low(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "low_resource_fallback_slot_index": 0, "slot_sequence": [2, 3]}
    data["resources"] = {
        "enabled": True,
        "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
        "health_min_percent": 35,
        "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
        "prowess_min_percent": 35,
        "recover_to_percent": 80,
        "wait_in_battle_when_low": True,
        "rest_check_interval_ms": 0,
        "fail_open_if_missing": False,
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    def zero_prowess_status():
        return ResourceStatus(
            health=ResourceBarStatus("health", True, 90.0, 1.0),
            prowess=ResourceBarStatus("prowess", True, 0.0, 1.0),
        )

    controller._resource_status_via_injector = zero_prowess_status
    controller.session.new_battle()
    controller._safe_transition(GameState.BATTLE_ACTIVE, reason="test")

    controller._use_ability4(blank_frame(), None)

    assert [request.metadata["skill_slot"] for request in sink.requests] == [0]
    assert any(
        event["event_type"] == "combat_low_resource_fallback" and event["fallback_slot"] == 0
        for event in logger.events
    )


def test_live_js_combat_continues_when_health_low_without_low_prowess(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["combat"] = {**data["combat"], "slot_sequence": [2, 3]}
    data["resources"] = {
        "enabled": True,
        "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
        "health_min_percent": 35,
        "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
        "prowess_min_percent": 35,
        "recover_to_percent": 80,
        "wait_in_battle_when_low": True,
        "rest_check_interval_ms": 0,
        "fail_open_if_missing": False,
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(config, sink_mode="live", logger=logger)
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    controller._resource_status_via_injector = lambda: None
    controller.session.new_battle()
    controller._safe_transition(GameState.BATTLE_ACTIVE, reason="test")

    controller._use_ability4(resource_frame(health=5, prowess=90), None)

    assert [request.metadata["skill_slot"] for request in sink.requests] == [2]
    assert any(
        event["event_type"] == "combat_low_resource_continuing" and event["low_resources"] == ["health"]
        for event in logger.events
    )


def test_resting_does_not_release_when_one_resource_is_low_and_other_missing(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["resources"] = {
        "enabled": True,
        "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
        "health_min_percent": 35,
        "prowess_bar_roi": None,
        "prowess_min_percent": 35,
        "recover_to_percent": 80,
        "rest_check_interval_ms": 0,
        "fail_open_if_missing": True,
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(resource_frame(health=20, prowess=90, include_target=True))
    assert controller.state_machine.state == GameState.RESTING

    controller.process_frame(resource_frame(health=20, prowess=90, include_target=True))

    assert controller.state_machine.state == GameState.RESTING
    assert not any(event["event_type"] == "resource_rest_released" for event in logger.events)


def test_low_resources_rest_after_cycle_completed(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["resources"] = {
        "enabled": True,
        "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
        "health_min_percent": 35,
        "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
        "prowess_min_percent": 35,
        "recover_to_percent": 80,
        "rest_check_interval_ms": 0,
        "fail_open_if_missing": False,
    }
    config = AutomationConfig.from_dict(data)
    controller = AutomationController(config, sink_mode="replay", logger=InMemoryEventLogger())

    for frame in [
        resource_frame(health=90, prowess=90, include_target=True),
        battle_frame(),
        battle_end_frame(),
        statistics_frame(),
        location_map_frame(health=90, prowess=20),
    ]:
        controller.process_frame(frame)

    assert controller.session.completed_cycles == 1
    assert controller.state_machine.state == GameState.RESTING
    assert controller.session.resource_waits == 1


def test_f12_blocks_mid_cycle(test_config: AutomationConfig) -> None:
    controller = AutomationController(test_config, sink_mode="replay", logger=InMemoryEventLogger())
    controller.process_frame(target_frame())
    controller.guard.emergency_stop()
    controller.process_frame(battle_frame())
    assert controller.state_machine.state == GameState.STOPPED
    assert controller.session.ability4_actions == 0


def test_target_click_retries_while_waiting_for_battle(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["target"] = {
        **data["target"],
        "click_retry_delay_ms": 0,
        "click_retry_offsets": [{"dx": 0, "dy": 0}, {"dx": 0, "dy": 35}],
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(target_frame())
    controller.process_frame(target_frame(x=140))
    controller.process_frame(target_frame(x=170))

    clicks = [event for event in logger.events if event.get("action_type") == "click_target"]
    retries = [event for event in logger.events if event["event_type"] == "target_click_retry"]
    reacquired = [event for event in logger.events if event["event_type"] == "target_reacquired"]
    assert len(clicks) == 3
    assert len(retries) == 2
    assert len(reacquired) == 2
    assert clicks[0]["x_frame"] < clicks[1]["x_frame"] < clicks[2]["x_frame"]
    assert controller.state_machine.state == GameState.BATTLE_WAIT


def test_battle_wait_retries_target_after_attack_click_if_battle_did_not_start(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    class OneShotAttackDetector:
        def __init__(self) -> None:
            self.calls = 0

        def detect(self, frame):
            self.calls += 1
            if self.calls == 1:
                return AttackButtonDetection(True, 0.95, Rect(10, 10, 100, 24), Point(25, 22))
            return AttackButtonDetection(False, 0.0, None, None)

    data = to_plain_dict(test_config)
    data["target"] = {
        **data["target"],
        "click_retry_delay_ms": 0,
        "click_retry_offsets": [{"dx": 0, "dy": 0}, {"dx": 0, "dy": 35}],
    }
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(target_frame())
    controller.attack_detector = OneShotAttackDetector()
    controller.process_frame(target_frame(x=140))
    controller.process_frame(target_frame(x=170))

    clicks = [event for event in logger.events if event.get("action_type") == "click_target"]
    assert controller.session.attack_actions == 1
    assert len(clicks) == 2
    assert clicks[0]["x_frame"] < clicks[1]["x_frame"]
    assert any(event["event_type"] == "target_click_retry" for event in logger.events)


def test_battle_wait_timeout_recovers_to_search(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["recovery"] = {**data["recovery"], "battle_wait_timeout_ms": 1000}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(target_frame())
    assert controller.state_machine.state == GameState.BATTLE_WAIT

    controller._state_entered_monotonic -= 2
    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert controller.session.battle_id is None
    assert controller.session.recoveries == 1
    assert any(event["event_type"] == "state_recovered" and event["reason"] == "battle_wait_timeout" for event in logger.events)


def test_viewport_search_exhaustion_recovers_instead_of_stopping(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["viewport"] = {
        **data["viewport"],
        "max_moves_per_search": 0,
        "scrollbar_enabled": False,
    }
    data["recovery"] = {**data["recovery"], "viewport_exhausted_pause_ms": 0}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert controller.session.recoveries == 1
    assert controller.session.errors == 0
    assert any(event["event_type"] == "viewport_search_exhausted" for event in logger.events)
    assert not any(event["event_type"] == "session_stopped" and event.get("reason") == "viewport_search_exhausted" for event in logger.events)


def test_runtime_overrides_apply_combat_popup_settings(test_config: AutomationConfig) -> None:
    config = _apply_runtime_overrides(
        test_config,
        {
            "combatSlotSequence": [2, "3", "2", 0],
            "combatFallbackZeroEnabled": False,
            "combatFallbackProwessPercent": "3",
            "combatClickIntervalMs": "450",
            "combatPreClickDelayMs": "75",
            "combatClickHoldMs": "25",
            "battleDamageBoostEnabled": True,
            "battleDamageBoostSlots": [6],
            "battleDamageBoostNames": ["Эликсир силы"],
            "recoveryHealthThreshold": "85",
            "recoveryProwessThreshold": "80",
            "goalLevel": "6",
            "maxDeathsPerSession": "2",
            "targetLocationName": "Длань Рода",
            "autonomousQuestDirector": True,
        },
    )

    assert config.combat.slot_sequence == (2, 3, 0)
    assert config.combat.low_resource_fallback_enabled is False
    assert config.combat.low_resource_fallback_percent == 3
    assert config.combat.click_interval_ms == 450
    assert config.combat.pre_click_delay_ms == 75
    assert config.combat.click_hold_ms == 25
    assert config.battle_item_recovery.damage_boost_enabled is True
    assert config.battle_item_recovery.damage_boost_slots == (6,)
    assert config.battle_item_recovery.damage_boost_names == ("Эликсир силы",)
    assert config.item_recovery.health_use_when_below_percent == 85
    assert config.item_recovery.prowess_use_when_below_percent == 80
    assert config.leveling.enabled is True
    assert config.leveling.target_level == 6
    assert config.leveling.max_deaths_per_session == 2
    assert config.leveling.target_location_name == "Длань Рода"
    assert config.leveling.autonomous_quest_director is True


def test_live_leveling_goal_stops_at_confirmed_character_level(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 6,
        "required_character_name": "v3g45",
    }
    controller = AutomationController(AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    controller._state_snapshot_via_injector = lambda: {
        "schemaVersion": 1,
        "sections": {
            "player": {"status": "available", "data": {"name": "v3g45", "level": 6, "xpPercent": 0.5}},
            "location": {"status": "partial", "data": {"pageKind": "area", "semanticName": "Длань Рода"}},
        },
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.STOPPED
    assert controller.current_character_name == "v3g45"
    assert controller.current_level == 6
    assert controller.current_xp_percent == 0.5
    assert any(event["event_type"] == "goal_level_reached" for event in controller.logger.events)


def test_live_leveling_goal_stops_on_character_mismatch(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
    }
    controller = AutomationController(AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    controller._state_snapshot_via_injector = lambda: {
        "schemaVersion": 1,
        "sections": {
            "player": {"status": "available", "data": {"name": "other", "level": 5, "xpPercent": 20}},
            "location": {"status": "partial", "data": {"pageKind": "area", "semanticName": None}},
        },
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.STOPPED
    assert controller.last_error_reason == "character_mismatch:other"


def test_death_guard_rejects_wrong_character_before_revive(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "hero-a",
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller._state_snapshot_via_injector = lambda **_: {
        "schemaVersion": 1,
        "snapshotId": "wrong-character-death",
        "sections": {
            "player": {"data": {"name": "hero-b", "level": 5, "xpPercent": 20}},
            "location": {"data": {"pageKind": "area", "semanticName": "Городская площадь"}},
            "deathRevive": {
                "data": {
                    "dead": True,
                    "freeReviveAvailable": True,
                    "freeReviveOptionCount": 1,
                }
            },
        },
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "character_mismatch:hero-b"
    assert sink.requests == []


def test_leveling_binds_first_character_when_config_name_is_empty(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "",
    }
    controller = AutomationController(AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    observed_name = "hero-a"

    def snapshot():
        return {
            "schemaVersion": 1,
            "sections": {
                "player": {
                    "data": {
                        "name": observed_name,
                        "level": 5,
                        "xpPercent": 20,
                        "hpPercent": 100,
                        "prowessPercent": 100,
                    }
                },
                "location": {"data": {"pageKind": "hunt", "semanticName": None}},
                "deathRevive": {"data": {"dead": False}},
                "quests": {"data": {"items": []}},
            },
        }

    controller._state_snapshot_via_injector = snapshot
    assert controller._observe_leveling_goal() is False
    assert controller._bound_character_name == "hero-a"

    observed_name = "hero-b"
    assert controller._observe_leveling_goal() is True
    assert controller.state_machine.state == GameState.STOPPED
    assert controller.last_error_reason == "character_mismatch:hero-b"


def test_leveling_policy_stops_before_actions_when_active_resources_are_unknown(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller._state_snapshot_via_injector = lambda: {
        "schemaVersion": 1,
        "sections": {
            "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20}},
            "location": {"data": {"pageKind": "hunt", "semanticName": None}},
            "deathRevive": {"data": {"dead": False}},
            "quests": {"data": {"items": []}},
        },
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.STOPPED
    assert controller.last_error_reason == "leveling_policy:unknown_observation"
    assert sink.requests == []


def test_leveling_stops_when_cached_snapshot_exceeds_freshness_limit(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
        "snapshot_stale_timeout_ms": 1000,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    cached = {
        "schemaVersion": 1,
        "sections": {
            "player": {
                "data": {
                    "name": "v3g45",
                    "level": 5,
                    "xpPercent": 20,
                    "hpPercent": 100,
                    "prowessPercent": 100,
                }
            },
            "location": {"data": {"pageKind": "hunt", "semanticName": None}},
            "deathRevive": {"data": {"dead": False}},
            "quests": {"data": {"items": []}},
        },
    }
    controller._state_snapshot_cache = cached
    controller._last_state_snapshot_success_monotonic = time.monotonic() - 2
    controller._state_snapshot_via_injector = lambda: cached

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.STOPPED
    assert controller.last_error_reason == "leveling_state_snapshot_stale"
    assert sink.requests == []


def test_leveling_uses_current_level_and_cached_quest_names_for_target(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
        "auto_target_level_offsets": [0],
        "auto_navigate_quest_targets": True,
    }
    data["target"] = {**data["target"], "allowed_levels": [], "allowed_names": []}
    controller = AutomationController(AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_level = 5
    controller._quest_target_names = ("Волколаков-живодеров",)
    controller._resources_allow_search = lambda frame: True

    assert controller._attack_visible_target_via_injector(blank_frame()) is True
    assert sink.requests[0].metadata["allowed_levels"] == [5]
    assert sink.requests[0].metadata["names"] == ["Волколаков-живодеров"]


def test_leveling_waits_for_finished_battle_resolution_with_transient_zero_resources(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    controller.state_machine.state = GameState.WAIT_BATTLE_END
    controller._state_snapshot_via_injector = lambda: {
        "schemaVersion": 1,
        "snapshotId": "battle-finished-transient",
        "sections": {
            "player": {
                "data": {
                    "name": "v3g45",
                    "level": 5,
                    "xpPercent": 20,
                    "hpPercent": 0,
                    "prowessPercent": 0,
                }
            },
            "location": {"data": {"pageKind": "battle", "semanticName": None}},
            "deathRevive": {"data": {"dead": None, "freeReviveAvailable": False}},
            "battle": {"data": {"rawHasFight": True, "hasFight": False, "finished": True}},
            "quests": {"data": {"loadStatus": "not_loaded", "items": []}},
        },
    }

    assert controller._observe_leveling_goal() is False
    assert controller.state_machine.state is GameState.WAIT_BATTLE_END
    assert controller.last_leveling_intent == "WAIT"
    assert controller.last_leveling_reason == "battle_resolution_pending"
    assert controller.last_error_reason is None


def test_leveling_treats_confirmed_hunt_app_as_alive_when_death_marker_is_transient(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    controller._state_snapshot_via_injector = lambda: {
        "schemaVersion": 1,
        "snapshotId": "hunt-after-battle",
        "sections": {
            "player": {
                "data": {
                    "name": "v3g45",
                    "level": 5,
                    "xpPercent": 20,
                    "hpPercent": 0,
                    "prowessPercent": 0,
                }
            },
            "location": {"data": {"pageKind": "hunt", "semanticName": None}},
            "deathRevive": {"data": {"dead": None, "freeReviveAvailable": False}},
            "battle": {"data": {"rawHasFight": False, "hasFight": False, "finished": False}},
            "hunt": {"data": {"hasHunt": True}},
            "quests": {"data": {"loadStatus": "not_loaded", "items": []}},
            "shopInventory": {"data": {"items": []}},
        },
    }

    assert controller._observe_leveling_goal() is False
    assert controller.state_machine.state is GameState.LOCATION_SEARCH
    assert controller.last_leveling_intent == "REST"
    assert controller.last_leveling_reason == "hp_below_threshold_no_allowed_item"
    assert controller.last_error_reason is None


def test_leveling_does_not_filter_farm_targets_by_quest_when_quest_navigation_is_off(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "auto_navigate_quest_targets": False,
    }
    data["target"] = {**data["target"], "allowed_names": []}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    controller._quest_target_names = ("Волколаков-живодеров",)

    assert controller._effective_target_names() == ()


def test_leveling_revives_once_then_returns_to_hunt(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
        "max_deaths_per_session": 3,
    }
    controller = AutomationController(AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    snapshots = [
            {
                "schemaVersion": 1,
                "sections": {
                    "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20}},
                    "location": {"data": {"pageKind": "area", "semanticName": "Городская площадь Арсы"}},
                    "deathRevive": {"data": {"dead": True, "freeReviveAvailable": True}},
                    "quests": {"data": {"items": []}},
                },
            },
            {
                "schemaVersion": 1,
                "sections": {
                    "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20}},
                    "location": {"data": {"pageKind": "area", "semanticName": "Городская площадь Арсы"}},
                    "deathRevive": {"data": {"dead": True, "freeReviveAvailable": True}},
                    "quests": {"data": {"items": []}},
                },
            },
            {
                "schemaVersion": 1,
                "sections": {
                    "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20}},
                    "location": {"data": {"pageKind": "area", "semanticName": "Городская площадь Арсы"}},
                    "deathRevive": {"data": {"dead": False, "freeReviveAvailable": False}},
                    "quests": {"data": {"items": []}},
                },
            },
        ]
    snapshot_index = 0

    def next_snapshot(force: bool = False) -> dict[str, object]:
        nonlocal snapshot_index
        snapshot = snapshots[min(snapshot_index, len(snapshots) - 1)]
        snapshot_index += 1
        return snapshot

    controller._state_snapshot_via_injector = next_snapshot
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 100.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 100.0, 1.0),
    )

    controller.process_frame(blank_frame())
    assert controller.state_machine.state == GameState.REVIVE_PENDING
    controller.process_frame(blank_frame())
    assert controller.state_machine.state == GameState.REVIVE_PENDING
    assert [request.action_type for request in sink.requests] == ["revive_free"]
    controller.process_frame(blank_frame())
    assert controller.state_machine.state == GameState.POST_REVIVE_RECOVERY
    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert controller.deaths_observed == 1
    assert [request.action_type for request in sink.requests] == ["revive_free", "open_hunt"]


def test_death_guard_blocks_recovery_until_revive_confirmed_when_leveling_disabled(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": False,
        "required_character_name": "v3g45",
        "max_deaths_per_session": 3,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink

    snapshots = iter(
        [
            {
                "schemaVersion": 1,
                "snapshotId": "dead-1",
                "sections": {
                    "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20}},
                    "location": {"data": {"pageKind": "area", "semanticName": "Городская площадь"}},
                    "deathRevive": {
                        "data": {
                            "dead": True,
                            "freeReviveAvailable": True,
                            "freeReviveOptionCount": 1,
                        }
                    },
                },
            },
            {
                "schemaVersion": 1,
                "snapshotId": "pending-1",
                "sections": {
                    "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20}},
                    "location": {"data": {"pageKind": "inventory", "semanticName": None}},
                    "deathRevive": {"data": {"dead": None, "freeReviveAvailable": False}},
                },
            },
            {
                "schemaVersion": 1,
                "snapshotId": "alive-1",
                "sections": {
                    "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20}},
                    "location": {"data": {"pageKind": "area", "semanticName": "Городская площадь"}},
                    "deathRevive": {"data": {"dead": False, "freeReviveAvailable": False}},
                },
            },
        ]
    )
    controller._state_snapshot_via_injector = lambda **_: next(snapshots)
    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 100.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 100.0, 1.0),
    )

    assert controller._observe_death_guard(force=True) is True
    assert controller.state_machine.state is GameState.REVIVE_PENDING
    assert [request.action_type for request in sink.requests] == ["revive_free"]

    assert controller._observe_death_guard(force=True) is True
    assert controller.state_machine.state is GameState.REVIVE_PENDING
    assert [request.action_type for request in sink.requests] == ["revive_free"]

    assert controller._observe_death_guard(force=True) is True
    assert controller.state_machine.state is GameState.POST_REVIVE_RECOVERY
    controller._handle_post_revive_recovery(blank_frame())
    assert controller.state_machine.state is GameState.LOCATION_SEARCH
    assert [request.action_type for request in sink.requests] == ["revive_free", "open_hunt"]


def test_death_checkpoint_prefers_last_confirmed_alive_area(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "enabled": False, "max_deaths_per_session": 3}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink

    controller._update_location_tracking(
        {"pageKind": "area", "semanticName": "Курганы бренности"},
        {"dead": False},
        {"hpPercent": 100},
    )
    controller.current_page_kind = "battle"
    controller.current_location_name = None

    assert controller._handle_leveling_death(
        {"dead": True, "freeReviveAvailable": True, "freeReviveOptionCount": 1}
    ) is True

    assert controller._last_alive_location_name == "Курганы бренности"
    assert controller._death_checkpoint is not None
    assert controller._death_checkpoint.location == "Курганы бренности"
    assert [request.action_type for request in sink.requests] == ["revive_free"]


def test_confirmed_revive_opens_compass_for_different_checkpoint_location(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "enabled": False, "max_deaths_per_session": 3}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller._last_alive_location_name = "Курганы бренности"
    controller.current_page_kind = "battle"
    controller._navigator_client_ids_for_parent = lambda: {"existing-navigator"}
    controller._route_destination_name = "stale destination"
    controller._route_destination_id = "stale-id"
    controller._route_expected_transitions = 99
    controller._route_step_submitted_from_id = "stale-step"

    assert controller._handle_leveling_death(
        {"dead": True, "freeReviveAvailable": True, "freeReviveOptionCount": 1}
    ) is True
    controller.current_page_kind = "area"
    controller.current_location_name = "Город Барбус"

    assert controller._handle_leveling_death({"dead": False, "freeReviveAvailable": False}) is True
    _complete_post_revive_resource_gate(controller)

    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING
    assert controller._navigator_target_name == "Курганы бренности"
    assert controller._navigator_requires_target_selection is True
    assert controller._route_recovery_kind == "post_revive_location"
    assert controller._navigator_existing_client_ids == {"existing-navigator"}
    assert controller._route_destination_name is None
    assert controller._route_destination_id is None
    assert controller._route_expected_transitions is None
    assert controller._route_step_submitted_from_id is None
    assert [request.action_type for request in sink.requests] == ["revive_free", "open_location_navigator"]


def test_post_revive_navigator_rejection_stops_without_started_telemetry(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    controller.state_machine.state = GameState.POST_REVIVE_RECOVERY
    controller.current_location_name = "Город Барбус"
    controller._death_checkpoint = RecoveryCheckpoint(
        activity=GameState.LOCATION_SEARCH.value,
        location="Курганы бренности",
        quest=None,
        snapshot_id="dead-navigator-rejected",
    )
    controller._post_revive_recovery_started_monotonic = time.monotonic()
    controller.action_executor.execute = lambda request: False

    _complete_post_revive_resource_gate(controller)

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "post_revive_location_route_navigator_open_failed"
    assert not any(
        event["event_type"] == "post_revive_route_recovery_started"
        for event in controller.logger.events
    )


def test_post_revive_unknown_checkpoint_stops_without_hunt(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.REVIVE_PENDING
    controller._death_checkpoint = RecoveryCheckpoint(
        activity=GameState.LOCATION_SEARCH.value,
        location="area",
        quest=None,
        snapshot_id="dead-unknown-location",
    )

    assert controller._complete_revive_recovery("revived") is True
    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "post_revive_checkpoint_location_unconfirmed"
    assert sink.requests == []


def test_death_while_navigator_pending_preserves_original_target(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "enabled": False, "max_deaths_per_session": 3}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="parent-client",
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.NAVIGATOR_PENDING
    controller.current_page_kind = "area"
    controller.current_location_name = "Курганы бренности"
    controller._last_alive_location_name = "Курганы бренности"
    controller._navigator_target_name = "Порт Барбуса"

    assert controller._handle_leveling_death(
        {"dead": True, "freeReviveAvailable": True, "freeReviveOptionCount": 1}
    ) is True
    assert controller._route_resume_target_name == "Порт Барбуса"

    controller.current_location_name = "Курганы бренности"
    assert controller._handle_leveling_death({"dead": False, "freeReviveAvailable": False}) is True
    _complete_post_revive_resource_gate(controller)

    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING
    assert controller._navigator_target_name == "Порт Барбуса"
    assert [request.action_type for request in sink.requests] == ["revive_free", "open_location_navigator"]


def test_post_revive_resources_recover_before_hunt(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["item_recovery"] = {**data["item_recovery"], "enabled": True, "open_hunt_after": True}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.POST_REVIVE_RECOVERY
    controller.current_location_name = "Городская площадь"
    controller._death_checkpoint = RecoveryCheckpoint(
        activity=GameState.LOCATION_SEARCH.value,
        location="Городская площадь",
        quest=None,
        snapshot_id="dead-low-resources",
    )
    controller._post_revive_recovery_started_monotonic = time.monotonic()
    controller._post_revive_resume_reason = "revived"
    statuses = iter(
        [
            ResourceStatus(
                health=ResourceBarStatus("health", True, 20.0, 1.0),
                prowess=ResourceBarStatus("prowess", True, 30.0, 1.0),
            ),
            ResourceStatus(
                health=ResourceBarStatus("health", True, 100.0, 1.0),
                prowess=ResourceBarStatus("prowess", True, 100.0, 1.0),
            ),
        ]
    )
    controller._resource_status_via_injector = lambda: next(statuses)

    controller._handle_post_revive_recovery(blank_frame())
    assert controller.state_machine.state is GameState.POST_REVIVE_RECOVERY
    assert [request.action_type for request in sink.requests] == ["use_recovery_items"]
    assert sink.requests[0].metadata["open_hunt_after"] is False

    controller._last_rest_check_monotonic = None
    controller._handle_post_revive_recovery(blank_frame())
    assert controller.state_machine.state is GameState.LOCATION_SEARCH
    assert [request.action_type for request in sink.requests] == ["use_recovery_items", "open_hunt"]


def test_leveling_routes_to_configured_location_before_farming(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
        "target_location_name": "Длань Рода",
        "auto_navigate_quest_targets": False,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "snapshotId": "configured-route-start",
        "sections": {
            "player": {
                "data": {
                    "name": "v3g45",
                    "level": 5,
                    "xpPercent": 20,
                    "hpPercent": 100,
                    "prowessPercent": 100,
                }
            },
            "location": {"data": {"pageKind": "hunt", "semanticName": None}},
            "deathRevive": {"data": {"dead": False, "freeReviveAvailable": False}},
            "battle": {"data": {"rawHasFight": False, "hasFight": False}},
            "quests": {"data": {"loadStatus": "not_loaded", "items": []}},
            "shopInventory": {"data": {"items": []}},
        },
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state is GameState.LOCATION_SEARCH
    assert [request.action_type for request in sink.requests] == ["open_area"]
    assert sink.requests[0].metadata["reason"] == "capture_configured_route_checkpoint"

    controller._search_pause_until_monotonic = 0.0
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "snapshotId": "configured-route-area",
        "sections": {
            "player": {
                "data": {
                    "name": "v3g45",
                    "level": 5,
                    "xpPercent": 20,
                    "hpPercent": 100,
                    "prowessPercent": 100,
                }
            },
            "location": {"data": {"pageKind": "area", "semanticName": "Городская площадь"}},
            "deathRevive": {"data": {"dead": False, "freeReviveAvailable": False}},
            "battle": {"data": {"rawHasFight": False, "hasFight": False}},
            "quests": {"data": {"loadStatus": "not_loaded", "items": []}},
            "shopInventory": {"data": {"items": []}},
        },
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING
    assert controller._navigator_target_name == "Длань Рода"
    assert controller._navigator_requires_target_selection is True
    assert controller._route_recovery_kind == "configured_location"
    assert [request.action_type for request in sink.requests] == ["open_area", "open_location_navigator"]


def test_configured_location_route_leaves_quests_before_opening_compass(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
        "target_location_name": "Чёрное капище",
        "auto_navigate_quest_targets": False,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "snapshotId": "configured-route-from-quests",
        "sections": {
            "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20, "hpPercent": 100, "prowessPercent": 100}},
            "location": {"data": {"pageKind": "quests", "semanticName": None}},
            "deathRevive": {"data": {"dead": False, "freeReviveAvailable": False}},
            "battle": {"data": {"rawHasFight": False, "hasFight": False}},
            "quests": {"data": {"loadStatus": "loaded", "items": []}},
            "shopInventory": {"data": {"items": []}},
        },
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state is GameState.LOCATION_SEARCH
    assert controller.last_error_reason is None
    assert [request.action_type for request in sink.requests] == ["open_hunt"]


def test_configured_location_route_waits_for_exact_arrival(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
        "target_location_name": "Длань Рода",
        "route_settle_ms": 1,
        "navigator_timeout_ms": 10000,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller._navigator_target_name = "Длань Рода"
    controller._route_recovery_kind = "configured_location"
    controller._route_go_submitted_monotonic = time.monotonic() - 1
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "sections": {"location": {"data": {"pageKind": "area", "semanticName": "Прокаленное плато"}}},
    }

    controller._handle_route_recovery()

    assert controller.state_machine.state is GameState.ROUTE_RECOVERY
    assert sink.requests == []


def test_configured_monster_route_is_not_reopened_after_confirmed_arrival(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 9,
        "target_location_name": "Белая Рысь [6]",
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller._navigator_target_name = "Белая Рысь [6]"
    controller._route_destination_name = "Белая Рысь [6]"
    controller._route_recovery_kind = "configured_location"
    controller.current_location_name = "Порт безбрежного моря"

    assert controller._finish_route_arrival("navigator_route_id_confirmed") is True

    assert controller._configured_route_completed_target == "Белая Рысь [6]"
    assert controller.state_machine.state is GameState.LOCATION_SEARCH
    controller.current_page_kind = "hunt"
    controller.current_location_name = "Порт безбрежного моря"
    assert controller._maybe_start_configured_location_route() is False
    assert [request.action_type for request in sink.requests] == ["open_hunt"]

    controller._last_alive_location_name = "Порт безбрежного моря"
    controller._state_snapshot_via_injector = lambda: {
        "schemaVersion": 1,
        "snapshotId": "hunt-after-configured-route",
        "sections": {
            "player": {
                "data": {
                    "name": "v3g45",
                    "level": 5,
                    "xpPercent": 20,
                    "hpPercent": 100,
                    "prowessPercent": 100,
                }
            },
            "location": {"data": {"pageKind": "hunt", "semanticName": None}},
            "deathRevive": {"data": {"dead": False}},
            "hunt": {"data": {"hasHunt": True}},
            "quests": {"data": {"items": []}},
        },
    }

    assert controller._observe_leveling_goal() is False
    assert controller.state_machine.state is GameState.LOCATION_SEARCH
    assert controller.last_leveling_intent == "FARM"
    assert controller.last_leveling_reason == "ready_to_farm"


def test_configured_route_captures_area_checkpoint_when_session_starts_in_hunt(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 9,
        "target_location_name": "Белая Рысь [6]",
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_page_kind = "hunt"
    controller.current_location_name = None
    controller._last_alive_location_name = None

    assert controller._maybe_start_configured_location_route() is True
    assert [request.action_type for request in sink.requests] == ["open_area"]
    assert sink.requests[0].metadata["reason"] == "capture_configured_route_checkpoint"


def test_location_route_executes_one_confirmed_step_per_location(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "route_settle_ms": 1, "navigator_timeout_ms": 10000}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="parent-client",
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller._navigator_target_name = "Порт Барбуса"
    controller._route_destination_name = "Порт Барбуса"
    controller._route_expected_transitions = 2
    controller._route_go_submitted_monotonic = time.monotonic() - 1
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "sections": {
            "location": {"data": {"pageKind": "area", "semanticName": "Курганы бренности"}},
            "battle": {"data": {"rawHasFight": False, "hasFight": False}},
        },
    }
    route = {
        "ok": True,
        "timerReady": True,
        "currentLocationId": "110",
        "targetLocationId": "200",
        "foundPath": ["121", "200"],
        "nextTransition": {"locId": "121", "name": "Туманные луга"},
    }
    controller._location_route_snapshot_via_injector = lambda: route

    controller._handle_route_recovery()
    controller._handle_route_recovery()

    assert [request.action_type for request in sink.requests] == ["location_route_step"]
    assert sink.requests[0].metadata["expected_current_location_id"] == "110"
    assert controller._route_destination_id == "200"

    route = {
        **route,
        "currentLocationId": "121",
        "foundPath": ["200"],
        "nextTransition": {"locId": "200", "name": "Порт Барбуса"},
    }
    controller._handle_route_recovery()

    assert [request.action_type for request in sink.requests] == [
        "location_route_step",
        "location_route_step",
    ]
    assert sink.requests[1].metadata["expected_current_location_id"] == "121"


def test_location_route_waits_for_transition_timer(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "route_settle_ms": 1}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="parent-client",
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller._route_destination_name = "Порт Барбуса"
    controller._route_expected_transitions = 1
    controller._route_go_submitted_monotonic = time.monotonic() - 1
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "sections": {
            "location": {"data": {"pageKind": "area", "semanticName": "Порт безбрежного моря"}},
            "battle": {"data": {"rawHasFight": False, "hasFight": False}},
        },
    }
    controller._location_route_snapshot_via_injector = lambda: {
        "ok": True,
        "timerReady": False,
        "transitionTimerSeconds": 4,
        "currentLocationId": "125",
        "targetLocationId": "200",
        "foundPath": ["200"],
        "nextTransition": {"locId": "200", "name": "Порт Барбуса"},
    }

    controller._handle_route_recovery()

    assert sink.requests == []
    assert controller.state_machine.state is GameState.ROUTE_RECOVERY


def test_location_route_confirms_saved_destination_after_compass_resets(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "route_settle_ms": 1}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="parent-client",
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller._route_destination_name = "Порт Барбуса"
    controller._route_destination_id = "200"
    controller._route_expected_transitions = 1
    controller._route_go_submitted_monotonic = time.monotonic() - 1
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "sections": {
            "location": {"data": {"pageKind": "area", "semanticName": None}},
            "battle": {"data": {"rawHasFight": False, "hasFight": False}},
        },
    }
    controller._location_route_snapshot_via_injector = lambda: {
        "ok": True,
        "timerReady": False,
        "currentLocationId": "200",
        "targetLocationId": "0",
        "foundPath": [],
        "nextTransition": None,
    }

    controller._handle_route_recovery()

    assert controller.state_machine.state is GameState.LOCATION_SEARCH
    assert [request.action_type for request in sink.requests] == ["open_hunt"]


def test_route_interrupted_by_battle_is_resumed_after_hunt_return(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "route_settle_ms": 1}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="parent-client",
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller.current_location_name = "Курганы бренности"
    controller._route_destination_name = "Порт Барбуса"
    controller._route_go_submitted_monotonic = time.monotonic() - 1
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "sections": {
            "location": {"data": {"pageKind": "battle", "semanticName": None}},
            "battle": {"data": {"rawHasFight": True, "hasFight": True, "finished": False}},
        },
    }

    controller._handle_route_recovery()

    assert controller.state_machine.state is GameState.BATTLE_ACTIVE
    assert controller._route_resume_target_name == "Порт Барбуса"

    controller.state_machine.state = GameState.COOLDOWN
    controller.current_location_name = "Курганы бренности"
    controller._complete_or_mark_incomplete_after_hunt_return(blank_frame(), reason="pvp_finished")

    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING
    assert controller._navigator_target_name == "Порт Барбуса"
    assert [request.action_type for request in sink.requests] == ["open_location_navigator"]


def test_death_during_route_returns_to_checkpoint_then_resumes_destination(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "enabled": False, "max_deaths_per_session": 3}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="parent-client",
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller.current_page_kind = "area"
    controller.current_location_name = "Курганы бренности"
    controller._last_alive_location_name = "Курганы бренности"
    controller._route_destination_name = "Порт Барбуса"

    assert controller._handle_leveling_death(
        {"dead": True, "freeReviveAvailable": True, "freeReviveOptionCount": 1}
    ) is True
    assert controller._route_resume_target_name == "Порт Барбуса"

    controller.current_location_name = "Город Барбус"
    assert controller._handle_leveling_death({"dead": False, "freeReviveAvailable": False}) is True
    _complete_post_revive_resource_gate(controller)
    assert controller._navigator_target_name == "Курганы бренности"

    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller.current_location_name = "Курганы бренности"
    controller._route_destination_name = "Курганы бренности"
    assert controller._finish_route_arrival("checkpoint_arrived") is True

    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING
    assert controller._navigator_target_name == "Порт Барбуса"
    assert [request.action_type for request in sink.requests] == [
        "revive_free",
        "open_location_navigator",
        "open_location_navigator",
    ]


def test_leveling_death_cap_stops_before_revive(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
        "max_deaths_per_session": 0,
    }
    controller = AutomationController(AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller._state_snapshot_via_injector = lambda: {
        "schemaVersion": 1,
        "sections": {
            "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20}},
            "location": {"data": {"pageKind": "other", "semanticName": None}},
            "deathRevive": {"data": {"dead": True, "freeReviveAvailable": True}},
            "quests": {"data": {"items": []}},
        },
    }

    controller.process_frame(blank_frame())

    assert controller.state_machine.state == GameState.STOPPED
    assert controller.last_error_reason == "death_recovery:max_deaths_reached"
    assert sink.requests == []


def test_leveling_ambiguous_revive_times_out_without_retry(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "revive_verify_timeout_ms": 1000,
    }
    controller = AutomationController(AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    controller.state_machine.state = GameState.REVIVE_PENDING
    controller._revive_requested_monotonic = time.monotonic() - 2
    controller._revive_attempted_for_current_death = True

    assert controller._handle_leveling_death({"dead": None}) is True
    assert controller.state_machine.state == GameState.STOPPED
    assert controller.last_error_reason == "free_revive_result_ambiguous"


def test_leveling_external_revive_recovers_dead_state(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "enabled": True, "target_level": 10}
    controller = AutomationController(AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.DEAD
    controller._death_latched = True
    controller.current_location_name = "Городская площадь"
    controller._death_checkpoint = RecoveryCheckpoint(
        activity=GameState.LOCATION_SEARCH.value,
        location="Городская площадь",
        quest=None,
        snapshot_id="external-death",
    )

    assert controller._handle_leveling_death({"dead": False}) is True
    _complete_post_revive_resource_gate(controller)
    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert [request.action_type for request in sink.requests] == ["open_hunt"]


def test_leveling_revive_restores_active_quest_route_checkpoint(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "auto_navigate_quest_targets": True,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_page_kind = "hunt"
    controller.current_location_name = "Дикий предел"
    controller._active_quest_id = "quest-91"
    controller._current_state_snapshot_id = "dead-state"

    assert controller._handle_leveling_death(
        {"dead": True, "freeReviveAvailable": True, "freeReviveOptionCount": 1}
    ) is True
    assert controller.state_machine.state is GameState.REVIVE_PENDING

    controller._current_state_snapshot_id = "alive-state"
    assert controller._handle_leveling_death({"dead": False, "freeReviveAvailable": False}) is True
    _complete_post_revive_resource_gate(controller)

    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING, controller.last_error_reason
    assert [request.action_type for request in sink.requests] == ["revive_free", "open_quests"]
    assert sink.requests[-1].metadata["checkpoint_quest"] == "quest-91"
    assert sink.requests[-1].metadata["checkpoint_location"] == "Дикий предел"


def test_leveling_periodically_refreshes_quest_targets(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["max_cycles"] = 10
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "quest_refresh_every_cycles": 5,
    }
    controller = AutomationController(AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False))
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_page_kind = "hunt"

    assert controller._maybe_start_quest_refresh() is False
    controller.session.completed_cycles = 5
    assert controller._maybe_start_quest_refresh() is True
    assert controller.state_machine.state == GameState.QUEST_REFRESH_PENDING
    controller.current_page_kind = "quests"
    controller._update_quest_target_names(
        {
            "items": [
                {
                    "objective": "Убейте Волколаков-живодеров и вернитесь к Вилене",
                    "navigation": [
                        {"text": "Дикий предел"},
                    ],
                }
            ]
        }
    )
    controller._handle_quest_refresh()

    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert controller._next_quest_refresh_cycle == 10
    assert controller._quest_target_names == ("Волколаков-живодеров",)
    assert controller._quest_route_locations == ("Дикий предел",)
    assert controller._quest_target_routes == {"Волколаков-живодеров": ("Дикий предел",)}
    assert [request.action_type for request in sink.requests] == ["open_quests", "open_hunt"]


def test_autonomous_quest_director_collects_every_catalog_page_before_intake(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["max_cycles"] = 10
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "autonomous_quest_director": True,
        "quest_catalog_max_pages": 3,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="replay", logger=InMemoryEventLogger()
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_page_kind = "area"
    controller._current_state_snapshot_id = "before-catalog"

    assert controller._maybe_start_quest_refresh()
    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING, controller.last_error_reason
    assert [(request.action_type, request.metadata["page"]) for request in sink.requests] == [
        ("open_quest_catalog", 0)
    ]

    def catalog_item(quest_id: str, page: int) -> dict[str, object]:
        return {
            "id": quest_id,
            "title": f"Quest {quest_id}",
            "status": "available",
            "description": "Work",
            "reward": "XP",
            "locationText": "In Wilds",
            "giverNames": ["Frank"],
            "navigation": [{"text": "Wilds"}],
            "catalogPage": page,
            "cardIndex": 0,
        }

    controller._observe_autonomous_quest_snapshot(
        {
            "loadStatus": "loaded",
            "mode": "avail",
            "currentPage": 0,
            "pageCount": 1,
            "hasNextPage": False,
            "snapshotId": "before-catalog",
            "items": [],
            "truncated": False,
        }
    )
    assert controller._quest_director.catalog.collected_pages == ()
    assert controller._quest_catalog_page_requested == 0

    controller._observe_autonomous_quest_snapshot(
        {
            "loadStatus": "loaded",
            "mode": "avail",
            "currentPage": 0,
            "pageCount": 2,
            "hasNextPage": True,
            "snapshotId": "catalog-page-0",
            "items": [catalog_item("1", 0)],
            "truncated": False,
        }
    )
    controller._handle_quest_refresh()
    assert [(request.action_type, request.metadata["page"]) for request in sink.requests] == [
        ("open_quest_catalog", 0),
        ("open_quest_catalog", 1),
    ]

    controller._observe_autonomous_quest_snapshot(
        {
            "loadStatus": "loaded",
            "mode": "avail",
            "currentPage": 1,
            "pageCount": 2,
            "hasNextPage": False,
            "snapshotId": "catalog-page-1",
            "items": [catalog_item("2", 1)],
            "truncated": False,
        }
    )
    controller._handle_quest_refresh()

    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert [request.action_type for request in sink.requests] == [
        "open_quest_catalog",
        "open_quest_catalog",
        "open_quests",
    ]
    stale_active_id = controller._quest_active_request_snapshot_id
    controller._observe_autonomous_quest_snapshot(
        {
            "loadStatus": "loaded",
            "mode": "started",
            "snapshotId": stale_active_id,
            "items": [],
            "truncated": False,
        }
    )
    assert controller._quest_director.active_snapshot_fresh is False
    controller._observe_autonomous_quest_snapshot(
        {"loadStatus": "loaded", "mode": "started", "snapshotId": "active-1", "items": [], "truncated": False}
    )
    controller._handle_quest_refresh()

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "quest_accept_executor_pending"
    assert [quest.id for quest in controller._quest_director.intake_queue] == ["1", "2"]


def test_autonomous_quest_director_enters_profit_farm_only_after_fresh_empty_catalog(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["max_cycles"] = 10
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "autonomous_quest_director": True,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="replay", logger=InMemoryEventLogger()
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_page_kind = "area"
    controller._maybe_start_quest_refresh()
    controller.current_page_kind = "quests"
    controller._observe_autonomous_quest_snapshot(
        {
            "loadStatus": "loaded",
            "mode": "avail",
            "currentPage": 0,
            "pageCount": 1,
            "hasNextPage": False,
            "snapshotId": "empty-catalog",
            "items": [],
            "truncated": False,
        }
    )

    controller._handle_quest_refresh()

    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING
    controller._observe_autonomous_quest_snapshot(
        {"loadStatus": "loaded", "mode": "started", "snapshotId": "empty-active", "items": [], "truncated": False}
    )
    controller._handle_quest_refresh()

    assert controller.state_machine.state is GameState.LOCATION_SEARCH, controller.last_error_reason
    assert [request.action_type for request in sink.requests] == [
        "open_quest_catalog",
        "open_quests",
        "open_hunt",
    ]
    assert controller._quest_director_decision().intent.value == "PROFIT_FARM"

    controller.session.completed_cycles = 5
    assert controller._maybe_start_quest_refresh()
    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert [request.action_type for request in sink.requests][-1] == "open_quest_catalog"


def test_loaded_quest_snapshot_is_bound_to_tab_and_atomically_selects_route(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict
    from src.antibot_cv.automation.quest_policy import QuestIntent
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
        "auto_navigate_quest_targets": True,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="client-a",
    )
    controller.current_character_name = "v3g45"
    controller.current_level = 5
    controller.current_xp_percent = 20.0
    controller.current_page_kind = "quests"
    controller._quest_origin_location_name = "Старая локация"
    controller._last_state_snapshot_client_id = "client-a"

    class FakeInjector:
        profile_id = "profile-a"

        def client_snapshot(self, client_id):
            return {
                "client_id": client_id,
                "client_seen": True,
                "version_ok": True,
                "profile_id": self.profile_id,
                "tab_id": 17,
            }

    fake = FakeInjector()
    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: fake)
    state = {"snapshotId": "state-1", "generatedAt": time.time()}
    section = {"source": {"href": "https://3kingdoms.ru/user_quest.php?mode=started"}}
    quest_data = {
        "loadStatus": "loaded",
        "snapshotId": "state-1",
        "generatedAt": state["generatedAt"],
        "pageKind": "quests",
        "href": "https://3kingdoms.ru/user_quest.php?mode=started",
        "items": [
            {
                "id": "91",
                "title": "Охота",
                "status": "active",
                "objectiveKind": "combat",
                "objective": "Уничтожьте 5 волколаков-живодеров в Диком пределе.",
                "navigation": [{"text": "Дикий предел"}],
            }
        ],
    }

    decision = controller._evaluate_quest_policy(state, section, quest_data)

    assert decision.intent is QuestIntent.NAVIGATE
    assert controller._quest_target_names == ("волколаков-живодеров",)
    assert controller._quest_route_locations == ("Дикий предел",)
    assert controller._quest_target_routes == {"волколаков-живодеров": ("Дикий предел",)}

    fake.profile_id = "profile-b"
    mismatched = controller._evaluate_quest_policy(
        {"snapshotId": "state-2", "generatedAt": time.time()}, section, {**quest_data, "snapshotId": "state-2"}
    )
    assert mismatched.intent is QuestIntent.STOP_UNSAFE
    assert mismatched.reason == "quest_snapshot_identity_mismatch"
    assert controller._quest_target_names == ()
    assert controller._quest_route_locations == ()


def test_empty_loaded_quest_snapshot_cannot_start_quest_driven_farm(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict
    from src.antibot_cv.automation.quest_policy import QuestIntent
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "required_character_name": "v3g45",
        "auto_navigate_quest_targets": True,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="client-a",
    )
    controller.current_character_name = "v3g45"
    controller.current_level = 5
    controller.current_xp_percent = 20.0
    controller.current_page_kind = "quests"
    controller._quest_origin_location_name = "Лес"
    controller._last_state_snapshot_client_id = "client-a"

    class FakeInjector:
        def client_snapshot(self, client_id):
            return {"client_id": client_id, "profile_id": "profile-a", "tab_id": 17}

    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: FakeInjector())
    now = time.time()
    decision = controller._evaluate_quest_policy(
        {"snapshotId": "empty", "generatedAt": now},
        {"source": {"href": "https://3kingdoms.ru/user_quest.php?mode=started"}},
        {
            "loadStatus": "loaded",
            "snapshotId": "empty",
            "generatedAt": now,
            "pageKind": "quests",
            "href": "https://3kingdoms.ru/user_quest.php?mode=started",
            "items": [],
        },
    )

    assert decision.intent is QuestIntent.STOP_UNSAFE
    assert decision.reason == "no_active_combat_quest"
    assert controller._quest_target_names == ()


def test_leveling_routes_through_child_navigator_for_matching_quest_location(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    from src.antibot_cv.automation.browser_injector import InjectorResult
    from src.antibot_cv.automation.config import to_plain_dict
    from src.antibot_cv.automation.quest_policy import QuestIntent
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "auto_navigate_quest_targets": True,
        "route_settle_ms": 1,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="parent-client",
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_page_kind = "quests"
    controller._quest_refresh_requested_monotonic = time.monotonic()
    controller._update_quest_target_names(
        {
            "items": [
                {
                    "objective": "Уничтожьте 5 волколаков-живодеров в Диком пределе.",
                    "navigation": [{"text": "Дикий предел"}],
                }
            ]
        }
    )
    controller._quest_policy_intent = QuestIntent.NAVIGATE
    controller._safe_transition(GameState.QUEST_REFRESH_PENDING, reason="test")

    class FakeInjector:
        def client_snapshot(self, client_id):
            assert client_id == "parent-client"
            return {"client_id": client_id, "profile_id": "profile-1", "tab_id": 101}

        def client_snapshots(self, *, within_s):
            assert within_s == 5.0
            return [
                {
                    "client_id": "navigator-client",
                    "client_seen": True,
                    "version_ok": True,
                    "profile_id": "profile-1",
                    "opener_tab_id": 101,
                    "href": "https://3kingdoms.ru/navigator.php?name=x",
                }
            ]

        def execute(self, command, *, timeout_s, client_id):
            assert command == "navigator_snapshot"
            assert timeout_s == 2.5
            assert client_id == "navigator-client"
            return InjectorResult(
                True,
                json.dumps(
                        {
                            "snapshotId": "navigator-state-1",
                            "generatedAt": time.time(),
                            "href": "https://3kingdoms.ru/navigator.php?name=x",
                            "target": "Дикий предел",
                            "currentLocation": False,
                            "hasRoute": True,
                            "routeTransitions": 2,
                            "visibleGoButtonCount": 1,
                        }
                ),
                client_id=client_id,
            )

    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: FakeInjector())

    controller._handle_quest_refresh()
    assert controller.state_machine.state == GameState.NAVIGATOR_PENDING
    assert controller._navigator_target_name == "Дикий предел"

    controller._handle_navigator_pending()
    assert controller.state_machine.state == GameState.ROUTE_RECOVERY
    controller._route_go_submitted_monotonic = time.monotonic() - 1
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "sections": {
            "location": {
                "status": "available",
                "data": {"pageKind": "area", "semanticName": "Дикий предел"},
            }
        },
    }
    controller._handle_route_recovery()

    assert controller.state_machine.state == GameState.LOCATION_SEARCH
    assert [request.action_type for request in sink.requests] == [
        "open_quest_navigator",
        "navigator_go",
        "open_hunt",
    ]
    assert sink.requests[1].metadata["navigator_client_id"] == "navigator-client"


def test_leveling_stops_on_ambiguous_quest_route_without_opening_hunt(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict
    from src.antibot_cv.automation.quest_policy import QuestIntent

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "auto_navigate_quest_targets": True,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_page_kind = "quests"
    controller._quest_refresh_requested_monotonic = time.monotonic()
    controller._quest_route_locations = ("Дикий предел", "Прокаленное плато")
    controller._quest_policy_intent = QuestIntent.NAVIGATE
    controller._safe_transition(GameState.QUEST_REFRESH_PENDING, reason="test")

    controller._handle_quest_refresh()

    assert controller.state_machine.state == GameState.STOPPED
    assert controller.last_error_reason == "quest_route_target_missing_or_ambiguous"
    assert sink.requests == []


def test_leveling_stops_when_navigator_submission_has_no_parent_page_postcondition(
    test_config: AutomationConfig,
) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "target_level": 10,
        "auto_navigate_quest_targets": True,
        "route_settle_ms": 1,
        "navigator_timeout_ms": 1,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="live", logger=InMemoryEventLogger(dry_run=False)
    )
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller._navigator_target_name = "Дикий предел"
    controller._route_go_submitted_monotonic = time.monotonic() - 2
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "sections": {"location": {"data": {"pageKind": "quests", "semanticName": None}}},
    }

    controller._handle_route_recovery()

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "navigator_route_result_unconfirmed"


def test_authoritative_empty_quest_snapshot_clears_old_target_names(test_config: AutomationConfig) -> None:
    controller = AutomationController(test_config, sink_mode="replay", logger=InMemoryEventLogger())
    controller._quest_target_names = ("Старый моб",)
    controller._quest_route_locations = ("Старая локация",)
    controller._quest_target_routes = {"Старый моб": ("Старая локация",)}

    controller._update_quest_target_names({"items": []})

    assert controller._quest_target_names == ()
    assert controller._quest_route_locations == ()
    assert controller._quest_target_routes == {}


def test_scrollbar_fallback_after_direction_search_exhausted(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["viewport"] = {
        **data["viewport"],
        "max_moves_per_search": 1,
        "scrollbar_enabled": True,
        "scrollbar_min_confidence": 0.5,
        "scrollbar_step_px": 60,
        "scrollbar_sequence": ["DOWN"],
        "max_scrollbar_moves_per_search": 1,
    }
    data["safety"] = {**data["safety"], "max_viewport_moves_per_search": 3}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(blank_frame())
    frame = blank_frame()
    frame[70:200, 280:300] = (0, 0, 200)
    controller.process_frame(frame)

    assert any(event["event_type"] == "viewport_scroll" for event in logger.events)
    assert any(event["event_type"] == "replay_intended_action" and event["action_type"] == "drag_scrollbar" for event in logger.events)


def test_scrollbar_interleaves_with_direction_search(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["viewport"] = {
        **data["viewport"],
        "max_moves_per_search": 4,
        "scrollbar_enabled": True,
        "scrollbar_every_direction_moves": 1,
        "scrollbar_min_confidence": 0.5,
        "scrollbar_step_px": 60,
        "scrollbar_sequence": ["DOWN"],
        "max_scrollbar_moves_per_search": 2,
    }
    data["safety"] = {**data["safety"], "max_viewport_moves_per_search": 8}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)
    frame = blank_frame()
    frame[70:200, 280:300] = (0, 0, 200)

    controller.process_frame(frame)
    controller.process_frame(frame)

    action_types = [event.get("action_type") for event in logger.events if event["event_type"] == "replay_intended_action"]
    assert action_types[:2] == ["viewport_move", "drag_scrollbar"]


def test_viewport_moves_wait_for_settle_interval(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    data = to_plain_dict(test_config)
    data["viewport"] = {**data["viewport"], "settle_ms": 1000, "max_moves_per_search": 3}
    config = AutomationConfig.from_dict(data)
    logger = InMemoryEventLogger()
    controller = AutomationController(config, sink_mode="replay", logger=logger)

    controller.process_frame(blank_frame())
    controller.process_frame(blank_frame())

    assert len([event for event in logger.events if event.get("action_type") == "viewport_move"]) == 1


def test_replay_mode_writes_summary(tmp_path, test_config: AutomationConfig) -> None:
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    for idx, frame in enumerate([target_frame(), battle_frame(), battle_end_frame(), statistics_frame(), location_map_frame()]):
        cv2.imwrite(str(frame_dir / f"{idx:03d}.png"), frame)
    config_path = tmp_path / "automation.json"
    data = config_to_dict(test_config)
    data["runs_dir"] = str(tmp_path / "runs")
    config_path.write_text(json.dumps(data), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "src.antibot_cv.automation.controller", "replay", "--config", str(config_path), "--input", str(frame_dir)],
        cwd=".",
        text=True,
        capture_output=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["completed_cycles"] == 1
    assert summary["dry_run"] is True


def test_inspect_resources_cli_writes_artifacts(tmp_path, test_config: AutomationConfig) -> None:
    frame_path = tmp_path / "resources.png"
    cv2.imwrite(str(frame_path), resource_frame(health=40, prowess=60))
    config_path = tmp_path / "automation.json"
    data = config_to_dict(test_config)
    data["resources"] = {
        "enabled": True,
        "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
        "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
    }
    config_path.write_text(json.dumps(data), encoding="utf-8")
    output_frame = tmp_path / "raw.png"
    output_overlay = tmp_path / "overlay.png"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.antibot_cv.automation.controller",
            "inspect-resources",
            "--config",
            str(config_path),
            "--input",
            str(frame_path),
            "--output-frame",
            str(output_frame),
            "--output-overlay",
            str(output_overlay),
        ],
        cwd=".",
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["health"]["percent"] == 41
    assert payload["prowess"]["percent"] == 61
    assert output_frame.exists()
    assert output_overlay.exists()


def test_validate_templates_cli_smoke(tmp_path) -> None:
    template = tmp_path / "template.png"
    cv2.imwrite(str(template), blank_frame(10, 10))
    templates = tmp_path / "templates.json"
    templates.write_text(
        json.dumps(
            {
                "templates": [
                    {
                        "template_id": "ok",
                        "path": str(template),
                        "threshold": 0.8,
                        "scales": [1.0],
                        "max_results": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = AutomationConfig.from_dict({"templates_path": str(templates)})
    config_path = tmp_path / "automation.json"
    config_path.write_text(json.dumps(config_to_dict(config)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "src.antibot_cv.automation.controller", "validate-templates", "--config", str(config_path)],
        cwd=".",
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["ok"] is True


def config_to_dict(config: AutomationConfig) -> dict:
    from src.antibot_cv.automation.config import to_plain_dict

    return to_plain_dict(config)
