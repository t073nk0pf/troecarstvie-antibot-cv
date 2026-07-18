from __future__ import annotations

from dataclasses import replace

import pytest

from src.antibot_cv.automation.quest_compiler import QuestCompileStatus, compile_quest_plan
from src.antibot_cv.automation.quest_evidence import EvidenceEnvelope
from src.antibot_cv.automation.quest_plan_evaluator import EvaluationCursor, EvaluationStatus
from src.antibot_cv.automation.quest_plan_model import (
    Acquire, AllOf, AlternativePolicy, AnyOf, CombatDrop, InteractNpc,
    Purchase, QuestGraph, QuestPlan, Sequence, TurnIn, Visit,
)

from .fixtures import entry, q31, q280, q304, q360
from .runner import DEFAULT_CONTEXT, QuestScenarioRunner, evidence_for


CAPABILITIES = {"combat_drop", "area_object", "gathering", "visit", "turn_in"}


def leaves(plan):
    def walk(node):
        if isinstance(node, (Sequence, AllOf)):
            return tuple(item for child in node.nodes for item in walk(child))
        return (node,)
    return walk(plan.graph.root)


def test_q280_existing_item_advances_only_to_turn_in() -> None:
    runner = QuestScenarioRunner()
    compiled = compile_quest_plan(q280())
    assert compiled.status is QuestCompileStatus.READY and compiled.plan is not None
    acquire, turn_in = leaves(compiled.plan)

    result = runner.run(q280(), evidence=(evidence_for(compiled.plan, acquire),), capabilities=CAPABILITIES)

    assert result.evaluation.status is EvaluationStatus.ACTIONABLE
    assert result.evaluation.next_atom == turn_in
    assert result.mutation_intents == ()


def test_q304_requires_blood_then_each_area_requirement_before_turn_in() -> None:
    runner = QuestScenarioRunner()
    compiled = compile_quest_plan(q304())
    assert compiled.plan is not None
    blood, moss, mushroom, leaves_item, turn_in = leaves(compiled.plan)
    evidence = [evidence_for(compiled.plan, blood, revision=7)]

    for expected in (moss, mushroom, leaves_item):
        result = runner.run(q304(), evidence=evidence, capabilities=CAPABILITIES)
        assert result.evaluation.next_atom == expected
        assert turn_in.requirement_id != result.evaluation.next_atom.requirement_id
        evidence.append(evidence_for(compiled.plan, expected, revision=8))

    result = runner.run(q304(), evidence=evidence, capabilities=CAPABILITIES)
    assert result.evaluation.next_atom == turn_in


def test_inventory_or_future_chat_evidence_only_satisfies_its_requirement() -> None:
    runner = QuestScenarioRunner()
    compiled = compile_quest_plan(q304())
    assert compiled.plan is not None
    blood, _, _, _, turn_in = leaves(compiled.plan)
    future_turn_in = evidence_for(compiled.plan, turn_in)

    result = runner.run(q304(), evidence=(future_turn_in,), capabilities=CAPABILITIES)

    assert result.evaluation.status is EvaluationStatus.UNSAFE
    assert result.evaluation.next_atom is None
    assert result.evaluation.reason == "out_of_order_evidence"
    states = {item.requirement_id: item.status for item in result.evaluation.requirements}
    assert states[turn_in.requirement_id] is EvaluationStatus.SATISFIED
    assert states[blood.requirement_id] is EvaluationStatus.ACTIONABLE


def test_real_q304_blood_chat_claim_cannot_complete_composite_root() -> None:
    runner = QuestScenarioRunner()
    compiled = compile_quest_plan(q304())
    assert compiled.plan is not None
    blood, moss, _, _, turn_in = leaves(compiled.plan)
    # A message about blood is scoped to the blood requirement.  Even when it
    # proves that atom, the composite root advances only to the first area item.
    blood_claim = evidence_for(compiled.plan, blood)
    result = runner.run(q304(), evidence=(blood_claim,), capabilities=CAPABILITIES)
    assert result.evaluation.next_atom == moss
    assert result.evaluation.next_atom != turn_in
    assert result.evaluation.status is EvaluationStatus.ACTIONABLE


def test_q304_turn_in_requires_newer_causal_revision_than_all_prerequisites() -> None:
    runner = QuestScenarioRunner()
    compiled = compile_quest_plan(q304())
    assert compiled.plan is not None
    blood, moss, mushroom, leaves_item, turn_in = leaves(compiled.plan)
    prerequisites = (
        evidence_for(compiled.plan, blood, revision=7),
        evidence_for(compiled.plan, moss, revision=8),
        evidence_for(compiled.plan, mushroom, revision=8),
        evidence_for(compiled.plan, leaves_item, revision=8),
    )
    noncausal = runner.run(
        q304(), evidence=(*prerequisites, evidence_for(compiled.plan, turn_in, revision=8)),
        capabilities=CAPABILITIES,
    )
    assert noncausal.evaluation.status is EvaluationStatus.UNSAFE
    assert noncausal.evaluation.reason == "noncausal_sequence_evidence"

    causal = runner.run(
        q304(), evidence=(*prerequisites, evidence_for(compiled.plan, turn_in, revision=9)),
        capabilities=CAPABILITIES,
    )
    assert causal.evaluation.status is EvaluationStatus.SATISFIED
    assert causal.evaluation.next_atom is None


def test_q31_preserves_order_and_blocks_purchase_without_capability() -> None:
    runner = QuestScenarioRunner()
    compiled = compile_quest_plan(q31())
    assert compiled.plan is not None
    wings, purchase, npc, turn_in = leaves(compiled.plan)
    assert isinstance(wings, Acquire) and isinstance(wings.source, CombatDrop)
    assert isinstance(purchase, Acquire) and isinstance(purchase.source, Purchase)
    assert isinstance(npc, InteractNpc) and isinstance(turn_in, TurnIn)

    result = runner.run(
        q31(), evidence=(evidence_for(compiled.plan, wings),),
        capabilities=CAPABILITIES,
    )

    assert result.evaluation.status is EvaluationStatus.BLOCKED
    assert result.evaluation.next_atom is None
    assert result.evaluation.reason == "blocked_capability"
    assert next(item for item in result.evaluation.requirements if item.requirement_id == purchase.requirement_id).reason == "blocked_capability"
    assert result.mutation_intents == ()


def test_q360_ordered_skeleton_does_not_guess_internal_request() -> None:
    runner = QuestScenarioRunner()
    compiled = compile_quest_plan(q360())
    assert compiled.status is QuestCompileStatus.READY and compiled.plan is not None
    assert "ordered_internal_request_unknown" in compiled.diagnostics
    first_visit, internal_npc, return_visit, turn_in = leaves(compiled.plan)
    assert isinstance(first_visit, Visit) and isinstance(internal_npc, InteractNpc)
    assert isinstance(return_visit, Visit) and isinstance(turn_in, TurnIn)

    result = runner.run(
        q360(), evidence=(evidence_for(compiled.plan, first_visit),),
        capabilities={"visit", "turn_in"},
    )
    assert result.evaluation.status is EvaluationStatus.UNKNOWN
    assert result.evaluation.next_atom is None
    assert result.evaluation.reason == "opaque_requirement_unknown"
    assert result.mutation_intents == ()

    claimed = runner.run(
        q360(),
        evidence=(
            evidence_for(compiled.plan, first_visit, revision=7),
            evidence_for(compiled.plan, internal_npc, revision=8),
        ),
        capabilities={"visit", "interact_npc", "turn_in"},
    )
    assert claimed.evaluation.status is EvaluationStatus.UNSAFE
    assert claimed.evaluation.next_atom is None


@pytest.mark.parametrize("bad", [
    entry("x", "Malformed", " ", navigation=()),
    entry("x", "Partial", "Найдите мох в неизвестном месте.", kind="collect", navigation=()),
    entry("360", "Зов Лихих земель", "Отправляйтесь к стражу Всебою либо к Туру.", navigation=(("Застава", "A"),)),
])
def test_malformed_partial_or_ambiguous_input_has_no_plan_or_actions(bad) -> None:
    result = QuestScenarioRunner().run(bad, capabilities=CAPABILITIES)
    assert result.compiled.status in {QuestCompileStatus.UNSAFE, QuestCompileStatus.UNKNOWN}
    assert result.compiled.plan is None
    assert result.evaluation is None
    assert result.mutation_intents == ()


def test_partial_area_parse_never_compiles_before_whole_objective_validation() -> None:
    partial = entry(
        "901", "Partial area",
        "Найдите в Пристанище трёх ветров Светящийся мох, затем сделайте неизвестное.",
        kind="collect",
        navigation=(("Пристанище трёх ветров", "Пристанище трёх ветров"),),
    )
    result = QuestScenarioRunner().run(partial, capabilities=CAPABILITIES)
    assert result.compiled.status in {QuestCompileStatus.UNSAFE, QuestCompileStatus.UNKNOWN}
    assert result.compiled.plan is None
    assert result.mutation_intents == ()


def test_registry_rejects_forged_identity_with_unrelated_source_contract() -> None:
    forged = entry(
        "304", "Цветочная болезнь", "Поговорите с неизвестным NPC.",
        kind="dialogue", navigation=(("Город", "Город"),),
    )
    result = QuestScenarioRunner().run(forged, capabilities=CAPABILITIES)
    assert result.compiled.status is QuestCompileStatus.UNSAFE
    assert result.compiled.plan is None
    assert result.mutation_intents == ()


def test_cursor_round_trip_preserves_next_atom_and_has_no_mutation_state() -> None:
    runner = QuestScenarioRunner()
    compiled = compile_quest_plan(q304())
    assert compiled.plan is not None
    blood = leaves(compiled.plan)[0]
    result = runner.run(q304(), evidence=(evidence_for(compiled.plan, blood),), capabilities=CAPABILITIES)
    cursor = runner.cursor(result)
    restored = EvaluationCursor.from_json(cursor.to_json())

    assert restored == cursor
    assert restored.next_requirement_id == result.evaluation.next_atom.requirement_id
    assert set(restored.to_dict()) == {
        "schema", "capability_version", "plan_fingerprint", "next_requirement_id",
        "evidence_fingerprints", "evidence_revision", "staged_requirement_id",
        "dispatch_state",
    }
    resumed = runner.run(
        q304(), evidence=(evidence_for(compiled.plan, blood),),
        capabilities=CAPABILITIES, cursor=restored,
    )
    assert resumed.evaluation.next_atom == result.evaluation.next_atom
    assert resumed.evaluation.reissue_allowed is False
    missing_evidence = runner.run(q304(), capabilities=CAPABILITIES, cursor=restored)
    assert missing_evidence.evaluation.status is EvaluationStatus.UNSAFE
    assert missing_evidence.evaluation.next_atom is None
    assert missing_evidence.evaluation.reissue_allowed is False
    assert result.mutation_intents == ()


def test_evidence_round_trip_and_ids_are_stable() -> None:
    compiled = compile_quest_plan(q304())
    repeated = compile_quest_plan(q304())
    assert compiled.plan is not None and repeated.plan is not None
    first = leaves(compiled.plan)[0]
    restored = EvidenceEnvelope.from_json(evidence_for(compiled.plan, first).to_canonical_json())
    assert restored == evidence_for(compiled.plan, first)
    assert compiled.plan.fingerprint == repeated.plan.fingerprint
    assert [item.requirement_id for item in leaves(compiled.plan)] == [
        item.requirement_id for item in leaves(repeated.plan)
    ]


def test_any_of_is_explicitly_fail_closed_and_never_selects_branch() -> None:
    plan = QuestPlan(
        "alternative", "Ambiguous",
        QuestGraph(AnyOf((Visit("Лес"), Visit("Поле")), AlternativePolicy.FAIL_CLOSED)),
    )
    from src.antibot_cv.automation.quest_plan_evaluator import evaluate_quest_plan
    result = evaluate_quest_plan(plan, capabilities={"visit"})
    assert result.status is EvaluationStatus.UNKNOWN
    assert result.next_atom is None


def test_duplicate_semantic_requirements_are_rejected_at_graph_boundary() -> None:
    with pytest.raises(ValueError, match="duplicate leaf requirement IDs"):
        QuestGraph(Sequence((Visit("Лес"), Visit("Лес"))))


def test_same_input_is_deterministic_and_similar_claim_becomes_ambiguous() -> None:
    runner = QuestScenarioRunner()
    compiled = compile_quest_plan(q304())
    assert compiled.plan is not None
    blood = leaves(compiled.plan)[0]
    exact = evidence_for(compiled.plan, blood, snapshot_id="inventory-1")
    repeat = runner.run(q304(), evidence=(exact,), capabilities=CAPABILITIES)
    assert repeat == runner.run(q304(), evidence=(exact,), capabilities=CAPABILITIES)
    assert compiled.plan.fingerprint == compile_quest_plan(q304()).plan.fingerprint

    similar = replace(
        evidence_for(compiled.plan, blood, snapshot_id="inventory-2"),
        fact=replace(exact.fact, subject="Кровь Свирепого кабана"),
    )
    ambiguous = runner.run(q304(), evidence=(exact, similar), capabilities=CAPABILITIES)
    assert ambiguous.evaluation.status is EvaluationStatus.UNSAFE
    assert any(item.reason == "ambiguous_evidence" for item in ambiguous.evaluation.requirements)
    assert ambiguous.mutation_intents == ()


def test_stale_or_foreign_actor_evidence_is_unsafe() -> None:
    compiled = compile_quest_plan(q280())
    assert compiled.plan is not None
    acquire = leaves(compiled.plan)[0]
    stale = replace(evidence_for(compiled.plan, acquire), generated_at=1.0)
    foreign = replace(evidence_for(compiled.plan, acquire), client_id="client-b")
    runner = QuestScenarioRunner()

    for envelope in (stale, foreign):
        result = runner.run(q280(), evidence=(envelope,), capabilities=CAPABILITIES, context=DEFAULT_CONTEXT)
        assert result.evaluation.status is EvaluationStatus.UNSAFE
        assert result.evaluation.next_atom is None
        assert result.mutation_intents == ()
