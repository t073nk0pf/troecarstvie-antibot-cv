from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.antibot_cv.automation.quest_active_catalog import (
    ActiveQuestCatalogAccumulator,
    ActiveQuestCatalogError,
)


def item(quest_id: str, title: str | None = None) -> dict[str, object]:
    return {
        "id": quest_id,
        "title": title if title is not None else f"Quest {quest_id}",
        "status": "active",
        "objective": {"kind": "combat", "targets": ["Wolf"]},
    }


def page(number: int, count: int, items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "loadStatus": "loaded",
        "mode": "started",
        "currentPage": number,
        "pageCount": count,
        "hasNextPage": number + 1 < count,
        "items": items,
        "truncated": False,
    }


def test_collects_sequential_pages_and_exposes_result_only_after_terminal_page() -> None:
    catalog = ActiveQuestCatalogAccumulator()

    assert catalog.revision == 0
    assert catalog.ingest(page(0, 2, [item("1"), item("2")])) is None
    assert catalog.complete is False
    assert catalog.next_page == 1
    assert catalog.collected_pages == (0,)
    with pytest.raises(ActiveQuestCatalogError, match="incomplete"):
        _ = catalog.result

    result = catalog.ingest(page(1, 2, [item("3")]))

    assert result is not None
    assert result == catalog.result
    assert tuple(entry.id for entry in result) == ("1", "2", "3")
    assert tuple(entry.title for entry in result) == ("Quest 1", "Quest 2", "Quest 3")
    assert catalog.complete is True
    assert catalog.next_page is None
    assert catalog.revision == 1


def test_empty_terminal_catalogue_is_distinct_from_incomplete_catalogue() -> None:
    catalog = ActiveQuestCatalogAccumulator()

    result = catalog.ingest(page(0, 1, []))

    assert result == ()
    assert catalog.result == ()
    assert catalog.complete is True


@pytest.mark.parametrize(
    "snapshot,reason",
    [
        ({}, "not loaded"),
        ({**page(0, 1, []), "mode": "avail"}, "not loaded"),
        ({**page(0, 1, []), "loadStatus": "not_loaded"}, "not loaded"),
        ({**page(0, 1, []), "truncated": True}, "truncated"),
        ({key: value for key, value in page(0, 1, []).items() if key != "truncated"}, "truncated"),
        ({**page(0, 1, []), "currentPage": True}, "currentPage"),
        ({**page(0, 1, []), "pageCount": 0}, "pageCount"),
        ({**page(0, 1, []), "hasNextPage": 1}, "hasNextPage"),
        ({**page(0, 2, []), "hasNextPage": False}, "contradicts"),
    ],
)
def test_rejects_untrusted_started_snapshot(snapshot: object, reason: str) -> None:
    with pytest.raises(ActiveQuestCatalogError, match=reason):
        ActiveQuestCatalogAccumulator().ingest(snapshot)


def test_rejects_nonsequential_and_inconsistent_pagination_without_partial_commit() -> None:
    catalog = ActiveQuestCatalogAccumulator()
    catalog.ingest(page(0, 3, [item("1")]))

    with pytest.raises(ActiveQuestCatalogError, match="sequentially"):
        catalog.ingest(page(2, 3, [item("2")]))
    assert catalog.collected_pages == (0,)

    with pytest.raises(ActiveQuestCatalogError, match="page count changed"):
        catalog.ingest(page(1, 2, [item("2")]))
    assert catalog.collected_pages == (0,)
    assert catalog.next_page == 1


@pytest.mark.parametrize(
    "bad_item,reason",
    [
        ({**item("1"), "status": "available"}, "non-active"),
        (item("synthetic"), "numeric string"),
        (item("0"), "numeric string"),
        ({**item("1"), "id": 1}, "numeric string"),
        (item(" 1"), "numeric string"),
        (item("1", ""), "exact nonempty"),
        (item("1", " Quest 1 "), "exact nonempty"),
    ],
)
def test_rejects_untrusted_active_quest_identity(bad_item: dict[str, object], reason: str) -> None:
    with pytest.raises(ActiveQuestCatalogError, match=reason):
        ActiveQuestCatalogAccumulator().ingest(page(0, 1, [bad_item]))


def test_rejects_duplicate_or_conflicting_id_across_pages() -> None:
    same_page = ActiveQuestCatalogAccumulator()
    with pytest.raises(ActiveQuestCatalogError, match="duplicate"):
        same_page.ingest(page(0, 1, [item("1"), item("1")]))

    cross_page = ActiveQuestCatalogAccumulator()
    cross_page.ingest(page(0, 2, [item("1")]))
    with pytest.raises(ActiveQuestCatalogError, match="conflicts across"):
        cross_page.ingest(page(1, 2, [item("1", "Changed title")]))
    assert cross_page.collected_pages == (0,)


def test_completed_result_is_immutable_and_defensively_copied() -> None:
    raw = item("1")
    catalog = ActiveQuestCatalogAccumulator()

    result = catalog.ingest(page(0, 1, [raw]))
    assert result is not None
    entry = result[0]
    raw["title"] = "Mutated"
    raw_objective = raw["objective"]
    assert isinstance(raw_objective, dict)
    raw_objective["targets"] = ["Dragon"]

    assert entry.title == "Quest 1"
    assert entry.data["title"] == "Quest 1"
    objective = entry.data["objective"]
    assert isinstance(objective, dict) is False
    assert objective["targets"] == ("Wolf",)  # type: ignore[index]
    with pytest.raises(TypeError):
        entry.data["title"] = "Changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        entry.title = "Changed"  # type: ignore[misc]


def test_reset_discards_previous_result_and_requires_a_new_terminal_page() -> None:
    catalog = ActiveQuestCatalogAccumulator()
    catalog.ingest(page(0, 1, [item("1")]))

    assert catalog.revision == 1

    catalog.reset()

    assert catalog.complete is False
    assert catalog.collected_pages == ()
    assert catalog.next_page == 0
    assert catalog.revision == 1
    with pytest.raises(ActiveQuestCatalogError, match="incomplete"):
        _ = catalog.result

    catalog.ingest(page(0, 1, []))
    assert catalog.revision == 2
