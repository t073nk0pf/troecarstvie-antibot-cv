from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.antibot_cv.automation.config import DetectionConfig
from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.viewport.coordinates import Rect


@dataclass(frozen=True)
class StatisticsDetection:
    detected: bool
    confidence: float
    stable_frames: int
    feature_count: int
    hunt_button_bbox: Rect | None = None


class StatisticsDetector:
    def __init__(self, config: DetectionConfig, registry: TemplateRegistry | None = None) -> None:
        self.config = config
        self.registry = registry
        self._stable_frames = 0

    def detect(self, frame: np.ndarray) -> StatisticsDetection:
        features: list[float] = []
        hunt_bbox: Rect | None = None
        if self.registry is not None:
            for template_id in ("statistics_header", "team_tables", "statistics_buttons", "hunt_button"):
                matches = self.registry.match(frame, template_id)
                if matches:
                    features.append(matches[0].confidence)
                    if template_id == "hunt_button":
                        hunt_bbox = matches[0].bbox

        color_features, synthetic_hunt = _synthetic_statistics_features(frame)
        features.extend(color_features)
        hunt_bbox = hunt_bbox or synthetic_hunt
        feature_count = sum(1 for value in features if value >= self.config.threshold)
        confidence = max(features) if features else 0.0
        immediate = feature_count >= 2
        self._stable_frames = self._stable_frames + 1 if immediate else 0
        return StatisticsDetection(
            detected=self._stable_frames >= self.config.confirm_frames,
            confidence=confidence,
            stable_frames=self._stable_frames,
            feature_count=feature_count,
            hunt_button_bbox=hunt_bbox,
        )


def _synthetic_statistics_features(frame: np.ndarray) -> tuple[list[float], Rect | None]:
    if frame.size == 0:
        return [], None
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    white_mask = cv2.inRange(hsv, np.array([0, 0, 180]), np.array([179, 60, 255]))
    cyan_mask = cv2.inRange(hsv, np.array([80, 60, 80]), np.array([100, 255, 255]))
    green_mask = cv2.inRange(hsv, np.array([45, 60, 80]), np.array([85, 255, 255]))
    red_mask = cv2.inRange(hsv, np.array([0, 70, 60]), np.array([12, 255, 255])) | cv2.inRange(
        hsv, np.array([168, 70, 60]), np.array([179, 255, 255])
    )
    top = white_mask[: max(1, int(height * 0.2)), :]
    middle = cyan_mask[int(height * 0.25) : int(height * 0.75), :]
    bottom = green_mask[int(height * 0.35) : int(height * 0.75), :]
    red_tables = red_mask[int(height * 0.18) : int(height * 0.42), :]
    red_buttons = _red_button_bboxes(red_mask, width, height)
    white_score = min(1.0, float(np.count_nonzero(top)) / max(1.0, top.size * 0.05))
    cyan_score = min(1.0, float(np.count_nonzero(middle)) / max(1.0, middle.size * 0.04))
    green_score = min(1.0, float(np.count_nonzero(bottom)) / max(1.0, bottom.size * 0.02))
    red_table_score = min(1.0, float(np.count_nonzero(red_tables)) / max(1.0, red_tables.size * 0.005))

    features: list[float] = []
    hunt_bbox = _hunt_button_from_red_buttons(red_buttons)
    stacked_button_score = _stacked_hunt_button_group_score(red_buttons, width, height)
    if hunt_bbox is not None and stacked_button_score >= 0.85:
        features.extend([stacked_button_score, 1.0])
    elif hunt_bbox is not None and red_table_score >= 0.7 and len(red_buttons) >= 2:
        features.extend([red_table_score, 1.0])
    if cyan_score >= 0.7 and green_score >= 0.7:
        features.extend([cyan_score, green_score, white_score])
        hunt_bbox = hunt_bbox or _largest_blob_bbox(green_mask)
    return features, hunt_bbox


def _red_button_bboxes(red_mask: np.ndarray, width: int, height: int) -> list[Rect]:
    button_zone = np.zeros_like(red_mask)
    y1 = int(height * 0.30)
    y2 = int(height * 0.60)
    x1 = int(width * 0.25)
    x2 = int(width * 0.75)
    button_zone[y1:y2, x1:x2] = red_mask[y1:y2, x1:x2]
    kernel = np.ones((5, 17), dtype=np.uint8)
    button_zone = cv2.morphologyEx(button_zone, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(button_zone, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    buttons: list[Rect] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < width * 0.08 or h < max(14, height * 0.035) or h > height * 0.14:
            continue
        if w / max(1, h) < 2.4:
            continue
        buttons.append(Rect(int(x), int(y), int(w), int(h)))
    return sorted(buttons, key=lambda rect: (rect.y, rect.x))


def _hunt_button_from_red_buttons(buttons: list[Rect]) -> Rect | None:
    if not buttons:
        return None
    button = max(buttons, key=lambda rect: rect.y)
    if button.height / max(1, button.width) <= 0.30:
        return None
    third = max(1, button.height // 3)
    return Rect(button.x, button.y + third * 2, button.width, button.height - third * 2)


def _stacked_hunt_button_group_score(buttons: list[Rect], width: int, height: int) -> float:
    if not buttons:
        return 0.0
    best = 0.0
    for button in buttons:
        center = button.center
        if not width * 0.30 <= center.x <= width * 0.70:
            continue
        if not height * 0.30 <= center.y <= height * 0.62:
            continue
        width_ratio = button.width / max(1, width)
        height_ratio = button.height / max(1, height)
        aspect = button.width / max(1, button.height)
        if not 0.08 <= width_ratio <= 0.36:
            continue
        if not 0.06 <= height_ratio <= 0.16:
            continue
        if not 2.2 <= aspect <= 5.5:
            continue
        width_score = min(1.0, width_ratio / 0.14)
        height_score = min(1.0, height_ratio / 0.08)
        best = max(best, min(width_score, height_score))
    return best


def _largest_blob_bbox(mask: np.ndarray) -> Rect | None:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) <= 0:
        return None
    x, y, w, h = cv2.boundingRect(contour)
    return Rect(int(x), int(y), int(w), int(h))
