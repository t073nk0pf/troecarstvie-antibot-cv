from __future__ import annotations

import pytest

from src.antibot_cv.automation.quest_catalog import QuestCatalogAccumulator, QuestCatalogError


def item(quest_id: str, page: int, index: int = 0) -> dict[str, object]:
    return {
        "id": quest_id,
        "title": f"Quest {quest_id}",
        "status": "available",
        "description": "Do the work",
        "reward": "100 XP",
        "locationText": "In Wilds at Frank",
        "giverNames": ["Frank"],
        "navigation": [{"text": "Wilds", "title": "Route"}],
        "catalogPage": page,
        "cardIndex": index,
    }


def page(number: int, count: int, items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "loadStatus": "loaded",
        "mode": "avail",
        "currentPage": number,
        "pageCount": count,
        "hasNextPage": number + 1 < count,
        "items": items,
        "truncated": False,
    }


def test_collects_all_pages_in_stable_page_and_card_order() -> None:
    catalog = QuestCatalogAccumulator()

    catalog.ingest(page(0, 2, [item("1", 0, 0), item("3", 0, 1)]))
    assert catalog.next_page == 1
    catalog.ingest(page(1, 2, [item("2", 1)]))

    assert catalog.complete is True
    assert catalog.collected_pages == (0, 1)
    assert [entry.id for entry in catalog.entries] == ["1", "3", "2"]
    assert catalog.director_refs[0].location == "Wilds"
    assert catalog.director_refs[0].giver_names == ("Frank",)


def test_repeated_page_is_rejected_in_sequential_refresh() -> None:
    catalog = QuestCatalogAccumulator()
    snapshot = page(0, 1, [item("1", 0)])

    catalog.ingest(snapshot)

    with pytest.raises(QuestCatalogError, match="sequentially|terminal"):
        catalog.ingest(snapshot)


@pytest.mark.parametrize(
    "snapshot,reason",
    [
        ({"loadStatus": "not_loaded", "mode": "avail"}, "not loaded"),
        (page(0, 1, [{**item("1", 0), "status": "active"}]), "non-available"),
        (page(0, 1, [{**item("1", 0), "catalogPage": 1}]), "different catalogue page"),
        ({**page(0, 1, [item("1", 0)]), "truncated": True}, "truncated"),
        (page(0, 1, [item("synthetic", 0)]), "identity is incomplete"),
    ],
)
def test_rejects_untrusted_catalogue_snapshots(snapshot: object, reason: str) -> None:
    with pytest.raises(QuestCatalogError, match=reason):
        QuestCatalogAccumulator().ingest(snapshot)


def test_follows_next_page_signal_and_rejects_cross_page_id_conflict() -> None:
    catalog = QuestCatalogAccumulator()
    catalog.ingest(page(0, 2, [item("1", 0)]))

    terminal = page(1, 3, [item("2", 1)])
    terminal["hasNextPage"] = False
    catalog.ingest(terminal)
    assert catalog.complete

    conflict = QuestCatalogAccumulator()
    conflict.ingest(page(0, 2, [item("1", 0)]))
    with pytest.raises(QuestCatalogError, match="conflicts across"):
        conflict.ingest(page(1, 2, [item("1", 1)]))
