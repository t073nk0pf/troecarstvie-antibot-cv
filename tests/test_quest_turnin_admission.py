from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_chain_state import QuestChainLease
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_plan_evaluator import (
    EvaluationContext,
    EvaluationStatus,
    PlanEvaluation,
    RequirementEvaluation,
)
from src.antibot_cv.automation.quest_plan_model import (
    Acquire,
    AreaObject,
    CombatDrop,
    QuestGraph,
    QuestPlan,
    Sequence,
    TurnIn,
)
from src.antibot_cv.automation.quest_turnin_admission import (
    ActiveCatalogAuthority,
    TurnInAdmissionStatus,
    admit_turn_in,
)


def _case(*, q304: bool = False):
    requirements = (
        Acquire("кровь", 5, CombatDrop("непобедимый кабан")),
        *(
            (
                Acquire("светящийся мох", 1, AreaObject("светящийся мох")),
                Acquire("пятнистый гриб", 1, AreaObject("пятнистый гриб")),
            ) if q304 else ()
        ),
        TurnIn("Колдунья Вилена" if q304 else "Разбойник Аскорд"),
    )
    quest_id, title = ("304", "Цветочная болезнь") if q304 else ("280", "Фамильная ступка")
    plan = QuestPlan(quest_id, title, QuestGraph(Sequence(requirements)))
    entry = ActiveQuestEntry(quest_id, title, MappingProxyType({"objective": "authoritative"}))
    quest_ref = QuestRef(
        quest_id, title, location="Пристанище" if q304 else "Земли Пращуров",
        giver_names=(("Колдунья Вилена" if q304 else "Разбойник Аскорд"),),
    )
    lease = QuestChainLease(
        quest_id, title, 4, "lease-fingerprint", ("lease-fingerprint",),
        accepted_ref=quest_ref, turn_in_ref_fingerprint="lease-fingerprint",
    )
    context = EvaluationContext("client", "profile", "tab", "baseline", 3, 100.0)
    authority = ActiveCatalogAuthority(
        (entry,), 10, "catalog-10", 99.0, 5.0, "client", "profile", "tab",
        "baseline", plan.fingerprint, "lease-fingerprint",
    )
    evaluations = tuple(
        RequirementEvaluation(
            leaf.requirement_id, leaf.step_id,
            EvaluationStatus.ACTIONABLE if isinstance(leaf, TurnIn) else EvaluationStatus.SATISFIED,
            "test", evidence_revision=None if isinstance(leaf, TurnIn) else 9,
        )
        for leaf in requirements
    )
    evaluation = PlanEvaluation(EvaluationStatus.ACTIONABLE, requirements[-1], evaluations, ())
    return plan, evaluation, lease, entry, quest_ref, context, authority


def _admit(case):
    plan, evaluation, lease, entry, quest_ref, context, authority = case
    return admit_turn_in(plan, evaluation, lease, entry, quest_ref, context, authority, now=100.0)


def test_q280_exact_authority_allows_turn_in() -> None:
    decision = _admit(_case())

    assert decision.status is TurnInAdmissionStatus.ALLOW
    assert decision.token is not None
    assert decision.token.quest_id == "280"
    assert decision.token.catalog_revision == 10


def test_final_refresh_causal_ancestor_allows_but_unknown_baseline_rejects() -> None:
    plan, evaluation, lease, entry, quest_ref, context, authority = _case()
    refreshed = replace(
        authority,
        causal_baseline="final-refresh-baseline",
        causal_ancestors=(context.causal_baseline,),
    )

    admitted = admit_turn_in(
        plan, evaluation, lease, entry, quest_ref, context, refreshed, now=100.0,
    )
    rejected = admit_turn_in(
        plan, evaluation, lease, entry, quest_ref,
        replace(context, causal_baseline="unknown-baseline"), refreshed, now=100.0,
    )

    assert admitted.status is TurnInAdmissionStatus.ALLOW
    assert rejected.status is TurnInAdmissionStatus.UNSAFE
    assert rejected.reason == "foreign_catalog_authority"


def test_q304_subset_cannot_admit_turn_in() -> None:
    case = _case(q304=True)
    plan, evaluation, lease, entry, quest_ref, context, authority = case
    blood, moss, mushroom, turn_in = evaluation.requirements
    partial = PlanEvaluation(
        EvaluationStatus.ACTIONABLE,
        plan.graph.root.nodes[1],
        (blood, replace(moss, status=EvaluationStatus.ACTIONABLE), replace(mushroom, status=EvaluationStatus.ACTIONABLE), turn_in),
        (),
    )

    decision = admit_turn_in(plan, partial, lease, entry, quest_ref, context, authority, now=100.0)

    assert decision.status is TurnInAdmissionStatus.BLOCKED
    assert decision.token is None


def test_stale_foreign_and_ambiguous_authority_fail_closed() -> None:
    plan, evaluation, lease, entry, quest_ref, context, authority = _case()
    other = ActiveQuestEntry(plan.quest_id, "Другое имя", MappingProxyType({}))

    stale = admit_turn_in(
        plan, evaluation, lease, entry, quest_ref, context,
        replace(authority, generated_at=90.0), now=100.0,
    )
    foreign = admit_turn_in(
        plan, evaluation, lease, entry, quest_ref, context,
        replace(authority, tab_id="foreign"), now=100.0,
    )
    ambiguous = admit_turn_in(
        plan, evaluation, lease, entry, quest_ref, context,
        replace(authority, entries=(entry, other)), now=100.0,
    )

    assert stale.status is TurnInAdmissionStatus.BLOCKED
    assert foreign.status is TurnInAdmissionStatus.UNSAFE
    assert ambiguous.status is TurnInAdmissionStatus.UNSAFE
    assert stale.token is foreign.token is ambiguous.token is None


def test_catalog_must_be_newer_than_prerequisite_evidence_and_cover_lease() -> None:
    plan, evaluation, lease, entry, quest_ref, context, authority = _case()

    not_newer = admit_turn_in(
        plan, evaluation, lease, entry, quest_ref, context,
        replace(authority, revision=9), now=100.0,
    )
    older_lease = admit_turn_in(
        plan, evaluation, replace(lease, selected_revision=11), entry, quest_ref, context,
        authority, now=100.0,
    )

    assert not_newer.reason == "catalog_not_newer_than_prerequisites"
    assert older_lease.reason == "catalog_older_than_lease"


def test_npc_and_fingerprint_bindings_are_exact() -> None:
    plan, evaluation, lease, entry, quest_ref, context, authority = _case()

    wrong_npc = replace(quest_ref, giver_names=("Не Аскорд",))
    npc_decision = admit_turn_in(
        plan, evaluation, replace(lease, accepted_ref=wrong_npc), entry, wrong_npc,
        context, authority, now=100.0,
    )
    fingerprint_decision = admit_turn_in(
        plan, evaluation, lease, entry, quest_ref, context,
        replace(authority, plan_fingerprint="foreign"), now=100.0,
    )

    assert npc_decision.reason == "turn_in_npc_mismatch"
    assert fingerprint_decision.reason == "plan_fingerprint_mismatch"
    assert npc_decision.token is fingerprint_decision.token is None
