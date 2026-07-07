from __future__ import annotations

import json
import subprocess
import sys
import time

import cv2

from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.automation.controller import AutomationController, _apply_runtime_overrides
from src.antibot_cv.automation.actions import DryRunActionSink
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
        "useSkillAvailable": True,
        "fightPath": "top.frames[1].frames[2]",
        "fightHref": "https://3kingdoms.ru/fight.php?1",
        "abilities": [{"id": -4626, "slot": 2, "name": "Возмездие I"}],
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
        "useSkillAvailable": True,
        "fightPath": "top.frames[1].frames[2]",
        "fightHref": "https://3kingdoms.ru/fight.php?abc",
        "abilities": [{"id": -4626, "slot": 2, "name": "Возмездие I"}],
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
            "recoveryHealthThreshold": "85",
            "recoveryProwessThreshold": "80",
        },
    )

    assert config.combat.slot_sequence == (2, 3, 0)
    assert config.combat.low_resource_fallback_enabled is False
    assert config.combat.low_resource_fallback_percent == 3
    assert config.combat.click_interval_ms == 450
    assert config.combat.pre_click_delay_ms == 75
    assert config.combat.click_hold_ms == 25
    assert config.item_recovery.health_use_when_below_percent == 85
    assert config.item_recovery.prowess_use_when_below_percent == 80


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
