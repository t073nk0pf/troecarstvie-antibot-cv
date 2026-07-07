from __future__ import annotations

import cv2

from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.entity_detection.green_labels import GreenLabelDetector
from src.antibot_cv.entity_detection.target_locator import TargetClickEstimator, TargetLocator
from src.antibot_cv.entity_detection.tracker import EntityTracker
from src.antibot_cv.viewport.coordinates import Point, Rect
from tests.conftest import blank_frame, target_frame


def test_green_label_detected(test_config: AutomationConfig) -> None:
    detector = GreenLabelDetector(test_config.target.green_label)
    candidates = detector.detect(target_frame())
    assert len(candidates) == 1
    assert candidates[0].green_pixel_ratio > 0.8
    assert candidates[0].label_color == "green"


def test_red_label_detected_when_enabled(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    config = AutomationConfig.from_dict(
        {
            **to_plain_dict(test_config),
            "target": {
                **to_plain_dict(test_config.target),
                "mode": "any_visible_label",
                "allowed_targets": ["red_label"],
                "preferred_target_order": ["red_label"],
                "green_label": {
                    **to_plain_dict(test_config.target.green_label),
                    "detect_red_labels": True,
                    "max_label_width": 320,
                },
            },
        }
    )
    frame = blank_frame(640, 400)
    cv2.putText(frame, "Artan mob [16]", (170, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 220), 2)

    candidates = GreenLabelDetector(config.target.green_label).detect(frame)
    targets = TargetLocator(config.target, GreenLabelDetector(config.target.green_label)).locate(frame)

    assert any(candidate.label_color == "red" for candidate in candidates)
    assert targets
    assert targets[0].target_id == "red_label"


def test_visible_red_label_requires_allowed_target(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    config = AutomationConfig.from_dict(
        {
            **to_plain_dict(test_config),
            "target": {
                **to_plain_dict(test_config.target),
                "mode": "any_visible_label",
                "allowed_targets": ["green_label"],
                "preferred_target_order": ["green_label"],
                "green_label": {
                    **to_plain_dict(test_config.target.green_label),
                    "detect_red_labels": True,
                    "max_label_width": 320,
                },
            },
        }
    )
    frame = blank_frame(640, 400)
    cv2.putText(frame, "Artan mob [16]", (170, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 220), 2)

    candidates = GreenLabelDetector(config.target.green_label).detect(frame)
    targets = TargetLocator(config.target, GreenLabelDetector(config.target.green_label)).locate(frame)

    assert any(candidate.label_color == "red" for candidate in candidates)
    assert targets == []


def test_red_label_ignored_in_green_only_mode(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    config = AutomationConfig.from_dict(
        {
            **to_plain_dict(test_config),
            "target": {
                **to_plain_dict(test_config.target),
                "green_label": {
                    **to_plain_dict(test_config.target.green_label),
                    "detect_red_labels": True,
                    "max_label_width": 320,
                },
            },
        }
    )
    frame = blank_frame(640, 400)
    cv2.putText(frame, "Artan mob [16]", (170, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 220), 2)

    targets = TargetLocator(config.target, GreenLabelDetector(config.target.green_label)).locate(frame)

    assert targets == []


def test_green_sprite_detected_when_label_is_missing(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    config = AutomationConfig.from_dict(
        {
            **to_plain_dict(test_config),
            "target": {
                **to_plain_dict(test_config.target),
                "allowed_targets": ["green_label", "green_sprite"],
                "preferred_target_order": ["green_label", "green_sprite"],
                "click_offset": {"dx": -35, "dy": -80},
            },
        }
    )
    frame = blank_frame(400, 260)
    cv2.circle(frame, (190, 120), 16, (0, 220, 0), -1)
    cv2.circle(frame, (212, 118), 10, (0, 0, 220), -1)

    targets = TargetLocator(config.target, GreenLabelDetector(config.target.green_label)).locate(frame)

    assert targets
    assert targets[0].target_id == "green_sprite"
    assert abs(targets[0].interaction_point.x - targets[0].label_center.x) <= 1
    assert abs(targets[0].interaction_point.y - targets[0].label_center.y) <= 1


def test_green_sprite_requires_red_companion(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    config = AutomationConfig.from_dict(
        {
            **to_plain_dict(test_config),
            "target": {
                **to_plain_dict(test_config.target),
                "allowed_targets": ["green_label", "green_sprite"],
                "preferred_target_order": ["green_label", "green_sprite"],
            },
        }
    )
    frame = blank_frame(400, 260)
    cv2.circle(frame, (190, 120), 16, (0, 220, 0), -1)

    targets = TargetLocator(config.target, GreenLabelDetector(config.target.green_label)).locate(frame)

    assert targets == []


def test_mob_sprite_template_detected_from_database(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    template = cv2.imread("assets/templates/targets/mob_sprite_02.png", cv2.IMREAD_COLOR)
    assert template is not None
    config = AutomationConfig.from_dict(
        {
            **to_plain_dict(test_config),
            "templates_path": "config/templates.example.json",
            "target": {
                **to_plain_dict(test_config.target),
                "mode": "template_label",
                "allowed_targets": ["mob_sprite_02"],
                "preferred_target_order": ["mob_sprite_02"],
                "sprite_template_ids": ["mob_sprite_02"],
                "click_offset": {"dx": -35, "dy": -80},
            },
        }
    )
    frame = blank_frame(360, 260)
    y, x = 90, 140
    h, w = template.shape[:2]
    frame[y : y + h, x : x + w] = template
    locator = TargetLocator(
        config.target,
        GreenLabelDetector(config.target.green_label),
        template_registry=TemplateRegistry.from_file(config.templates_path),
    )

    targets = locator.locate(frame)

    assert targets
    assert targets[0].target_id == "mob_sprite_02"
    assert abs(targets[0].interaction_point.x - (x + w / 2)) <= 1
    assert abs(targets[0].interaction_point.y - (y + h / 2)) <= 1


def test_visible_label_near_map_edge_is_not_clamped_into_target(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    config = AutomationConfig.from_dict(
        {
            **to_plain_dict(test_config),
            "target": {
                **to_plain_dict(test_config.target),
                "mode": "any_visible_label",
                "allowed_targets": ["red_label"],
                "preferred_target_order": ["red_label"],
                "search_roi": {"x": 100, "y": 100, "width": 260, "height": 180},
                "click_offset": {"dx": 0, "dy": -55},
                "interaction_margin_px": 20,
                "green_label": {
                    **to_plain_dict(test_config.target.green_label),
                    "detect_red_labels": True,
                    "min_label_width": 45,
                    "max_label_width": 320,
                    "max_label_height": 36,
                },
            },
        }
    )
    frame = blank_frame(460, 340)
    cv2.putText(frame, "False edge [16]", (130, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 220), 2)
    locator = TargetLocator(
        config.target,
        GreenLabelDetector(config.target.green_label),
        game_field_roi=config.target.search_roi,
    )

    assert locator.locate(frame) == []


def test_visible_label_inside_map_margin_is_clickable(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    config = AutomationConfig.from_dict(
        {
            **to_plain_dict(test_config),
            "target": {
                **to_plain_dict(test_config.target),
                "mode": "any_visible_label",
                "allowed_targets": ["red_label"],
                "preferred_target_order": ["red_label"],
                "search_roi": {"x": 100, "y": 100, "width": 260, "height": 180},
                "click_offset": {"dx": 0, "dy": -55},
                "interaction_margin_px": 20,
                "green_label": {
                    **to_plain_dict(test_config.target.green_label),
                    "detect_red_labels": True,
                    "min_label_width": 45,
                    "max_label_width": 320,
                    "max_label_height": 36,
                },
            },
        }
    )
    frame = blank_frame(460, 340)
    cv2.putText(frame, "Mob target [16]", (130, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 220), 2)
    locator = TargetLocator(
        config.target,
        GreenLabelDetector(config.target.green_label),
        game_field_roi=config.target.search_roi,
    )

    targets = locator.locate(frame)

    assert len(targets) == 1
    assert targets[0].interaction_point.y >= config.target.search_roi.y + config.target.interaction_margin_px


def test_no_green_label(test_config: AutomationConfig) -> None:
    detector = GreenLabelDetector(test_config.target.green_label)
    assert detector.detect(blank_frame()) == []


def test_thick_green_blob_is_not_label(test_config: AutomationConfig) -> None:
    frame = blank_frame()
    frame[80:110, 100:165] = (0, 255, 0)
    detector = GreenLabelDetector(test_config.target.green_label)
    assert detector.detect(frame) == []


def test_green_label_detected_on_grass_background(test_config: AutomationConfig) -> None:
    frame = blank_frame()
    frame[40:160, 30:290] = (30, 105, 20)
    cv2.rectangle(frame, (78, 93), (128, 101), (0, 255, 0), -1)
    cv2.rectangle(frame, (135, 93), (220, 101), (0, 255, 0), -1)

    candidates = GreenLabelDetector(test_config.target.green_label).detect(frame)

    assert len(candidates) == 1
    assert candidates[0].bbox.width >= 140


def test_multiple_candidates_ranked(test_config: AutomationConfig) -> None:
    frame = target_frame(20)
    frame[130:142, 180:260] = (0, 255, 0)
    detector = GreenLabelDetector(test_config.target.green_label)
    candidates = detector.detect(frame)
    assert len(candidates) == 2
    assert candidates[0].confidence >= candidates[1].confidence


def test_target_locator_offset_and_clamp(test_config: AutomationConfig) -> None:
    locator = TargetLocator(
        test_config.target,
        GreenLabelDetector(test_config.target.green_label),
        estimator=TargetClickEstimator(test_config.target.click_offset),
        game_field_roi=Rect(0, 0, 320, 240),
    )
    target = locator.locate(target_frame())[0]
    assert target.target_id == "steppe_jackal"
    assert target.interaction_point.y < target.label_center.y


def test_target_click_clamps_to_game_field_roi(test_config: AutomationConfig) -> None:
    locator = TargetLocator(
        test_config.target,
        GreenLabelDetector(test_config.target.green_label),
        estimator=TargetClickEstimator(test_config.target.click_offset),
        game_field_roi=Rect(0, 110, 320, 130),
    )
    target = locator.locate(target_frame(110, 112))[0]
    assert target.label_center.y < 130
    assert target.interaction_point.y == 110


def test_target_locator_search_roi_excludes_ui(test_config: AutomationConfig) -> None:
    from src.antibot_cv.automation.config import to_plain_dict

    config = AutomationConfig.from_dict(
        {
            **to_plain_dict(test_config),
            "target": {
                **to_plain_dict(test_config.target),
                "search_roi": {"x": 0, "y": 0, "width": 160, "height": 160},
            },
        }
    )
    frame = target_frame(200, 180)
    locator = TargetLocator(config.target, GreenLabelDetector(config.target.green_label))
    assert locator.locate(frame) == []


def test_moving_target_preserves_track(test_config: AutomationConfig) -> None:
    locator = TargetLocator(test_config.target, GreenLabelDetector(test_config.target.green_label))
    tracker = EntityTracker(max_distance=100, track_timeout_s=1)
    first = tracker.update(locator.locate(target_frame(100)), now=1.0)[0]
    second = tracker.update(locator.locate(target_frame(120)), now=1.2)[0]
    assert first.track_id == second.track_id
    assert second.velocity_x > 0


def test_stale_track_removed(test_config: AutomationConfig) -> None:
    locator = TargetLocator(test_config.target, GreenLabelDetector(test_config.target.green_label))
    tracker = EntityTracker(track_timeout_s=0.5)
    tracker.update(locator.locate(target_frame(100)), now=1.0)
    tracker.update([], now=2.0)
    assert tracker.tracks == []
