from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.viewport.coordinates import Point, Rect


@dataclass(frozen=True)
class AttackButtonDetection:
    detected: bool
    confidence: float
    bbox: Rect | None = None
    center: Point | None = None


class AttackButtonDetector:
    def __init__(self, registry: TemplateRegistry | None = None) -> None:
        self.registry = registry

    def detect(self, frame: np.ndarray) -> AttackButtonDetection:
        if self.registry is not None:
            matches = self.registry.match(frame, "attack_button")
            if matches:
                best = matches[0]
                return AttackButtonDetection(True, best.confidence, best.bbox, best.center)
        return _detect_color_attack_button(frame)


def _detect_color_attack_button(frame: np.ndarray) -> AttackButtonDetection:
    if frame.size == 0:
        return AttackButtonDetection(False, 0.0)
    height, width = frame.shape[:2]
    roi = Rect(int(width * 0.50), int(height * 0.10), int(width * 0.22), int(height * 0.12))
    crop = frame[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width]
    if crop.size == 0:
        return AttackButtonDetection(False, 0.0)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([12, 255, 255])) | cv2.inRange(
        hsv, np.array([170, 70, 50]), np.array([180, 255, 255])
    )
    kernel = np.ones((3, 3), dtype=np.uint8)
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_button_width = max(90, int(width * 0.055))
    candidates: list[tuple[float, Rect]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if not (min_button_width <= w <= int(width * 0.14) and 24 <= h <= 65 and area >= 450):
            continue
        rect = Rect(roi.x + x, roi.y + y, w, h)
        candidates.append((area, rect))
    if not candidates:
        return AttackButtonDetection(False, 0.0)
    selected_area, button = min(candidates, key=lambda item: item[1].x)
    if not _selected_target_name_present(frame, button):
        return AttackButtonDetection(False, 0.0)
    center = _action_icon_center(red, button, roi)
    confidence = min(1.0, max(0.65, selected_area / max(1.0, button.width * button.height)))
    return AttackButtonDetection(True, confidence, button, center)


def _selected_target_name_present(frame: np.ndarray, button: Rect) -> bool:
    height, width = frame.shape[:2]
    x1 = int(max(0, button.right))
    x2 = int(min(width, button.right + max(180, button.width * 1.8)))
    y1 = int(max(0, button.y - button.height * 0.35))
    y2 = int(min(height, button.bottom + button.height * 0.60))
    if x2 <= x1 or y2 <= y1:
        return False
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([45, 60, 80]), np.array([90, 255, 255]))
    white = cv2.inRange(hsv, np.array([0, 0, 150]), np.array([179, 80, 255]))
    mask = green | white
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 7), dtype=np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, _y, w, h = cv2.boundingRect(contour)
        if x < 4:
            continue
        if w >= max(90, int(button.width * 0.55)) and 6 <= h <= max(26, int(button.height * 0.75)) and w / max(1, h) >= 4:
            return True
    return False


def _action_icon_center(red_mask: np.ndarray, button: Rect, roi: Rect) -> Point:
    local = red_mask[button.y - roi.y : button.y - roi.y + button.height, button.x - roi.x : button.x - roi.x + button.width]
    contours, _ = cv2.findContours(local, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    icon_candidates: list[tuple[float, Rect]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if h < button.height * 0.55 or w > button.width * 0.45:
            continue
        if x > button.width * 0.35:
            continue
        icon_candidates.append((area, Rect(button.x + x, button.y + y, w, h)))
    if icon_candidates:
        _, icon = max(icon_candidates, key=lambda item: item[0])
        return icon.center
    return Point(button.x + min(button.width * 0.16, button.height * 0.75), button.center.y)
