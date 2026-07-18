from __future__ import annotations

from src.antibot_cv.automation.quest_compiler import compile_quest_plan
from src.antibot_cv.automation.quest_plan_evaluator import EvaluationStatus
from src.antibot_cv.automation.quest_plan_model import Acquire, iter_requirements
from src.antibot_cv.automation.quest_shadow_comparator import (
    ShadowComparisonStatus,
    compare_shadow_decision,
)

from .fixtures import entry, q280, q304
from .runner import DEFAULT_CONTEXT, evidence_for


def test_q280_existing_item_matches_legacy_turn_in() -> None:
    active = q280()
    compiled = compile_quest_plan(active)
    assert compiled.plan is not None
    acquire = tuple(iter_requirements(compiled.plan.graph.root))[0]
    result = compare_shadow_decision(
        active,
        {
            "quest_id": "280",
            "status": "actionable",
            "atom_kind": "turn_in",
            "target": "Разбойник Аскорд",
        },
        evidence=(evidence_for(compiled.plan, acquire),),
        capabilities=("area_object", "turn_in"),
        context=DEFAULT_CONTEXT,
    )

    assert result.status is ShadowComparisonStatus.MATCH
    assert result.code == "decision_match"
    assert result.semantic is not None
    assert result.semantic.status is EvaluationStatus.ACTIONABLE


def test_q304_blood_complete_exposes_intentional_legacy_divergence() -> None:
    active = q304()
    compiled = compile_quest_plan(active)
    assert compiled.plan is not None
    requirements = tuple(iter_requirements(compiled.plan.graph.root))
    blood = requirements[0]
    moss = requirements[1]
    assert isinstance(blood, Acquire) and isinstance(moss, Acquire)

    result = compare_shadow_decision(
        active,
        {"quest_id": "304", "status": "unsafe", "reason": "legacy_terminal_ambiguity"},
        evidence=(evidence_for(compiled.plan, blood),),
        capabilities=("combat_drop", "area_object", "turn_in"),
        context=DEFAULT_CONTEXT,
    )

    assert result.status is ShadowComparisonStatus.DIVERGED
    assert result.code == "status_diverged"
    assert result.semantic is not None
    assert result.semantic.atom_kind == "acquire"
    assert result.semantic.target == "Светящийся мох"
    assert result.semantic.requirement_id == moss.requirement_id


def test_malformed_objective_is_incomparable() -> None:
    malformed = entry("991", "Сломанный", " ")
    result = compare_shadow_decision(
        malformed,
        {"quest_id": "991", "status": "unknown", "reason": "legacy_parse_failed"},
    )

    assert result.status is ShadowComparisonStatus.INCOMPARABLE
    assert result.code == "semantic_compile_unsafe"
    assert result.semantic is None


def test_same_input_has_stable_code_and_fingerprint() -> None:
    active = q304()
    compiled = compile_quest_plan(active)
    assert compiled.plan is not None
    blood = tuple(iter_requirements(compiled.plan.graph.root))[0]
    kwargs = {
        "evidence": (evidence_for(compiled.plan, blood),),
        "capabilities": ("combat_drop", "area_object", "turn_in"),
        "context": DEFAULT_CONTEXT,
    }
    legacy = {"quest_id": "304", "status": "unsafe", "reason": "legacy_terminal_ambiguity"}

    first = compare_shadow_decision(active, legacy, **kwargs)
    second = compare_shadow_decision(active, legacy, **kwargs)

    assert first == second
    assert first.code == second.code
    assert first.fingerprint == second.fingerprint
