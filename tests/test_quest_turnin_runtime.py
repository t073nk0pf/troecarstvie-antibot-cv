from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_chain_runtime import QuestChainLease
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint
from src.antibot_cv.automation.quest_turnin_runtime import (
    QuestTurnInError,
    QuestTurnInIntent,
    QuestTurnInPhase,
    QuestTurnInRuntime,
)


def completed_entry(*, complete: bool = True) -> ActiveQuestEntry:
    return ActiveQuestEntry(
        "246",
        "Охота на волка",
        MappingProxyType(
            {
                "id": "246",
                "title": "Охота на волка",
                "status": "active",
                "objective": "Убить Волка 5/5",
                "navigation": (
                    MappingProxyType({"text": "Тёмный лес", "target": "Тёмный лес"}),
                ),
                "progress": MappingProxyType(
                    {"current": 5, "required": 5, "complete": complete}
                ),
            }
        ),
    )


def identities(entry: ActiveQuestEntry) -> tuple[QuestChainLease, QuestRef]:
    fingerprint, reason = quest_step_fingerprint(entry)
    assert fingerprint is not None, reason
    return (
        QuestChainLease(entry.id, entry.title, 3, fingerprint, (fingerprint,)),
        QuestRef(entry.id, entry.title, location="Южная застава", giver_names=("Воевода Ратмир",)),
    )


def area(*items: dict[str, object]) -> dict[str, object]:
    bound_items = [{**item, "routeRef": item.get("routeRef", str(398 + index))} for index, item in enumerate(items)]
    return {
        "ok": True,
        "truncated": False,
        "snapshotId": "area-npcs-turn-in-1",
        "location": {"id": "77", "name": "Южная застава"},
        "items": bound_items,
    }


def dialog(**updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "truncated": False,
        "identityMatches": True,
        "expectedName": "Воевода Ратмир",
        "snapshotId": "npc-dialog-turn-in-1",
        "questActions": [],
        "dialogActions": [],
        "doneActions": [],
    }
    result.update(updates)
    for key in ("questActions", "dialogActions", "doneActions"):
        for action in result.get(key) or []:
            action.setdefault("npcId", "12")
    return result


def begun() -> QuestTurnInRuntime:
    entry = completed_entry()
    lease, quest_ref = identities(entry)
    runtime = QuestTurnInRuntime()
    runtime.begin(entry, lease=lease, quest_ref=quest_ref, already_at_location=True)
    opened = runtime.decide_area_npc(
        area({"dataId": "12", "name": "Воевода Ратмир", "actionable": True})
    )
    runtime.acknowledge(opened)
    return runtime


def test_completed_identity_routes_to_exact_snapshot_bound_npc() -> None:
    entry = completed_entry()
    lease, quest_ref = identities(entry)
    runtime = QuestTurnInRuntime()

    assert runtime.begin(entry, lease=lease, quest_ref=quest_ref, already_at_location=False).phase is QuestTurnInPhase.ROUTE
    assert runtime.mark_route_arrived().phase is QuestTurnInPhase.NPC_LOOKUP
    decision = runtime.decide_area_npc(
        area({"dataId": "12", "name": "Воевода Ратмир", "actionable": True})
    )

    assert decision.intent is QuestTurnInIntent.OPEN_NPC
    assert decision.action_type == "open_exact_npc"
    assert dict(decision.action_metadata) == {
        "expected_snapshot_id": "area-npcs-turn-in-1",
        "expected_location_id": "77",
        "npc_id": "12",
        "expected_route_ref": "398",
        "expected_name": "Воевода Ратмир",
        "expected_dialog_name": "Воевода Ратмир",
        "quest_id": "246",
    }


def test_terminal_collection_evidence_can_begin_when_catalogue_has_no_progress() -> None:
    entry = completed_entry(complete=False)
    lease, quest_ref = identities(entry)
    runtime = QuestTurnInRuntime()

    with pytest.raises(QuestTurnInError, match="not confirmed complete"):
        runtime.begin(entry, lease=lease, quest_ref=quest_ref, already_at_location=False)

    assert runtime.begin(
        entry,
        lease=lease,
        quest_ref=quest_ref,
        already_at_location=False,
        terminal_collection_confirmed=True,
    ).phase is QuestTurnInPhase.ROUTE


def test_turn_in_proxy_click_keeps_quest_giver_as_dialog_identity() -> None:
    entry = completed_entry()
    lease, quest_ref = identities(entry)
    runtime = QuestTurnInRuntime()
    runtime.begin(entry, lease=lease, quest_ref=quest_ref, already_at_location=True)

    decision = runtime.decide_area_npc(
        area({"dataId": "0", "name": "Дом Ратмира", "actionable": True})
    )

    assert decision.action_metadata["npc_id"] == "0"
    assert decision.action_metadata["expected_name"] == "Дом Ратмира"
    assert decision.action_metadata["expected_dialog_name"] == "Воевода Ратмир"


def test_vilena_proxy_keeps_endpoint_and_resulting_dialogue_identities_separate() -> None:
    entry = ActiveQuestEntry("304", "Цветочная болезнь", MappingProxyType({
        "id": "304", "title": "Цветочная болезнь", "status": "active",
        "objective": "Вернитесь к колдунье Вилене.",
        "navigation": (MappingProxyType({
            "text": "Пристанище трёх ветров",
            "target": "Пристанище трёх ветров",
        }),),
        "progress": MappingProxyType({"current": 1, "required": 1, "complete": True}),
    }))
    fingerprint, reason = quest_step_fingerprint(entry)
    assert fingerprint is not None, reason
    lease = QuestChainLease("304", entry.title, 3, fingerprint, (fingerprint,))
    quest_ref = QuestRef(
        "304",
        "Цветочная болезнь",
        location="Длань Рода",
        giver_names=("Колдунья Вилена",),
    )
    runtime = QuestTurnInRuntime()
    pending = runtime.begin(
        entry, lease=lease, quest_ref=quest_ref, already_at_location=True,
        resulting_dialog_npc_id="110",
        expected_resulting_dialog_name="Колдунья Вилена",
        expected_location_id="128",
        expected_endpoint_npc_id="4",
        expected_endpoint_npc_name="Палатка Вилены",
    )
    snapshot = area({"dataId": "4", "name": "Палатка Вилены", "actionable": True})
    snapshot["location"] = {"id": "128", "name": "Длань Рода"}

    opened_proxy = runtime.decide_area_npc(snapshot)
    pending = runtime.acknowledge(opened_proxy)

    assert pending.endpoint_npc_id == "4"
    assert pending.endpoint_npc_name == "Палатка Вилены"
    assert pending.resulting_dialog_npc_id == "110"
    assert pending.resulting_dialog_name == "Колдунья Вилена"
    opened_quest = runtime.decide_dialog(dialog(
        expectedName="Колдунья Вилена",
        questActions=[{
            "questId": "304", "title": "Цветочная болезнь", "action": "open",
            "npcId": "4", "npcInstanceId": "110",
            "visible": True, "disabled": False,
        }],
    ))
    assert opened_quest.action_metadata["npc_id"] == "4"
    assert opened_quest.action_metadata["expected_npc_instance_id"] == "110"
    assert opened_quest.action_metadata["expected_name"] == "Колдунья Вилена"
    runtime.acknowledge(opened_quest)

    answered = runtime.decide_dialog(dialog(
        expectedName="Колдунья Вилена",
        dialogActions=[{
            "questId": "304", "npcId": "4", "npcInstanceId": "110",
            "action": "answer", "ref": "81",
            "text": "Вот цветы.", "visible": True, "disabled": False,
        }],
    ))
    assert answered.action_metadata["expected_name"] == "Колдунья Вилена"
    runtime.acknowledge(answered)

    completed = runtime.decide_dialog(dialog(
        expectedName="Колдунья Вилена",
        doneActions=[{
            "questId": "304", "npcId": "4", "npcInstanceId": "110",
            "action": "done", "pointId": "9",
            "text": "Получить награду", "visible": True, "disabled": False,
        }],
    ))
    assert completed.action_metadata["expected_name"] == "Колдунья Вилена"
    assert runtime.acknowledge(completed, active_catalog_revision=7).phase is QuestTurnInPhase.VERIFY_ACTIVE
    assert runtime.verify_terminal((), catalog_complete=True, catalog_revision=8) == "304"


@pytest.mark.parametrize(
    "missing",
    [
        "expected_location_id",
        "expected_endpoint_npc_id",
        "expected_endpoint_npc_name",
        "expected_resulting_dialog_name",
    ],
)
def test_canonical_turn_in_target_rejects_every_partial_identity(missing: str) -> None:
    entry = completed_entry()
    lease, quest_ref = identities(entry)
    identity = {
        "expected_location_id": "128",
        "expected_endpoint_npc_id": "4",
        "expected_endpoint_npc_name": "Палатка Вилены",
        "resulting_dialog_npc_id": "110",
        "expected_resulting_dialog_name": "Колдунья Вилена",
    }
    identity[missing] = None

    with pytest.raises(QuestTurnInError) as exc:
        QuestTurnInRuntime().begin(
            entry,
            lease=lease,
            quest_ref=quest_ref,
            already_at_location=True,
            **identity,
        )

    assert exc.value.unsafe_reason == "turn_in_endpoint_identity_invalid"


def test_canonical_turn_in_target_allows_unobserved_optional_dialog_instance() -> None:
    entry = completed_entry()
    lease, quest_ref = identities(entry)

    pending = QuestTurnInRuntime().begin(
        entry,
        lease=lease,
        quest_ref=quest_ref,
        already_at_location=True,
        resulting_dialog_npc_id=None,
        expected_resulting_dialog_name="Десятник Берторд",
        expected_location_id="276",
        expected_endpoint_npc_id="9",
        expected_endpoint_npc_name="Десятник Берторд",
    )

    assert pending.location_id == "276"
    assert pending.endpoint_npc_id == "9"
    assert pending.endpoint_npc_name == "Десятник Берторд"
    assert pending.resulting_dialog_name == "Десятник Берторд"
    assert pending.resulting_dialog_npc_id is None


@pytest.mark.parametrize("invalid", [True, "1" * 41, "１２", "110x", 0, -1])
def test_explicit_invalid_dialog_instance_never_weakens_to_absent(invalid: object) -> None:
    entry = completed_entry()
    lease, quest_ref = identities(entry)

    with pytest.raises(QuestTurnInError) as exc:
        QuestTurnInRuntime().begin(
            entry,
            lease=lease,
            quest_ref=quest_ref,
            already_at_location=True,
            resulting_dialog_npc_id=invalid,  # type: ignore[arg-type]
            expected_resulting_dialog_name="Десятник Берторд",
            expected_location_id="276",
            expected_endpoint_npc_id="9",
            expected_endpoint_npc_name="Десятник Берторд",
        )

    assert exc.value.unsafe_reason == "turn_in_dialog_identity_invalid"


def test_dialogue_snapshot_resulting_identity_is_checked_before_state_mutation() -> None:
    runtime = begun()
    issued = runtime.decide_dialog(dialog(questActions=[{
        "questId": "246", "title": "Охота", "action": "open",
        "visible": True, "disabled": False,
    }]))
    before = runtime.pending

    with pytest.raises(QuestTurnInError) as exc:
        runtime.decide_dialog(dialog(
            expectedName="Палатка Вилены",
            questActions=[{
                "questId": "246", "title": "Охота", "action": "open",
                "visible": True, "disabled": False,
            }],
        ))

    assert exc.value.unsafe_reason == "turn_in_dialog_identity_mismatch"
    assert runtime.pending == before
    assert runtime.acknowledge(issued).quest_opened is True

def test_dialogue_emits_existing_guarded_actions_and_requires_terminal_refresh() -> None:
    runtime = begun()
    opened = runtime.decide_dialog(
        dialog(questActions=[{
            "questId": "246", "title": "Охота на волка", "action": "open",
            "visible": True, "disabled": False,
        }])
    )
    assert opened.intent is QuestTurnInIntent.OPEN_QUEST
    runtime.acknowledge(opened)

    answered = runtime.decide_dialog(
        dialog(snapshotId="npc-dialog-answer", dialogActions=[{
            "questId": "246", "npcId": "12", "action": "answer", "ref": "81",
            "text": "Волки уничтожены.", "visible": True, "disabled": False,
        }])
    )
    assert answered.action_type == "npc_quest_action"
    runtime.acknowledge(answered)

    completed = runtime.decide_dialog(
        dialog(snapshotId="npc-dialog-done", doneActions=[{
            "questId": "246", "npcId": "12", "action": "done", "pointId": "9",
            "text": "Получить награду", "visible": True, "disabled": False,
        }])
    )
    assert completed.intent is QuestTurnInIntent.COMPLETE_QUEST
    assert completed.action_metadata["action"] == "done"
    assert runtime.acknowledge(completed, active_catalog_revision=7).phase is QuestTurnInPhase.VERIFY_ACTIVE

    with pytest.raises(QuestTurnInError, match="complete catalogue"):
        runtime.verify_terminal((), catalog_complete=False, catalog_revision=8)
    with pytest.raises(QuestTurnInError, match="newer active catalogue"):
        runtime.verify_terminal((), catalog_complete=True, catalog_revision=7)
    with pytest.raises(QuestTurnInError, match="newer active catalogue"):
        runtime.verify_terminal((), catalog_complete=True, catalog_revision=6)
    with pytest.raises(QuestTurnInError, match="remains active"):
        runtime.verify_terminal((completed_entry(),), catalog_complete=True, catalog_revision=8)
    assert runtime.verify_terminal((), catalog_complete=True, catalog_revision=8) == "246"
    assert runtime.pending is None


def test_turn_in_infers_directly_opened_dialogue_from_one_safe_answer() -> None:
    runtime = begun()

    answer = runtime.decide_dialog(dialog(dialogActions=[{
        "questId": "246", "npcId": "12", "action": "answer", "ref": "81",
        "text": "Вот добытые трофеи.", "visible": True, "disabled": False,
    }]))

    assert answer.intent is QuestTurnInIntent.ANSWER_DIALOG
    assert answer.action_metadata["inferred_already_open"] is True
    updated = runtime.acknowledge(answer)
    assert updated.quest_opened is True
    assert updated.last_answer_ref == "81"


def test_turn_in_treats_the_just_submitted_answer_as_pending_settle() -> None:
    runtime = begun()
    opened = runtime.decide_dialog(dialog(questActions=[{
        "questId": "246", "title": "Разговор с Ратмиром", "action": "open",
        "visible": True, "disabled": False,
    }]))
    runtime.acknowledge(opened)
    answered = runtime.decide_dialog(dialog(dialogActions=[{
        "questId": "246", "npcId": "12", "action": "answer", "ref": "81",
        "text": "Трофеи доставлены.", "visible": True, "disabled": False,
    }]))
    runtime.acknowledge(answered)

    with pytest.raises(QuestTurnInError, match="previously submitted") as error:
        runtime.decide_dialog(dialog(dialogActions=[{
            "questId": "246", "npcId": "12", "action": "answer", "ref": "81",
            "text": "Трофеи доставлены.", "visible": True, "disabled": False,
        }]))
    assert error.value.unsafe_reason == "turn_in_action_not_advanced"


def test_open_action_is_selected_by_quest_id_when_dialogue_title_differs() -> None:
    runtime = begun()

    decision = runtime.decide_dialog(dialog(questActions=[
        {
            "questId": "999",
            "title": "Мечта разбойника",
            "action": "open",
            "visible": True,
            "disabled": False,
        },
        {
            "questId": "246",
            "title": "Разговор с Ратмиром о дневнике бандита",
            "action": "open",
            "visible": True,
            "disabled": False,
        },
    ]))

    assert decision.intent is QuestTurnInIntent.OPEN_QUEST
    assert decision.action_metadata["quest_id"] == "246"
    assert decision.action_metadata["expected_title"] == (
        "Разговор с Ратмиром о дневнике бандита"
    )
    assert decision.action_metadata["expected_name"] == "Воевода Ратмир"


def test_fresh_advanced_step_continues_same_quest_chain() -> None:
    runtime = begun()
    assert runtime.pending is not None
    runtime.pending = replace(
        runtime.pending,
        phase=QuestTurnInPhase.VERIFY_ACTIVE,
        completion_after_revision=7,
    )
    advanced = ActiveQuestEntry(
        "246",
        "Охота на волка",
        MappingProxyType({
            "id": "246",
            "title": "Охота на волка",
            "status": "active",
            "objective": "Убить Лиса 0/3",
            "navigation": (
                MappingProxyType({"text": "Лиса", "target": "Лиса [5]"}),
            ),
            "progress": MappingProxyType(
                {"current": 0, "required": 3, "complete": False}
            ),
        }),
    )

    assert runtime.verify_continuation(
        (advanced,), catalog_complete=True, catalog_revision=8
    ) == advanced
    runtime.confirm_continuation()
    assert runtime.pending is None


def test_same_completed_step_is_not_accepted_as_continuation() -> None:
    runtime = begun()
    assert runtime.pending is not None
    runtime.pending = replace(
        runtime.pending,
        phase=QuestTurnInPhase.VERIFY_ACTIVE,
        completion_after_revision=7,
    )

    with pytest.raises(QuestTurnInError) as exc_info:
        runtime.verify_continuation(
            (completed_entry(),), catalog_complete=True, catalog_revision=8
        )

    assert exc_info.value.unsafe_reason == "turn_in_step_not_advanced"
    assert runtime.pending is not None


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda entry, lease, ref: (entry, lease, QuestRef("999", ref.title, location=ref.location, giver_names=ref.giver_names)), "turn_in_identity_mismatch"),
        (lambda entry, lease, ref: (completed_entry(complete=False), lease, ref), "turn_in_objective_not_complete"),
        (lambda entry, lease, ref: (entry, lease, QuestRef(ref.id, ref.title, location=ref.location, giver_names=("A", "B"))), "turn_in_route_missing_or_ambiguous"),
    ],
)
def test_begin_fails_closed_without_consistent_completed_bounded_identity(mutate, reason: str) -> None:
    entry = completed_entry()
    lease, quest_ref = identities(entry)
    changed_entry, changed_lease, changed_ref = mutate(entry, lease, quest_ref)
    with pytest.raises(QuestTurnInError) as exc:
        QuestTurnInRuntime().begin(
            changed_entry, lease=changed_lease, quest_ref=changed_ref, already_at_location=False
        )
    assert exc.value.unsafe_reason == reason


def test_malformed_or_ambiguous_completion_fails_closed_and_stale_decision_is_rejected() -> None:
    runtime = begun()
    opened = runtime.decide_dialog(dialog(questActions=[{
        "questId": "246", "title": "Охота", "action": "open", "visible": True, "disabled": False,
    }]))
    runtime.acknowledge(opened)

    both = dialog(
        dialogActions=[{"questId": "246", "npcId": "12", "action": "answer", "ref": "1", "text": "A", "visible": True, "disabled": False}],
        doneActions=[{"questId": "246", "npcId": "12", "action": "done", "pointId": "2", "text": "D", "visible": True, "disabled": False}],
    )
    with pytest.raises(QuestTurnInError) as exc:
        runtime.decide_dialog(both)
    assert exc.value.unsafe_reason == "turn_in_action_ambiguous"
    with pytest.raises(RuntimeError, match="stale"):
        runtime.acknowledge(opened)


def test_action_collections_and_possibly_relevant_items_are_strictly_validated() -> None:
    runtime = begun()
    with pytest.raises(QuestTurnInError) as non_list:
        runtime.decide_dialog(dialog(questActions=None))
    assert non_list.value.unsafe_reason == "turn_in_action_collection_invalid"

    with pytest.raises(QuestTurnInError) as malformed_neighbor:
        runtime.decide_dialog(dialog(questActions=[
            {"questId": "246", "title": "Охота", "action": "open", "visible": True, "disabled": False},
            {"questId": "", "action": "open", "visible": True, "disabled": False},
        ]))
    assert malformed_neighbor.value.unsafe_reason == "turn_in_action_collection_invalid"

    with pytest.raises(QuestTurnInError) as malformed_relevant:
        runtime.decide_dialog(dialog(questActions=[
            {"questId": "246", "title": "Охота", "action": "open", "visible": "yes", "disabled": False},
        ]))
    assert malformed_relevant.value.unsafe_reason == "turn_in_action_collection_invalid"

    with pytest.raises(QuestTurnInError) as wrong_action_neighbor:
        runtime.decide_dialog(dialog(questActions=[
            {"questId": "246", "title": "Охота", "action": "open", "visible": True, "disabled": False},
            {"questId": "246", "title": "Охота", "action": "unknown", "visible": True, "disabled": False},
        ]))
    assert wrong_action_neighbor.value.unsafe_reason == "turn_in_action_collection_invalid"


def test_area_snapshot_requires_nonempty_exact_location_name() -> None:
    entry = completed_entry()
    lease, quest_ref = identities(entry)
    runtime = QuestTurnInRuntime()
    runtime.begin(entry, lease=lease, quest_ref=quest_ref, already_at_location=True)
    snapshot = area({"dataId": "12", "name": "Воевода Ратмир", "actionable": True})
    snapshot["location"] = {"id": "77", "name": ""}

    with pytest.raises(QuestTurnInError) as exc:
        runtime.decide_area_npc(snapshot)
    assert exc.value.unsafe_reason == "turn_in_location_mismatch"
