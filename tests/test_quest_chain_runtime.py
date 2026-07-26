from types import MappingProxyType

import pytest

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_chain_runtime import ChainRefreshState, QuestChainRuntime
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_objective_runtime import (
    legacy_quest_step_fingerprint,
    select_monster_hunt_objective,
)


def _entry(quest_id: str, target: str, *, objective: str | None = None) -> ActiveQuestEntry:
    data = MappingProxyType(
        {
            "id": quest_id,
            "title": f"Quest {quest_id}",
            "status": "active",
            "objective": objective or f"Убить {target}",
            "navigation": (MappingProxyType({"text": target, "target": target}),),
            "progress": None,
        }
    )
    return ActiveQuestEntry(quest_id, f"Quest {quest_id}", data)


def test_chain_stays_pinned_when_catalogue_order_changes() -> None:
    first = _entry("2", "Волк [5]")
    objective = select_monster_hunt_objective((first,), current_level_cap=5).objective
    assert objective is not None
    chain = QuestChainRuntime()
    chain.pin(objective, revision=1)

    result = chain.reconcile((_entry("1", "Лиса [4]"), first), current_level_cap=5)

    assert result.state is ChainRefreshState.SAME_STEP
    assert result.lease.quest_id == "2"


def test_chain_advances_to_unsupported_dialogue_without_releasing_lease() -> None:
    first = _entry("2", "Волк [5]")
    objective = select_monster_hunt_objective((first,), current_level_cap=5).objective
    assert objective is not None
    chain = QuestChainRuntime()
    chain.pin(objective, revision=1)
    dialogue = _entry("2", "Туманные луга", objective="Поговорите с алхимиком")

    result = chain.reconcile((dialogue,), current_level_cap=5)

    assert result.state is ChainRefreshState.EXECUTOR_REQUIRED
    assert chain.lease is not None and chain.lease.quest_id == "2"
    assert chain.lease.completed_steps == 1


def test_advanced_fingerprint_can_bind_the_next_turn_in_ref() -> None:
    first = _entry("108", "Сулемайт", objective="Вернитесь к Сулемайту")
    second = _entry("108", "Рокош", objective="Отправляйтесь к Рокошу")
    chain = QuestChainRuntime()
    chain.pin_entry(first, revision=1)
    chain.bind_accepted_ref(QuestRef(
        "108", "Quest 108", location="Прокалённое плато",
        giver_names=("Ремесленник Сулемайт",),
    ))

    advanced = chain.reconcile((second,), current_level_cap=5)
    rebound = chain.bind_accepted_ref(QuestRef(
        "108", "Quest 108", location="Заросли терновника",
        giver_names=("Герой Рокош",),
    ))

    assert advanced.state is ChainRefreshState.EXECUTOR_REQUIRED
    assert rebound.accepted_ref is not None
    assert rebound.accepted_ref.giver_names == ("Герой Рокош",)
    assert rebound.turn_in_ref_fingerprint == rebound.current_fingerprint


def test_chain_rejects_fingerprint_loop_and_unverified_removal() -> None:
    first = _entry("2", "Волк [5]")
    objective = select_monster_hunt_objective((first,), current_level_cap=5).objective
    assert objective is not None
    chain = QuestChainRuntime()
    chain.pin(objective, revision=1)
    second = _entry("2", "Лиса [5]")
    assert chain.reconcile((second,), current_level_cap=5).state is ChainRefreshState.ADVANCED
    assert chain.reconcile((first,), current_level_cap=5).state is ChainRefreshState.LOOP_UNSAFE
    assert chain.reconcile((), current_level_cap=5).state is ChainRefreshState.REMOVED_UNVERIFIED


def test_chain_checkpoint_round_trip_preserves_the_pinned_step() -> None:
    first = _entry("246", "Волк [5]")
    objective = select_monster_hunt_objective((first,), current_level_cap=5).objective
    assert objective is not None
    chain = QuestChainRuntime()
    chain.pin(objective, revision=3)
    chain.reconcile((_entry("246", "Лиса [5]"),), current_level_cap=5)
    checkpoint = chain.checkpoint()
    assert checkpoint is not None

    restored = QuestChainRuntime()
    lease = restored.restore(checkpoint)

    assert lease.quest_id == "246"
    assert lease.completed_steps == 1
    assert lease.current_fingerprint == lease.visited_fingerprints[-1]


def test_awaiting_executor_round_trip_retains_lease_and_checkpoint() -> None:
    entry = _entry("31", "Городская площадь", objective="Купите панцирь у Богдана")
    chain = QuestChainRuntime()
    chain.pin_entry(entry, revision=2)
    chain.mark_awaiting_executor(
        "objective_executor_unavailable",
        capability_version="objective_router_v2",
    )
    checkpoint = chain.checkpoint()
    assert checkpoint is not None

    restored = QuestChainRuntime()
    lease = restored.restore(checkpoint)

    assert lease is not None and lease.quest_id == "31"
    assert lease.awaiting_executor_reason == "objective_executor_unavailable"


def test_record_awaiting_executor_is_exact_identity_bound_and_idempotent() -> None:
    entry = _entry("31", "Городская площадь", objective="Купите панцирь у Богдана")
    chain = QuestChainRuntime()
    chain.pin_entry(entry, revision=2)

    first = chain.record_awaiting_executor(
        "31", "objective_executor_unavailable", "objective_router_v2",
    )
    second = chain.record_awaiting_executor(
        "31", "objective_executor_unavailable", "objective_router_v2",
    )

    assert first == second == chain.lease
    with pytest.raises(RuntimeError, match="does not match"):
        chain.record_awaiting_executor(
            "32", "objective_executor_unavailable", "objective_router_v2",
        )
    with pytest.raises(ValueError, match="identity is invalid"):
        chain.record_awaiting_executor(
            "bad", "objective_executor_unavailable", "objective_router_v2",
        )


def test_legacy_fingerprint_migrates_without_false_step_completion() -> None:
    entry = _entry("31", "Волк [5]")
    legacy, _ = legacy_quest_step_fingerprint(entry)
    assert legacy is not None
    chain = QuestChainRuntime()
    chain.restore(
        {
            "quest_id": "31",
            "quest_title": "Quest 31",
            "selected_revision": 1,
            "current_fingerprint": legacy,
            "visited_fingerprints": [legacy],
            "completed_steps": 0,
        }
    )

    result = chain.reconcile((entry,), current_level_cap=5)

    assert result.state is ChainRefreshState.SAME_STEP
    assert result.lease.completed_steps == 0
    assert result.lease.current_fingerprint != legacy


def test_legacy_migration_preserves_a_b_a_loop_detection() -> None:
    first = _entry("31", "Волк [5]")
    second = _entry("31", "Лиса [5]")
    legacy_first, _ = legacy_quest_step_fingerprint(first)
    legacy_second, _ = legacy_quest_step_fingerprint(second)
    assert legacy_first is not None and legacy_second is not None
    chain = QuestChainRuntime()
    chain.restore(
        {
            "quest_id": "31",
            "quest_title": "Quest 31",
            "selected_revision": 1,
            "current_fingerprint": legacy_second,
            "visited_fingerprints": [legacy_first, legacy_second],
            "completed_steps": 1,
        }
    )

    migrated = chain.reconcile((second,), current_level_cap=5)
    loop = chain.reconcile((first,), current_level_cap=5)

    assert migrated.state is ChainRefreshState.SAME_STEP
    assert migrated.lease.completed_steps == 1
    assert loop.state is ChainRefreshState.LOOP_UNSAFE


def test_chain_checkpoint_round_trip_preserves_authoritative_accepted_ref() -> None:
    entry = _entry("246", "Волк [5]")
    chain = QuestChainRuntime()
    chain.pin_entry(entry, revision=3)
    quest_ref = QuestRef(
        "246",
        "Quest 246",
        location="Южная застава",
        giver_names=("Воевода Ратмир",),
        catalog_page=2,
    )
    chain.bind_accepted_ref(quest_ref)

    checkpoint = chain.checkpoint()
    assert checkpoint is not None
    restored = QuestChainRuntime().restore(checkpoint)

    assert restored.accepted_ref == quest_ref
    assert restored.turn_in_ref_fingerprint == restored.current_fingerprint


def test_legacy_chain_checkpoint_restores_without_turn_in_ref() -> None:
    entry = _entry("246", "Волк [5]")
    chain = QuestChainRuntime()
    chain.pin_entry(entry, revision=3)
    checkpoint = chain.checkpoint()
    assert checkpoint is not None and "accepted_ref" not in checkpoint

    assert QuestChainRuntime().restore(checkpoint).accepted_ref is None


def test_pending_accept_ref_round_trip_without_pinned_lease() -> None:
    quest_ref = QuestRef(
        "246",
        "Quest 246",
        location="Южная застава",
        giver_names=("Воевода Ратмир",),
    )
    chain = QuestChainRuntime()
    chain.stage_accepted_ref(quest_ref)
    checkpoint = chain.checkpoint()
    assert checkpoint is not None

    restored = QuestChainRuntime()
    assert restored.restore(checkpoint) is None
    assert restored.pending_accepted_ref == quest_ref


def test_pending_turn_in_completion_round_trip() -> None:
    entry = _entry("246", "Волк [5]")
    chain = QuestChainRuntime()
    chain.pin_entry(entry, revision=3)
    chain.stage_turn_in_completion(active_catalog_revision=7)
    checkpoint = chain.checkpoint()
    assert checkpoint is not None

    restored_runtime = QuestChainRuntime()
    restored_runtime.restore(checkpoint)

    assert restored_runtime.pending_turn_in_completion is not None
    assert restored_runtime.pending_turn_in_completion.completed_fingerprint == (
        restored_runtime.lease.current_fingerprint
    )
    assert restored_runtime.pending_turn_in_completion_restored is True


def test_chain_restore_rejects_partial_or_reordered_history() -> None:
    chain = QuestChainRuntime()

    with pytest.raises(ValueError):
        chain.restore({"quest_id": "246"})
    with pytest.raises(ValueError):
        chain.restore(
            {
                "quest_id": "246",
                "quest_title": "Quest 246",
                "selected_revision": 1,
                "current_fingerprint": "second",
                "visited_fingerprints": ["second", "first"],
                "completed_steps": 1,
            }
        )


def test_restore_cross_invariants_are_transactional() -> None:
    original = _entry("31", "Волк [5]")
    chain = QuestChainRuntime()
    chain.pin_entry(original, revision=1)
    before = chain.checkpoint()
    assert before is not None
    conflicting = dict(before)
    conflicting["completed_steps"] = 7

    with pytest.raises(ValueError, match="completed quest steps"):
        chain.restore(conflicting)

    assert chain.checkpoint() == before


def test_restore_rejects_pending_ref_and_quarantine_conflicts() -> None:
    entry = _entry("31", "Волк [5]")
    chain = QuestChainRuntime()
    chain.pin_entry(entry, revision=1)
    checkpoint = chain.checkpoint()
    assert checkpoint is not None
    fingerprint = checkpoint["current_fingerprint"]
    pending_conflict = {
        **checkpoint,
        "pending_accepted_ref": {
            "id": "32",
            "title": "Quest 32",
            "accept_ref": None,
            "location": "Город",
            "giver_names": ["Воевода"],
            "catalog_page": 0,
        },
    }
    quarantine_conflict = {
        **checkpoint,
        "quarantines": [
            {
                "quest_id": "31",
                "quest_title": "Quest 31",
                "fingerprint": fingerprint,
                "reason": "objective_alternative_unsafe",
                "capability_version": "objective_router_v2",
                "recorded_at": 1.0,
            }
        ],
    }

    with pytest.raises(ValueError, match="pending accepted reference conflicts"):
        QuestChainRuntime().restore(pending_conflict)
    with pytest.raises(ValueError, match="quarantine conflicts"):
        QuestChainRuntime().restore(quarantine_conflict)


def test_restore_requires_full_pending_and_accepted_ref_equality() -> None:
    entry = _entry("31", "Волк [5]")
    chain = QuestChainRuntime()
    chain.pin_entry(entry, revision=1)
    accepted = QuestRef(
        "31",
        "Quest 31",
        location="Город",
        giver_names=("Воевода",),
        catalog_page=0,
    )
    chain.bind_accepted_ref(accepted)
    checkpoint = chain.checkpoint()
    assert checkpoint is not None
    checkpoint["pending_accepted_ref"] = {
        "id": "31",
        "title": "Quest 31",
        "accept_ref": None,
        "location": "Другой город",
        "giver_names": ["Воевода"],
        "catalog_page": 0,
    }

    with pytest.raises(ValueError, match="pending accepted reference conflicts"):
        QuestChainRuntime().restore(checkpoint)


def test_restore_rejects_no_lease_pending_ref_quarantined_same_quest() -> None:
    pending = {
        "id": "31",
        "title": "Quest 31",
        "accept_ref": None,
        "location": "Город",
        "giver_names": ["Воевода"],
        "catalog_page": 0,
    }
    quarantine = {
        "quest_id": "31",
        "quest_title": "Quest 31",
        "fingerprint": "fingerprint",
        "reason": "objective_alternative_unsafe",
        "capability_version": "objective_router_v2",
        "recorded_at": 1.0,
    }

    with pytest.raises(ValueError, match="pending accepted reference conflicts with quarantine"):
        QuestChainRuntime().restore(
            {
                "pending_accepted_ref": pending,
                "quarantines": [quarantine],
            }
        )


def test_restore_rejects_multiple_quarantines_for_one_quest() -> None:
    base = {
        "quest_id": "31",
        "quest_title": "Quest 31",
        "reason": "objective_alternative_unsafe",
        "capability_version": "objective_router_v2",
        "recorded_at": 1.0,
    }
    with pytest.raises(ValueError, match="invalid quest quarantine evidence"):
        QuestChainRuntime().restore(
            {
                "quarantines": [
                    {**base, "fingerprint": "first"},
                    {**base, "fingerprint": "second"},
                ]
            }
        )
