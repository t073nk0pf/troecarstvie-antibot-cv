from __future__ import annotations

from src.antibot_cv.automation.mutation_lease import (
    ActorKey,
    MutationLeaseCoordinator,
    MutationLeaseMode,
    MutationTarget,
)


def test_generation_seed_allows_new_process_authority_to_advance() -> None:
    target = MutationTarget("profile-a", 17)
    first = MutationLeaseCoordinator(generation_seed=100)
    second = MutationLeaseCoordinator(generation_seed=101)

    assert first.bind(target, "client-a") == 100
    assert second.bind(target, "client-a") == 101
from src.antibot_cv.automation.quest_local_outcome import local_quarantine_reason
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_chain_runtime import QuestChainRuntime


def test_exclusive_run_blocks_control_api_mutation_for_same_profile_tab() -> None:
    coordinator = MutationLeaseCoordinator()
    target = MutationTarget("profile-a", 17)

    run = coordinator.try_acquire(target, "controller:run-1", MutationLeaseMode.EXCLUSIVE_RUN)
    api = coordinator.try_acquire(target, "control-api:open-area", MutationLeaseMode.TRANSIENT)

    assert run is not None
    assert api is None


def test_different_tabs_can_hold_mutation_leases_concurrently() -> None:
    coordinator = MutationLeaseCoordinator()

    first = coordinator.try_acquire(
        MutationTarget("profile-a", 17), "controller:run-1", MutationLeaseMode.EXCLUSIVE_RUN,
    )
    second = coordinator.try_acquire(
        MutationTarget("profile-a", 18), "control-api:open-area", MutationLeaseMode.TRANSIENT,
    )

    assert first is not None
    assert second is not None


def test_actor_generation_and_fence_are_monotonic_and_stale_binding_is_rejected() -> None:
    coordinator = MutationLeaseCoordinator()
    actor = ActorKey("profile-a", 17)

    generation_1 = coordinator.bind(actor, "client-old")
    assert coordinator.bind(actor, "client-old") == generation_1
    first = coordinator.try_acquire(
        actor, "quest:one", MutationLeaseMode.TRANSIENT,
        actor_generation=generation_1,
    )
    assert first is not None
    assert coordinator.bind(actor, "client-new") == generation_1
    assert coordinator.try_acquire(
        actor, "quest:new-while-held", MutationLeaseMode.TRANSIENT,
        actor_generation=generation_1,
    ) is None
    assert coordinator.release(first)

    generation_2 = coordinator.bind(actor, "client-new")
    assert generation_2 == generation_1 + 1
    assert coordinator.try_acquire(
        actor, "quest:stale", MutationLeaseMode.TRANSIENT,
        actor_generation=generation_1,
    ) is None
    second = coordinator.try_acquire(
        actor, "quest:two", MutationLeaseMode.TRANSIENT,
        actor_generation=generation_2,
    )
    assert second is not None
    assert second.fencing_token > first.fencing_token
    assert coordinator.release(first) is False
    assert coordinator.holder(actor) == second


def test_local_quarantine_reason_matches_durable_schema() -> None:
    assert local_quarantine_reason(
        "turn_in_no_progress", "turn_in_not_next_actionable_atom",
    ) == "turn_in_no_progress_turn_in_not_next_actionable_atom"


def test_turn_in_local_reason_persists_in_real_quest_chain(tmp_path) -> None:
    entry = ActiveQuestEntry("304", "Цветочная болезнь", {
        "id": "304",
        "title": "Цветочная болезнь",
        "status": "active",
        "objective": "Вернитесь к колдунье Вилене.",
        "navigation": (),
        "progress": {"current": 1, "required": 1, "complete": True},
    })
    reason = local_quarantine_reason(
        "turn_in_no_progress", "turn_in_not_next_actionable_atom",
    )
    path = tmp_path / "quest-chain.json"
    chain = QuestChainRuntime(state_path=path)

    recorded = chain.quarantine_entry(
        entry,
        reason=reason,
        capability_version="quest_objective_v1",
    )
    restored = QuestChainRuntime(state_path=path)

    assert recorded.reason == reason
    assert restored.quarantines == (recorded,)
