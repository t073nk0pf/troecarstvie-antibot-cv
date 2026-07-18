from __future__ import annotations

import time

import pytest

from src.antibot_cv.automation.quest_engine_coordinator import (
    CoordinationContext,
    QuestCoordinationError,
    QuestEngineCoordinator,
)
from src.antibot_cv.automation.quest_evidence import (
    EvidenceEnvelope,
    EvidenceSourceKind,
    FactKind,
    SatisfiedFact,
)
from src.antibot_cv.automation.quest_execution_model import RouteKind, RouteLease
from src.antibot_cv.automation.quest_executor_adapters import (
    ExecutorContext,
    VisitExecutor,
    default_quest_executors,
)
from src.antibot_cv.automation.quest_executor_registry import QuestExecutorRegistry
from src.antibot_cv.automation.quest_plan_evaluator import EvaluationContext, EvaluationStatus
from src.antibot_cv.automation.quest_plan_model import (
    Acquire,
    Purchase,
    QuestGraph,
    QuestPlan,
    Sequence,
    TurnIn,
    Visit,
)
from src.antibot_cv.automation.quest_step_run import (
    StepActorIdentity,
    StepRunJournal,
    StepRunPhase,
)


def _visit_fixture(tmp_path):
    atom = Visit("Старая площадь")
    plan = QuestPlan("q-visit", "Дойти", QuestGraph(atom))
    actor = StepActorIdentity("client-1", "profile-1", "tab-1")
    now = time.time()
    lease = RouteLease(
        kind=RouteKind.QUEST_LOCATION,
        actor_id="actor-1",
        client_id=actor.client_id,
        profile_id=actor.profile_id,
        tab_id=actor.tab_id,
        quest_id=plan.quest_id,
        step_fingerprint=atom.step_id,
        revision="5",
        generated_at=now - 1,
        issued_at=now - 0.5,
        expires_at=now + 60,
    )
    context = CoordinationContext(
        actor=actor,
        evaluation=EvaluationContext(
            actor.client_id, actor.profile_id, actor.tab_id, "base-5", 5, now
        ),
        executor=ExecutorContext(
            quest_id=plan.quest_id,
            plan_fingerprint=plan.fingerprint,
            actor_id="actor-1",
            client_id=actor.client_id,
            profile_id=actor.profile_id,
            tab_id=actor.tab_id,
            revision="5",
            now=now,
            max_snapshot_age_s=30,
            route_lease=lease,
            action_type="open_location_navigator",
        ),
        baseline_revision=5,
    )
    journal = StepRunJournal(tmp_path / "step-run.json")
    registry = QuestExecutorRegistry(default_quest_executors())
    return atom, plan, context, journal, registry


def _visited_evidence(atom, plan, context, *, revision=6):
    return EvidenceEnvelope(
        quest_id=plan.quest_id,
        quest_title=plan.quest_title,
        plan_fingerprint=plan.fingerprint,
        step_fingerprint=atom.step_id,
        requirement_id=atom.requirement_id,
        source_kind=EvidenceSourceKind.LOCATION,
        snapshot_id=f"location-{revision}",
        revision=revision,
        client_id=context.actor.client_id,
        profile_id=context.actor.profile_id,
        tab_id=context.actor.tab_id,
        generated_at=context.executor.now,
        freshness_seconds=60,
        complete=True,
        causal_baseline="base-5",
        fact=SatisfiedFact(FactKind.VISITED, atom.location, True, True),
        payload={},
    )


def test_prepare_stages_before_return_and_never_reissues_after_restore(tmp_path):
    atom, plan, context, journal, registry = _visit_fixture(tmp_path)
    coordinator = QuestEngineCoordinator(registry, journal)

    first = coordinator.prepare(plan, (), capabilities=("visit",), context=context)

    assert first.dispatch_allowed is True
    assert first.intent is not None
    assert first.run is not None and first.run.phase is StepRunPhase.STAGED
    persisted = journal.load(actor=context.actor)
    assert persisted == first.run
    assert persisted.staged_mutation is not None
    assert persisted.staged_mutation.mutation_id == first.intent.idempotency_key
    assert persisted.staged_mutation.intent_payload is not None
    assert persisted.staged_mutation.intent_payload["idempotency_key"] == first.intent.idempotency_key

    restored = QuestEngineCoordinator(registry, StepRunJournal(journal.path))
    second = restored.prepare(plan, (), capabilities=("visit",), context=context)

    assert second.dispatch_allowed is False
    assert second.intent is None
    assert second.reason == "mutation_already_committed"
    assert second.run == first.run


def test_claim_dispatch_is_exact_and_moves_to_ack_pending_once(tmp_path):
    _, plan, context, journal, registry = _visit_fixture(tmp_path)
    coordinator = QuestEngineCoordinator(registry, journal)
    prepared = coordinator.prepare(plan, (), capabilities=("visit",), context=context)
    assert prepared.intent is not None

    claimed = coordinator.claim_dispatch(actor=context.actor, intent=prepared.intent)

    assert claimed.phase is StepRunPhase.ACK_PENDING
    with pytest.raises(QuestCoordinationError, match="not dispatchable"):
        coordinator.claim_dispatch(actor=context.actor, intent=prepared.intent)


def test_failed_intent_planning_leaves_retryable_planned_cursor(tmp_path):
    _, plan, context, journal, _ = _visit_fixture(tmp_path)

    class FlakyVisitExecutor:
        executor_id = "quest.visit"

        def __init__(self):
            self.delegate = VisitExecutor()
            self.fail = True

        def can_execute(self, atom):
            return self.delegate.can_execute(atom)

        def plan_intent(self, atom, *, context=None):
            if self.fail:
                self.fail = False
                raise RuntimeError("planning failed")
            return self.delegate.plan_intent(atom, context=context)

        def reconcile(self, atom, *, evidence):
            return self.delegate.reconcile(atom, evidence=evidence)

        def checkpoint(self):
            return self.delegate.checkpoint()

        def restore(self, payload):
            self.delegate.restore(payload)

    executor = FlakyVisitExecutor()
    coordinator = QuestEngineCoordinator(QuestExecutorRegistry((executor,)), journal)

    with pytest.raises(RuntimeError, match="planning failed"):
        coordinator.prepare(plan, (), capabilities=("visit",), context=context)
    planned = journal.load(actor=context.actor)
    assert planned is not None and planned.phase is StepRunPhase.PLANNED

    retried = coordinator.prepare(plan, (), capabilities=("visit",), context=context)
    assert retried.dispatch_allowed is True
    assert retried.run is not None and retried.run.phase is StepRunPhase.STAGED


def test_reconcile_settles_staged_run_only_from_newer_authoritative_evidence(tmp_path):
    atom, plan, context, journal, registry = _visit_fixture(tmp_path)
    coordinator = QuestEngineCoordinator(registry, journal)
    coordinator.prepare(plan, (), capabilities=("visit",), context=context)

    with pytest.raises(QuestCoordinationError, match="newer authoritative"):
        coordinator.reconcile(
            plan,
            (_visited_evidence(atom, plan, context, revision=5),),
            capabilities=("visit",),
            context=context,
        )

    result = coordinator.reconcile(
        plan,
        (_visited_evidence(atom, plan, context, revision=6),),
        capabilities=("visit",),
        context=context,
    )

    assert result.dispatch_allowed is False
    assert result.intent is None
    assert result.evaluation.status is EvaluationStatus.SATISFIED
    assert result.run is not None and result.run.phase is StepRunPhase.SETTLED
    assert result.run.settled_revision == 6


def test_real_restart_with_fresh_executor_registry_reconciles_from_durable_binding(tmp_path):
    atom, plan, context, journal, registry = _visit_fixture(tmp_path)
    QuestEngineCoordinator(registry, journal).prepare(
        plan, (), capabilities=("visit",), context=context,
    )

    restarted = QuestEngineCoordinator(
        QuestExecutorRegistry(default_quest_executors()), StepRunJournal(journal.path),
    )
    result = restarted.reconcile(
        plan,
        (_visited_evidence(atom, plan, context, revision=6),),
        capabilities=("visit",),
        context=context,
    )

    assert result.run is not None and result.run.phase is StepRunPhase.SETTLED


def test_foreign_actor_cannot_resume_durable_run(tmp_path):
    _, plan, context, journal, registry = _visit_fixture(tmp_path)
    coordinator = QuestEngineCoordinator(registry, journal)
    coordinator.prepare(plan, (), capabilities=("visit",), context=context)
    foreign = StepActorIdentity("client-2", "profile-1", "tab-1")

    with pytest.raises(ValueError, match="foreign step run actor"):
        journal.load(actor=foreign)


def test_purchase_without_capability_is_blocked_without_executor_fallback(tmp_path):
    atom = Acquire("Соль", source=Purchase("Купец"))
    plan = QuestPlan("q-buy", "Покупка", QuestGraph(atom))
    actor = StepActorIdentity("client-1", "profile-1", "tab-1")
    now = time.time()
    # The context lease is intentionally unrelated: BLOCKED evaluation must
    # return before registry selection or mutation planning can inspect it.
    lease = RouteLease(
        RouteKind.QUEST_LOCATION, "actor-1", actor.client_id, actor.profile_id,
        actor.tab_id, plan.quest_id, atom.step_id, "0", now, now, now + 30,
    )
    context = CoordinationContext(
        actor,
        EvaluationContext(actor.client_id, actor.profile_id, actor.tab_id, "base-0", 0, now),
        ExecutorContext(
            plan.quest_id, plan.fingerprint, "actor-1", actor.client_id,
            actor.profile_id, actor.tab_id, "0", now, 30, lease,
        ),
        0,
    )
    journal = StepRunJournal(tmp_path / "purchase.json")
    coordinator = QuestEngineCoordinator(
        QuestExecutorRegistry(default_quest_executors()), journal
    )

    result = coordinator.prepare(plan, (), capabilities=(), context=context)

    assert result.evaluation.status is EvaluationStatus.BLOCKED
    assert result.dispatch_allowed is False
    assert result.intent is None
    assert journal.load(actor=actor) is None


def test_plan_and_context_binding_must_match_before_journal_write(tmp_path):
    _, plan, context, journal, registry = _visit_fixture(tmp_path)
    foreign_plan = QuestPlan("other", plan.quest_title, plan.graph)
    coordinator = QuestEngineCoordinator(registry, journal)

    with pytest.raises(QuestCoordinationError, match="quest mismatch"):
        coordinator.prepare(foreign_plan, (), capabilities=("visit",), context=context)
    assert journal.load(actor=context.actor) is None


def test_settled_sequence_advances_to_next_atom_after_restart(tmp_path):
    first = Visit("Старая площадь")
    second = TurnIn("Староста")
    plan = QuestPlan("q-sequence", "Путь", QuestGraph(Sequence((first, second))))
    actor = StepActorIdentity("client-1", "profile-1", "tab-1")
    now = time.time()

    def context_for(atom, baseline, route_kind):
        lease = RouteLease(
            route_kind, "actor-1", actor.client_id, actor.profile_id, actor.tab_id,
            plan.quest_id, atom.step_id, str(baseline), now - 1, now - 0.5, now + 60,
        )
        return CoordinationContext(
            actor,
            EvaluationContext(
                actor.client_id, actor.profile_id, actor.tab_id, "base-sequence", baseline, now
            ),
                ExecutorContext(
                    plan.quest_id, plan.fingerprint, "actor-1", actor.client_id,
                    actor.profile_id, actor.tab_id, str(baseline), now, 30, lease,
                    action_type=(
                        "open_exact_npc" if route_kind is RouteKind.QUEST_TURN_IN
                        else "open_location_navigator"
                    ),
                ),
            baseline,
        )

    first_context = context_for(first, 5, RouteKind.QUEST_LOCATION)
    journal = StepRunJournal(tmp_path / "sequence.json")
    registry = QuestExecutorRegistry(default_quest_executors())
    coordinator = QuestEngineCoordinator(registry, journal)
    coordinator.prepare(plan, (), capabilities=("visit", "turn_in"), context=first_context)
    first_evidence = EvidenceEnvelope(
        quest_id=plan.quest_id,
        quest_title=plan.quest_title,
        plan_fingerprint=plan.fingerprint,
        step_fingerprint=first.step_id,
        requirement_id=first.requirement_id,
        source_kind=EvidenceSourceKind.LOCATION,
        snapshot_id="visited-6",
        revision=6,
        client_id=actor.client_id,
        profile_id=actor.profile_id,
        tab_id=actor.tab_id,
        generated_at=now,
        freshness_seconds=60,
        complete=True,
        causal_baseline="base-sequence",
        fact=SatisfiedFact(FactKind.VISITED, first.location, True, True),
        payload={},
    )
    coordinator.reconcile(
        plan, (first_evidence,), capabilities=("visit", "turn_in"), context=first_context
    )

    restored = QuestEngineCoordinator(registry, StepRunJournal(journal.path))
    second_context = context_for(second, 6, RouteKind.QUEST_TURN_IN)
    advanced = restored.prepare(
        plan,
        (first_evidence,),
        capabilities=("visit", "turn_in"),
        context=second_context,
    )

    assert advanced.dispatch_allowed is True
    assert advanced.intent is not None
    assert advanced.intent.requirement_id == second.requirement_id
    assert advanced.run is not None and advanced.run.phase is StepRunPhase.STAGED
    assert advanced.run.requirement_id == second.requirement_id
