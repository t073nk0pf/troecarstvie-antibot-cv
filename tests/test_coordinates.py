from __future__ import annotations

from src.antibot_cv.viewport.coordinates import CoordinateMapper, MonitorGeometry, Point, Rect


def test_positive_origin_mapping() -> None:
    mapper = CoordinateMapper(MonitorGeometry(0, 0, 1000, 800), Rect(100, 50, 400, 300))
    assert mapper.frame_to_capture(Point(5, 6)) == Point(105, 56)
    assert mapper.capture_to_frame(Point(105, 56)) == Point(5, 6)


def test_negative_origin_mapping() -> None:
    mapper = CoordinateMapper(MonitorGeometry(-1920, 0, 1920, 1080), Rect(0, 0, 1920, 1080))
    assert mapper.frame_to_pynput(Point(100, 50)) == Point(-1820, 50)


def test_retina_scale_mapping() -> None:
    mapper = CoordinateMapper(
        MonitorGeometry(0, 0, 1440, 900, capture_width=2880, capture_height=1800, logical_width=1440, logical_height=900)
    )
    assert mapper.capture_to_logical(Point(288, 180)) == Point(144, 90)
    assert mapper.logical_to_capture(Point(144, 90)) == Point(288, 180)


def test_roi_offset_and_clamp() -> None:
    mapper = CoordinateMapper(MonitorGeometry(0, 0, 500, 500), Rect(10, 20, 100, 80))
    assert mapper.normalized_to_frame(0.5, 0.5) == Point(50, 40)
    assert mapper.clamp_frame_point(Point(999, -1)) == Point(100, 0)
