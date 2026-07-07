from __future__ import annotations

import math
import time
from dataclasses import dataclass

from src.antibot_cv.entity_detection.target_locator import LocatedTarget
from src.antibot_cv.viewport.coordinates import Point, Rect


@dataclass
class EntityTrack:
    track_id: int
    target_id: str
    bbox: Rect
    label_center: Point
    estimated_sprite_point: Point
    first_seen_ts: float
    last_seen_ts: float
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    confidence: float = 0.0


class EntityTracker:
    def __init__(self, *, max_distance: float = 80.0, track_timeout_s: float = 1.5) -> None:
        self.max_distance = max_distance
        self.track_timeout_s = track_timeout_s
        self._tracks: dict[int, EntityTrack] = {}
        self._next_track_id = 1

    @property
    def tracks(self) -> list[EntityTrack]:
        return list(self._tracks.values())

    def update(self, detections: list[LocatedTarget], now: float | None = None) -> list[EntityTrack]:
        now = now if now is not None else time.monotonic()
        self._remove_stale(now)
        matched_track_ids: set[int] = set()
        output: list[EntityTrack] = []
        for detection in detections:
            track = self._nearest_track(detection, matched_track_ids)
            if track is None:
                track = self._create_track(detection, now)
            else:
                dt = max(now - track.last_seen_ts, 1e-6)
                track.velocity_x = (detection.label_center.x - track.label_center.x) / dt
                track.velocity_y = (detection.label_center.y - track.label_center.y) / dt
                track.bbox = detection.bbox
                track.label_center = detection.label_center
                track.estimated_sprite_point = detection.interaction_point
                track.last_seen_ts = now
                track.confidence = detection.confidence
            matched_track_ids.add(track.track_id)
            output.append(track)
        return output

    def _nearest_track(self, detection: LocatedTarget, used: set[int]) -> EntityTrack | None:
        best: EntityTrack | None = None
        best_distance = self.max_distance
        for track in self._tracks.values():
            if track.track_id in used or track.target_id != detection.target_id:
                continue
            distance = math.hypot(track.label_center.x - detection.label_center.x, track.label_center.y - detection.label_center.y)
            if distance <= best_distance:
                best = track
                best_distance = distance
        return best

    def _create_track(self, detection: LocatedTarget, now: float) -> EntityTrack:
        track = EntityTrack(
            track_id=self._next_track_id,
            target_id=detection.target_id,
            bbox=detection.bbox,
            label_center=detection.label_center,
            estimated_sprite_point=detection.interaction_point,
            first_seen_ts=now,
            last_seen_ts=now,
            confidence=detection.confidence,
        )
        self._tracks[track.track_id] = track
        self._next_track_id += 1
        return track

    def _remove_stale(self, now: float) -> None:
        stale = [track_id for track_id, track in self._tracks.items() if now - track.last_seen_ts > self.track_timeout_s]
        for track_id in stale:
            del self._tracks[track_id]
