from __future__ import annotations

import time

import pytest

from src.antibot_cv.automation.quest_evidence import (
    EvidenceEnvelope, EvidenceSourceKind, FactKind, SatisfiedFact,
)
from src.antibot_cv.automation.quest_execution_model import RouteKind, RouteLease
from src.antibot_cv.automation.quest_executor_adapters import (
    AreaObjectExecutor, CombatDropExecutor, EvidenceReconciliationError,
    ExecutorContext, ExecutorContextError, GatheringExecutor, KillExecutor,
    NpcInteractionExecutor, TurnInExecutor, VisitExecutor, default_quest_executors,
)
from src.antibot_cv.automation.quest_executor_registry import ExecutorNotFoundError, QuestExecutorRegistry
from src.antibot_cv.automation.quest_plan_model import (
    Acquire, AreaObject, CombatDrop, Gathering, InteractNpc, Kill, Purchase, TurnIn, Visit,
)


def context(atom, kind: RouteKind, **overrides) -> ExecutorContext:
    default_action = (
        "area_object_snapshot" if kind is RouteKind.QUEST_AREA_OBJECT
        else "open_exact_npc" if kind in {
            RouteKind.QUEST_DIALOGUE, RouteKind.QUEST_ORDERED_HANDOFF,
            RouteKind.QUEST_TURN_IN,
        }
        else "open_location_navigator"
    )
    values = dict(
        quest_id="q304", plan_fingerprint="plan-1", actor_id="actor-1",
        client_id="client-1", profile_id="profile-1", tab_id="tab-1",
        revision="rev-1", now=110.0, max_snapshot_age_s=20.0,
        route_lease=RouteLease(
            kind=kind, actor_id="actor-1", client_id="client-1", profile_id="profile-1",
            tab_id="tab-1", quest_id="q304", step_fingerprint=atom.step_id,
            revision="rev-1", generated_at=100.0, issued_at=105.0, expires_at=120.0,
        ),
        action_type=default_action,
    )
    values.update(overrides)
    return ExecutorContext(**values)


def evidence(atom, source: EvidenceSourceKind, **overrides) -> EvidenceEnvelope:
    now = time.time()
    values = dict(
        quest_id="q304", quest_title="Травник", plan_fingerprint="plan-1",
        step_fingerprint=atom.step_id, requirement_id=atom.requirement_id,
        source_kind=source, snapshot_id="snap-2", revision=2,
        client_id="client-1", profile_id="profile-1", tab_id="tab-1",
        generated_at=now, freshness_seconds=60.0, complete=True, causal_baseline="snap-1",
        fact=SatisfiedFact(FactKind.COUNT_AT_LEAST, "item", 1, 1), payload={},
    )
    values.update(overrides)
    return EvidenceEnvelope(**values)


@pytest.mark.parametrize(
    ("executor", "atom", "kind", "action"),
    [
        (KillExecutor(), Kill("кабан", 3), RouteKind.QUEST_LOCATION, "open_hunt"),
        (CombatDropExecutor(), Acquire("кровь", source=CombatDrop("кабан")), RouteKind.QUEST_LOCATION, "open_hunt"),
        (AreaObjectExecutor(), Acquire("мох", source=AreaObject("мшистый камень")), RouteKind.QUEST_AREA_OBJECT, "area_object_snapshot"),
        (GatheringExecutor(), Acquire("листья", source=Gathering("куст")), RouteKind.QUEST_LOCATION, "open_location_navigator"),
        (VisitExecutor(), Visit("Речной тракт"), RouteKind.QUEST_LOCATION, "open_location_navigator"),
        (NpcInteractionExecutor(), InteractNpc("Алхимик"), RouteKind.QUEST_DIALOGUE, "open_exact_npc"),
        (NpcInteractionExecutor(), InteractNpc("Алхимик", "отдать письмо"), RouteKind.QUEST_ORDERED_HANDOFF, "open_exact_npc"),
        (TurnInExecutor(), TurnIn("Травник"), RouteKind.QUEST_TURN_IN, "open_exact_npc"),
    ],
)
def test_capability_adapters_plan_typed_deterministic_intent(executor, atom, kind, action):
    first = executor.plan_intent(atom, context=context(atom, kind, action_type=action))
    second = executor.plan_intent(atom, context=context(atom, kind, action_type=action))
    assert first == second
    assert first.action_type == action
    assert first.requirement_id == atom.requirement_id
    assert first.route_lease.kind is kind


def test_registry_has_no_purchase_executor():
    registry = QuestExecutorRegistry(default_quest_executors())
    with pytest.raises(ExecutorNotFoundError):
        registry.select(Acquire("соль", source=Purchase("Лавочник")))


def test_plan_intent_fails_closed_for_missing_stale_and_foreign_lease():
    atom = Visit("тракт")
    executor = VisitExecutor()
    with pytest.raises(ExecutorContextError, match="typed executor context"):
        executor.plan_intent(atom)
    with pytest.raises(ExecutorContextError, match="expired"):
        executor.plan_intent(atom, context=context(atom, RouteKind.QUEST_LOCATION, now=121.0))
    foreign = context(atom, RouteKind.QUEST_LOCATION, client_id="client-foreign")
    with pytest.raises(ExecutorContextError, match="identity_mismatch"):
        executor.plan_intent(atom, context=foreign)


def test_existing_runtime_action_is_adapted_only_for_owned_domain():
    atom = Acquire("мох", source=AreaObject("камень"))
    executor = AreaObjectExecutor()
    intent = executor.plan_intent(
        atom,
        context=context(
            atom,
            RouteKind.QUEST_AREA_OBJECT,
            action_type="inspect_area_object",
            action_metadata={"candidateId": "candidate-1"},
        ),
    )
    assert intent.action_type == "inspect_area_object"
    assert intent.metadata["candidateId"] == "candidate-1"

    with pytest.raises(ExecutorContextError, match="not owned"):
        executor.plan_intent(
            atom,
            context=context(
                atom, RouteKind.QUEST_AREA_OBJECT, action_type="npc_quest_action",
            ),
        )


def test_reconcile_requires_planned_exact_binding_and_authoritative_source():
    atom = Acquire("мох", source=AreaObject("камень"))
    executor = AreaObjectExecutor()
    envelope = evidence(atom, EvidenceSourceKind.INVENTORY)
    with pytest.raises(EvidenceReconciliationError, match="no planned binding"):
        executor.reconcile(atom, evidence=envelope)
    executor.plan_intent(atom, context=context(atom, RouteKind.QUEST_AREA_OBJECT))
    result = executor.reconcile(atom, evidence=envelope)
    assert result.satisfied is True
    with pytest.raises(EvidenceReconciliationError, match="foreign evidence"):
        executor.reconcile(atom, evidence=evidence(atom, EvidenceSourceKind.INVENTORY, tab_id="tab-x"))
    with pytest.raises(EvidenceReconciliationError, match="not authoritative"):
        executor.reconcile(atom, evidence=evidence(atom, EvidenceSourceKind.NPC_DIALOG))


def test_reconcile_rejects_stale_and_requirement_mismatch():
    atom = Kill("кабан")
    executor = KillExecutor()
    executor.plan_intent(atom, context=context(atom, RouteKind.QUEST_LOCATION))
    with pytest.raises(EvidenceReconciliationError, match="evidence_stale"):
        executor.reconcile(
            atom,
            evidence=evidence(atom, EvidenceSourceKind.COMBAT, generated_at=1.0, freshness_seconds=1.0),
        )
    other = Kill("волк")
    with pytest.raises(EvidenceReconciliationError, match="requirement binding mismatch"):
        executor.reconcile(atom, evidence=evidence(other, EvidenceSourceKind.COMBAT))


def test_checkpoint_restore_preserves_binding_and_rejects_malformed_payload():
    atom = TurnIn("Травник")
    executor = TurnInExecutor()
    executor.plan_intent(atom, context=context(atom, RouteKind.QUEST_TURN_IN))
    executor.reconcile(atom, evidence=evidence(atom, EvidenceSourceKind.TURN_IN))
    restored = TurnInExecutor()
    restored.restore(executor.checkpoint())
    assert restored.checkpoint() == executor.checkpoint()
    malformed = dict(executor.checkpoint())
    malformed["extra"] = True
    with pytest.raises(ValueError, match="unknown or missing"):
        restored.restore(malformed)
    malformed_revision = dict(executor.checkpoint())
    malformed_revision["requirements"] = {
        atom.requirement_id: {**malformed_revision["requirements"][atom.requirement_id], "revision": True}
    }
    with pytest.raises(ValueError, match="malformed"):
        restored.restore(malformed_revision)
