from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.antibot_cv.automation.config import Ability4Config
from src.antibot_cv.detection.templates import TemplateRegistry
from src.antibot_cv.viewport.coordinates import Point, Rect


@dataclass(frozen=True)
class AbilitySlot:
    index: int
    bbox: Rect
    center: Point
    confidence: float
    ready: bool


class AbilityBarDetector:
    def __init__(self, config: Ability4Config, registry: TemplateRegistry | None = None) -> None:
        self.config = config
        self.registry = registry

    def detect_slots(self, frame: np.ndarray, battle_panel: Rect | None = None) -> list[AbilitySlot]:
        circle_slots = _detect_circular_slots(frame, battle_panel, self.config.normalized_position[0])
        if len(circle_slots) >= 4:
            return circle_slots[:4]
        bar_bbox = self._detect_bar(frame, battle_panel)
        if bar_bbox is None:
            return []
        slot_width = max(1, bar_bbox.width // 8)
        gap = max(1, slot_width // 5)
        start_x = bar_bbox.x + max(0, int((bar_bbox.width - (4 * slot_width + 3 * gap)) / 2))
        slots: list[AbilitySlot] = []
        for index in range(1, 5):
            x = start_x + (index - 1) * (slot_width + gap)
            bbox = Rect(x, bar_bbox.y, slot_width, bar_bbox.height)
            ready = self.is_ready(frame, bbox)
            slots.append(AbilitySlot(index, bbox, bbox.center, 0.8 if ready else 0.55, ready))
        return slots

    def slot4(self, frame: np.ndarray, battle_panel: Rect | None = None) -> AbilitySlot | None:
        slots = self.detect_slots(frame, battle_panel)
        return slots[3] if len(slots) >= 4 else None

    def _detect_bar(self, frame: np.ndarray, battle_panel: Rect | None) -> Rect | None:
        if self.registry is not None:
            matches = self.registry.match(frame, "ability_bar")
            if matches:
                return matches[0].bbox
        height, width = frame.shape[:2]
        panel = battle_panel or Rect(0, 0, width, height)
        nx, ny = self.config.normalized_position
        center = Point(panel.x + panel.width * nx, panel.y + panel.height * ny)
        bar_width = max(80, int(panel.width * 0.35))
        bar_height = max(18, int(panel.height * 0.08))
        return Rect(int(center.x - bar_width / 2), int(center.y - bar_height / 2), bar_width, bar_height)

    def is_ready(self, frame: np.ndarray, bbox: Rect) -> bool:
        crop = frame[bbox.y : bbox.y + bbox.height, bbox.x : bbox.x + bbox.width]
        if crop.size == 0:
            return False
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        saturation = float(np.mean(hsv[:, :, 1]))
        brightness = float(np.mean(hsv[:, :, 2]))
        edges = cv2.Canny(crop, 50, 120)
        edge_density = float(np.count_nonzero(edges)) / float(edges.size)
        return saturation > 35 and brightness > 35 and edge_density > 0.01


def _detect_circular_slots(frame: np.ndarray, battle_panel: Rect | None, target_slot4_x_ratio: float) -> list[AbilitySlot]:
    if battle_panel is None or frame.size == 0:
        return []
    height, width = frame.shape[:2]
    left = max(0, battle_panel.x)
    right = min(width, battle_panel.right)
    top = max(0, int(battle_panel.y + battle_panel.height * 0.52))
    bottom = min(height, battle_panel.bottom)
    if right <= left or bottom <= top:
        return []
    crop = frame[top:bottom, left:right]
    if crop.size == 0:
        return []

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    min_radius = max(12, int(battle_panel.height * 0.04))
    max_radius = max(min_radius + 6, int(battle_panel.height * 0.09))
    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(22, int(battle_panel.width * 0.045)),
        param1=80,
        param2=18,
        minRadius=min_radius,
        maxRadius=max_radius,
    )
    if circles is None:
        return []

    candidates: list[tuple[float, AbilitySlot]] = []
    for raw_x, raw_y, raw_radius in np.round(circles[0]).astype(int):
        center = Point(left + int(raw_x), top + int(raw_y))
        radius = int(raw_radius)
        if center.y < battle_panel.y + battle_panel.height * 0.63:
            continue
        score = _slot_circle_score(frame, center, radius)
        if score <= 0:
            continue
        slot_size = max(18, radius * 2)
        bbox = Rect(int(center.x - slot_size / 2), int(center.y - slot_size / 2), slot_size, slot_size)
        ready = _slot_ready_from_bbox(frame, bbox)
        candidates.append((score, AbilitySlot(0, bbox, center, 0.9 if ready else 0.65, ready)))

    if len(candidates) < 4:
        return []
    deduped: list[tuple[float, AbilitySlot]] = []
    for score, slot in sorted(candidates, key=lambda item: (-item[1].center.y, -item[0], item[1].center.x)):
        if any(abs(slot.center.x - existing.center.x) < max(18, slot.bbox.width * 0.55) for _, existing in deduped):
            continue
        deduped.append((score, slot))
    if len(deduped) < 4:
        return []

    selected = _select_ability_slot_row(deduped, battle_panel, target_slot4_x_ratio)
    if len(selected) < 4:
        return []
    return [
        AbilitySlot(index=index, bbox=slot.bbox, center=slot.center, confidence=slot.confidence, ready=slot.ready)
        for index, (_, slot) in enumerate(selected, start=1)
    ]


def _select_ability_slot_row(
    candidates: list[tuple[float, AbilitySlot]],
    battle_panel: Rect,
    target_slot4_x_ratio: float,
) -> list[tuple[float, AbilitySlot]]:
    rows: list[list[tuple[float, AbilitySlot]]] = []
    for _, seed in sorted(candidates, key=lambda item: item[1].center.y, reverse=True):
        row = [item for item in candidates if abs(item[1].center.y - seed.center.y) <= 24]
        if len(row) >= 4:
            rows.append(row)
    if not rows:
        return []
    target_slot4_x = battle_panel.x + battle_panel.width * target_slot4_x_ratio
    target_row_y = battle_panel.y + battle_panel.height * 0.78
    groups: list[list[tuple[float, AbilitySlot]]] = []
    for row in rows:
        ordered = sorted(row, key=lambda item: item[1].center.x)
        groups.extend(ordered[index : index + 4] for index in range(0, len(ordered) - 3))
    if not groups:
        return []

    def group_score(group: list[tuple[float, AbilitySlot]]) -> tuple[float, float, float]:
        centers_x = [slot.center.x for _, slot in group]
        centers_y = [slot.center.y for _, slot in group]
        deltas = np.diff(centers_x)
        avg_score = sum(score for score, _ in group) / len(group)
        ready_bonus = sum(1 for _, slot in group if slot.ready) / len(group)
        slot4_distance = abs(group[-1][1].center.x - target_slot4_x) / max(1.0, battle_panel.width)
        row_distance = abs(float(np.median(centers_y)) - target_row_y) / max(1.0, battle_panel.height)
        spacing_penalty = float(np.std(deltas)) / max(1.0, float(np.mean(deltas))) if len(deltas) and float(np.mean(deltas)) > 0 else 1.0
        slot4_ready_score = 0.55 if group[-1][1].ready else -0.55
        return (
            avg_score
            + ready_bonus * 0.25
            + slot4_ready_score
            - slot4_distance * 1.5
            - row_distance * 0.35
            - spacing_penalty * 0.25,
            -slot4_distance,
            -spacing_penalty,
        )

    return max(groups, key=group_score)


def _slot_circle_score(frame: np.ndarray, center: Point, radius: int) -> float:
    size = max(16, radius * 2)
    left = max(0, int(center.x - size / 2))
    top = max(0, int(center.y - size / 2))
    right = min(frame.shape[1], left + size)
    bottom = min(frame.shape[0], top + size)
    bbox = Rect(left, top, max(0, right - left), max(0, bottom - top))
    crop = frame[bbox.y : bbox.y + bbox.height, bbox.x : bbox.x + bbox.width]
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    grayish = (hsv[:, :, 1] < 110) & (hsv[:, :, 2] > 35)
    red = ((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168)) & (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 50)
    edges = cv2.Canny(crop, 50, 120)
    gray_ratio = float(np.count_nonzero(grayish)) / float(grayish.size)
    red_ratio = float(np.count_nonzero(red)) / float(red.size)
    edge_density = float(np.count_nonzero(edges)) / float(edges.size)
    if red_ratio > 0.22:
        return 0.0
    if gray_ratio < 0.25 or edge_density < 0.02:
        return 0.0
    return min(1.0, gray_ratio + edge_density * 3.0)


def _slot_ready_from_bbox(frame: np.ndarray, bbox: Rect) -> bool:
    crop = frame[bbox.y : bbox.y + bbox.height, bbox.x : bbox.x + bbox.width]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    saturation = float(np.mean(hsv[:, :, 1]))
    brightness = float(np.mean(hsv[:, :, 2]))
    edges = cv2.Canny(crop, 50, 120)
    edge_density = float(np.count_nonzero(edges)) / float(edges.size)
    return saturation > 20 and brightness > 30 and edge_density > 0.015
