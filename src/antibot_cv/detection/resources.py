from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.antibot_cv.automation.config import ResourceConfig
from src.antibot_cv.viewport.coordinates import Rect


@dataclass(frozen=True)
class ResourceBarStatus:
    resource_id: str
    detected: bool
    percent: float | None
    confidence: float
    bbox: Rect | None = None


@dataclass(frozen=True)
class ResourceStatus:
    health: ResourceBarStatus
    prowess: ResourceBarStatus

    @property
    def complete(self) -> bool:
        return self.health.percent is not None and self.prowess.percent is not None


class ResourceDetector:
    def __init__(self, config: ResourceConfig) -> None:
        self.config = config

    def detect(self, frame: np.ndarray) -> ResourceStatus:
        return ResourceStatus(
            health=self._detect_bar(frame, "health", self.config.health_bar_roi, _health_ranges()),
            prowess=self._detect_bar(frame, "prowess", self.config.prowess_bar_roi, _prowess_ranges()),
        )

    def _detect_bar(
        self,
        frame: np.ndarray,
        resource_id: str,
        explicit_roi: Rect | None,
        ranges: tuple[tuple[np.ndarray, np.ndarray], ...],
    ) -> ResourceBarStatus:
        if frame.size == 0:
            return ResourceBarStatus(resource_id, False, None, 0.0)
        if explicit_roi is not None:
            return _detect_explicit_bar(frame, resource_id, explicit_roi, ranges)
        return _detect_auto_bar(frame, resource_id, self.config.roi, ranges)


def _detect_explicit_bar(
    frame: np.ndarray,
    resource_id: str,
    roi: Rect,
    ranges: tuple[tuple[np.ndarray, np.ndarray], ...],
) -> ResourceBarStatus:
    frame_bounds = Rect(0, 0, frame.shape[1], frame.shape[0])
    left = int(max(frame_bounds.x, roi.x))
    top = int(max(frame_bounds.y, roi.y))
    right = int(min(frame_bounds.right, roi.right))
    bottom = int(min(frame_bounds.bottom, roi.bottom))
    if right <= left or bottom <= top:
        return ResourceBarStatus(resource_id, False, None, 0.0)

    crop = frame[top:bottom, left:right]
    mask = _color_mask(crop, ranges)
    if mask.size == 0:
        return ResourceBarStatus(resource_id, False, None, 0.0)

    min_pixels_per_column = max(1, int(mask.shape[0] * 0.25))
    active_columns = np.count_nonzero(mask, axis=0) >= min_pixels_per_column
    active_indices = np.flatnonzero(active_columns)
    if active_indices.size <= 0:
        return ResourceBarStatus(resource_id, False, None, 0.0)
    first_active = int(active_indices[0])
    if first_active > max(4, int(roi.width * 0.05)):
        return ResourceBarStatus(resource_id, False, None, 0.0)
    active_count = _left_fill_width(active_indices)
    if active_count < max(4, int(roi.width * 0.03)):
        return ResourceBarStatus(resource_id, False, None, 0.0)

    percent = min(100.0, max(0.0, active_count / max(1, roi.width) * 100.0))
    fill_mask = mask[:, :active_count]
    ys, xs = np.nonzero(fill_mask)
    bbox = Rect(int(left + xs.min()), int(top + ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
    confidence = min(1.0, active_count / max(1.0, float(roi.width)))
    return ResourceBarStatus(resource_id, True, percent, confidence, bbox)


def _left_fill_width(active_indices: np.ndarray) -> int:
    start = int(active_indices[0])
    last = int(active_indices[-1])
    span = max(1, last - start + 1)
    density = float(active_indices.size) / float(span)
    if density < 0.45:
        return 0
    return last + 1


def _detect_auto_bar(
    frame: np.ndarray,
    resource_id: str,
    search_roi: Rect | None,
    ranges: tuple[tuple[np.ndarray, np.ndarray], ...],
) -> ResourceBarStatus:
    height, width = frame.shape[:2]
    roi = search_roi or Rect(0, 0, width, max(1, int(height * 0.45)))
    left = int(max(0, roi.x))
    top = int(max(0, roi.y))
    right = int(min(width, roi.right))
    bottom = int(min(height, roi.bottom))
    if right <= left or bottom <= top:
        return ResourceBarStatus(resource_id, False, None, 0.0)

    crop = frame[top:bottom, left:right]
    mask = _color_mask(crop, ranges)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, Rect]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 35 or h < 3 or h > 24 or w / max(1, h) < 4:
            continue
        area = float(cv2.contourArea(contour))
        candidates.append((area, Rect(int(left + x), int(top + y), int(w), int(h))))
    if not candidates:
        return ResourceBarStatus(resource_id, False, None, 0.0)
    area, bbox = max(candidates, key=lambda item: item[0])
    confidence = min(1.0, area / max(1.0, bbox.width * bbox.height))
    return ResourceBarStatus(resource_id, True, None, confidence, bbox)


def _color_mask(frame: np.ndarray, ranges: tuple[tuple[np.ndarray, np.ndarray], ...]) -> np.ndarray:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lower, upper in ranges:
        mask |= cv2.inRange(hsv, lower, upper)
    kernel = np.ones((2, 3), dtype=np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def _health_ranges() -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    return (
        (np.array([0, 70, 60]), np.array([12, 255, 255])),
        (np.array([168, 70, 60]), np.array([179, 255, 255])),
        (np.array([40, 70, 60]), np.array([90, 255, 255])),
    )


def _prowess_ranges() -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    return (
        (np.array([85, 50, 50]), np.array([135, 255, 255])),
        (np.array([75, 50, 50]), np.array([100, 255, 255])),
    )
