"""Action-free coordination for one semantic quest mutation.

This module owns only the observe/evaluate/plan/checkpoint/reconcile sequence.
It deliberately cannot execute a :class:`MutationIntent`; callers must pass a
newly staged intent through the normal ActionExecutor/SafetyGuard boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable

from .quest_evidence import EvidenceEnvelope
from .quest_execution_model import MutationIntent, canonical_json
from .quest_executor_adapters import ExecutorContext
from .quest_executor_registry import QuestExecutorRegistry
from .quest_plan_evaluator import (
    EvaluationContext,
    EvaluationStatus,
    PlanEvaluation,
    evaluate_quest_plan,
)
from .quest_plan_model import QuestLeaf, QuestPlan, iter_requirements
from .quest_step_run import (
    StagedMutation,
    StepActorIdentity,
    StepRun,
    StepRunJournal,
    StepRunPhase,
)


COORDINATOR_CAPABILITY_VERSION = "quest_engine_coordinator_v1"


class QuestCoordinationError(RuntimeError):
    """The durable cursor or supplied authority does not bind exactly."""


@dataclass(frozen=True, slots=True)
class CoordinationContext:
    actor: StepActorIdentity
    evaluation: EvaluationContext
    executor: ExecutorContext
    baseline_revision: int
    retry_budget: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.baseline_revision, bool) or not isinstance(self.baseline_revision, int):
            raise ValueError("baseline_revision must be a non-negative integer")
        if self.baseline_revision < 0:
            raise ValueError("baseline_revision must be a non-negative integer")
        if isinstance(self.retry_budget, bool) or not isinstance(self.retry_budget, int):
            raise ValueError("retry_budget must be a positive integer")
        if self.retry_budget <= 0:
            raise ValueError("retry_budget must be a positive integer")
        identities = (
            (self.actor.client_id, self.evaluation.client_id, self.executor.client_id),
            (self.actor.profile_id, self.evaluation.profile_id, self.executor.profile_id),
            (self.actor.tab_id, self.evaluation.tab_id, self.executor.tab_id),
        )
        if any(len(set(values)) != 1 for values in identities):
            raise ValueError("coordinator actor identity mismatch")


@dataclass(frozen=True, slots=True)
class CoordinationResult:
    evaluation: PlanEvaluation
    run: StepRun | None
    intent: MutationIntent | None
    dispatch_allowed: bool
    reason: str


class QuestEngineCoordinator:
    """Coordinate typed quest execution without touching the action boundary."""

    def __init__(self, registry: QuestExecutorRegistry, journal: StepRunJournal) -> None:
        self._registry = registry
        self._journal = journal

    def prepare(
        self,
        plan: QuestPlan,
        evidence: Iterable[EvidenceEnvelope],
        *,
        capabilities: Iterable[str],
        context: CoordinationContext,
    ) -> CoordinationResult:
        """Stage one intent durably and return it exactly once to the caller."""

        self._validate_plan_context(plan, context)
        evaluation = evaluate_quest_plan(
            plan,
            tuple(evidence),
            capabilities=capabilities,
            context=context.evaluation,
        )
        if evaluation.status is not EvaluationStatus.ACTIONABLE or evaluation.next_atom is None:
            return CoordinationResult(evaluation, None, None, False, evaluation.reason)

        atom = evaluation.next_atom
        executor = self._registry.select(atom)
        current = self._journal.load(actor=context.actor)
        if current is not None:
            if current.phase is StepRunPhase.SETTLED and (
                current.requirement_id != atom.requirement_id or current.step_id != atom.step_id
            ):
                if (
                    current.quest_id != plan.quest_id
                    or current.plan_fingerprint != plan.fingerprint
                    or current.actor != context.actor
                    or current.settled_revision is None
                    or context.baseline_revision < current.settled_revision
                ):
                    raise QuestCoordinationError("settled step cannot advance from stale context")
                replacement = StepRun(
                    capability_version=COORDINATOR_CAPABILITY_VERSION,
                    quest_id=plan.quest_id,
                    plan_fingerprint=plan.fingerprint,
                    step_id=atom.step_id,
                    requirement_id=atom.requirement_id,
                    executor_kind=executor.executor_id,
                    actor=context.actor,
                    baseline_revision=context.baseline_revision,
                    retry_budget=context.retry_budget,
                )
                run = self._journal.compare_and_swap(
                    expected_revision=current.journal_revision,
                    replacement=replacement,
                    actor=context.actor,
                )
            else:
                self._validate_run(current, plan, atom, executor.executor_id, context)
                run = current
            # STAGED and ACK_PENDING are deliberately never reissued, including
            # after process restart.  Only newer evidence may settle them.
            if not run.may_issue_mutation:
                return CoordinationResult(evaluation, run, None, False, "mutation_already_committed")
        else:
            planned = StepRun(
                capability_version=COORDINATOR_CAPABILITY_VERSION,
                quest_id=plan.quest_id,
                plan_fingerprint=plan.fingerprint,
                step_id=atom.step_id,
                requirement_id=atom.requirement_id,
                executor_kind=executor.executor_id,
                actor=context.actor,
                baseline_revision=context.baseline_revision,
                retry_budget=context.retry_budget,
            )
            run = self._journal.compare_and_swap(
                expected_revision=None, replacement=planned, actor=context.actor
            )

        intent = executor.plan_intent(atom, context=context.executor)
        self._validate_intent(intent, run)
        staged = run.stage(
            StagedMutation(
                mutation_id=intent.idempotency_key,
                action_kind=intent.action_type,
                payload_fingerprint=intent.fingerprint,
                staged_from_revision=run.baseline_revision,
                intent_payload=json.loads(canonical_json(intent.canonical_data())),
            )
        )
        stored = self._journal.compare_and_swap(
            expected_revision=run.journal_revision, replacement=staged, actor=context.actor
        )
        return CoordinationResult(evaluation, stored, intent, True, "intent_staged")

    def mark_ack_pending(self, *, actor: StepActorIdentity, mutation_id: str) -> StepRun:
        """Persist acknowledgement without granting a second dispatch."""

        run = self._required_run(actor)
        replacement = run.mark_ack_pending(mutation_id)
        return self._journal.compare_and_swap(
            expected_revision=run.journal_revision, replacement=replacement, actor=actor
        )

    def claim_dispatch(self, *, actor: StepActorIdentity, intent: MutationIntent) -> StepRun:
        """Claim one exact staged intent before the caller reaches its sink."""

        run = self._required_run(actor)
        mutation = run.staged_mutation
        if run.phase is not StepRunPhase.STAGED or mutation is None:
            raise QuestCoordinationError("staged mutation is not dispatchable")
        if (
            intent.requirement_id != run.requirement_id
            or intent.step_fingerprint != run.step_id
            or intent.idempotency_key != mutation.mutation_id
            or intent.action_type != mutation.action_kind
            or intent.fingerprint != mutation.payload_fingerprint
        ):
            raise QuestCoordinationError("foreign mutation dispatch claim")
        return self.mark_ack_pending(actor=actor, mutation_id=mutation.mutation_id)

    def reconcile(
        self,
        plan: QuestPlan,
        evidence: Iterable[EvidenceEnvelope],
        *,
        capabilities: Iterable[str],
        context: CoordinationContext,
    ) -> CoordinationResult:
        """Settle only from newer, complete evidence for the exact staged leaf."""

        self._validate_plan_context(plan, context)
        run = self._required_run(context.actor)
        atom = self._bound_atom(plan, run)
        executor = self._registry.select(atom)
        self._validate_run(run, plan, atom, executor.executor_id, context)
        if run.phase is StepRunPhase.SETTLED:
            evaluation = evaluate_quest_plan(
                plan, tuple(evidence), capabilities=capabilities, context=context.evaluation
            )
            return CoordinationResult(evaluation, run, None, False, "already_settled")
        if run.phase not in {StepRunPhase.STAGED, StepRunPhase.ACK_PENDING}:
            raise QuestCoordinationError("step has no committed mutation to reconcile")

        items = tuple(evidence)
        matching = tuple(item for item in items if item.requirement_id == run.requirement_id)
        if len(matching) != 1 or matching[0].revision <= run.baseline_revision:
            raise QuestCoordinationError("settlement requires one newer authoritative envelope")
        evaluation = evaluate_quest_plan(
            plan, items, capabilities=capabilities, context=context.evaluation
        )
        requirement = next(
            (item for item in evaluation.requirements if item.requirement_id == run.requirement_id),
            None,
        )
        if requirement is None or requirement.status is not EvaluationStatus.SATISFIED:
            raise QuestCoordinationError("staged requirement is not authoritatively satisfied")
        result = executor.reconcile(atom, evidence=matching[0], context=context.executor)
        if getattr(result, "satisfied", None) is not True:
            raise QuestCoordinationError("executor did not settle the staged requirement")

        pending = run
        if pending.phase is StepRunPhase.STAGED:
            assert pending.staged_mutation is not None
            pending = self._journal.compare_and_swap(
                expected_revision=pending.journal_revision,
                replacement=pending.mark_ack_pending(pending.staged_mutation.mutation_id),
                actor=context.actor,
            )
        settled = self._journal.compare_and_swap(
            expected_revision=pending.journal_revision,
            replacement=pending.settle(authoritative_revision=matching[0].revision),
            actor=context.actor,
        )
        return CoordinationResult(evaluation, settled, None, False, "mutation_settled")

    def _required_run(self, actor: StepActorIdentity) -> StepRun:
        run = self._journal.load(actor=actor)
        if run is None:
            raise QuestCoordinationError("step run is missing")
        return run

    @staticmethod
    def _bound_atom(plan: QuestPlan, run: StepRun) -> QuestLeaf:
        matches = tuple(
            atom for atom in iter_requirements(plan.graph.root)
            if atom.requirement_id == run.requirement_id and atom.step_id == run.step_id
        )
        if len(matches) != 1:
            raise QuestCoordinationError("durable step is not uniquely bound to plan")
        return matches[0]

    @staticmethod
    def _validate_plan_context(plan: QuestPlan, context: CoordinationContext) -> None:
        if context.executor.quest_id != plan.quest_id:
            raise QuestCoordinationError("executor context quest mismatch")
        if context.executor.plan_fingerprint != plan.fingerprint:
            raise QuestCoordinationError("executor context plan mismatch")
        if context.baseline_revision != context.evaluation.min_revision:
            raise QuestCoordinationError("evaluation baseline mismatch")

    @staticmethod
    def _validate_run(
        run: StepRun,
        plan: QuestPlan,
        atom: QuestLeaf,
        executor_id: str,
        context: CoordinationContext,
    ) -> None:
        expected = (
            COORDINATOR_CAPABILITY_VERSION,
            plan.quest_id,
            plan.fingerprint,
            atom.step_id,
            atom.requirement_id,
            executor_id,
            context.actor,
            context.baseline_revision,
        )
        observed = (
            run.capability_version,
            run.quest_id,
            run.plan_fingerprint,
            run.step_id,
            run.requirement_id,
            run.executor_kind,
            run.actor,
            run.baseline_revision,
        )
        if observed != expected:
            raise QuestCoordinationError("foreign or stale durable step binding")

    @staticmethod
    def _validate_intent(intent: MutationIntent, run: StepRun) -> None:
        if (
            intent.requirement_id != run.requirement_id
            or intent.step_fingerprint != run.step_id
            or intent.metadata.get("executor_id") != run.executor_kind
            or intent.metadata.get("quest_id") != run.quest_id
            or intent.metadata.get("plan_fingerprint") != run.plan_fingerprint
        ):
            raise QuestCoordinationError("executor returned a foreign mutation intent")


__all__ = [
    "COORDINATOR_CAPABILITY_VERSION",
    "CoordinationContext",
    "CoordinationResult",
    "QuestCoordinationError",
    "QuestEngineCoordinator",
]
