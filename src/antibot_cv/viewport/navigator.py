from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.antibot_cv.viewport.coordinates import Point


class Direction(Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    WEST = "WEST"
    EAST = "EAST"


@dataclass(frozen=True)
class ViewportMove:
    direction: Direction
    point: Point | None
    move_index: int


class ViewportNavigator:
    def next_move(self) -> ViewportMove | None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError
