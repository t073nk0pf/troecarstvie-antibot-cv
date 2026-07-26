from __future__ import annotations

import numpy as np

from src.antibot_cv.automation.capture import ScreenCapture
from src.antibot_cv.automation.config import CaptureConfig
from src.antibot_cv.automation.controller import AutomationController
from src.antibot_cv.automation.performance_policy import capture_fps_for_state, evidence_hash_view
from src.antibot_cv.automation.state_machine import GameState


def test_capture_fps_is_state_aware_and_caps_configured_60_fps() -> None:
    assert capture_fps_for_state(GameState.ROUTE_RECOVERY, 60) == 2
    assert capture_fps_for_state(GameState.RESTING, 60) == 2
    assert capture_fps_for_state(GameState.QUEST_REFRESH_PENDING, 60) == 2
    assert capture_fps_for_state(GameState.LOCATION_SEARCH, 60) == 5
    assert capture_fps_for_state(GameState.BATTLE_ACTIVE, 60) == 30
    assert capture_fps_for_state(GameState.BATTLE_ACTIVE, 5) == 5


def test_evidence_hash_view_downsamples_without_eager_copy() -> None:
    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    sampled = evidence_hash_view(frame)
    assert sampled.shape == (20, 30, 3)
    assert np.shares_memory(frame, sampled)


def test_controller_evidence_hash_is_lazy_and_cached(monkeypatch) -> None:
    calls: list[tuple[int, ...]] = []
    monkeypatch.setattr(
        "src.antibot_cv.automation.controller.frame_hash",
        lambda frame: calls.append(frame.shape) or "sampled-hash",
    )
    controller = AutomationController.__new__(AutomationController)
    controller._frame_for_evidence_hash = np.zeros((80, 120, 3), dtype=np.uint8)
    controller._cached_frame_hash = None

    assert calls == []
    assert controller._last_frame_hash == "sampled-hash"
    assert controller._last_frame_hash == "sampled-hash"
    assert calls == [(20, 30, 3)]


class _FakeShot:
    def __init__(self, pixels: np.ndarray) -> None:
        self.pixels = pixels
        self.height, self.width = pixels.shape[:2]

    def __array__(self, dtype=None, copy=None):
        return np.asarray(self.pixels, dtype=dtype)


class _FakeMss:
    monitors = [{}, {"left": 0, "top": 0, "width": 3, "height": 2}]

    def __init__(self) -> None:
        self.value = 0

    def grab(self, _rect):
        self.value += 1
        pixels = np.full((2, 3, 4), self.value, dtype=np.uint8)
        return _FakeShot(pixels)


def test_capture_reuses_contiguous_bgr_buffer() -> None:
    capture = ScreenCapture(CaptureConfig())
    capture._mss = _FakeMss()  # noqa: SLF001 - deterministic allocation test.
    first = capture.capture_frame()
    first_pointer = first.__array_interface__["data"][0]
    second = capture.capture_frame()

    assert first.shape == (2, 3, 3)
    assert first.flags.c_contiguous
    assert second.__array_interface__["data"][0] == first_pointer
    assert np.all(second == 2)
