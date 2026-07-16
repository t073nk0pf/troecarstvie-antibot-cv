from __future__ import annotations

import pytest

from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_intake_runtime import (
    QuestAcceptPhase,
    QuestIntakeDecisionError,
    QuestIntakeIntent,
    QuestIntakeRuntime,
)


def quest(*, givers: tuple[str, ...] = ("Моряк Кентур",)) -> QuestRef:
    return QuestRef("314", "Письмо моряку", location="Порт", giver_names=givers, catalog_page=0)


def area_snapshot(*items: dict[str, object]) -> dict[str, object]:
    return {
        "ok": True,
        "snapshotId": "area-npcs-1",
        "location": {"id": "125", "name": "Порт"},
        "items": list(items),
        "truncated": False,
    }


def dialog_snapshot(**updates: object) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "ok": True,
        "truncated": False,
        "identityMatches": True,
        "snapshotId": "npc-dialog-1",
        "headers": [],
        "actions": [],
        "questActions": [],
        "dialogActions": [],
        "acceptActions": [],
    }
    snapshot.update(updates)
    return snapshot


def ready_dialog_runtime() -> QuestIntakeRuntime:
    runtime = QuestIntakeRuntime()
    runtime.begin(quest(), already_at_location=True)
    decision = runtime.decide_area_npc(
        area_snapshot({"dataId": "6", "name": "Моряк Кентур", "actionable": True})
    )
    runtime.acknowledge(decision)
    return runtime


def test_intake_requires_one_giver_and_returns_snapshot_bound_npc_intent() -> None:
    runtime = QuestIntakeRuntime()
    with pytest.raises(ValueError, match="exactly one giver"):
        runtime.begin(quest(givers=("A", "B")), already_at_location=True)

    pending = runtime.begin(quest(), already_at_location=True)
    assert pending.phase is QuestAcceptPhase.NPC_LOOKUP
    decision = runtime.decide_area_npc(
        area_snapshot(
            {"dataId": "6", "name": "Моряк Кентур", "actionable": True},
            {"dataId": "7", "name": "Другой", "actionable": True},
        )
    )

    assert decision.intent is QuestIntakeIntent.OPEN_NPC
    assert decision.action_type == "open_exact_npc"
    assert dict(decision.action_metadata) == {
        "expected_snapshot_id": "area-npcs-1",
        "expected_location_id": "125",
        "npc_id": "6",
        "expected_name": "Моряк Кентур",
        "expected_dialog_name": "Моряк Кентур",
        "quest_id": "314",
    }
    with pytest.raises(TypeError):
        decision.action_metadata["npc_id"] = "7"  # type: ignore[index]
    assert runtime.pending is not None and runtime.pending.phase is QuestAcceptPhase.NPC_LOOKUP
    assert runtime.acknowledge(decision).phase is QuestAcceptPhase.NPC_DIALOG


def test_intake_rejects_unconfirmed_or_ambiguous_area_snapshot() -> None:
    runtime = QuestIntakeRuntime()
    runtime.begin(quest(), already_at_location=False)
    assert runtime.mark_route_arrived().phase is QuestAcceptPhase.NPC_LOOKUP

    unconfirmed = area_snapshot({"dataId": "6", "name": "Моряк Кентур", "actionable": True})
    del unconfirmed["truncated"]
    with pytest.raises(QuestIntakeDecisionError, match="unconfirmed"):
        runtime.decide_area_npc(unconfirmed)

    with pytest.raises(QuestIntakeDecisionError, match="ambiguous"):
        runtime.decide_area_npc(
            area_snapshot(
                {"dataId": "6", "name": "Моряк Кентур", "actionable": True},
                {"dataId": "7", "name": "Моряк Кентур", "actionable": True},
            )
        )


def test_dialog_decisions_normalize_title_and_choose_open_answer_then_accept() -> None:
    runtime = ready_dialog_runtime()

    opened = runtime.decide_dialog(
        dialog_snapshot(
            questActions=[
                {
                    "questId": "314",
                    "title": "  ПИСЬМО   МОРЯКУ ",
                    "action": "open",
                    "visible": True,
                    "disabled": False,
                }
            ]
        )
    )
    assert opened.intent is QuestIntakeIntent.OPEN_QUEST
    assert dict(opened.action_metadata) == {
        "expected_snapshot_id": "npc-dialog-1",
        "npc_id": "6",
        "quest_id": "314",
        "expected_title": "Письмо моряку",
        "action": "open",
    }
    runtime.acknowledge(opened)

    answered = runtime.decide_dialog(
        dialog_snapshot(
            snapshotId="npc-dialog-2",
            dialogActions=[
                {
                    "questId": "314",
                    "npcId": "6",
                    "action": "answer",
                    "visible": True,
                    "disabled": False,
                    "ref": "401",
                    "text": "Я доставлю письмо.",
                }
            ],
        )
    )
    assert answered.intent is QuestIntakeIntent.ANSWER_DIALOG
    assert answered.action_metadata["expected_ref"] == "401"
    assert runtime.acknowledge(answered).dialog_steps == 1

    accepted = runtime.decide_dialog(
        dialog_snapshot(
            snapshotId="npc-dialog-3",
            headers=["Письмо моряку"],
            actions=[{"containerText": "Ваша цель: доставить письмо"}],
            acceptActions=[
                {
                    "questId": "314",
                    "npcId": "6",
                    "action": "accept",
                    "visible": True,
                    "disabled": False,
                    "text": "Взять задание",
                }
            ],
        )
    )
    assert accepted.intent is QuestIntakeIntent.ACCEPT_QUEST
    assert accepted.action_metadata["expected_text"] == "Взять задание"
    assert runtime.acknowledge(accepted).phase is QuestAcceptPhase.VERIFY_STARTED
    runtime.finish("314")
    assert runtime.pending is None


def test_dialog_prefers_the_only_reply_without_refusal_language() -> None:
    runtime = ready_dialog_runtime()
    runtime.acknowledge(
        runtime.decide_dialog(
            dialog_snapshot(
                questActions=[
                    {
                        "questId": "314",
                        "title": "Письмо моряку",
                        "action": "open",
                        "visible": True,
                        "disabled": False,
                    }
                ]
            )
        )
    )

    decision = runtime.decide_dialog(
        dialog_snapshot(
            dialogActions=[
                {
                    "questId": "314",
                    "npcId": "6",
                    "action": "answer",
                    "visible": True,
                    "disabled": False,
                    "ref": "401",
                    "text": "Где мне искать моряка?",
                },
                {
                    "questId": "314",
                    "npcId": "6",
                    "action": "answer",
                    "visible": True,
                    "disabled": False,
                    "ref": "402",
                    "text": "Не могу я сейчас отправиться в путь.",
                },
            ]
        )
    )

    assert decision.intent is QuestIntakeIntent.ANSWER_DIALOG
    assert decision.action_metadata["expected_ref"] == "401"


@pytest.mark.parametrize(
    "snapshot,unsafe_reason",
    [
        (dialog_snapshot(truncated=True), "quest_accept_dialog_snapshot_invalid"),
        (dialog_snapshot(identityMatches=False), "quest_accept_dialog_identity_mismatch"),
        (dialog_snapshot(snapshotId="wrong"), "quest_accept_dialog_snapshot_invalid"),
    ],
)
def test_dialog_decision_fails_closed_on_snapshot_identity(
    snapshot: dict[str, object],
    unsafe_reason: str,
) -> None:
    runtime = ready_dialog_runtime()

    with pytest.raises(QuestIntakeDecisionError) as raised:
        runtime.decide_dialog(snapshot)

    assert raised.value.unsafe_reason == unsafe_reason


def test_terminal_accept_requires_title_objective_and_unique_action() -> None:
    runtime = ready_dialog_runtime()
    runtime.mark_quest_opened()
    missing_objective = dialog_snapshot(
        headers=["Письмо моряку"],
        acceptActions=[
            {
                "questId": "314",
                "npcId": "6",
                "action": "accept",
                "visible": True,
                "disabled": False,
                "text": "Взять задание",
            }
        ],
    )

    with pytest.raises(QuestIntakeDecisionError) as raised:
        runtime.decide_dialog(missing_objective)

    assert raised.value.unsafe_reason == "quest_accept_terminal_evidence_missing"
