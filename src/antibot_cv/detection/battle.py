from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.antibot_cv.automation.config import BattleConfig
from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.viewport.coordinates import Rect


@dataclass(frozen=True)
class SignalResult:
    signal_id: str
    confidence: float
    bbox: Rect | None = None
    weight: float = 1.0


@dataclass(frozen=True)
class BattleDetection:
    detected: bool
    confidence: float
    signals: tuple[SignalResult, ...]
    stable_frames: int
    panel_bbox: Rect | None = None


class BattleDetector:
    def __init__(self, config: BattleConfig, registry: TemplateRegistry | None = None) -> None:
        self.config = config
        self.registry = registry
        self._stable_frames = 0

    def detect(self, frame: np.ndarray, signals: list[SignalResult] | None = None) -> BattleDetection:
        signals = signals if signals is not None else self._extract_signals(frame)
        confirmed = [signal for signal in signals if signal.confidence >= 0.5]
        weighted = sum(signal.confidence * signal.weight for signal in confirmed)
        immediate = len(confirmed) >= self.config.min_signals or weighted >= self.config.weighted_threshold
        self._stable_frames = self._stable_frames + 1 if immediate else 0
        detected = self._stable_frames >= self.config.confirm_frames
        confidence = min(1.0, weighted / max(1.0, self.config.weighted_threshold))
        panel_bbox = next((signal.bbox for signal in signals if signal.signal_id == "battle_panel" and signal.bbox), None)
        return BattleDetection(detected, confidence, tuple(signals), self._stable_frames, panel_bbox)

    def _extract_signals(self, frame: np.ndarray) -> list[SignalResult]:
        signals: list[SignalResult] = []
        if self.registry is not None:
            for template_id in ("enemy_header", "timer_hourglass", "ability_bar", "battle_panel"):
                matches = self.registry.match(frame, template_id)
                if matches:
                    signals.append(SignalResult(template_id, matches[0].confidence, matches[0].bbox))

        if frame.size == 0:
            return signals
        height, width = frame.shape[:2]
        signals.extend(
            [
                _battle_panel_layout_signal(frame),
                _color_signal(frame, "enemy_header_area", Rect(int(width * 0.35), 0, int(width * 0.3), max(1, int(height * 0.12))), "red"),
                _color_signal(frame, "timer_area", Rect(int(width * 0.47), 0, int(width * 0.06), max(1, int(height * 0.15))), "yellow"),
                _color_signal(frame, "ability_bar_area", Rect(int(width * 0.25), int(height * 0.78), int(width * 0.5), int(height * 0.18)), "blue"),
            ]
        )
        return [signal for signal in signals if signal.confidence > 0]


def _color_signal(frame: np.ndarray, signal_id: str, roi: Rect, color: str) -> SignalResult:
    crop = frame[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width]
    if crop.size == 0:
        return SignalResult(signal_id, 0.0, roi)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    if color == "red":
        mask = cv2.inRange(hsv, np.array([0, 80, 80]), np.array([10, 255, 255])) | cv2.inRange(
            hsv, np.array([170, 80, 80]), np.array([179, 255, 255])
        )
    elif color == "yellow":
        mask = cv2.inRange(hsv, np.array([20, 80, 80]), np.array([40, 255, 255]))
    else:
        mask = cv2.inRange(hsv, np.array([90, 60, 60]), np.array([130, 255, 255]))
    confidence = float(np.count_nonzero(mask)) / float(mask.size)
    return SignalResult(signal_id, min(1.0, confidence * 2.0), roi)


def _battle_panel_layout_signal(frame: np.ndarray) -> SignalResult:
    if frame.size == 0:
        return SignalResult("battle_panel", 0.0, None, weight=1.6)
    height, width = frame.shape[:2]
    roi = Rect(int(width * 0.34), int(height * 0.15), int(width * 0.34), int(height * 0.34))
    crop = frame[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width]
    if crop.size == 0:
        return SignalResult("battle_panel", 0.0, roi, weight=1.6)

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, np.array([0, 75, 55]), np.array([12, 255, 255])) | cv2.inRange(
        hsv, np.array([168, 75, 55]), np.array([179, 255, 255])
    )
    yellow = cv2.inRange(hsv, np.array([18, 70, 70]), np.array([42, 255, 255]))
    contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bar_rects: list[Rect] = []
    for contour in contours:
        x, y, contour_width, contour_height = cv2.boundingRect(contour)
        if contour_width < max(45, int(roi.width * 0.08)):
            continue
        if contour_height < 3 or contour_height > max(28, int(roi.height * 0.08)):
            continue
        if contour_width / max(1, contour_height) < 4:
            continue
        bar_rects.append(Rect(roi.x + x, roi.y + y, contour_width, contour_height))

    header_cluster = _select_battle_header_cluster(bar_rects, roi, width)
    if len(header_cluster) < 2:
        return SignalResult("battle_panel", 0.0, roi, weight=1.6)

    top = min(rect.y for rect in header_cluster)
    left = min(rect.x for rect in header_cluster)
    right = max(rect.right for rect in header_cluster)
    cluster_width = max(1, right - left)
    panel_margin_x = max(55, int(cluster_width * 0.25), int(width * 0.035))
    panel_width = max(180, cluster_width + panel_margin_x * 2)
    panel_height = max(int(height * 0.24), int(cluster_width * 1.10))
    panel_center_x = int((left + right) / 2)
    panel = Rect(
        max(0, int(panel_center_x - panel_width / 2)),
        max(0, int(top - height * 0.035)),
        min(width - max(0, int(panel_center_x - panel_width / 2)), panel_width),
        min(height - max(0, int(top - height * 0.035)), panel_height),
    )
    panel_crop = frame[panel.y : panel.y + panel.height, panel.x : panel.x + panel.width]
    if panel_crop.size == 0:
        return SignalResult("battle_panel", 0.0, roi, weight=1.6)
    panel_hsv = cv2.cvtColor(panel_crop, cv2.COLOR_BGR2HSV)
    dark = ((panel_hsv[:, :, 2] < 95) & (panel_hsv[:, :, 1] < 120)) | (panel_hsv[:, :, 2] < 60)
    dark_ratio = float(np.count_nonzero(dark)) / float(dark.size)
    if dark_ratio < 0.20:
        return SignalResult("battle_panel", 0.0, roi, weight=1.6)

    red_ratio = float(np.count_nonzero(red)) / float(red.size)
    yellow_ratio = float(np.count_nonzero(yellow)) / float(yellow.size)
    bar_score = min(1.0, len(header_cluster) / 3.0)
    color_score = min(1.0, red_ratio * 24.0 + yellow_ratio * 8.0 + dark_ratio)
    confidence = max(0.0, min(1.0, 0.50 * bar_score + 0.50 * color_score))
    return SignalResult("battle_panel", confidence, panel, weight=1.6)


def _select_battle_header_cluster(bar_rects: list[Rect], roi: Rect, frame_width: int) -> list[Rect]:
    y_tolerance = max(8.0, roi.height * 0.035)
    gap_limit = max(70.0, roi.width * 0.12)
    eligible = [rect for rect in bar_rects if rect.height >= 7 and rect.center.y <= roi.y + roi.height * 0.55]
    clusters: list[list[Rect]] = []
    for seed in sorted(eligible, key=lambda rect: rect.center.y):
        row = sorted((rect for rect in eligible if abs(rect.center.y - seed.center.y) <= y_tolerance), key=lambda rect: rect.x)
        current: list[Rect] = []
        previous: Rect | None = None
        for rect in row:
            if previous is not None and rect.x - previous.right > gap_limit:
                if len(current) >= 2:
                    clusters.append(current)
                current = []
            current.append(rect)
            previous = rect
        if len(current) >= 2:
            clusters.append(current)
    if not clusters:
        return []

    frame_center_x = frame_width / 2

    def score(cluster: list[Rect]) -> tuple[float, float, int]:
        left = min(rect.x for rect in cluster)
        right = max(rect.right for rect in cluster)
        center_x = (left + right) / 2
        width = right - left
        center_penalty = abs(center_x - frame_center_x) / max(1.0, frame_width)
        return (len(cluster) * 2.0 + width / max(1.0, roi.width) - center_penalty, -center_penalty, len(cluster))

    return max(clusters, key=score)
