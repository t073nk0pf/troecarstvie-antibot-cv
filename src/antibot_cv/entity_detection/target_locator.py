from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from src.antibot_cv.automation.config import ClickOffset, TargetConfig
from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.entity_detection.green_labels import GreenLabelDetector, LabelCandidate
from src.antibot_cv.viewport.coordinates import Point, Rect


class TargetTextRecognizer(Protocol):
    def recognize(self, frame: np.ndarray, candidate: LabelCandidate) -> str | None:
        ...


class TemplateTargetRecognizer:
    def __init__(self, registry: TemplateRegistry, target_template_ids: dict[str, str]) -> None:
        self.registry = registry
        self.target_template_ids = target_template_ids

    def recognize(self, frame: np.ndarray, candidate: LabelCandidate) -> str | None:
        crop = frame[
            candidate.bbox.y : candidate.bbox.y + candidate.bbox.height,
            candidate.bbox.x : candidate.bbox.x + candidate.bbox.width,
        ]
        best_target: str | None = None
        best_confidence = 0.0
        for target_id, template_id in self.target_template_ids.items():
            matches = self.registry.match(crop, template_id)
            if matches and matches[0].confidence > best_confidence:
                best_confidence = matches[0].confidence
                best_target = target_id
        return best_target


@dataclass(frozen=True)
class LocatedTarget:
    target_id: str
    bbox: Rect
    label_center: Point
    interaction_point: Point
    confidence: float
    green_pixel_ratio: float


class TargetClickEstimator:
    def __init__(self, default_offset: ClickOffset, per_target_offsets: dict[str, ClickOffset] | None = None) -> None:
        self.default_offset = default_offset
        self.per_target_offsets = per_target_offsets or {}

    def estimate(self, candidate: LabelCandidate, target_id: str, game_field_roi: Rect | None = None) -> Point:
        offset = self.per_target_offsets.get(target_id, self.default_offset)
        point = Point(candidate.label_center.x + offset.dx, candidate.label_center.y + offset.dy)
        if game_field_roi is not None:
            point = game_field_roi.clamp(point)
        return point


class TargetLocator:
    def __init__(
        self,
        config: TargetConfig,
        label_detector: GreenLabelDetector,
        recognizer: TargetTextRecognizer | None = None,
        estimator: TargetClickEstimator | None = None,
        game_field_roi: Rect | None = None,
        template_registry: TemplateRegistry | None = None,
    ) -> None:
        self.config = config
        self.label_detector = label_detector
        self.recognizer = recognizer
        self.estimator = estimator or TargetClickEstimator(config.click_offset, config.per_target_click_offsets)
        self.game_field_roi = game_field_roi
        self.template_registry = template_registry
        self._center_click_target_ids = {"green_sprite", *config.sprite_template_ids}
        self._priority = {target_id: rank for rank, target_id in enumerate(config.preferred_target_order)}

    def locate(self, frame: np.ndarray) -> list[LocatedTarget]:
        detect_frame = frame
        offset_x = 0
        offset_y = 0
        if self.config.search_roi is not None:
            roi = self.config.search_roi
            detect_frame = frame[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width]
            offset_x = roi.x
            offset_y = roi.y
        located: list[LocatedTarget] = []
        raw_candidates = self.label_detector.detect(detect_frame)
        for candidate in raw_candidates:
            candidate = _offset_candidate(candidate, offset_x, offset_y)
            self._append_located(frame, candidate, located)
        if not located:
            for candidate in self._sprite_template_candidates(detect_frame):
                candidate = _offset_candidate(candidate, offset_x, offset_y)
                self._append_located(frame, candidate, located)
        if not located and "green_sprite" in self.config.allowed_targets:
            for candidate in _detect_green_sprite_candidates(detect_frame):
                candidate = _offset_candidate(candidate, offset_x, offset_y)
                self._append_located(frame, candidate, located)
        located.sort(key=lambda item: (self._priority.get(item.target_id, 999), -item.confidence))
        return located

    def _append_located(self, frame: np.ndarray, candidate: LabelCandidate, located: list[LocatedTarget]) -> None:
        target_id = self._target_id(frame, candidate)
        if target_id is None:
            return
        if target_id in self._center_click_target_ids:
            raw_interaction_point = candidate.label_center
        else:
            raw_interaction_point = self.estimator.estimate(candidate, target_id)
        if self.config.interaction_margin_px > 0 and self.game_field_roi is not None and not _point_in_rect_with_margin(
            raw_interaction_point,
            self.game_field_roi,
            self.config.interaction_margin_px,
        ):
            return
        if target_id in self._center_click_target_ids:
            interaction_point = self.game_field_roi.clamp(candidate.label_center) if self.game_field_roi is not None else candidate.label_center
        else:
            interaction_point = self.estimator.estimate(candidate, target_id, self.game_field_roi)
        located.append(
            LocatedTarget(
                target_id=target_id,
                bbox=candidate.bbox,
                label_center=candidate.label_center,
                interaction_point=interaction_point,
                confidence=candidate.confidence,
                green_pixel_ratio=candidate.green_pixel_ratio,
            )
        )

    def _sprite_template_candidates(self, frame: np.ndarray) -> list[LabelCandidate]:
        if self.template_registry is None:
            return []
        candidates: list[LabelCandidate] = []
        for template_id in self.config.sprite_template_ids:
            if template_id not in self.config.allowed_targets:
                continue
            for match in self.template_registry.match(frame, template_id):
                candidates.append(
                    LabelCandidate(
                        bbox=match.bbox,
                        label_center=match.center,
                        confidence=match.confidence,
                        green_pixel_ratio=0.0,
                        target_id=template_id,
                        label_color="sprite",
                    )
                )
        candidates.sort(key=lambda item: (item.confidence, item.bbox.width * item.bbox.height), reverse=True)
        return candidates

    def _target_id(self, frame: np.ndarray, candidate: LabelCandidate) -> str | None:
        if candidate.target_id is not None:
            if candidate.target_id in self.config.allowed_targets:
                return candidate.target_id
            return None
        if self.config.mode == "any_allowed_green_label":
            if candidate.label_color != "green":
                return None
            return self.config.preferred_target_order[0] if self.config.preferred_target_order else "green_label"
        if self.config.mode == "any_visible_label":
            target_id = f"{candidate.label_color}_label"
            if target_id in self.config.allowed_targets:
                return target_id
            return None
        if self.recognizer is None:
            return None
        target_id = self.recognizer.recognize(frame, candidate)
        if target_id in self.config.allowed_targets:
            return target_id
        return None


def _offset_candidate(candidate: LabelCandidate, dx: int, dy: int) -> LabelCandidate:
    if dx == 0 and dy == 0:
        return candidate
    bbox = Rect(candidate.bbox.x + dx, candidate.bbox.y + dy, candidate.bbox.width, candidate.bbox.height)
    return LabelCandidate(
        bbox=bbox,
        label_center=Point(candidate.label_center.x + dx, candidate.label_center.y + dy),
        confidence=candidate.confidence,
        green_pixel_ratio=candidate.green_pixel_ratio,
        target_id=candidate.target_id,
        label_color=candidate.label_color,
    )


def _point_in_rect_with_margin(point: Point, rect: Rect, margin: int) -> bool:
    return (
        rect.x + margin <= point.x <= rect.right - margin
        and rect.y + margin <= point.y <= rect.bottom - margin
    )


def _detect_green_sprite_candidates(frame: np.ndarray) -> list[LabelCandidate]:
    if frame.size == 0:
        return []

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(hsv, np.array([38, 80, 45], dtype=np.uint8), np.array([95, 255, 255], dtype=np.uint8))
    red_mask = cv2.inRange(hsv, np.array([0, 80, 45], dtype=np.uint8), np.array([14, 255, 255], dtype=np.uint8))
    red_mask |= cv2.inRange(hsv, np.array([166, 80, 45], dtype=np.uint8), np.array([179, 255, 255], dtype=np.uint8))

    green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, np.ones((2, 2), dtype=np.uint8))
    green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
    contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[LabelCandidate] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if not _valid_green_sprite_geometry(width, height):
            continue
        green_area = float(cv2.contourArea(contour))
        if green_area < 80:
            continue

        pad = max(8, int(max(width, height) * 0.45))
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(frame.shape[1], x + width + pad)
        y2 = min(frame.shape[0], y + height + pad)
        red_roi = red_mask[y1:y2, x1:x2]
        red_pixels = int(np.count_nonzero(red_roi))
        if red_pixels < 20:
            continue

        red_y, red_x = np.where(red_roi > 0)
        if red_x.size == 0 or red_y.size == 0:
            continue
        union_x1 = min(x, x1 + int(red_x.min()))
        union_y1 = min(y, y1 + int(red_y.min()))
        union_x2 = max(x + width, x1 + int(red_x.max()) + 1)
        union_y2 = max(y + height, y1 + int(red_y.max()) + 1)
        bbox = Rect(union_x1, union_y1, union_x2 - union_x1, union_y2 - union_y1)
        confidence = min(1.0, 0.45 + min(green_area / 1000.0, 0.35) + min(red_pixels / 400.0, 0.2))
        candidates.append(
            LabelCandidate(
                bbox=bbox,
                label_center=bbox.center,
                confidence=confidence,
                green_pixel_ratio=min(1.0, green_area / max(1.0, width * height)),
                target_id="green_sprite",
                label_color="green",
            )
        )
    candidates.sort(key=lambda item: (item.confidence, item.bbox.width * item.bbox.height), reverse=True)
    return candidates


def _valid_green_sprite_geometry(width: int, height: int) -> bool:
    aspect_ratio = width / max(1, height)
    return 14 <= width <= 90 and 12 <= height <= 90 and 0.45 <= aspect_ratio <= 2.4
