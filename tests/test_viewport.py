from __future__ import annotations

from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.automation.config import to_plain_dict
from src.antibot_cv.viewport.coordinates import Rect
from src.antibot_cv.viewport.direction_pad import DirectionPadNavigator
from src.antibot_cv.viewport.scrollbar import ScrollbarNavigator, ScrollDirection
from tests.conftest import blank_frame


def test_fixed_sequence(test_config: AutomationConfig) -> None:
    config = AutomationConfig.from_dict({**to_plain_dict(test_config), "viewport": {"max_moves_per_search": 4}})
    navigator = DirectionPadNavigator(config.viewport, Rect(0, 0, 320, 240))
    assert [navigator.next_move().direction.value for _ in range(4)] == ["NORTH", "SOUTH", "WEST", "EAST"]


def test_max_moves(test_config: AutomationConfig) -> None:
    navigator = DirectionPadNavigator(test_config.viewport, Rect(0, 0, 320, 240))
    assert navigator.next_move() is not None
    assert navigator.next_move() is not None
    assert navigator.next_move() is not None
    assert navigator.next_move() is None


def test_reset_after_target(test_config: AutomationConfig) -> None:
    navigator = DirectionPadNavigator(test_config.viewport, Rect(0, 0, 320, 240))
    navigator.next_move()
    navigator.reset()
    assert navigator.moves == 0
    assert navigator.next_move().direction.value == "NORTH"


def test_scrollbar_thumb_detection_and_drag() -> None:
    frame = blank_frame(220, 420)
    frame[90:340, 170:195] = (0, 0, 190)
    navigator = ScrollbarNavigator(roi=Rect(140, 20, 70, 380), min_confidence=0.5)
    thumb, confidence = navigator.detect_thumb(frame)
    assert thumb is not None
    assert confidence >= 0.5
    move = navigator.next_drag(ScrollDirection.DOWN, frame, step_px=80)
    assert move is not None
    assert move.end.y > move.start.y
