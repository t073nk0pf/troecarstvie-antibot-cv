from __future__ import annotations

from dataclasses import replace

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_compiler import compile_quest_plan
from src.antibot_cv.automation.quest_evidence import (
    EvidenceEnvelope,
    EvidenceSourceKind,
    FactKind,
    SatisfiedFact,
)
from src.antibot_cv.automation.quest_plan_evaluator import EvaluationContext, EvaluationStatus
from src.antibot_cv.automation.quest_plan_model import Acquire, TurnIn, iter_requirements
from src.antibot_cv.automation.quest_semantic_runtime import (
    QuestSemanticDisposition,
    evaluate_semantic_quest_runtime,
)
from src.antibot_cv.automation.quest_shadow_legacy_adapter import (
    LegacyDecisionEnvelope,
    LegacyDecisionStatus,
)


CONTEXT = EvaluationContext("client", "profile", "tab", "baseline", 1, now=100.0)


def _entry(quest_id: str) -> ActiveQuestEntry:
    if quest_id == "280":
        title = "Фамильная ступка"
        objective = "Вернитесь к разбойнику Аскорду в Земли Пращуров."
        kind = "dialogue"
        navigation = ({"text": "Земли Пращуров", "target": "Земли Пращуров"},)
    elif quest_id == "304":
        title = "Цветочная болезнь"
        objective = "Убивая Непобедимых кабанов, получите 5 пузырьков крови, также найдите в Пристанище трёх ветров Светящийся мох, в Длани Рода Пятнистый гриб и 5 свежих листьев кустарника на Просторах безмолвия. Собрав необходимое, возвращайтесь к колдунье Вилене."
        kind = "collect"
        navigation = (
            {"text": "Непобедимый кабан", "target": "Непобедимый кабан [5]"},
            {"text": "Пристанище трёх ветров", "target": "Пристанище трёх ветров"},
            {"text": "Длани Рода", "target": "Длань Рода"},
            {"text": "Просторах безмолвия", "target": "Просторы безмолвия"},
        )
    else:
        title, objective, kind, navigation = "Other", "Do something", "unknown", ()
    data = {
        "status": "active", "id": quest_id, "title": title,
        "objective": objective, "objectiveKind": kind, "navigation": navigation,
    }
    return ActiveQuestEntry(quest_id, title, data)


def _legacy(quest_id: str) -> LegacyDecisionEnvelope:
    return LegacyDecisionEnvelope(quest_id, LegacyDecisionStatus.UNKNOWN, reason="legacy")


def _inventory(entry: ActiveQuestEntry, requirement_index: int, observed: int) -> EvidenceEnvelope:
    plan = compile_quest_plan(entry).plan
    assert plan is not None
    requirement = tuple(iter_requirements(plan.graph.root))[requirement_index]
    assert isinstance(requirement, Acquire)
    return EvidenceEnvelope(
        quest_id=entry.id, quest_title=entry.title,
        plan_fingerprint=plan.fingerprint, step_fingerprint=requirement.step_id,
        requirement_id=requirement.requirement_id,
        source_kind=EvidenceSourceKind.INVENTORY, snapshot_id="inventory-1", revision=1,
        client_id="client", profile_id="profile", tab_id="tab", generated_at=100.0,
        freshness_seconds=10.0, complete=True, causal_baseline="baseline",
        fact=SatisfiedFact(FactKind.COUNT_AT_LEAST, requirement.item, observed, requirement.count),
        payload={},
    )


def test_q280_inventory_satisfied_advances_authoritatively_to_turn_in() -> None:
    entry = _entry("280")
    outcome = evaluate_semantic_quest_runtime(
        "q280_q304", entry, (_inventory(entry, 0, 1),), CONTEXT,
        ("area_object", "turn_in"), _legacy("280"),
    )

    assert outcome.disposition is QuestSemanticDisposition.AUTHORITATIVE
    assert outcome.status is EvaluationStatus.ACTIONABLE
    assert isinstance(outcome.next_atom, TurnIn)
    assert outcome.next_atom.npc == "Разбойник Аскорд"
    assert outcome.legacy_fallback_allowed is False


def test_q304_blood_satisfied_advances_authoritatively_to_moss() -> None:
    entry = _entry("304")
    outcome = evaluate_semantic_quest_runtime(
        "q280_q304", entry, (_inventory(entry, 0, 5),), CONTEXT,
        ("combat_drop", "area_object", "turn_in"), _legacy("304"),
    )

    assert outcome.status is EvaluationStatus.ACTIONABLE
    assert isinstance(outcome.next_atom, Acquire)
    assert outcome.next_atom.item == "Светящийся мох"


def test_allowlisted_malformed_entry_fails_closed_without_atom_or_fallback() -> None:
    entry = _entry("280")
    malformed = ActiveQuestEntry(entry.id, entry.title, {**entry.data, "objective": ""})
    outcome = evaluate_semantic_quest_runtime(
        "q280_q304", malformed, (), CONTEXT, (), _legacy("280")
    )

    assert outcome.status is EvaluationStatus.UNSAFE
    assert outcome.next_atom is None
    assert outcome.legacy_fallback_allowed is False


def test_allowlisted_foreign_evidence_fails_closed_without_atom_or_fallback() -> None:
    entry = _entry("280")
    foreign = replace(_inventory(entry, 0, 1), quest_id="999")
    outcome = evaluate_semantic_quest_runtime(
        "q280_q304", entry, (foreign,), CONTEXT,
        ("area_object", "turn_in"), _legacy("280"),
    )

    assert outcome.status is EvaluationStatus.UNSAFE
    assert outcome.next_atom is None
    assert outcome.legacy_fallback_allowed is False


def test_nonallowlisted_quest_yields_to_legacy() -> None:
    outcome = evaluate_semantic_quest_runtime(
        "q280_q304", _entry("999"), (), CONTEXT, (), _legacy("999")
    )

    assert outcome.disposition is QuestSemanticDisposition.NOT_HANDLED
    assert outcome.legacy_fallback_allowed is True
    assert outcome.next_atom is None


def test_shadow_mode_is_action_free_and_deterministic() -> None:
    entry = _entry("280")
    arguments = ("shadow", entry, (), CONTEXT, ("area_object",), _legacy("280"))

    first = evaluate_semantic_quest_runtime(*arguments)
    second = evaluate_semantic_quest_runtime(*arguments)

    assert first.disposition is QuestSemanticDisposition.SHADOW
    assert first.next_atom is None
    assert first.comparison is not None
    assert second.comparison is not None
    assert first.comparison.fingerprint == second.comparison.fingerprint
