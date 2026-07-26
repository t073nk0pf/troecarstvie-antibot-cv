"""Small list-compatible bounded collectors for diagnostic test data."""

from __future__ import annotations

from typing import Generic, TypeVar


T = TypeVar("T")


class BoundedList(list[T], Generic[T]):
    def __init__(self, max_items: int) -> None:
        super().__init__()
        self.max_items = max(0, int(max_items))

    def append(self, item: T) -> None:
        if self.max_items == 0:
            return
        super().append(item)
        overflow = len(self) - self.max_items
        if overflow > 0:
            del self[:overflow]
