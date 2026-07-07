from __future__ import annotations

from dataclasses import dataclass

from src.antibot_cv.automation.config import ViewportConfig
from src.antibot_cv.viewport.coordinates import Point, Rect
from src.antibot_cv.viewport.navigator import Direction, ViewportMove, ViewportNavigator


@dataclass(frozen=True)
class DirectionPadGeometry:
    roi: Rect
    normalized_points: dict[Direction, tuple[float, float]]

    def point_for(self, direction: Direction) -> Point:
        nx, ny = self.normalized_points[direction]
        return Point(self.roi.x + self.roi.width * nx, self.roi.y + self.roi.height * ny)


class DirectionPadNavigator(ViewportNavigator):
    def __init__(self, config: ViewportConfig, frame_bounds: Rect) -> None:
        self.config = config
        self.frame_bounds = frame_bounds
        self._index = 0
        self._moves = 0
        self.geometry = DirectionPadGeometry(
            roi=config.direction_pad_roi or _default_direction_pad_roi(frame_bounds),
            normalized_points={Direction[key]: tuple(value) for key, value in config.direction_pad_points.items()},
        )
        self.sequence = [Direction[item] for item in config.search_sequence]

    @property
    def moves(self) -> int:
        return self._moves

    def update_roi(self, roi: Rect) -> None:
        self.geometry = DirectionPadGeometry(roi=roi, normalized_points=self.geometry.normalized_points)

    def next_move(self) -> ViewportMove | None:
        if self._moves >= self.config.max_moves_per_search:
            return None
        direction = self.sequence[self._index % len(self.sequence)]
        self._index += 1
        self._moves += 1
        return ViewportMove(direction, self.geometry.point_for(direction), self._moves)

    def reset(self) -> None:
        self._index = 0
        self._moves = 0


def _default_direction_pad_roi(frame_bounds: Rect) -> Rect:
    size = max(60, min(frame_bounds.width, frame_bounds.height) // 5)
    return Rect(frame_bounds.width - size - 20, frame_bounds.height - size - 20, size, size)
