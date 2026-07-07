from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.antibot_cv.viewport.coordinates import Point, Rect


@dataclass(frozen=True)
class TemplateEntry:
    template_id: str
    path: str
    threshold: float = 0.8
    scales: tuple[float, ...] = (1.0,)
    search_roi: Rect | None = None
    max_results: int = 1


@dataclass(frozen=True)
class TemplateMatch:
    template_id: str
    bbox: Rect
    center: Point
    confidence: float
    scale: float


@dataclass(frozen=True)
class TemplateValidationIssue:
    template_id: str
    path: str
    message: str


class TemplateRegistry:
    def __init__(self, entries: dict[str, TemplateEntry], base_dir: str | Path = ".") -> None:
        self.entries = entries
        self.base_dir = Path(base_dir)

    @classmethod
    def from_file(cls, path: str | Path) -> "TemplateRegistry":
        config_path = Path(path)
        if not config_path.exists():
            return cls({}, config_path.parent)
        with config_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        entries: dict[str, TemplateEntry] = {}
        for item in raw.get("templates", []):
            entry = _entry_from_dict(item)
            if entry.template_id in entries:
                raise ValueError(f"Duplicate template_id: {entry.template_id}")
            entries[entry.template_id] = entry
        return cls(entries, config_path.parent)

    def get(self, template_id: str) -> TemplateEntry | None:
        return self.entries.get(template_id)

    def validate(self) -> list[TemplateValidationIssue]:
        issues: list[TemplateValidationIssue] = []
        for entry in self.entries.values():
            if not (0 <= entry.threshold <= 1):
                issues.append(TemplateValidationIssue(entry.template_id, entry.path, "threshold must be between 0 and 1"))
            if entry.max_results < 1:
                issues.append(TemplateValidationIssue(entry.template_id, entry.path, "max_results must be positive"))
            if not entry.scales:
                issues.append(TemplateValidationIssue(entry.template_id, entry.path, "scales must not be empty"))
            resolved = self.resolve_path(entry.path)
            if not resolved.exists():
                issues.append(TemplateValidationIssue(entry.template_id, entry.path, "template file is missing"))
                continue
            image = cv2.imread(str(resolved), cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                issues.append(TemplateValidationIssue(entry.template_id, entry.path, "template image cannot be read"))
        return issues

    def resolve_path(self, path: str) -> Path:
        raw = Path(path)
        if raw.is_absolute():
            return raw
        project_relative = Path.cwd() / raw
        if project_relative.exists():
            return project_relative
        return self.base_dir / raw

    def match(self, frame: np.ndarray, template_id: str) -> list[TemplateMatch]:
        entry = self.entries.get(template_id)
        if entry is None:
            return []
        template_path = self.resolve_path(entry.path)
        if not template_path.exists():
            return []
        template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
        if template is None or template.size == 0:
            return []
        return match_template(frame, template, entry)


def match_template(frame: np.ndarray, template: np.ndarray, entry: TemplateEntry) -> list[TemplateMatch]:
    if frame.size == 0 or template.size == 0:
        return []
    search_frame = frame
    offset_x = 0
    offset_y = 0
    if entry.search_roi is not None:
        roi = entry.search_roi
        height, width = frame.shape[:2]
        x1 = max(0, min(width, roi.x))
        y1 = max(0, min(height, roi.y))
        x2 = max(x1, min(width, roi.x + roi.width))
        y2 = max(y1, min(height, roi.y + roi.height))
        if x2 <= x1 or y2 <= y1:
            return []
        search_frame = frame[y1:y2, x1:x2]
        offset_x = x1
        offset_y = y1
    if search_frame.size == 0:
        return []

    if search_frame.ndim == 3:
        search_frame = cv2.cvtColor(search_frame, cv2.COLOR_BGR2GRAY)
    if template.ndim == 3:
        template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    matches: list[TemplateMatch] = []
    for scale in entry.scales:
        if scale <= 0:
            continue
        scaled = cv2.resize(template, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scaled.shape[0] > search_frame.shape[0] or scaled.shape[1] > search_frame.shape[1]:
            continue
        if float(np.std(scaled)) < 1e-6:
            result = cv2.matchTemplate(search_frame, scaled, cv2.TM_SQDIFF_NORMED)
            confidence_map = 1.0 - result
        else:
            confidence_map = cv2.matchTemplate(search_frame, scaled, cv2.TM_CCOEFF_NORMED)
        candidate_locations = np.where(confidence_map >= entry.threshold)
        for y, x in zip(candidate_locations[0], candidate_locations[1]):
            confidence = float(confidence_map[y, x])
            bbox = Rect(offset_x + int(x), offset_y + int(y), int(scaled.shape[1]), int(scaled.shape[0]))
            matches.append(TemplateMatch(entry.template_id, bbox, bbox.center, confidence, scale))

    matches.sort(key=lambda item: item.confidence, reverse=True)
    return _non_max_suppress(matches)[: entry.max_results]


def _non_max_suppress(matches: list[TemplateMatch], overlap_threshold: float = 0.5) -> list[TemplateMatch]:
    kept: list[TemplateMatch] = []
    for match in matches:
        if all(_iou(match.bbox, other.bbox) < overlap_threshold for other in kept):
            kept.append(match)
    return kept


def _iou(left: Rect, right: Rect) -> float:
    x1 = max(left.x, right.x)
    y1 = max(left.y, right.y)
    x2 = min(left.right, right.right)
    y2 = min(left.bottom, right.bottom)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if intersection == 0:
        return 0.0
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union


def _entry_from_dict(item: dict[str, Any]) -> TemplateEntry:
    roi = item.get("search_roi")
    return TemplateEntry(
        template_id=item["template_id"],
        path=item["path"],
        threshold=float(item.get("threshold", 0.8)),
        scales=tuple(float(scale) for scale in item.get("scales", [1.0])),
        search_roi=None if roi is None else Rect(**roi),
        max_results=int(item.get("max_results", 1)),
    )
