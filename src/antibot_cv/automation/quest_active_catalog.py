"""Fail-closed aggregation of paginated active-quest snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


class ActiveQuestCatalogError(ValueError):
    """Raised when an active-quest catalogue snapshot is not authoritative."""


@dataclass(frozen=True)
class ActiveQuestEntry:
    """One immutable active quest copied from a validated ``started`` page."""

    id: str
    title: str
    data: Mapping[str, object]


class ActiveQuestCatalogAccumulator:
    """Collect strictly sequential ``started`` pages into one immutable result.

    The accumulated result is deliberately unavailable until the terminal page
    has been validated.  This prevents a partially loaded catalogue from being
    mistaken for a complete (possibly empty) active-quest list.
    """

    def __init__(self, *, max_pages: int = 20, max_items: int = 500) -> None:
        if (
            not isinstance(max_pages, int)
            or isinstance(max_pages, bool)
            or max_pages <= 0
            or not isinstance(max_items, int)
            or isinstance(max_items, bool)
            or max_items <= 0
        ):
            raise ValueError("active catalogue limits must be positive integers")
        self.max_pages = max_pages
        self.max_items = max_items
        self._page_count: int | None = None
        self._pages: dict[int, tuple[ActiveQuestEntry, ...]] = {}
        self._by_id: dict[str, ActiveQuestEntry] = {}
        self._revision = 0

    @property
    def page_count(self) -> int | None:
        return self._page_count

    @property
    def revision(self) -> int:
        """Monotonic count of fully validated catalogue snapshots."""

        return self._revision

    @property
    def collected_pages(self) -> tuple[int, ...]:
        return tuple(sorted(self._pages))

    @property
    def complete(self) -> bool:
        return self._page_count is not None and len(self._pages) == self._page_count

    @property
    def next_page(self) -> int | None:
        return None if self.complete else len(self._pages)

    @property
    def result(self) -> tuple[ActiveQuestEntry, ...]:
        if not self.complete:
            raise ActiveQuestCatalogError("active catalogue is incomplete")
        return tuple(entry for page in sorted(self._pages) for entry in self._pages[page])

    def reset(self) -> None:
        self._page_count = None
        self._pages.clear()
        self._by_id.clear()

    def ingest(self, snapshot: object) -> tuple[ActiveQuestEntry, ...] | None:
        """Validate and add one page, returning the result only when complete."""

        if self.complete:
            raise ActiveQuestCatalogError("active catalogue is already complete")
        if not isinstance(snapshot, dict):
            raise ActiveQuestCatalogError("active catalogue snapshot must be an object")
        if snapshot.get("loadStatus") != "loaded" or snapshot.get("mode") != "started":
            raise ActiveQuestCatalogError("started active catalogue is not loaded")
        if snapshot.get("truncated") is not False:
            raise ActiveQuestCatalogError("active catalogue page is truncated or unconfirmed")

        page = _non_negative_int(snapshot.get("currentPage"), "currentPage")
        page_count = _positive_int(snapshot.get("pageCount"), "pageCount")
        has_next_page = snapshot.get("hasNextPage")
        if not isinstance(has_next_page, bool):
            raise ActiveQuestCatalogError("hasNextPage must be boolean")
        if page_count > self.max_pages or page >= page_count:
            raise ActiveQuestCatalogError("active catalogue pagination is outside configured bounds")
        if page != len(self._pages):
            raise ActiveQuestCatalogError("active catalogue pages must be ingested sequentially")
        if self._page_count is not None and page_count != self._page_count:
            raise ActiveQuestCatalogError("active catalogue page count changed during refresh")
        expected_has_next = page + 1 < page_count
        if has_next_page is not expected_has_next:
            raise ActiveQuestCatalogError("active catalogue next-page signal contradicts page count")

        raw_items = snapshot.get("items")
        if not isinstance(raw_items, list):
            raise ActiveQuestCatalogError("active catalogue items are missing")
        parsed = tuple(_parse_entry(item) for item in raw_items)
        if len(self._by_id) + len(parsed) > self.max_items:
            raise ActiveQuestCatalogError("active catalogue item limit exceeded")
        if len({entry.id for entry in parsed}) != len(parsed):
            raise ActiveQuestCatalogError("duplicate active quest id on catalogue page")
        for entry in parsed:
            previous = self._by_id.get(entry.id)
            if previous is not None:
                qualifier = "conflicts across pages" if previous != entry else "is duplicated across pages"
                raise ActiveQuestCatalogError(f"active quest id {qualifier}")

        # Commit only after the entire page has passed validation.
        if self._page_count is None:
            self._page_count = page_count
        self._pages[page] = parsed
        for entry in parsed:
            self._by_id[entry.id] = entry
        if self.complete:
            self._revision += 1
            return self.result
        return None


def _parse_entry(raw: object) -> ActiveQuestEntry:
    if not isinstance(raw, dict) or raw.get("status") != "active":
        raise ActiveQuestCatalogError("active catalogue contains a non-active quest")
    raw_id = raw.get("id")
    if (
        not isinstance(raw_id, str)
        or raw_id != raw_id.strip()
        or not raw_id.isdecimal()
        or int(raw_id) <= 0
    ):
        raise ActiveQuestCatalogError("active quest id must be a positive numeric string")
    raw_title = raw.get("title")
    if not isinstance(raw_title, str) or raw_title != raw_title.strip() or not raw_title:
        raise ActiveQuestCatalogError("active quest title must be an exact nonempty string")
    frozen = _freeze_value(raw)
    if not isinstance(frozen, Mapping):  # Defensive assertion for static type narrowing.
        raise ActiveQuestCatalogError("active quest payload is invalid")
    return ActiveQuestEntry(raw_id, raw_title, frozen)


def _freeze_value(value: object) -> object:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ActiveQuestCatalogError("active quest payload keys must be strings")
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ActiveQuestCatalogError("active quest payload contains an unsupported value")


def _non_negative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ActiveQuestCatalogError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, field: str) -> int:
    result = _non_negative_int(value, field)
    if result <= 0:
        raise ActiveQuestCatalogError(f"{field} must be positive")
    return result
