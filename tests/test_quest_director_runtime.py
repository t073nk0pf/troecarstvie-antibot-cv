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


def test_structurally_incomplete_active_card_does_not_block_valid_quest() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(
        0,
        1,
        {
            "id": "375",
            "title": "Крылатый помощник",
            "status": "active",
            "objective": None,
            "navigation": [{"text": "Ярмарку", "target": "Ярмарка дивностей"}],
            "progress": None,
        },
        active_monster("198", target="Шершень-мститель [3]"),
    ))

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert decision.quest is not None and decision.quest.id == "198"


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


def test_confirmed_accept_persists_authoritative_ref_before_restart(tmp_path) -> None:
    state_path = tmp_path / "quest-chain.json"
    runtime = QuestDirectorRuntime(chain_state_path=state_path)
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1, available("1", 0)))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1))
    runtime.decision()
    accepted = runtime.begin_accept("1")
    runtime.invalidate_active_snapshot()
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1, active_monster("1")))

    runtime.acknowledge_accept("1")

    assert runtime.chain.lease is not None
    assert runtime.chain.lease.accepted_ref == accepted
    restored = QuestDirectorRuntime(chain_state_path=state_path)
    assert restored.chain.lease is not None
    assert restored.chain.lease.accepted_ref == accepted


def test_accept_ref_recovers_after_restart_between_action_and_ack(tmp_path) -> None:
    state_path = tmp_path / "quest-chain.json"
    runtime = QuestDirectorRuntime(chain_state_path=state_path)
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1, available("1", 0)))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1))
    runtime.decision()

    accepted = runtime.begin_accept("1")

    restored = QuestDirectorRuntime(chain_state_path=state_path)
    assert restored.chain.lease is None
    assert restored.chain.pending_accepted_ref == accepted

    restored.begin_active_refresh()
    restored.ingest_active_page(active_page(0, 1, active_monster("1")))

    assert restored.chain.pending_accepted_ref is None
    assert restored.chain.lease is not None
    assert restored.chain.lease.accepted_ref == accepted


def _staged_completed_turn_in(tmp_path) -> tuple[QuestDirectorRuntime, object]:
    state_path = tmp_path / "turn-in-chain.json"
    runtime = QuestDirectorRuntime(chain_state_path=state_path)
    runtime.begin_active_refresh()
    completed = active_monster(
        "1",
        progress={"current": 3, "required": 3, "complete": True},
    )
    runtime.ingest_active_page(active_page(0, 1, completed))
    runtime.chain.pin_entry(
        runtime.active_catalog.result[0],
        revision=runtime.active_catalog.revision,
    )
    runtime.chain.bind_accepted_ref(QuestRef(
        "1", "Quest 1", location="Wilds", giver_names=("Frank",),
    ))
    runtime.chain.stage_turn_in_completion(
        active_catalog_revision=runtime.active_catalog.revision,
    )
    return runtime, state_path


def test_staged_turn_in_recovery_releases_only_after_fresh_absence(tmp_path) -> None:
    _, state_path = _staged_completed_turn_in(tmp_path)
    restored = QuestDirectorRuntime(chain_state_path=state_path)

    restored.begin_active_refresh()
    restored.ingest_active_page(active_page(0, 1))

    assert restored.chain.lease is None
    assert restored.turn_in_recovery_outcome == "terminal_absence"
    assert restored.completed_since_refresh == 1
    assert restored.active_objective is None
    assert restored.active_objective_revision is None


def test_staged_turn_in_recovery_reconciles_fresh_continuation(tmp_path) -> None:
    original, state_path = _staged_completed_turn_in(tmp_path)
    old_fingerprint = original.chain.lease.current_fingerprint
    restored = QuestDirectorRuntime(chain_state_path=state_path)

    restored.begin_active_refresh()
    restored.ingest_active_page(active_page(0, 1, active_monster("1", target="Лиса [5]")))

    assert restored.turn_in_recovery_outcome == "awaiting_level_cap"
    assert restored.chain.pending_turn_in_completion is not None

    restored.decision(current_level_cap=5)

    assert restored.chain.lease is not None
    assert restored.chain.lease.current_fingerprint != old_fingerprint
    assert restored.chain.lease.turn_in_ref_fingerprint == old_fingerprint
    assert restored.chain.pending_turn_in_completion is None
    assert restored.turn_in_recovery_outcome == "continued"


def test_staged_before_done_recovery_clears_evidence_for_same_step_retry(tmp_path) -> None:
    _, state_path = _staged_completed_turn_in(tmp_path)
    restored = QuestDirectorRuntime(chain_state_path=state_path)

    restored.begin_active_refresh()
    restored.ingest_active_page(active_page(
        0,
        1,
        active_monster(
            "1",
            progress={"current": 3, "required": 3, "complete": True},
        ),
    ))

    assert restored.chain.lease is not None
    assert restored.chain.pending_turn_in_completion is None
    assert restored.turn_in_recovery_outcome == "same_step_retry"


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


def test_unsupported_nonempty_catalogue_never_becomes_profit_farm() -> None:
    runtime = QuestDirectorRuntime()
    malformed = available("77", 0)
    malformed["giverNames"] = []
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1, malformed))
    runtime.observe_active([])

    decision = runtime.decision()
    assert decision.intent is QuestDirectorIntent.WAIT
    assert decision.reason == "unsupported_available_quests_present"
    assert runtime.available_quests == ()
    assert [(item.quest_id, item.reason) for item in runtime.unsupported_available_entries] == [
        ("77", "missing_giver")
    ]
    assert [entry.id for entry in runtime.catalog.entries] == ["77"]


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


def test_execution_selects_first_safe_catalogue_step_without_monster_bias() -> None:
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
    assert decision.quest is not None and decision.quest.id == "1"
    assert runtime.active_objective is None
    assert runtime.active_route_plan is not None
    assert runtime.active_route_plan.kind.value == "npc_dialogue_or_handoff"
    assert runtime.active_objective_revision == 1


def test_preferred_chain_refreshes_available_then_active_and_preempts_intake(tmp_path) -> None:
    state_path = tmp_path / "quest-chain.json"
    runtime = QuestDirectorRuntime(
        pinned_quest_id="246",
        chain_state_path=state_path,
    )
    assert runtime.decision().intent is QuestDirectorIntent.REFRESH_AVAILABLE
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
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
    assert runtime.preferred_quest_id == ""
    assert state_path.exists()

    restored = QuestDirectorRuntime(chain_state_path=state_path)
    assert restored.chain.lease is not None
    assert restored.chain.lease.quest_id == "246"
    assert restored.decision().intent is QuestDirectorIntent.REFRESH_ACTIVE


def test_preferred_available_quest_is_accepted_after_complete_snapshots() -> None:
    runtime = QuestDirectorRuntime(pinned_quest_id="31")

    assert runtime.decision().intent is QuestDirectorIntent.REFRESH_AVAILABLE
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1, available("31", 0), available("91", 0)))
    assert runtime.decision().intent is QuestDirectorIntent.REFRESH_ACTIVE
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1))

    decision = runtime.decision()

    assert decision.intent is QuestDirectorIntent.ACCEPT_QUEST
    assert decision.quest is not None and decision.quest.id == "31"
    assert [quest.id for quest in decision.intake_queue] == ["31"]


def test_preferred_quest_stops_only_after_available_and_active_catalogues_are_fresh() -> None:
    runtime = QuestDirectorRuntime(pinned_quest_id="31")
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1, available("91", 0)))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1, active_monster("246")))

    decision = runtime.decision()

    assert decision.intent is QuestDirectorIntent.STOP_UNSAFE
    assert decision.reason == "preferred_pinned_quest_not_available_or_active"


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
    assert runtime.active_objective is None
    assert runtime.active_route_plan is not None
    assert runtime.active_route_plan.kind.value == "turn_in"


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


def test_execution_quarantines_after_bounded_confirmed_victories_without_progress() -> None:
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

    assert stopped.intent is QuestDirectorIntent.WAIT
    assert stopped.reason == "quest_active_quarantined"
    assert len(runtime.chain.quarantines) == 1
    assert "unchanged_after_victory_budget" in runtime.chain.quarantines[0].reason


def test_progress_regression_quarantines_pinned_quest_and_selects_next_safe() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        active_page(
            0,
            1,
            active_monster("2", progress={"current": 3, "required": 5, "complete": False}),
            {
                "id": "3",
                "title": "Quest 3",
                "status": "active",
                "objective": "Поговорите с воеводой.",
                "objectiveKind": "dialogue",
                "navigation": [{"text": "Город", "target": "Город"}],
                "progress": None,
            },
        )
    )
    assert runtime.decision(current_level_cap=5).quest.id == "2"
    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        active_page(
            0,
            1,
            active_monster("2", progress={"current": 2, "required": 5, "complete": False}),
            {
                "id": "3",
                "title": "Quest 3",
                "status": "active",
                "objective": "Поговорите с воеводой.",
                "objectiveKind": "dialogue",
                "navigation": [{"text": "Город", "target": "Город"}],
                "progress": None,
            },
        )
    )

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert decision.quest is not None and decision.quest.id == "3"
    assert [item.quest_id for item in runtime.chain.quarantines] == ["2"]
    assert "progress_regressed" in runtime.chain.quarantines[0].reason


def test_pinned_level_cap_regression_is_quarantined_but_fresh_absence_is_global() -> None:
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(catalog_page(0, 1))
    runtime.begin_active_refresh()
    runtime.ingest_active_page(active_page(0, 1, active_monster("2")))
    assert runtime.decision(current_level_cap=5).quest.id == "2"
    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        active_page(0, 1, active_monster("2", target="Кабан-секач [6]"))
    )

    quarantined = runtime.decision(current_level_cap=5)

    assert quarantined.intent is QuestDirectorIntent.WAIT
    assert [item.quest_id for item in runtime.chain.quarantines] == ["2"]

    absent = QuestDirectorRuntime()
    absent.begin_catalog_refresh()
    absent.ingest_catalog_page(catalog_page(0, 1))
    absent.begin_active_refresh()
    absent.ingest_active_page(active_page(0, 1, active_monster("2")))
    assert absent.decision(current_level_cap=5).quest.id == "2"
    absent.begin_active_refresh()
    absent.ingest_active_page(active_page(0, 1))

    stopped = absent.decision(current_level_cap=5)

    assert stopped.intent is QuestDirectorIntent.STOP_UNSAFE
    assert absent.chain.quarantines == ()
