from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.antibot_cv.automation.config import CaptureConfig
from src.antibot_cv.viewport.coordinates import CoordinateMapper, MonitorGeometry, Point, Rect


@dataclass(frozen=True)
class CalibrationReport:
    monitors: list[dict[str, int]]
    selected_monitor: dict[str, int]
    screenshot_dimensions: tuple[int, int]
    logical_mouse_position: tuple[float, float] | None
    scale_x: float
    scale_y: float
    selected_game_roi: dict[str, int] | None

    def as_dict(self) -> dict[str, object]:
        return {
            "monitors": self.monitors,
            "selected_monitor": self.selected_monitor,
            "screenshot_dimensions": self.screenshot_dimensions,
            "logical_mouse_position": self.logical_mouse_position,
            "scale_x": self.scale_x,
            "scale_y": self.scale_y,
            "selected_game_roi": self.selected_game_roi,
        }


class ScreenCapture:
    def __init__(self, config: CaptureConfig) -> None:
        self.config = config
        self._mss = None

    def __enter__(self) -> "ScreenCapture":
        import mss

        self._mss = mss.mss()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._mss is not None:
            self._mss.close()
            self._mss = None

    @property
    def mss(self) -> object:
        if self._mss is None:
            import mss

            self._mss = mss.mss()
        return self._mss

    def monitor(self) -> dict[str, int]:
        monitors = self.mss.monitors  # type: ignore[attr-defined]
        index = min(max(1, self.config.monitor_index), len(monitors) - 1)
        return dict(monitors[index])

    def mapper(self) -> CoordinateMapper:
        monitor = self.monitor()
        shot = self.mss.grab(monitor)  # type: ignore[attr-defined]
        geometry = MonitorGeometry(
            left=monitor["left"],
            top=monitor["top"],
            width=monitor["width"],
            height=monitor["height"],
            capture_width=shot.width,
            capture_height=shot.height,
            logical_width=self.config.logical_width or monitor["width"],
            logical_height=self.config.logical_height or monitor["height"],
        )
        roi = self.config.roi or Rect(0, 0, shot.width, shot.height)
        return CoordinateMapper(geometry, roi)

    def capture_frame(self) -> np.ndarray:
        monitor = self.monitor()
        grab_rect = dict(monitor)
        if self.config.roi is not None:
            grab_rect = {
                "left": monitor["left"] + self.config.roi.x,
                "top": monitor["top"] + self.config.roi.y,
                "width": self.config.roi.width,
                "height": self.config.roi.height,
            }
        shot = self.mss.grab(grab_rect)  # type: ignore[attr-defined]
        frame = np.array(shot)
        return frame[:, :, :3].copy()


def calibrate_capture(config: CaptureConfig) -> CalibrationReport:
    with ScreenCapture(config) as capture:
        monitors = [dict(monitor) for monitor in capture.mss.monitors]  # type: ignore[attr-defined]
        selected = capture.monitor()
        shot = capture.mss.grab(selected)  # type: ignore[attr-defined]
        mouse_position: tuple[float, float] | None = None
        try:
            from pynput.mouse import Controller

            pos = Controller().position
            mouse_position = (float(pos[0]), float(pos[1]))
        except Exception:
            mouse_position = None
        mapper = capture.mapper()
        roi = config.roi
        return CalibrationReport(
            monitors=monitors,
            selected_monitor=selected,
            screenshot_dimensions=(shot.width, shot.height),
            logical_mouse_position=mouse_position,
            scale_x=mapper.monitor.scale_x,
            scale_y=mapper.monitor.scale_y,
            selected_game_roi=None if roi is None else {"x": roi.x, "y": roi.y, "width": roi.width, "height": roi.height},
        )


def draw_mapping_preview(frame: np.ndarray, mapper: CoordinateMapper, points: list[Point] | None = None) -> np.ndarray:
    import cv2

    preview = frame.copy()
    height, width = preview.shape[:2]
    cv2.rectangle(preview, (0, 0), (width - 1, height - 1), (255, 255, 255), 1)
    for point in points or []:
        clamped = mapper.clamp_frame_point(point)
        cv2.drawMarker(preview, (int(clamped.x), int(clamped.y)), (0, 255, 255), cv2.MARKER_CROSS, 18, 2)
    return preview
