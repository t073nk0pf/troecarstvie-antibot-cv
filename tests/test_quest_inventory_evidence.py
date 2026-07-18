from __future__ import annotations

import pytest

from src.antibot_cv.automation.quest_inventory_evidence import (
    QuestInventoryEvidenceStatus,
    build_quest_inventory_evidence,
    merge_authoritative_inventory_evidence,
)
from src.antibot_cv.automation.quest_inventory_guard import QuestInventoryGuardResult
from src.antibot_cv.automation.gathering_activity_runtime import GatheringRequirement
from src.antibot_cv.automation.quest_plan_model import (
    Acquire,
    AllOf,
    AreaObject,
    CombatDrop,
    QuestGraph,
    QuestPlan,
    Sequence,
    TurnIn,
)


def _snapshot(*items: tuple[str, int], **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "ok": True,
        "category": "quest",
        "categoryConfirmed": True,
        "truncated": False,
        "snapshotId": "inventory-7",
        "generatedAt": 100.0,
        "revision": 7,
        "clientId": "client",
        "profileId": "profile",
        "tabId": "tab",
        "causalBaseline": "baseline",
        "items": [{"artAltTitle": name, "count": count} for name, count in items],
    }
    value.update(overrides)
    return value


def _guard(*collected: tuple[str, int], confirmed: bool = True) -> QuestInventoryGuardResult:
    requirements = tuple(GatheringRequirement(name, count) for name, count in collected)
    return QuestInventoryGuardResult(
        confirmed, bool(collected), requirements, collected,
        "quest_items_complete" if collected else "quest_items_missing",
    )


def _build(plan: QuestPlan, snapshot: object, guard: QuestInventoryGuardResult):
    return build_quest_inventory_evidence(
        plan, snapshot, guard,
        client_id="client", profile_id="profile", tab_id="tab",
        causal_baseline="baseline", now=100.0,
    )


def test_q280_item_emits_only_exact_acquire_requirement() -> None:
    acquire = Acquire("Фамильная ступка", source=CombatDrop("Свирепый кентавр"))
    turn_in = TurnIn("Разбойник Аскорд")
    plan = QuestPlan("280", "Фамильная ступка", QuestGraph(Sequence((acquire, turn_in))))

    result = _build(
        plan, _snapshot(("Фамильная ступка", 1)), _guard(("Фамильная ступка", 1)),
    )

    assert result.status is QuestInventoryEvidenceStatus.READY
    assert [item.requirement_id for item in result.envelopes] == [acquire.requirement_id]
    assert result.envelopes[0].step_fingerprint == acquire.step_id
    assert result.envelopes[0].fact.observed == 1


def test_q304_blood_never_satisfies_area_items_or_composite() -> None:
    blood = Acquire("Пузырёк крови", 5, CombatDrop("Непобедимый кабан"))
    moss = Acquire("Светящийся мох", source=AreaObject("Светящийся мох"))
    mushroom = Acquire("Пятнистый гриб", source=AreaObject("Пятнистый гриб"))
    plan = QuestPlan(
        "304", "Цветочная болезнь",
        QuestGraph(Sequence((AllOf((blood, moss, mushroom)), TurnIn("Вилена")))),
    )

    result = _build(
        plan,
        _snapshot(("Кровь Непобедимого кабана", 5)),
        _guard(("Кровь Непобедимого кабана", 5)),
    )

    assert result.status is QuestInventoryEvidenceStatus.READY
    assert tuple(item.requirement_id for item in result.envelopes) == (blood.requirement_id,)
    assert result.envelopes[0].fact.subject == "Пузырёк крови"


def test_unmet_count_emits_no_evidence() -> None:
    acquire = Acquire("Фамильная ступка", 2, CombatDrop("Кентавр"))
    plan = QuestPlan("280", "Ступка", QuestGraph(acquire))
    result = _build(plan, _snapshot(("Фамильная ступка", 1)), _guard(("Фамильная ступка", 1)))
    assert result.status is QuestInventoryEvidenceStatus.READY
    assert result.envelopes == ()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"ok": False}, "inventory_snapshot_not_ok"),
        ({"categoryConfirmed": False}, "quest_inventory_category_unconfirmed"),
        ({"truncated": True}, "quest_inventory_partial"),
        ({"snapshotId": ""}, "inventory_provenance_missing"),
        ({"generatedAt": None}, "inventory_provenance_missing"),
        ({"revision": None}, "inventory_revision_invalid"),
        ({"clientId": "foreign"}, "inventory_actor_identity_mismatch"),
        ({"causalBaseline": "foreign"}, "inventory_causal_baseline_mismatch"),
    ],
)
def test_partial_or_foreign_snapshot_is_unsafe(overrides: dict[str, object], reason: str) -> None:
    acquire = Acquire("Ступка", source=CombatDrop("Кентавр"))
    plan = QuestPlan("280", "Ступка", QuestGraph(acquire))
    result = _build(plan, _snapshot(("Ступка", 1), **overrides), _guard(("Ступка", 1)))
    assert result.status is QuestInventoryEvidenceStatus.UNSAFE
    assert result.reason == reason
    assert result.envelopes == ()


def test_similar_second_inventory_item_makes_binding_ambiguous() -> None:
    blood = Acquire("Пузырёк крови", 5, CombatDrop("Непобедимый кабан"))
    plan = QuestPlan("304", "Цветочная болезнь", QuestGraph(blood))
    result = _build(
        plan,
        _snapshot(("Кровь Непобедимого кабана", 5), ("Кровь Свирепого кабана", 5)),
        _guard(("Кровь Непобедимого кабана", 5)),
    )
    assert result.status is QuestInventoryEvidenceStatus.UNSAFE
    assert result.reason == "quest_inventory_item_binding_ambiguous"
    assert result.envelopes == ()


def test_guard_must_be_confirmed_and_match_same_snapshot_counts() -> None:
    acquire = Acquire("Ступка", source=CombatDrop("Кентавр"))
    plan = QuestPlan("280", "Ступка", QuestGraph(acquire))
    unconfirmed = _build(plan, _snapshot(("Ступка", 1)), _guard(("Ступка", 1), confirmed=False))
    mismatched = _build(plan, _snapshot(("Ступка", 1)), _guard(("Ступка", 2)))
    assert unconfirmed.status is QuestInventoryEvidenceStatus.UNSAFE
    assert mismatched.status is QuestInventoryEvidenceStatus.UNSAFE
    assert unconfirmed.envelopes == mismatched.envelopes == ()


def test_stale_snapshot_is_unsafe() -> None:
    acquire = Acquire("Ступка", source=CombatDrop("Кентавр"))
    plan = QuestPlan("280", "Ступка", QuestGraph(acquire))
    result = build_quest_inventory_evidence(
        plan, _snapshot(("Ступка", 1)), _guard(("Ступка", 1)),
        client_id="client", profile_id="profile", tab_id="tab",
        causal_baseline="baseline", freshness_seconds=10.0, now=111.0,
    )
    assert result.status is QuestInventoryEvidenceStatus.UNSAFE
    assert result.envelopes == ()


def test_authoritative_merge_preserves_first_satisfaction_and_retracts_absence() -> None:
    blood = Acquire("Пузырёк крови", 5, CombatDrop("Непобедимый кабан"))
    moss = Acquire("Светящийся мох", source=AreaObject("Светящийся мох"))
    plan = QuestPlan("304", "Цветочная болезнь", QuestGraph(Sequence((blood, moss))))
    first = _build(
        plan, _snapshot(("Кровь Непобедимого кабана", 5)),
        _guard(("Кровь Непобедимого кабана", 5)),
    ).envelopes
    second = build_quest_inventory_evidence(
        plan,
        _snapshot(
            ("Кровь Непобедимого кабана", 5), ("Светящийся мох", 1),
            revision=8, snapshotId="inventory-8", generatedAt=101.0,
        ),
        _guard(("Кровь Непобедимого кабана", 5), ("Светящийся мох", 1)),
        client_id="client", profile_id="profile", tab_id="tab",
        causal_baseline="baseline", now=101.0,
    ).envelopes

    merged = merge_authoritative_inventory_evidence(first, second, plan=plan)
    by_id = {item.requirement_id: item for item in merged}
    assert by_id[blood.requirement_id].revision == 7
    assert by_id[moss.requirement_id].revision == 8

    absent = build_quest_inventory_evidence(
        plan,
        _snapshot(("Светящийся мох", 1), revision=9, snapshotId="inventory-9", generatedAt=102.0),
        _guard(("Светящийся мох", 1)),
        client_id="client", profile_id="profile", tab_id="tab",
        causal_baseline="baseline", now=102.0,
    ).envelopes
    retracted = merge_authoritative_inventory_evidence(merged, absent, plan=plan)
    assert tuple(item.requirement_id for item in retracted) == (moss.requirement_id,)
