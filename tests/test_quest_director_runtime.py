from __future__ import annotations

import pytest

from src.antibot_cv.automation.quest_director_policy import QuestDirectorIntent, QuestRef
from src.antibot_cv.automation.quest_director_runtime import QuestDirectorRuntime


def available(quest_id: str, page: int) -> dict[str, object]:
    return {
        "id": quest_id,
        "title": f"Quest {quest_id}",
        "status": "available",
        "description": "Work",
        "reward": "XP",
        "locationText": "At Wilds",
        "giverNames": ["Frank"],
        "navigation": [{"text": "Wilds"}],
        "catalogPage": page,
        "cardIndex": 0,
    }


def catalog_page(page: int, count: int, *items: dict[str, object]) -> dict[str, object]:
    return {
        "loadStatus": "loaded",
        "mode": "avail",
        "currentPage": page,
        "pageCount": count,
        "hasNextPage": page + 1 < count,
        "items": list(items),
        "truncated": False,
    }


def test_catalog_refresh_builds_intake_queue_across_every_page() -> None:
    runtime = QuestDirectorRuntime()
    assert runtime.decision().intent is QuestDirectorIntent.REFRESH_AVAILABLE

    runtime.begin_catalog_refresh()
    assert runtime.ingest_catalog_page(catalog_page(0, 2, available("1", 0))) == 1
    assert runtime.ingest_catalog_page(catalog_page(1, 2, available("2", 1))) is None

    assert runtime.decision().intent is QuestDirectorIntent.REFRESH_ACTIVE
    runtime.observe_active([])

    decision = runtime.decision()
    assert decision.intent is QuestDirectorIntent.ACCEPT_QUEST
    assert [quest.id for quest in runtime.intake_queue] == ["1", "2"]
    assert decision.quest is not None and decision.quest.location == "Wilds"


def test_accept_ack_requires_queue_head_and_active_observation_controls_execution() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1, available("1", 0), available("2", 0)))
    runtime.observe_active([])
    assert runtime.decision().intent is QuestDirectorIntent.ACCEPT_QUEST

    with pytest.raises(RuntimeError, match="queue head"):
        runtime.acknowledge_accept("2")
    runtime.acknowledge_accept("1")
    runtime.acknowledge_accept("2")
    runtime.observe_active([{"id": "1", "title": "Quest 1", "status": "active"}])

    assert runtime.decision().intent is QuestDirectorIntent.EXECUTE_ACTIVE


def test_farming_requires_fresh_empty_catalogue_and_refreshes_after_five_completions() -> None:
    runtime = QuestDirectorRuntime(refresh_every_completed=5)
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.observe_active([])
    assert runtime.decision().intent is QuestDirectorIntent.PROFIT_FARM

    runtime.active_quests = tuple(QuestRef(f"q{index}", f"Quest {index}") for index in range(5))
    for index in range(5):
        runtime.mark_quest_completed(f"q{index}")

    assert runtime.decision().intent is QuestDirectorIntent.REFRESH_AVAILABLE


def test_expired_empty_catalogue_forces_rediscovery_before_farming() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.observe_active([])
    runtime.expire_available_snapshot()

    assert runtime.decision().intent is QuestDirectorIntent.REFRESH_AVAILABLE


def test_new_catalog_refresh_discards_stale_intake_queue() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1, available("1", 0)))
    runtime.observe_active([])
    assert runtime.decision().intake_queue

    runtime.begin_catalog_refresh()

    assert runtime.intake_queue == ()
    assert runtime.available_quests == ()


def test_active_snapshot_rejects_synthetic_identity() -> None:
    runtime = QuestDirectorRuntime()

    with pytest.raises(ValueError, match="identity"):
        runtime.observe_active([{"id": "active:title", "title": "Title", "status": "active"}])
