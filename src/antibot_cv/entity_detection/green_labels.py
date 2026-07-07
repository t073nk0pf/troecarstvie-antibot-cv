from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.antibot_cv.automation.config import GreenLabelConfig
from src.antibot_cv.viewport.coordinates import Point, Rect


@dataclass(frozen=True)
class LabelCandidate:
    bbox: Rect
    label_center: Point
    confidence: float
    green_pixel_ratio: float
    target_id: str | None = None
    label_color: str = "green"


class GreenLabelDetector:
    def __init__(self, config: GreenLabelConfig) -> None:
        self.config = config

    def detect(self, frame: np.ndarray) -> list[LabelCandidate]:
        if frame.size == 0:
            return []
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        lower = np.array([self.config.green_h_min, self.config.green_s_min, self.config.green_v_min], dtype=np.uint8)
        upper = np.array([self.config.green_h_max, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        kernel = np.ones((3, 3), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        candidates = self._candidates_from_mask(mask, label_color="green")
        if not candidates:
            lower = np.array(
                [self.config.green_h_min, max(self.config.green_s_min, 160), max(self.config.green_v_min, 120)],
                dtype=np.uint8,
            )
            text_mask = cv2.inRange(hsv, lower, upper)
            text_mask = cv2.morphologyEx(text_mask, cv2.MORPH_OPEN, np.ones((2, 2), dtype=np.uint8))
            text_mask = cv2.dilate(text_mask, np.ones((3, 7), dtype=np.uint8), iterations=1)
            text_mask = cv2.morphologyEx(text_mask, cv2.MORPH_CLOSE, np.ones((3, 9), dtype=np.uint8))
            candidates = self._candidates_from_mask(text_mask, label_color="green")
        if self.config.detect_red_labels:
            red_mask = cv2.inRange(hsv, np.array([0, 70, 60], dtype=np.uint8), np.array([12, 255, 255], dtype=np.uint8))
            red_mask |= cv2.inRange(hsv, np.array([168, 70, 60], dtype=np.uint8), np.array([179, 255, 255], dtype=np.uint8))
            red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, np.ones((2, 2), dtype=np.uint8))
            red_mask = cv2.dilate(red_mask, np.ones((3, 7), dtype=np.uint8), iterations=1)
            red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, np.ones((3, 9), dtype=np.uint8))
            candidates.extend(self._candidates_from_mask(red_mask, label_color="red"))
        candidates.sort(key=lambda item: (item.confidence, item.bbox.width), reverse=True)
        return candidates

    def _candidates_from_mask(self, mask: np.ndarray, *, label_color: str) -> list[LabelCandidate]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates: list[LabelCandidate] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if not self._valid_geometry(width, height):
                continue
            component = mask[y : y + height, x : x + width]
            green_ratio = float(np.count_nonzero(component)) / float(width * height)
            confidence = min(1.0, green_ratio * min(width / max(1, self.config.min_label_width), 2.0) / 2.0)
            bbox = Rect(int(x), int(y), int(width), int(height))
            candidates.append(
                LabelCandidate(
                    bbox=bbox,
                    label_center=bbox.center,
                    confidence=confidence,
                    green_pixel_ratio=green_ratio,
                    label_color=label_color,
                )
            )
        return candidates

    def _valid_geometry(self, width: int, height: int) -> bool:
        aspect_ratio = width / max(1, height)
        return (
            self.config.min_label_width <= width <= self.config.max_label_width
            and self.config.min_label_height <= height <= self.config.max_label_height
            and aspect_ratio >= 3.0
        )
