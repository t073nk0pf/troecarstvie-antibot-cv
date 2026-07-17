from __future__ import annotations

from dataclasses import replace
import json
from types import MappingProxyType

import pytest

from src.antibot_cv.automation.quest_chain_runtime import QuestChainRuntime
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_npc_action_journal import (
    NpcQuestActionKind,
    NpcQuestActionPhase,
    NpcQuestActionSettle,
    NpcQuestActionStatus,
    make_pending_npc_quest_action,
    parse_npc_quest_action_outcome,
    restore_pending_npc_action,
    serialize_pending_npc_action,
    settle_accept_action,
    settle_dialog_action,
    dialog_semantic_fingerprint,
)
from src.antibot_cv.automation.quest_npc_open_navigation import make_pending_npc_open


def ref() -> QuestRef:
    return QuestRef("269", "Вступление в Чёрную лигу", None, "След Велета", ("чёрного мага Вагарда",), 0)


def dialog_stage():
    quest = ref()
    return make_pending_npc_open(
        client_id="client-a", profile_id="profile-a", tab_id=42,
        quest_id=quest.id, quest_title=quest.title, quest_accept_ref=None,
        quest_catalog_page=0, giver_name=quest.giver_names[0], npc_id="10",
        npc_name="Башня Вагарда", location_id="132", location_name=quest.location,
        area_snapshot_id="area-npcs-q269", area_generated_at=99.0, issued_at=100.0,
    )


def action(kind="open", *, opened=False, steps=0):
    source = {
        "questActions": [{"questId": "269", "npcId": "10", "action": "open"}],
        "dialogActions": [], "acceptActions": [],
    } if kind == "open" else {
        "questActions": [], "dialogActions": [{
            "questId": "269", "npcId": "10", "action": "answer", "ref": "401", "text": "Дальше",
        }], "acceptActions": [],
    }
    return make_pending_npc_quest_action(
        ref=ref(), client_id="client-a", profile_id="profile-a", tab_id=42,
        giver_name=ref().giver_names[0], npc_id="10", action=kind,
        expected_snapshot_id="npc-dialog-before", expected_generated_at=109.0,
        source_semantic_fingerprint=dialog_semantic_fingerprint(source),
        expected_ref="401" if kind == "answer" else None,
        expected_text="Дальше" if kind == "answer" else ("Взять задание" if kind == "accept" else None),
        quest_opened=opened, dialog_steps=steps, issued_at=110.0,
    )


def snapshot(**updates):
    value = {
        "ok": True, "truncated": False, "snapshotId": "npc-dialog-after",
        "generatedAt": 111.0, "pageKind": "npc", "href": "https://3kingdoms.ru/npc.php?f_id=10",
        "expectedName": ref().giver_names[0], "identityMatches": True, "npcId": "10",
        "expectedNpcId": "10", "matchingHeaders": ["Чёрный маг Вагард"],
        "questActions": [], "dialogActions": [{
            "questId": "269", "npcId": "10", "action": "answer", "ref": "402",
            "text": "Новый шаг", "visible": True, "disabled": False,
        }], "acceptActions": [],
    }
    value.update(updates)
    return value


def test_outcome_only_explicit_not_issued_is_safe_to_rollback() -> None:
    explicit = parse_npc_quest_action_outcome(
        result_ok=False, message=json.dumps({"outcome": "NOT_ISSUED", "mutationIssued": False}), client_id="client-a",
    )
    assert explicit.status is NpcQuestActionStatus.NOT_ISSUED
    timed_out = parse_npc_quest_action_outcome(
        result_ok=False, message="injector_delivery_timeout", client_id="client-a",
    )
    assert timed_out.status is NpcQuestActionStatus.NOT_ISSUED
    assert not timed_out.dispatched
    done = parse_npc_quest_action_outcome(
        result_ok=True,
        message=json.dumps({
            "outcome": "ACK_PENDING", "mutationIssued": True,
            "message": "npc_quest_action_submitted", "action": "done",
        }),
        client_id="client-a",
    )
    assert done.status is NpcQuestActionStatus.ACK_PENDING
    assert done.dispatched and done.reason == "npc_quest_action_submitted"
    for payload in (
        {"outcome": "NOT_ISSUED", "mutationIssued": True},
        {"message": "npc_quest_action_submitted"},
        {"outcome": "CONFIRMED", "mutationIssued": False},
    ):
        assert parse_npc_quest_action_outcome(
            result_ok=False, message=json.dumps(payload), client_id="client-a",
        ).status is NpcQuestActionStatus.ACK_PENDING


def test_done_journal_accepts_zero_based_npc_identity() -> None:
    pending = make_pending_npc_quest_action(
        ref=ref(), client_id="client-a", profile_id="profile-a", tab_id=42,
        giver_name=ref().giver_names[0], npc_id="0", action="done",
        expected_snapshot_id="npc-dialog-before", expected_generated_at=109.0,
        source_semantic_fingerprint="a" * 64, expected_point_id="402",
        expected_text="Завершить задание", quest_opened=True, dialog_steps=1,
        issued_at=110.0,
    )
    assert pending.action is NpcQuestActionKind.DONE
    assert pending.npc_id == "0"


def test_open_and_answer_require_semantic_successor_not_snapshot_id_only() -> None:
    opened = action()
    assert settle_dialog_action(opened, snapshot(), client_id="client-a", profile_id="profile-a", tab_id=42, now=112.0) is NpcQuestActionSettle.ACCEPT
    answered = action("answer", opened=True)
    identical = snapshot(dialogActions=[{
        "questId": "269", "npcId": "10", "action": "answer", "ref": "401", "text": "Дальше",
    }])
    assert settle_dialog_action(answered, identical, client_id="client-a", profile_id="profile-a", tab_id=42, now=112.0) is NpcQuestActionSettle.WAIT
    assert settle_dialog_action(answered, snapshot(), client_id="client-b", profile_id="profile-a", tab_id=42, now=112.0) is NpcQuestActionSettle.STOP
    assert settle_dialog_action(answered, snapshot(generatedAt=109.0), client_id="client-a", profile_id="profile-a", tab_id=42, now=112.0) is NpcQuestActionSettle.WAIT


@pytest.mark.parametrize(
    ("pending", "updates"),
    [
        (action(), {"expected_title": "Другое задание"}),
        (action(), {"expected_point_id": "7"}),
        (action("answer", opened=True), {"expected_point_id": "7"}),
        (action("accept", opened=True), {"expected_ref": "401"}),
        (action("accept", opened=True), {"expected_point_id": "7"}),
    ],
)
def test_pending_action_rejects_extraneous_or_noncanonical_identity(pending, updates) -> None:
    with pytest.raises(ValueError):
        replace(pending, **updates)


@pytest.mark.parametrize(
    "updates",
    [
        {"quest_opened": 1},
        {"dialog_steps": False},
        {"expected_snapshot_id": "npc-dialog-" + "x" * 111},
        {"quest_catalog_page": True},
        {"quest_catalog_page": -1},
        {"quest_catalog_page": 10_001},
        {"quest_accept_ref": "0"},
        {"quest_location": "x" * 501},
        {"giver_name": "x" * 501},
        {"source_semantic_fingerprint": "A" * 64},
        {"source_semantic_fingerprint": "g" * 64},
    ],
)
def test_pending_action_rejects_noncanonical_checkpoint_shape(updates) -> None:
    with pytest.raises(ValueError):
        replace(action(), **updates)


@pytest.mark.parametrize(
    "updates",
    [
        {"expectedName": "другой NPC"},
        {"expectedNpcId": "11"},
        {"npcId": "11"},
        {"identityMatches": False},
        {"matchingHeaders": []},
        {"matchingHeaders": ["Вагард", "Вагард"]},
        {"matchingHeaders": ["  "]},
    ],
)
def test_dialog_settle_rejects_inexact_npc_identity(updates) -> None:
    assert settle_dialog_action(
        action(), snapshot(**updates), client_id="client-a", profile_id="profile-a", tab_id=42, now=112.0,
    ) is NpcQuestActionSettle.WAIT


def test_chain_journal_stage_ack_settle_is_durable_and_restart_safe(tmp_path) -> None:
    state = tmp_path / "chain.json"
    chain = QuestChainRuntime(state_path=state)
    chain.stage_accepted_ref(ref())
    base = dialog_stage()
    chain.stage_npc_open(base)
    chain.settle_npc_open(base)
    pending = action()
    chain.stage_npc_quest_action(pending)
    acknowledged = chain.mark_npc_quest_action_dispatched(pending)
    assert acknowledged.phase is NpcQuestActionPhase.ACK_PENDING
    restored = QuestChainRuntime(state_path=state)
    assert restored.pending_npc_action == acknowledged
    assert restore_pending_npc_action(serialize_pending_npc_action(acknowledged)) == acknowledged
    updated = restored.settle_npc_dialog_action(acknowledged, semantic_fingerprint="a" * 64)
    assert updated.quest_opened is True
    final = QuestChainRuntime(state_path=state)
    assert final.pending_npc_action is None and final.pending_npc_dialog.quest_opened is True


def test_executor_not_issued_clear_and_atomic_failure_rollback(tmp_path, monkeypatch) -> None:
    state = tmp_path / "chain.json"
    chain = QuestChainRuntime(state_path=state)
    chain.stage_accepted_ref(ref())
    base = dialog_stage()
    chain.stage_npc_open(base)
    chain.settle_npc_open(base)
    pending = action()
    chain.stage_npc_quest_action(pending)
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
    )
    with pytest.raises(OSError):
        chain.mark_npc_quest_action_dispatched(pending)
    assert chain.pending_npc_action == pending


def test_dialog_settle_write_failure_restores_action_and_dialog(tmp_path, monkeypatch) -> None:
    chain = QuestChainRuntime(state_path=tmp_path / "chain.json")
    chain.stage_accepted_ref(ref())
    base = dialog_stage()
    chain.stage_npc_open(base)
    chain.settle_npc_open(base)
    pending = action()
    chain.stage_npc_quest_action(pending)
    pending = chain.mark_npc_quest_action_dispatched(pending)
    previous_dialog = chain.pending_npc_dialog
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
    )

    with pytest.raises(OSError):
        chain.settle_npc_dialog_action(pending, semantic_fingerprint="b" * 64)

    assert chain.pending_npc_action == pending
    assert chain.pending_npc_dialog == previous_dialog


@pytest.mark.parametrize("updates", [
    {"client_id": "client-b"}, {"profile_id": "profile-b"}, {"tab_id": 43},
])
def test_action_stage_requires_exact_dialog_transport_identity(tmp_path, updates) -> None:
    chain = QuestChainRuntime(state_path=tmp_path / "chain.json")
    chain.stage_accepted_ref(ref())
    base = dialog_stage()
    chain.stage_npc_open(base)
    chain.settle_npc_open(base)

    with pytest.raises(RuntimeError, match="conflicts"):
        chain.stage_npc_quest_action(replace(action(), **updates))

    assert chain.pending_npc_action is None


def test_ambiguity_watermark_is_durable_and_write_failure_rolls_back(tmp_path, monkeypatch) -> None:
    state = tmp_path / "chain.json"
    chain = QuestChainRuntime(state_path=state)
    chain.stage_accepted_ref(ref())
    base = dialog_stage()
    chain.stage_npc_open(base)
    chain.settle_npc_open(base)
    updated = chain.record_npc_dialog_ambiguity(
        base, snapshot_id="npc-dialog-ambiguous", generated_at=101.0,
    )
    assert QuestChainRuntime(state_path=state).pending_npc_dialog == updated
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
    )
    with pytest.raises(OSError):
        chain.record_npc_dialog_ambiguity(
            updated, snapshot_id="npc-dialog-ambiguous-new", generated_at=102.0,
        )
    assert chain.pending_npc_dialog == updated


def test_final_accept_recovery_write_failure_restores_all_pending_state(tmp_path, monkeypatch) -> None:
    chain = QuestChainRuntime(state_path=tmp_path / "chain.json")
    chain.stage_accepted_ref(ref())
    base = replace(dialog_stage(), quest_opened=True)
    chain.stage_npc_open(base)
    chain.settle_npc_open(base)
    pending = action("accept", opened=True)
    chain.stage_npc_quest_action(pending)
    pending = chain.mark_npc_quest_action_dispatched(pending)
    entry = ActiveQuestEntry("269", ref().title, MappingProxyType({
        "id": "269", "title": ref().title, "status": "active",
        "objective": "Убить Волк [5]",
        "navigation": (MappingProxyType({"text": "Волк [5]", "target": "Волк [5]"}),),
        "progress": None,
    }))
    previous_dialog = chain.pending_npc_dialog
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
    )

    with pytest.raises(OSError):
        chain.recover_staged_active_ref(entry, revision=1, expected_ref=ref())

    assert chain.lease is None
    assert chain.pending_accepted_ref == ref()
    assert chain.pending_npc_dialog == previous_dialog
    assert chain.pending_npc_action == pending


def test_accept_requires_complete_unique_exact_active_catalog() -> None:
    staged = action("accept", opened=True)
    verifying = staged.__class__(**{**staged.__dict__, "phase": NpcQuestActionPhase.ACCEPT_VERIFY})
    exact = ref()
    assert settle_accept_action(verifying, (exact,), complete=False, now=111.0) is NpcQuestActionSettle.WAIT
    assert settle_accept_action(verifying, (), complete=True, now=111.0) is NpcQuestActionSettle.STOP
    assert settle_accept_action(verifying, (exact, exact), complete=True, now=111.0) is NpcQuestActionSettle.STOP
    wrong_title = QuestRef(exact.id, "Другое задание", None, exact.location, exact.giver_names, 0)
    assert settle_accept_action(verifying, (wrong_title,), complete=True, now=111.0) is NpcQuestActionSettle.STOP
    assert settle_accept_action(verifying, (exact,), complete=True, now=111.0) is NpcQuestActionSettle.ACCEPT
    restored = restore_pending_npc_action(serialize_pending_npc_action(verifying))
    assert restored is not None
    assert settle_accept_action(restored, (exact,), complete=False, now=restored.deadline) is NpcQuestActionSettle.STOP
