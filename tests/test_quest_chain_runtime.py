from types import MappingProxyType

import pytest

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_chain_runtime import ChainRefreshState, QuestChainRuntime
from src.antibot_cv.automation.quest_objective_runtime import select_monster_hunt_objective


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
