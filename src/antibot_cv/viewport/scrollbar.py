from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from src.antibot_cv.viewport.coordinates import Point, Rect


class ScrollDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True)
class ScrollbarMove:
    direction: ScrollDirection
    start: Point
    end: Point
    confidence: float


class ScrollbarNavigator:
    def __init__(self, roi: Rect | None = None, min_confidence: float = 0.75) -> None:
        self.roi = roi
        self.min_confidence = min_confidence

    def detect_thumb(self, frame: object) -> tuple[Rect | None, float]:
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            return None, 0.0
        search_roi = self.roi or Rect(0, 0, frame.shape[1], frame.shape[0])
        crop = frame[
            max(0, search_roi.y) : min(frame.shape[0], search_roi.y + search_roi.height),
            max(0, search_roi.x) : min(frame.shape[1], search_roi.x + search_roi.width),
        ]
        if crop.size == 0:
            return None, 0.0

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        red_mask = cv2.inRange(hsv, np.array([0, 80, 60]), np.array([10, 255, 255])) | cv2.inRange(
            hsv, np.array([170, 80, 60]), np.array([179, 255, 255])
        )
        kernel = np.ones((5, 5), dtype=np.uint8)
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best_bbox: Rect | None = None
        best_confidence = 0.0
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if width < 8 or height < 35:
                continue
            aspect = height / max(1, width)
            if aspect < 1.8:
                continue
            area = float(cv2.contourArea(contour))
            fill_ratio = area / max(1.0, float(width * height))
            height_score = min(1.0, height / max(80.0, crop.shape[0] * 0.25))
            narrow_score = max(0.0, 1.0 - abs(width - 24) / 80.0)
            confidence = min(1.0, 0.45 * fill_ratio + 0.4 * height_score + 0.15 * narrow_score)
            if confidence > best_confidence:
                best_confidence = confidence
                best_bbox = Rect(search_roi.x + int(x), search_roi.y + int(y), int(width), int(height))
        return best_bbox, best_confidence

    def next_drag(self, direction: ScrollDirection, frame: object, step_px: int = 40) -> ScrollbarMove | None:
        thumb, confidence = self.detect_thumb(frame)
        if thumb is None or confidence < self.min_confidence:
            return None
        start = thumb.center
        dy = -abs(step_px) if direction == ScrollDirection.UP else abs(step_px)
        bounds = self.roi
        end = Point(start.x, start.y + dy)
        if bounds is not None:
            end = bounds.clamp(end)
        return ScrollbarMove(direction, start, end, confidence)
