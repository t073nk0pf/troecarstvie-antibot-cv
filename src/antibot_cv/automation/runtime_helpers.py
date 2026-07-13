from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Iterable

import cv2
import numpy as np

from src.antibot_cv.viewport.coordinates import Point, Rect


def normalize_phrase(value: object) -> str:
    return " ".join(re.findall(r"[0-9a-zа-я]+", str(value or "").casefold().replace("ё", "е")))


def snapshot_epoch_seconds(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def normalized_phrase_matches(observed: object, expected: object) -> bool:
    observed_value = normalize_phrase(observed)
    expected_value = normalize_phrase(expected)
    if not observed_value or not expected_value:
        return False
    return observed_value == expected_value or observed_value in expected_value or expected_value in observed_value


def same_location_name(observed: object, expected: object) -> bool:
    observed_value = normalize_phrase(observed)
    expected_value = normalize_phrase(expected)
    return bool(observed_value and expected_value and observed_value == expected_value)


def is_semantic_location_name(value: object) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    normalized = normalize_phrase(text)
    if len(normalized) < 3 or len(text) > 180:
        return False
    if normalized in {"area", "hunt", "main", "battle", "inventory", "quests", "navigator", "other"}:
        return False
    return not bool(re.match(r"^(?:https?://|/|[a-z_]+\.php(?:\?|$))", text, flags=re.IGNORECASE))


def navigator_target_kind(value: object) -> str:
    del value
    return "auto"


def clean_quest_route_label(value: object) -> str | None:
    label = re.sub(r"проложить\s+путь", "", str(value or ""), flags=re.IGNORECASE)
    label = re.sub(r"\s+", " ", label).strip(" \t\r\n,.;:-")
    if len(normalize_phrase(label)) < 3:
        return None
    return label[:180]


def extract_quest_combat_targets(objective: str, *, configured_names: Iterable[str] = ()) -> tuple[str, ...]:
    text = re.sub(r"\s+", " ", str(objective or "")).strip()
    if not text:
        return ()
    normalized_objective = normalize_phrase(text)
    targets: list[str] = []
    for configured in configured_names:
        name = str(configured or "").strip()
        if name and normalize_phrase(name) in normalized_objective and name not in targets:
            targets.append(name)
    patterns = (
        r"\b(?:убейте|убить|уничтожьте|уничтожить|истребите|истребить|одолейте|победите)\s+(?:не\s+менее\s+)?(?:\d+\s+)?(?P<name>[a-zа-яё][^,.;:]{2,90}?)(?=\s+(?:в|на|у|и|или|после|затем|чтобы|для|из|до|вернитесь)\b|[,.;:]|$)",
        r"\b(?:нападите|напасть|охотьтесь)\s+на\s+(?:\d+\s+)?(?P<name>[a-zа-яё][^,.;:]{2,90}?)(?=\s+(?:в|на|у|и|или|после|затем|чтобы|для|из|до|вернитесь)\b|[,.;:]|$)",
    )
    generic = {"монстров", "монстра", "противников", "противника", "врагов", "врага", "существ", "существо"}
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = re.sub(r"\s+", " ", match.group("name")).strip(" \t\r\n,.;:-")
            candidate = re.sub(r"\s+\d+\s*/\s*\d+$", "", candidate).strip()
            normalized = normalize_phrase(candidate)
            if len(normalized) < 4 or normalized in generic or candidate in targets:
                continue
            targets.append(candidate[:120])
    return tuple(targets)


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return max(0.0, min(100.0, number))


def optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def attack_click_point(result: object, attempt: int) -> Point:
    center = getattr(result, "center", None)
    bbox = getattr(result, "bbox", None)
    if bbox is None:
        return center
    variants = (
        Point(bbox.x + bbox.width * 0.52, bbox.center.y),
        center,
        Point(bbox.x + bbox.width * 0.70, bbox.center.y),
        Point(bbox.x + bbox.width * 0.38, bbox.center.y),
    )
    point = variants[min(attempt, len(variants) - 1)]
    return point if point is not None else bbox.center


def frame_has_visual_content(frame: np.ndarray) -> bool:
    if frame.size == 0:
        return False
    return float(np.count_nonzero(frame)) / float(frame.size) > 0.03


def snapshot_main_href(snapshot: dict[str, object] | None) -> str:
    if snapshot is None:
        return ""
    return str(snapshot.get("mainHref") or "").lower()


def location_map_present(frame: np.ndarray, direction_pad_roi: Rect | None) -> bool:
    if frame.size == 0:
        return False
    if direction_pad_roi is not None and _green_region_present(frame, direction_pad_roi, threshold=0.08):
        return True
    return _hunt_map_layout_present(frame)


def game_shell_present(frame: np.ndarray) -> bool:
    if frame.size == 0:
        return False
    return _top_game_toolbar_present(frame) and _right_game_buttons_present(frame)


def _top_game_toolbar_present(frame: np.ndarray) -> bool:
    height, width = frame.shape[:2]
    crop = frame[int(height * 0.11) : int(height * 0.20), int(width * 0.55) : int(width * 0.98)]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    saturated = cv2.inRange(hsv, np.array([0, 55, 45]), np.array([179, 255, 255]))
    return float(np.count_nonzero(saturated)) / float(saturated.size) > 0.22


def _right_game_buttons_present(frame: np.ndarray) -> bool:
    height, width = frame.shape[:2]
    crop = frame[int(height * 0.18) : int(height * 0.55), int(width * 0.94) : int(width * 0.995)]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, np.array([8, 60, 70]), np.array([35, 255, 255]))
    green = cv2.inRange(hsv, np.array([35, 45, 45]), np.array([95, 255, 255]))
    red = cv2.inRange(hsv, np.array([0, 60, 55]), np.array([12, 255, 255])) | cv2.inRange(
        hsv, np.array([170, 60, 55]), np.array([180, 255, 255])
    )
    return float(np.count_nonzero(gold | green | red)) / float(gold.size) > 0.10


def _green_region_present(frame: np.ndarray, roi: Rect, *, threshold: float) -> bool:
    height, width = frame.shape[:2]
    x1, y1 = max(0, roi.x), max(0, roi.y)
    x2, y2 = min(width, roi.right), min(height, roi.bottom)
    if x2 <= x1 or y2 <= y1:
        return False
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 40, 30]), np.array([90, 255, 220]))
    return float(np.count_nonzero(green)) / float(green.size) > threshold


def _hunt_map_layout_present(frame: np.ndarray) -> bool:
    height, width = frame.shape[:2]
    map_crop = frame[int(height * 0.12) : int(height * 0.93), int(width * 0.18) : int(width * 0.82)]
    if map_crop.size == 0:
        return False
    hsv = cv2.cvtColor(map_crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 35, 25]), np.array([95, 255, 230]))
    if float(np.count_nonzero(green)) / float(green.size) < 0.18:
        return False
    right_crop = frame[int(height * 0.15) : int(height * 0.93), int(width * 0.70) : int(width * 0.83)]
    if right_crop.size == 0:
        return False
    hsv_right = cv2.cvtColor(right_crop, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv_right, np.array([0, 70, 45]), np.array([12, 255, 255])) | cv2.inRange(
        hsv_right, np.array([170, 70, 45]), np.array([180, 255, 255])
    )
    red_by_column = (red > 0).mean(axis=0)
    red_column_count = int(np.count_nonzero(red_by_column > 0.1))
    max_red_column = float(red_by_column.max()) if red_by_column.size else 0.0
    return max_red_column > 0.25 and 8 <= red_column_count <= 45


def detect_direction_pad_roi(frame: np.ndarray) -> Rect | None:
    if frame.size == 0:
        return None
    height, width = frame.shape[:2]
    x_offset, y_offset = int(width * 0.12), int(height * 0.08)
    crop = frame[y_offset : int(height * 0.35), x_offset : int(width * 0.35)]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 40, 30]), np.array([95, 255, 230]))
    contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, Rect]] = []
    for contour in contours:
        x, y, contour_width, contour_height = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if contour_width < 45 or contour_height < 45 or area < 1200:
            continue
        if contour_width > 135 or contour_height > 135:
            continue
        aspect = contour_width / max(1, contour_height)
        if not 0.65 <= aspect <= 1.35:
            continue
        candidates.append((area, Rect(x_offset + x, y_offset + y, contour_width, contour_height)))
    if not candidates:
        return None
    _, best = max(candidates, key=lambda item: item[0])
    size = max(70, int(max(best.width, best.height) * 1.35))
    center = best.center
    return Rect(int(center.x - size / 2), int(center.y - size / 2), size, size)


def browser_confirm_dialog_present(frame: np.ndarray) -> bool:
    return browser_confirm_dialog_cancel_point(frame) is not None


def browser_confirm_dialog_cancel_point(frame: np.ndarray) -> Point | None:
    if frame.size == 0:
        return None
    height, width = frame.shape[:2]
    x1, x2 = int(width * 0.32), int(width * 0.70)
    y1, y2 = int(height * 0.07), int(height * 0.23)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    blue, green, red = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
    dark = (blue < 70) & (green < 70) & (red < 70)
    button_like = ((red > 85) & (blue > 75) & (green < 190) & ((red > green + 15) | (blue > green + 15))) | (
        (red > 190) & (green > 145) & (blue > 145)
    )
    if float(np.count_nonzero(dark)) / float(dark.size) <= 0.45:
        return None
    if float(np.count_nonzero(button_like)) / float(button_like.size) <= 0.003:
        return None
    lower = np.zeros_like(button_like, dtype=np.uint8)
    lower[int(lower.shape[0] * 0.45) :, :] = button_like[int(lower.shape[0] * 0.45) :, :].astype(np.uint8) * 255
    contours, _ = cv2.findContours(lower, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[Rect] = []
    for contour in contours:
        x, y, width_value, height_value = cv2.boundingRect(contour)
        if width_value < 18 or height_value < 8 or width_value * height_value < 140:
            continue
        candidates.append(Rect(x1 + x, y1 + y, width_value, height_value))
    if candidates:
        return min(candidates, key=lambda rect: rect.x).center
    return Point(width * 0.52, height * 0.164)
