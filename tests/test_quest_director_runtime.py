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


def active_page(page: int, count: int, *items: dict[str, object]) -> dict[str, object]:
    return {
        "loadStatus": "loaded",
        "mode": "started",
        "currentPage": page,
        "pageCount": count,
        "hasNextPage": page + 1 < count,
        "items": list(items),
        "truncated": False,
    }


def active_monster(
    quest_id: str,
    *,
    target: str = "Кабан-секач [5]",
    progress: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": quest_id,
        "title": f"Quest {quest_id}",
        "status": "active",
        "objective": "Убивая Кабанов-секачей, получите трофей.",
        "navigation": [
            {"text": "Кабанов-секачей", "target": target},
            {"text": "Врата Древних", "target": "Врата Древних"},
        ],
        "progress": progress,
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
        runtime.begin_accept("2")
    runtime.begin_accept("1")
    runtime.invalidate_active_snapshot()
    assert runtime.decision().intent is QuestDirectorIntent.WAIT
    assert [quest.id for quest in runtime.intake_queue] == ["1", "2"]
    with pytest.raises(RuntimeError, match="fresh active"):
        runtime.acknowledge_accept("1")
    runtime.observe_active([{"id": "1", "title": "Quest 1", "status": "active"}])
    runtime.acknowledge_accept("1")

    next_decision = runtime.decision()
    assert next_decision.intent is QuestDirectorIntent.ACCEPT_QUEST
    assert next_decision.quest is not None and next_decision.quest.id == "2"


def test_paginated_active_refresh_is_not_fresh_until_terminal_page() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_active_refresh()

    assert runtime.ingest_active_page(active_page(0, 2, {"id": "1", "title": "Quest 1", "status": "active"})) == 1
    assert runtime.active_snapshot_fresh is False
    assert runtime.ingest_active_page(active_page(1, 2, {"id": "2", "title": "Quest 2", "status": "active"})) is None
    assert runtime.active_snapshot_fresh is True
    assert [quest.id for quest in runtime.active_quests] == ["1", "2"]


def test_accept_ack_rejects_missing_or_wrong_active_quest_without_losing_queue() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1, available("1", 0)))
    runtime.observe_active([])
    runtime.decision()
    runtime.begin_accept("1")
    runtime.invalidate_active_snapshot()
    runtime.observe_active([])

    with pytest.raises(RuntimeError, match="missing from active"):
        runtime.acknowledge_accept("1")
    assert [quest.id for quest in runtime.intake_queue] == ["1"]
    assert runtime.pending_accept is not None


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


def test_execution_selects_supported_monster_from_complete_catalog_not_first_ref() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        active_page(
            0,
            1,
            {
                "id": "1",
                "title": "Dialogue",
                "status": "active",
                "objective": "Поговорите с воеводой.",
                "navigation": [{"text": "Город", "target": "Город"}],
                "progress": None,
            },
            active_monster("2"),
        )
    )

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert decision.quest is not None and decision.quest.id == "2"
    assert runtime.active_objective is not None
    assert runtime.active_objective.monster.target == "Кабан-секач [5]"
    assert runtime.active_objective_revision == 1


def test_preferred_chain_refreshes_active_first_and_preempts_intake(tmp_path) -> None:
    state_path = tmp_path / "quest-chain.json"
    runtime = QuestDirectorRuntime(
        pinned_quest_id="246",
        chain_state_path=state_path,
    )
    assert runtime.decision().intent is QuestDirectorIntent.REFRESH_ACTIVE
    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        active_page(
            0,
            1,
            {
                "id": "246",
                "title": "Хворь скакунов",
                "status": "active",
                "objective": "Отправляйтесь к алхимику Филониду в Туманные луга.",
                "navigation": [{"text": "Туманные луга", "target": "Туманные луга"}],
                "progress": None,
            },
        )
    )
    runtime.discovery_initialized = True
    runtime.available_snapshot_fresh = True
    runtime.available_quests = (QuestRef("91", "Side quest"),)

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert decision.quest is not None and decision.quest.id == "246"
    assert decision.reason == "pinned_chain_requires_non_monster_executor"
    assert state_path.exists()

    restored = QuestDirectorRuntime(chain_state_path=state_path)
    assert restored.chain.lease is not None
    assert restored.chain.lease.quest_id == "246"
    assert restored.decision().intent is QuestDirectorIntent.REFRESH_ACTIVE


def test_confirmed_terminal_removal_releases_persisted_chain(tmp_path) -> None:
    state_path = tmp_path / "quest-chain.json"
    runtime = QuestDirectorRuntime(pinned_quest_id="246", chain_state_path=state_path)
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1, active_monster("246")))
    assert runtime.decision(current_level_cap=5).intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert state_path.exists()

    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1))
    runtime.confirm_terminal_removal("246")

    assert runtime.chain.lease is None
    assert not state_path.exists()
    assert runtime.completed_since_refresh == 1


def test_explicit_pin_replaces_a_different_persisted_chain_as_deferred(tmp_path) -> None:
    state_path = tmp_path / "quest-chain.json"
    persisted = QuestDirectorRuntime(pinned_quest_id="246", chain_state_path=state_path)
    persisted.begin_active_refresh()
    persisted.ingest_active_page(active_page(0, 1, active_monster("246")))
    assert persisted.decision(current_level_cap=5).intent is QuestDirectorIntent.EXECUTE_ACTIVE

    replacement = QuestDirectorRuntime(pinned_quest_id="31", chain_state_path=state_path)

    assert replacement.chain.lease is None
    assert replacement.preferred_quest_id == "31"
    assert not state_path.exists()


def test_execution_fails_closed_without_complete_active_catalog() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.observe_active([{"id": "2", "title": "Quest 2", "status": "active"}])

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.STOP_UNSAFE
    assert decision.reason == "quest_objective_active_catalog_incomplete"


def test_execution_requires_full_refresh_and_keeps_completed_step_on_pinned_chain() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        active_page(0, 1, active_monster("2", progress={"current": 3, "required": 5, "complete": False}))
    )
    assert runtime.decision(current_level_cap=5).intent is QuestDirectorIntent.EXECUTE_ACTIVE

    runtime.begin_active_refresh()
    assert runtime.decision(current_level_cap=5).intent is QuestDirectorIntent.REFRESH_ACTIVE
    runtime.ingest_active_page(
        active_page(0, 1, active_monster("2", progress={"current": 4, "required": 5, "complete": False}))
    )
    assert runtime.decision(current_level_cap=5).intent is QuestDirectorIntent.EXECUTE_ACTIVE

    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        active_page(0, 1, active_monster("2", progress={"current": 5, "required": 5, "complete": True}))
    )
    continued = runtime.decision(current_level_cap=5)
    assert continued.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert continued.quest is not None and continued.quest.id == "2"
    assert runtime.chain.lease is not None and runtime.chain.lease.quest_id == "2"
    assert runtime.active_objective is not None and runtime.active_objective.complete is True


def test_pinned_chain_survives_catalogue_reordering_and_unsupported_next_step() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1, active_monster("2"), active_monster("3", target="Лиса [4]")))
    first = runtime.decision(current_level_cap=5)
    assert first.quest is not None and first.quest.id == "2"

    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        active_page(
            0,
            1,
            active_monster("3", target="Лиса [4]"),
            {
                "id": "2",
                "title": "Quest 2",
                "status": "active",
                "objective": "Поговорите с алхимиком.",
                "navigation": [{"text": "Туманные луга", "target": "Туманные луга"}],
                "progress": None,
            },
        )
    )

    decision = runtime.decision(current_level_cap=5)
    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert decision.quest is not None and decision.quest.id == "2"
    assert decision.reason == "pinned_chain_requires_non_monster_executor"
    assert runtime.chain.lease is not None and runtime.chain.lease.quest_id == "2"
    assert runtime.active_objective is None


def test_execution_stops_after_bounded_confirmed_victories_without_progress() -> None:
    runtime = QuestDirectorRuntime(max_unchanged_victories=2)
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1, active_monster("2", progress=None)))
    assert runtime.decision(current_level_cap=5).intent is QuestDirectorIntent.EXECUTE_ACTIVE

    runtime.begin_active_refresh(after_confirmed_victory=True)
    runtime.ingest_active_page(active_page(0, 1, active_monster("2", progress=None)))
    assert runtime.decision(current_level_cap=5).intent is QuestDirectorIntent.EXECUTE_ACTIVE

    runtime.begin_active_refresh(after_confirmed_victory=True)
    runtime.ingest_active_page(active_page(0, 1, active_monster("2", progress=None)))
    stopped = runtime.decision(current_level_cap=5)

    assert stopped.intent is QuestDirectorIntent.STOP_UNSAFE
    assert "unchanged_after_victory_budget" in stopped.reason
