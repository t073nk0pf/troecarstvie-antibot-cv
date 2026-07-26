"""Validated aggregation of the paginated global available-quest catalogue."""

from __future__ import annotations

from dataclasses import dataclass

from src.antibot_cv.automation.quest_director_policy import QuestRef


class QuestCatalogError(ValueError):
    """Raised when a catalogue page cannot be trusted for autonomous intake."""


@dataclass(frozen=True)
class QuestCatalogEntry:
    id: str
    title: str
    description: str | None
    reward: str | None
    location: str | None
    giver_names: tuple[str, ...]
    navigation: tuple[dict[str, object], ...]
    catalog_page: int
    card_index: int

    def director_ref(self) -> QuestRef:
        return QuestRef(
            id=self.id,
            title=self.title,
            location=self.location,
            giver_names=self.giver_names,
            catalog_page=self.catalog_page,
        )


class QuestCatalogAccumulator:
    """Collect every available page, rejecting partial or contradictory data."""

    def __init__(self, *, max_pages: int = 20, max_items: int = 500) -> None:
        if max_pages <= 0 or max_items <= 0:
            raise ValueError("catalogue limits must be positive")
        self.max_pages = max_pages
        self.max_items = max_items
        self._page_count: int | None = None
        self._terminal_page: int | None = None
        self._pages: dict[int, tuple[QuestCatalogEntry, ...]] = {}
        self._by_id: dict[str, QuestCatalogEntry] = {}

    @property
    def page_count(self) -> int | None:
        return self._page_count

    @property
    def collected_pages(self) -> tuple[int, ...]:
        return tuple(sorted(self._pages))

    @property
    def complete(self) -> bool:
        return self._terminal_page is not None and len(self._pages) == self._terminal_page + 1

    @property
    def next_page(self) -> int | None:
        if self.complete:
            return None
        return len(self._pages)

    @property
    def entries(self) -> tuple[QuestCatalogEntry, ...]:
        result: list[QuestCatalogEntry] = []
        for page in sorted(self._pages):
            result.extend(self._pages[page])
        return tuple(result)

    @property
    def director_refs(self) -> tuple[QuestRef, ...]:
        return tuple(entry.director_ref() for entry in self.entries)

    def reset(self) -> None:
        self._page_count = None
        self._terminal_page = None
        self._pages.clear()
        self._by_id.clear()

    def ingest(self, data: object) -> tuple[QuestCatalogEntry, ...]:
        if not isinstance(data, dict):
            raise QuestCatalogError("catalogue snapshot must be an object")
        if data.get("loadStatus") != "loaded" or data.get("mode") != "avail":
            raise QuestCatalogError("available catalogue is not loaded")
        if data.get("truncated") is True:
            raise QuestCatalogError("catalogue page is truncated")

        page = _non_negative_int(data.get("currentPage"), "currentPage")
        advertised_page_count = _positive_int(data.get("pageCount"), "pageCount")
        has_next_page = data.get("hasNextPage")
        if not isinstance(has_next_page, bool):
            raise QuestCatalogError("hasNextPage must be boolean")
        if advertised_page_count > self.max_pages or page >= self.max_pages:
            raise QuestCatalogError("catalogue pagination is outside configured bounds")
        expected_page = len(self._pages)
        if page != expected_page:
            raise QuestCatalogError("catalogue pages must be ingested sequentially")
        if self._terminal_page is not None:
            raise QuestCatalogError("catalogue already reached its terminal page")
        if has_next_page and page + 1 >= self.max_pages:
            raise QuestCatalogError("catalogue exceeds configured page bound")

        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            raise QuestCatalogError("catalogue items are missing")
        parsed = tuple(_parse_entry(item, page) for item in raw_items)
        if len(self._by_id) + len(parsed) > self.max_items:
            raise QuestCatalogError("catalogue item limit exceeded")
        if len({entry.id for entry in parsed}) != len(parsed):
            raise QuestCatalogError("duplicate quest id on catalogue page")

        for entry in parsed:
            previous = self._by_id.get(entry.id)
            if previous is not None and previous != entry:
                raise QuestCatalogError("quest id conflicts across catalogue pages")

        self._page_count = page + 2 if has_next_page else page + 1
        if not has_next_page:
            self._terminal_page = page
        self._pages[page] = parsed
        for entry in parsed:
            self._by_id[entry.id] = entry
        return parsed


def _parse_entry(raw: object, page: int) -> QuestCatalogEntry:
    if not isinstance(raw, dict) or raw.get("status") != "available":
        raise QuestCatalogError("catalogue contains a non-available quest")
    quest_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not quest_id or not quest_id.isdecimal() or int(quest_id) <= 0 or not title:
        raise QuestCatalogError("catalogue quest identity is incomplete")
    item_page = _non_negative_int(raw.get("catalogPage"), "catalogPage")
    if item_page != page:
        raise QuestCatalogError("quest belongs to a different catalogue page")
    card_index = _non_negative_int(raw.get("cardIndex"), "cardIndex")
    giver_names = _string_tuple(raw.get("giverNames"))
    navigation_raw = raw.get("navigation")
    if not isinstance(navigation_raw, list):
        raise QuestCatalogError("quest navigation is missing")
    navigation = tuple(dict(entry) for entry in navigation_raw if isinstance(entry, dict))
    if len(navigation) != len(navigation_raw):
        raise QuestCatalogError("quest navigation contains invalid entries")
    return QuestCatalogEntry(
        id=quest_id,
        title=title,
        description=_optional_string(raw.get("description")),
        reward=_optional_string(raw.get("reward")),
        location=_quest_location(raw, navigation),
        giver_names=giver_names,
        navigation=navigation,
        catalog_page=item_page,
        card_index=card_index,
    )


def _quest_location(raw: dict[str, object], navigation: tuple[dict[str, object], ...]) -> str | None:
    for entry in navigation:
        label = _optional_string(entry.get("target") or entry.get("text") or entry.get("title"))
        if label:
            return label
    return _optional_string(raw.get("locationText"))


def _optional_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise QuestCatalogError("quest giver list is missing")
    names = tuple(str(item).strip() for item in value if str(item).strip())
    if len(names) != len(value):
        raise QuestCatalogError("quest giver list contains invalid names")
    return names


def _non_negative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise QuestCatalogError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, field: str) -> int:
    result = _non_negative_int(value, field)
    if result <= 0:
        raise QuestCatalogError(f"{field} must be positive")
    return result
