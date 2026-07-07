from __future__ import annotations

import cv2
import numpy as np
import pytest

from src.antibot_cv.automation.config import AutomationConfig


@pytest.fixture
def test_config() -> AutomationConfig:
    return AutomationConfig.from_dict(
        {
            "dry_run": True,
            "max_cycles": 3,
            "capture_fps": 30,
            "target": {
                "mode": "any_allowed_green_label",
                "allowed_targets": ["steppe_jackal", "young_lynx"],
                "preferred_target_order": ["steppe_jackal", "young_lynx"],
                "stable_frames": 1,
                "click_offset": {"dx": 0, "dy": -20},
                "green_label": {
                    "green_h_min": 45,
                    "green_h_max": 85,
                    "green_s_min": 80,
                    "green_v_min": 80,
                    "min_label_width": 8,
                    "max_label_width": 260,
                    "min_label_height": 4,
                    "max_label_height": 70,
                },
            },
            "battle": {"confirm_frames": 1, "min_signals": 2, "weighted_threshold": 1.5},
            "battle_end": {"confirm_frames": 1, "threshold": 0.7},
            "statistics": {"confirm_frames": 1, "threshold": 0.7},
            "resources": {"fail_open_if_missing": True},
            "viewport": {"max_moves_per_search": 3, "settle_ms": 0},
            "safety": {"max_actions_per_minute": 30, "max_viewport_moves_per_search": 3, "max_consecutive_errors": 3},
            "templates_path": "tests/fixtures/no-templates.json",
        }
    )


def blank_frame(width: int = 320, height: int = 240) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def target_frame(x: int = 110, y: int = 120) -> np.ndarray:
    frame = blank_frame()
    cv2.rectangle(frame, (x, y), (x + 70, y + 12), (0, 255, 0), -1)
    return frame


def location_map_frame(width: int = 320, height: int = 240, *, health: float | None = None, prowess: float | None = None) -> np.ndarray:
    frame = blank_frame(width, height)
    frame[:, :] = (185, 165, 135)
    cv2.rectangle(frame, (int(width * 0.2), int(height * 0.25)), (int(width * 0.82), int(height * 0.92)), (45, 130, 45), -1)
    cv2.rectangle(frame, (int(width * 0.74), int(height * 0.28)), (int(width * 0.78), int(height * 0.90)), (0, 0, 180), -1)
    if health is not None or prowess is not None:
        cv2.rectangle(frame, (0, 0), (width, 45), (35, 35, 35), -1)
    if health is not None:
        health_width = int(100 * max(0, min(100, health)) / 100)
        cv2.rectangle(frame, (20, 12), (20 + health_width, 20), (0, 0, 255), -1)
    if prowess is not None:
        prowess_width = int(100 * max(0, min(100, prowess)) / 100)
        cv2.rectangle(frame, (20, 28), (20 + prowess_width, 36), (255, 0, 0), -1)
    return frame


def resource_frame(health: float = 100, prowess: float = 100, *, include_target: bool = False) -> np.ndarray:
    frame = target_frame() if include_target else blank_frame()
    health_width = int(100 * max(0, min(100, health)) / 100)
    prowess_width = int(100 * max(0, min(100, prowess)) / 100)
    cv2.rectangle(frame, (20, 12), (20 + health_width, 20), (0, 0, 255), -1)
    cv2.rectangle(frame, (20, 28), (20 + prowess_width, 36), (255, 0, 0), -1)
    return frame


def battle_frame() -> np.ndarray:
    frame = blank_frame()
    height, width = frame.shape[:2]
    cv2.rectangle(frame, (int(width * 0.38), 4), (int(width * 0.62), 22), (0, 0, 255), -1)
    cv2.rectangle(frame, (int(width * 0.48), 8), (int(width * 0.52), 28), (0, 255, 255), -1)
    cv2.rectangle(frame, (int(width * 0.25), int(height * 0.78)), (int(width * 0.75), int(height * 0.94)), (255, 0, 0), -1)
    for idx in range(4):
        x = int(width * 0.33) + idx * 28
        cv2.rectangle(frame, (x, int(height * 0.82)), (x + 20, int(height * 0.9)), (255, 255, 255), 2)
        cv2.line(frame, (x, int(height * 0.82)), (x + 20, int(height * 0.9)), (255, 255, 255), 1)
    return frame


def central_battle_frame(width: int = 1280, height: int = 720) -> np.ndarray:
    frame = blank_frame(width, height)
    frame[:, :] = (205, 185, 150)
    panel_x = int(width * 0.37)
    panel_y = int(height * 0.18)
    panel_w = int(width * 0.28)
    panel_h = int(height * 0.36)
    cv2.rectangle(frame, (panel_x, panel_y), (panel_x + panel_w, panel_y + panel_h), (65, 70, 60), -1)
    cv2.rectangle(frame, (panel_x + 45, panel_y + 22), (panel_x + 150, panel_y + 32), (0, 0, 210), -1)
    cv2.rectangle(frame, (panel_x + panel_w - 150, panel_y + 22), (panel_x + panel_w - 45, panel_y + 32), (0, 0, 210), -1)
    cv2.putText(frame, "-10", (panel_x + panel_w // 2 - 20, panel_y + 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 230, 230), 2)
    slot_y = panel_y + int(panel_h * 0.78)
    for index, x_offset in enumerate((70, 118, 166, 214)):
        center = (panel_x + x_offset, slot_y)
        cv2.circle(frame, center, 20, (40, 40, 40), -1)
        cv2.circle(frame, center, 18, (140, 140, 140), 2)
        cv2.line(frame, (center[0] - 10, center[1] + 10), (center[0] + 10, center[1] - 10), (230, 230, 230), 2)
        if index == 3:
            cv2.circle(frame, center, 12, (210, 210, 210), 1)
    return frame


def battle_end_frame() -> np.ndarray:
    frame = blank_frame()
    cv2.rectangle(frame, (120, 70), (210, 135), (255, 0, 255), -1)
    cv2.rectangle(frame, (145, 118), (185, 138), (0, 128, 255), -1)
    return frame


def statistics_frame() -> np.ndarray:
    frame = blank_frame()
    cv2.rectangle(frame, (90, 12), (230, 35), (255, 255, 255), -1)
    cv2.rectangle(frame, (45, 70), (140, 170), (255, 255, 0), -1)
    cv2.rectangle(frame, (180, 70), (275, 170), (255, 255, 0), -1)
    cv2.rectangle(frame, (135, 95), (190, 125), (0, 220, 0), -1)
    return frame


def statistics_red_frame() -> np.ndarray:
    frame = blank_frame()
    red = (0, 0, 220)
    cv2.putText(frame, "STATISTICS", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, red, 2)
    cv2.rectangle(frame, (20, 55), (145, 90), red, 2)
    cv2.rectangle(frame, (170, 55), (300, 90), red, 2)
    for y in (110, 119, 128):
        cv2.rectangle(frame, (115, y), (205, y + 12), red, 2)
    return frame
