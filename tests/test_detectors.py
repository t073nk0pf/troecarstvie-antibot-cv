from __future__ import annotations

import cv2
import numpy as np

from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.detection.attack import AttackButtonDetector
from src.antibot_cv.detection.ability import AbilityBarDetector
from src.antibot_cv.detection.battle import BattleDetector
from src.antibot_cv.detection.battle_end import BattleEndDetector
from src.antibot_cv.detection.resources import ResourceDetector
from src.antibot_cv.detection.statistics import StatisticsDetector
from src.antibot_cv.detection.templates import TemplateEntry, TemplateRegistry, match_template
from src.antibot_cv.viewport.coordinates import Rect
from tests.conftest import battle_end_frame, battle_frame, blank_frame, central_battle_frame, resource_frame, statistics_frame, statistics_red_frame


def test_battle_insufficient_signals(test_config: AutomationConfig) -> None:
    detector = BattleDetector(test_config.battle)
    assert detector.detect(blank_frame()).detected is False


def test_battle_confirmed_signals(test_config: AutomationConfig) -> None:
    detector = BattleDetector(test_config.battle)
    result = detector.detect(battle_frame())
    assert result.detected is True
    assert result.confidence > 0.5


def test_battle_detects_central_live_layout(test_config: AutomationConfig) -> None:
    result = BattleDetector(test_config.battle).detect(central_battle_frame())

    assert result.detected is True
    assert result.panel_bbox is not None
    assert result.panel_bbox.y > 50


def test_battle_panel_ignores_light_game_shell_red_bars() -> None:
    config = AutomationConfig.from_dict(
        {
            "battle": {"confirm_frames": 1, "min_signals": 1, "weighted_threshold": 0.8},
            "templates_path": "tests/fixtures/no-templates.json",
        }
    )
    frame = blank_frame(2048, 1280)
    frame[:, :] = (150, 185, 205)
    cv2.rectangle(frame, (430, 260), (1320, 720), (170, 205, 220), -1)
    cv2.rectangle(frame, (700, 220), (805, 232), (0, 0, 210), -1)
    cv2.rectangle(frame, (820, 224), (930, 235), (0, 0, 210), -1)
    cv2.rectangle(frame, (955, 222), (1065, 234), (0, 0, 210), -1)
    cv2.putText(frame, "0", (900, 285), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 230, 230), 2)

    result = BattleDetector(config.battle).detect(frame)

    assert result.detected is False
    assert result.panel_bbox is None


def test_battle_debounce(test_config: AutomationConfig) -> None:
    config = test_config.with_overrides()
    config = AutomationConfig.from_dict({**config_to_dict(config), "battle": {"confirm_frames": 2, "min_signals": 2, "weighted_threshold": 1.5}})
    detector = BattleDetector(config.battle)
    assert detector.detect(battle_frame()).detected is False
    assert detector.detect(battle_frame()).detected is True


def test_ability_slot4_selection(test_config: AutomationConfig) -> None:
    detector = AbilityBarDetector(test_config.ability4)
    slot = detector.slot4(battle_frame(), Rect(0, 0, 320, 240))
    assert slot is not None
    assert slot.index == 4
    assert slot.confidence >= test_config.ability4.slot_confidence_threshold


def test_ability_slot4_selection_from_central_battle_panel(test_config: AutomationConfig) -> None:
    frame = central_battle_frame()
    battle = BattleDetector(test_config.battle).detect(frame)
    slot = AbilityBarDetector(test_config.ability4).slot4(frame, battle.panel_bbox)

    assert slot is not None
    assert slot.index == 4
    assert slot.center.y > battle.panel_bbox.y + battle.panel_bbox.height * 0.6
    assert slot.confidence >= test_config.ability4.slot_confidence_threshold


def test_ability_slot4_selection_ignores_neighbor_circles(test_config: AutomationConfig) -> None:
    frame = central_battle_frame()
    expected_slot4 = (int(1280 * 0.37) + 214, int(720 * 0.18) + int(int(720 * 0.36) * 0.78))
    for center in ((expected_slot4[0] + 95, expected_slot4[1] - 6), (expected_slot4[0] + 138, expected_slot4[1] + 8)):
        cv2.circle(frame, center, 20, (40, 40, 40), -1)
        cv2.circle(frame, center, 18, (150, 150, 150), 2)

    battle = BattleDetector(test_config.battle).detect(frame)
    slot = AbilityBarDetector(test_config.ability4).slot4(frame, battle.panel_bbox)

    assert slot is not None
    assert abs(slot.center.x - expected_slot4[0]) <= 35
    assert abs(slot.center.y - expected_slot4[1]) <= 35


def test_attack_button_color_fallback_detects_top_button() -> None:
    frame = blank_frame(1280, 720)
    cv2.rectangle(frame, (690, 92), (820, 128), (0, 0, 180), -1)
    cv2.rectangle(frame, (700, 98), (810, 122), (45, 38, 70), -1)
    cv2.rectangle(frame, (735, 103), (800, 114), (0, 0, 210), -1)
    cv2.rectangle(frame, (825, 96), (980, 108), (0, 255, 0), -1)
    cv2.rectangle(frame, (825, 112), (930, 122), (245, 245, 245), -1)

    result = AttackButtonDetector().detect(frame)

    assert result.detected is True
    assert result.center is not None
    assert 705 <= result.center.x <= 725


def test_attack_button_color_fallback_ignores_blank() -> None:
    assert AttackButtonDetector().detect(blank_frame(640, 400)).detected is False


def test_attack_button_color_fallback_ignores_toolbar_icon() -> None:
    frame = blank_frame(1280, 720)
    cv2.rectangle(frame, (690, 92), (820, 128), (0, 0, 180), -1)
    cv2.rectangle(frame, (825, 90), (900, 130), (0, 0, 160), -1)
    cv2.rectangle(frame, (910, 90), (970, 130), (220, 140, 40), -1)

    result = AttackButtonDetector().detect(frame)

    assert result.detected is False


def test_battle_end_popup_and_button(test_config: AutomationConfig) -> None:
    detector = BattleEndDetector(test_config.battle_end)
    result = detector.detect(battle_end_frame())
    assert result.detected is True
    assert result.popup_bbox is not None
    assert result.exit_button_bbox is not None


def test_battle_end_exit_button_only_can_confirm(test_config: AutomationConfig) -> None:
    frame = blank_frame(320, 240)
    cv2.rectangle(frame, (110, 170), (210, 205), (0, 128, 255), -1)

    result = BattleEndDetector(test_config.battle_end).detect(frame)

    assert result.detected is True
    assert result.exit_button_bbox is not None


def test_battle_end_detects_small_exit_button_on_large_frame(test_config: AutomationConfig) -> None:
    frame = blank_frame(2048, 1280)
    cv2.rectangle(frame, (990, 460), (1055, 505), (255, 0, 255), -1)
    cv2.rectangle(frame, (1000, 535), (1052, 550), (0, 128, 255), -1)

    result = BattleEndDetector(test_config.battle_end).detect(frame)

    assert result.detected is True
    assert result.exit_button_bbox is not None
    assert abs(result.exit_button_bbox.center.x - 1026) <= 8
    assert abs(result.exit_button_bbox.center.y - 542) <= 8


def test_battle_end_ignores_huge_orange_location_background(test_config: AutomationConfig) -> None:
    frame = blank_frame(1280, 720)
    cv2.rectangle(frame, (0, 120), (950, 640), (0, 128, 255), -1)
    cv2.rectangle(frame, (720, 80), (780, 145), (255, 0, 255), -1)

    result = BattleEndDetector(test_config.battle_end).detect(frame)

    assert result.detected is False
    assert result.exit_button_bbox is None


def test_battle_end_debounce(test_config: AutomationConfig) -> None:
    config = AutomationConfig.from_dict({**config_to_dict(test_config), "battle_end": {"confirm_frames": 2, "threshold": 0.7}})
    detector = BattleEndDetector(config.battle_end)
    assert detector.detect(battle_end_frame()).detected is False
    assert detector.detect(battle_end_frame()).detected is True


def test_statistics_insufficient_signals(test_config: AutomationConfig) -> None:
    detector = StatisticsDetector(test_config.statistics)
    assert detector.detect(blank_frame()).detected is False


def test_statistics_confirmed_and_hunt_button(test_config: AutomationConfig) -> None:
    detector = StatisticsDetector(test_config.statistics)
    result = detector.detect(statistics_frame())
    assert result.detected is True
    assert result.feature_count >= 2
    assert result.hunt_button_bbox is not None


def test_statistics_red_screen_detects_hunt_button(test_config: AutomationConfig) -> None:
    detector = StatisticsDetector(test_config.statistics)
    result = detector.detect(statistics_red_frame())
    assert result.detected is True
    assert result.feature_count >= 2
    assert result.hunt_button_bbox is not None
    assert result.hunt_button_bbox.y >= 120


def test_statistics_detects_merged_red_hunt_button_stack(test_config: AutomationConfig) -> None:
    frame = blank_frame(640, 400)
    red = (0, 0, 220)
    cv2.rectangle(frame, (280, 136), (374, 168), red, -1)

    result = StatisticsDetector(test_config.statistics).detect(frame)

    assert result.detected is True
    assert result.hunt_button_bbox is not None
    assert result.hunt_button_bbox.y >= 156


def test_statistics_does_not_detect_location_map_labels(test_config: AutomationConfig) -> None:
    frame = blank_frame()
    cv2.rectangle(frame, (110, 95), (220, 108), (0, 255, 0), -1)
    cv2.rectangle(frame, (125, 205), (245, 225), (0, 0, 220), -1)

    result = StatisticsDetector(test_config.statistics).detect(frame)

    assert result.detected is False
    assert result.hunt_button_bbox is None


def test_statistics_does_not_detect_shop_buttons(test_config: AutomationConfig) -> None:
    frame = blank_frame()
    frame[:, :] = (205, 185, 150)
    red = (0, 0, 220)
    cv2.rectangle(frame, (60, 70), (145, 84), red, 2)
    cv2.rectangle(frame, (170, 70), (255, 84), red, 2)
    cv2.rectangle(frame, (70, 125), (145, 139), red, 2)
    cv2.rectangle(frame, (180, 160), (255, 174), red, 2)

    result = StatisticsDetector(test_config.statistics).detect(frame)

    assert result.detected is False
    assert result.hunt_button_bbox is None


def test_statistics_does_not_detect_map_with_single_red_blob(test_config: AutomationConfig) -> None:
    frame = blank_frame()
    frame[:, :] = (190, 170, 140)
    cv2.rectangle(frame, (60, 95), (260, 185), (30, 120, 25), -1)
    cv2.rectangle(frame, (190, 80), (285, 130), (0, 0, 160), -1)

    result = StatisticsDetector(test_config.statistics).detect(frame)

    assert result.detected is False
    assert result.hunt_button_bbox is None


def test_resource_detector_explicit_bars(test_config: AutomationConfig) -> None:
    config = AutomationConfig.from_dict(
        {
            **config_to_dict(test_config),
            "resources": {
                "enabled": True,
                "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
                "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
            },
        }
    )
    result = ResourceDetector(config.resources).detect(resource_frame(health=30, prowess=75))
    assert result.health.detected is True
    assert result.prowess.detected is True
    assert result.health.percent == 31
    assert result.prowess.percent == 76


def test_resource_detector_ignores_sparse_decorative_pixels(test_config: AutomationConfig) -> None:
    config = AutomationConfig.from_dict(
        {
            **config_to_dict(test_config),
            "resources": {
                "enabled": True,
                "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
                "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
            },
        }
    )
    frame = blank_frame()
    cv2.rectangle(frame, (30, 12), (32, 20), (0, 0, 255), -1)
    cv2.rectangle(frame, (110, 28), (116, 36), (255, 0, 0), -1)

    result = ResourceDetector(config.resources).detect(frame)

    assert result.health.detected is False
    assert result.prowess.detected is False


def test_resource_detector_accepts_dense_gapped_bar(test_config: AutomationConfig) -> None:
    config = AutomationConfig.from_dict(
        {
            **config_to_dict(test_config),
            "resources": {
                "enabled": True,
                "health_bar_roi": {"x": 20, "y": 12, "width": 100, "height": 9},
                "prowess_bar_roi": {"x": 20, "y": 28, "width": 100, "height": 9},
            },
        }
    )
    frame = blank_frame()
    cv2.rectangle(frame, (20, 12), (104, 20), (0, 255, 0), -1)
    cv2.rectangle(frame, (48, 12), (50, 20), (0, 0, 0), -1)
    cv2.rectangle(frame, (75, 12), (79, 20), (0, 0, 0), -1)
    cv2.rectangle(frame, (20, 28), (94, 36), (255, 0, 0), -1)

    result = ResourceDetector(config.resources).detect(frame)

    assert result.health.detected is True
    assert result.health.percent == 85
    assert result.prowess.detected is True
    assert result.prowess.percent == 75


def test_template_matching() -> None:
    frame = blank_frame()
    cv2.rectangle(frame, (50, 60), (70, 80), (255, 255, 255), -1)
    template = frame[60:81, 50:71].copy()
    matches = match_template(frame, template, TemplateEntry("white_box", "unused", threshold=0.99, max_results=1))
    assert matches
    assert matches[0].bbox.x == 50


def test_template_registry_missing_file(tmp_path) -> None:
    config = tmp_path / "templates.json"
    config.write_text(
        '{"templates":[{"template_id":"missing","path":"missing.png","threshold":0.8,"scales":[1.0],"max_results":1}]}',
        encoding="utf-8",
    )
    registry = TemplateRegistry.from_file(config)
    issues = registry.validate()
    assert issues[0].message == "template file is missing"


def config_to_dict(config: AutomationConfig) -> dict:
    from src.antibot_cv.automation.config import to_plain_dict

    return to_plain_dict(config)
