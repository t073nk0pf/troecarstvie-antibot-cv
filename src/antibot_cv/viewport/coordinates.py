from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2, self.y + self.height / 2)

    def contains(self, point: Point) -> bool:
        return self.x <= point.x <= self.right and self.y <= point.y <= self.bottom

    def clamp(self, point: Point) -> Point:
        return Point(
            min(max(point.x, self.x), self.right),
            min(max(point.y, self.y), self.bottom),
        )


@dataclass(frozen=True)
class MonitorGeometry:
    left: int
    top: int
    width: int
    height: int
    capture_width: int | None = None
    capture_height: int | None = None
    logical_width: int | None = None
    logical_height: int | None = None

    @property
    def scale_x(self) -> float:
        capture_width = self.capture_width or self.width
        logical_width = self.logical_width or self.width
        return capture_width / logical_width

    @property
    def scale_y(self) -> float:
        capture_height = self.capture_height or self.height
        logical_height = self.logical_height or self.height
        return capture_height / logical_height


class CoordinateMapper:
    """Maps capture pixels, frame ROI, logical screen, and pynput coordinates."""

    def __init__(self, monitor: MonitorGeometry, roi: Rect | None = None) -> None:
        self.monitor = monitor
        self.roi = roi or Rect(0, 0, monitor.capture_width or monitor.width, monitor.capture_height or monitor.height)

    def capture_to_frame(self, point: Point) -> Point:
        return Point(point.x - self.roi.x, point.y - self.roi.y)

    def frame_to_capture(self, point: Point) -> Point:
        return Point(point.x + self.roi.x, point.y + self.roi.y)

    def capture_to_logical(self, point: Point) -> Point:
        return Point(
            self.monitor.left + point.x / self.monitor.scale_x,
            self.monitor.top + point.y / self.monitor.scale_y,
        )

    def logical_to_capture(self, point: Point) -> Point:
        return Point(
            (point.x - self.monitor.left) * self.monitor.scale_x,
            (point.y - self.monitor.top) * self.monitor.scale_y,
        )

    def frame_to_pynput(self, point: Point) -> Point:
        return self.capture_to_logical(self.frame_to_capture(point))

    def normalized_to_frame(self, nx: float, ny: float, rect: Rect | None = None) -> Point:
        bounds = rect or Rect(0, 0, self.roi.width, self.roi.height)
        return Point(bounds.x + bounds.width * nx, bounds.y + bounds.height * ny)

    def clamp_frame_point(self, point: Point, bounds: Rect | None = None) -> Point:
        bounds = bounds or Rect(0, 0, self.roi.width, self.roi.height)
        return bounds.clamp(point)
