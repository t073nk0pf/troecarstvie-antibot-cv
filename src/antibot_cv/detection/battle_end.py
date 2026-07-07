from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.antibot_cv.automation.config import DetectionConfig
from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.viewport.coordinates import Rect


@dataclass(frozen=True)
class BattleEndDetection:
    detected: bool
    confidence: float
    stable_frames: int
    popup_bbox: Rect | None = None
    exit_button_bbox: Rect | None = None


class BattleEndDetector:
    def __init__(self, config: DetectionConfig, registry: TemplateRegistry | None = None) -> None:
        self.config = config
        self.registry = registry
        self._stable_frames = 0

    def detect(self, frame: np.ndarray) -> BattleEndDetection:
        popup_bbox, popup_confidence = self._match_or_color(frame, "victory_popup", "purple")
        exit_bbox, exit_confidence = self._match_or_color(frame, "exit_button", "orange")
        popup_bbox = _valid_popup_bbox(frame, popup_bbox)
        popup_confidence = popup_confidence if popup_bbox is not None else 0.0
        exit_bbox = _valid_exit_button_bbox(frame, exit_bbox)
        exit_confidence = exit_confidence if exit_bbox is not None else 0.0
        if popup_bbox is not None and exit_bbox is not None:
            confidence = max(popup_confidence, exit_confidence, min(1.0, (popup_confidence + exit_confidence) / 2.0))
        elif exit_bbox is not None and exit_confidence >= max(0.85, self.config.threshold):
            confidence = exit_confidence
        else:
            confidence = 0.0
        immediate = confidence >= self.config.threshold and exit_bbox is not None
        self._stable_frames = self._stable_frames + 1 if immediate else 0
        return BattleEndDetection(
            detected=self._stable_frames >= self.config.confirm_frames,
            confidence=confidence,
            stable_frames=self._stable_frames,
            popup_bbox=popup_bbox,
            exit_button_bbox=exit_bbox,
        )

    def _match_or_color(self, frame: np.ndarray, template_id: str, color: str) -> tuple[Rect | None, float]:
        if self.registry is not None:
            matches = self.registry.match(frame, template_id)
            if matches:
                return matches[0].bbox, matches[0].confidence
        return _detect_color_blob(frame, color)


def _detect_color_blob(frame: np.ndarray, color: str) -> tuple[Rect | None, float]:
    if frame.size == 0:
        return None, 0.0
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    if color == "purple":
        mask = cv2.inRange(hsv, np.array([135, 70, 70]), np.array([165, 255, 255]))
    else:
        mask = cv2.inRange(hsv, np.array([10, 70, 70]), np.array([25, 255, 255]))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, 0.0
    contour = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(contour)
    expected_area = 900.0 if color == "purple" else 250.0
    confidence = min(1.0, float(cv2.contourArea(contour)) / expected_area)
    return Rect(int(x), int(y), int(w), int(h)), confidence


def _valid_popup_bbox(frame: np.ndarray, bbox: Rect | None) -> Rect | None:
    if bbox is None or frame.size == 0:
        return None
    height, width = frame.shape[:2]
    if bbox.width < 30 or bbox.height < 20:
        return None
    if bbox.width > width * 0.55 or bbox.height > height * 0.45:
        return None
    return bbox


def _valid_exit_button_bbox(frame: np.ndarray, bbox: Rect | None) -> Rect | None:
    if bbox is None or frame.size == 0:
        return None
    height, width = frame.shape[:2]
    if bbox.width < 24 or bbox.height < 10:
        return None
    if bbox.width > min(320, width * 0.35):
        return None
    if bbox.height > min(120, height * 0.18):
        return None
    center = bbox.center
    if center.x < width * 0.18 or center.x > width * 0.86:
        return None
    if center.y < height * 0.18 or center.y > height * 0.92:
        return None
    aspect = bbox.width / max(1, bbox.height)
    if not 1.0 <= aspect <= 8.5:
        return None
    return bbox
