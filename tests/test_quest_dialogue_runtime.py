from __future__ import annotations

from types import MappingProxyType

import pytest

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_dialogue_runtime import (
    QuestDialogueError,
    QuestDialogueIntent,
    QuestDialoguePhase,
    QuestDialogueRuntime,
    parse_dialogue_objective,
)


def entry(*, navigation: tuple[object, ...] | None = None, objective: str | None = None) -> ActiveQuestEntry:
    data = MappingProxyType(
        {
            "id": "246",
            "title": "Хворь скакунов",
            "status": "active",
            "objective": objective
            or "Отправляйтесь к алхимику Филониду в Туманные луга и покажите ему сено.",
            "navigation": navigation
            if navigation is not None
            else (MappingProxyType({"text": "Туманные луга", "target": "Туманные луга"}),),
            "progress": None,
        }
    )
    return ActiveQuestEntry("246", "Хворь скакунов", data)


def area(*items: dict[str, object], name: str = "Туманные луга") -> dict[str, object]:
    return {
        "ok": True,
        "truncated": False,
        "snapshotId": "area-npcs-246",
        "location": {"id": "121", "name": name},
        "items": list(items),
    }


def dialog(**updates: object) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "ok": True,
        "truncated": False,
        "identityMatches": True,
        "snapshotId": "npc-dialog-246",
        "questActions": [],
        "dialogActions": [],
        "doneActions": [],
    }
    snapshot.update(updates)
    return snapshot


def ready() -> QuestDialogueRuntime:
    runtime = QuestDialogueRuntime()
    runtime.begin(entry(), already_at_location=True)
    opened = runtime.decide_area_npc(
        area({"dataId": "9", "name": "Алхимик Филонид", "actionable": True})
    )
    runtime.acknowledge(opened)
    return runtime


def test_parse_travel_to_npc_objective_with_inflected_query() -> None:
    parsed = parse_dialogue_objective(entry())

    assert parsed.quest_id == "246"
    assert parsed.quest_title == "Хворь скакунов"
    assert parsed.npc_query == "алхимику Филониду"
    assert parsed.location == "Туманные луга"
    assert len(parsed.fingerprint) == 64


@pytest.mark.parametrize(
    "bad_entry",
    [
        entry(navigation=()),
        entry(
            navigation=(
                MappingProxyType({"text": "A", "target": "A"}),
                MappingProxyType({"text": "B", "target": "B"}),
            )
        ),
        entry(objective="Отправляйтесь в Туманные луга."),
        entry(
            navigation=(MappingProxyType({"text": "Филонид", "target": "Дом Филонида"}),)
        ),
    ],
)
def test_parser_fails_closed_on_missing_or_ambiguous_location_or_npc(
    bad_entry: ActiveQuestEntry,
) -> None:
    with pytest.raises(QuestDialogueError):
        parse_dialogue_objective(bad_entry)


def test_route_and_area_lookup_emit_exact_snapshot_bound_npc_action() -> None:
    runtime = QuestDialogueRuntime()
    assert runtime.begin(entry(), already_at_location=False).phase is QuestDialoguePhase.ROUTE
    assert runtime.mark_route_arrived().phase is QuestDialoguePhase.NPC_LOOKUP

    decision = runtime.decide_area_npc(
        area(
            {"dataId": "9", "name": "Алхимик Филонид", "actionable": True},
            {"dataId": "10", "name": "Кузнец", "actionable": True},
        )
    )

    assert decision.intent is QuestDialogueIntent.OPEN_NPC
    assert decision.action_type == "open_exact_npc"
    assert dict(decision.action_metadata) == {
        "expected_snapshot_id": "area-npcs-246",
        "expected_location_id": "121",
        "npc_id": "9",
        "expected_name": "Алхимик Филонид",
        "expected_dialog_name": "Алхимик Филонид",
        "quest_id": "246",
    }
    assert runtime.acknowledge(decision).phase is QuestDialoguePhase.NPC_DIALOG


def test_area_lookup_rejects_wrong_location_and_ambiguous_stem_match() -> None:
    runtime = QuestDialogueRuntime()
    runtime.begin(entry(), already_at_location=True)
    with pytest.raises(QuestDialogueError) as wrong:
        runtime.decide_area_npc(
            area({"dataId": "9", "name": "Алхимик Филонид", "actionable": True}, name="Арс")
        )
    assert wrong.value.unsafe_reason == "dialogue_location_mismatch"

    with pytest.raises(QuestDialogueError) as ambiguous:
        runtime.decide_area_npc(
            area(
                {"dataId": "9", "name": "Алхимик Филонид", "actionable": True},
                {"dataId": "10", "name": "Алхимик Филонид", "actionable": True},
            )
        )
    assert ambiguous.value.unsafe_reason == "dialogue_npc_missing_or_ambiguous"


def test_dialogue_opens_same_quest_answers_and_requests_active_verification() -> None:
    runtime = ready()
    opened = runtime.decide_dialog(
        dialog(
            questActions=[
                {
                    "questId": "246",
                    "title": "Разговор с Филонидом о подозрительном сене",
                    "action": "open",
                    "visible": True,
                    "disabled": False,
                },
                {
                    "questId": "999",
                    "title": "Other",
                    "action": "open",
                    "visible": True,
                    "disabled": False,
                },
            ]
        )
    )
    assert opened.intent is QuestDialogueIntent.OPEN_QUEST
    assert opened.action_metadata["expected_title"] == (
        "Разговор с Филонидом о подозрительном сене"
    )
    runtime.acknowledge(opened)

    answered = runtime.decide_dialog(
        dialog(
            snapshotId="npc-dialog-answer",
            dialogActions=[
                {
                    "questId": "246",
                    "npcId": "9",
                    "action": "answer",
                    "visible": True,
                    "disabled": False,
                    "ref": "401",
                    "text": "Вот подозрительное сено.",
                }
            ],
        )
    )
    assert answered.intent is QuestDialogueIntent.ANSWER_DIALOG
    assert runtime.acknowledge(answered).dialog_steps == 1

    completed = runtime.decide_dialog(
        dialog(
            snapshotId="npc-dialog-done",
            doneActions=[
                {
                    "questId": "246",
                    "npcId": "9",
                    "action": "done",
                    "visible": True,
                    "disabled": False,
                    "pointId": "402",
                    "text": "Продолжить",
                },
                {
                    "questId": "999",
                    "npcId": "9",
                    "action": "done",
                    "visible": True,
                    "disabled": False,
                    "pointId": "999",
                    "text": "Other",
                },
            ],
        )
    )
    assert completed.intent is QuestDialogueIntent.COMPLETE_STEP
    assert completed.action_metadata["expected_point_id"] == "402"
    pending = runtime.acknowledge(completed)
    assert pending.phase is QuestDialoguePhase.VERIFY_ACTIVE
    runtime.finish_verified(
        quest_id="246", previous_fingerprint=pending.objective.fingerprint
    )
    assert runtime.pending is None


def test_dialogue_is_bounded_and_fails_closed_on_ambiguous_progression() -> None:
    runtime = ready()
    runtime.pending = runtime.pending and runtime.pending.__class__(
        **{**runtime.pending.__dict__, "quest_opened": True, "dialog_steps": 20}
    )
    answer = {
        "questId": "246",
        "npcId": "9",
        "action": "answer",
        "visible": True,
        "disabled": False,
        "ref": "401",
        "text": "Continue",
    }
    with pytest.raises(QuestDialogueError) as bounded:
        runtime.decide_dialog(dialog(dialogActions=[answer]), max_steps=20)
    assert bounded.value.unsafe_reason == "dialogue_step_limit_exceeded"

    runtime.pending = runtime.pending and runtime.pending.__class__(
        **{**runtime.pending.__dict__, "dialog_steps": 0}
    )
    with pytest.raises(QuestDialogueError) as ambiguous:
        runtime.decide_dialog(
            dialog(
                dialogActions=[answer],
                doneActions=[
                    {
                        "questId": "246",
                        "npcId": "9",
                        "action": "done",
                        "visible": True,
                        "disabled": False,
                        "pointId": "402",
                        "text": "Done",
                    }
                ],
            )
        )
    assert ambiguous.value.unsafe_reason == "dialogue_action_ambiguous"
