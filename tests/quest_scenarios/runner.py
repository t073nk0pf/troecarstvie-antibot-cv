from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_compiler import QuestCompileResult, compile_quest_plan
from src.antibot_cv.automation.quest_compiler_adapters import LEGACY_COMPILER_ADAPTERS
from src.antibot_cv.automation.quest_evidence import (
    EvidenceEnvelope,
    EvidenceSourceKind,
    FactKind,
    SatisfiedFact,
)
from src.antibot_cv.automation.quest_plan_evaluator import (
    EvaluationContext,
    EvaluationCursor,
    PlanEvaluation,
    cursor_from_evaluation,
    evaluate_quest_plan,
)
from src.antibot_cv.automation.quest_plan_model import Acquire, InteractNpc, Kill, QuestLeaf, TurnIn, Visit


DEFAULT_CONTEXT = EvaluationContext(
    client_id="client-a",
    profile_id="profile-a",
    tab_id="42",
    causal_baseline="active-revision-7",
    min_revision=7,
    now=1_000.0,
)


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    compiled: QuestCompileResult
    evaluation: PlanEvaluation | None
    mutation_intents: tuple[object, ...] = ()


class QuestScenarioRunner:
    """Compile/evaluate harness with an intentionally empty mutation surface."""

    def run(
        self,
        entry: ActiveQuestEntry,
        *,
        evidence: Iterable[EvidenceEnvelope] = (),
        capabilities: Iterable[str] = (),
        context: EvaluationContext = DEFAULT_CONTEXT,
        cursor: EvaluationCursor | None = None,
    ) -> ScenarioResult:
        compiled = compile_quest_plan(entry, adapters=LEGACY_COMPILER_ADAPTERS)
        if compiled.plan is None:
            return ScenarioResult(compiled, None)
        evaluation = evaluate_quest_plan(
            compiled.plan,
            evidence,
            capabilities=capabilities,
            context=context,
            cursor=cursor,
        )
        return ScenarioResult(compiled, evaluation)

    @staticmethod
    def cursor(result: ScenarioResult, *, revision: int = 7) -> EvaluationCursor:
        if result.compiled.plan is None or result.evaluation is None:
            raise ValueError("scenario has no evaluation")
        return cursor_from_evaluation(result.compiled.plan, result.evaluation, revision)


def evidence_for(
    plan,
    leaf: QuestLeaf,
    *,
    observed: int | bool | None = None,
    subject: str | None = None,
    snapshot_id: str | None = None,
    context: EvaluationContext = DEFAULT_CONTEXT,
    revision: int | None = None,
) -> EvidenceEnvelope:
    if isinstance(leaf, (Acquire, Kill)):
        required: int | bool = leaf.count
        value: int | bool = leaf.count if observed is None else observed
        fact_kind = FactKind.COUNT_AT_LEAST
        source = EvidenceSourceKind.INVENTORY if isinstance(leaf, Acquire) else EvidenceSourceKind.COMBAT
        expected_subject = leaf.item if isinstance(leaf, Acquire) else leaf.target
    elif isinstance(leaf, Visit):
        required = True
        value = True if observed is None else observed
        fact_kind = FactKind.VISITED
        source = EvidenceSourceKind.LOCATION
        expected_subject = leaf.location
    elif isinstance(leaf, InteractNpc):
        required = True
        value = True if observed is None else observed
        fact_kind = FactKind.INTERACTED
        source = EvidenceSourceKind.NPC_DIALOG
        expected_subject = leaf.npc
    elif isinstance(leaf, TurnIn):
        required = True
        value = True if observed is None else observed
        fact_kind = FactKind.TURNED_IN
        source = EvidenceSourceKind.TURN_IN
        expected_subject = leaf.npc
    else:
        raise TypeError("unsupported leaf")
    return EvidenceEnvelope(
        quest_id=plan.quest_id,
        quest_title=plan.quest_title,
        plan_fingerprint=plan.fingerprint,
        step_fingerprint=leaf.step_id,
        requirement_id=leaf.requirement_id,
        source_kind=source,
        snapshot_id=snapshot_id or f"snapshot-{leaf.requirement_id[-8:]}",
        revision=context.min_revision if revision is None else revision,
        client_id=context.client_id,
        profile_id=context.profile_id,
        tab_id=context.tab_id,
        generated_at=(context.now or 1_000.0) - 1,
        freshness_seconds=30,
        complete=True,
        causal_baseline=context.causal_baseline,
        fact=SatisfiedFact(fact_kind, subject or expected_subject, value, required),
        payload={},
    )
