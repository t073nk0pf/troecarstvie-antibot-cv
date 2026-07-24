from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.antibot_cv.automation.quest_chain_runtime import QuestChainRuntime
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_director_runtime import QuestDirectorRuntime
from src.antibot_cv.automation.quest_npc_open_navigation import (
    NpcOpenSettleStatus,
    NpcOpenStatus,
    make_pending_npc_open,
    npc_open_command_payload,
    parse_npc_open_outcome,
    restore_pending_npc_open,
    serialize_pending_npc_open,
    settle_npc_open_snapshot,
)
from src.antibot_cv.automation.quest_intake_runtime import QuestAcceptPhase, QuestIntakeRuntime


def quest() -> QuestRef:
    return QuestRef(
        "269", "Вступление в Чёрную лигу", None, "След Велета",
        ("чёрного мага Вагарда",), 0,
    )


def pending():
    ref = quest()
    return make_pending_npc_open(
        client_id="client-a", profile_id="profile-a", tab_id=42,
        quest_id=ref.id, quest_title=ref.title, quest_accept_ref=ref.accept_ref,
        quest_catalog_page=ref.catalog_page, giver_name=ref.giver_names[0],
        npc_id="771", route_ref="398", npc_name="Башня Вагарда", location_id="125",
        location_name=ref.location, area_snapshot_id="area-npcs-q269",
        area_generated_at=99.0, issued_at=100.0, settle_timeout_s=20.0,
    )


def dialog(**changes):
    ref = quest()
    value = {
        "ok": True, "truncated": False, "snapshotId": "npc-dialog-q269",
        "generatedAt": 101.0, "pageKind": "npc",
        "href": "https://3kingdoms.ru/npc.php?f_id=771",
        "expectedName": ref.giver_names[0], "expectedNpcId": "771",
        "identityMatches": True, "npcId": "771",
        "matchingHeaders": ["Чёрный маг Вагард"],
        "questActions": [{
            "questId": "269", "npcId": "771", "action": "open",
            "title": ref.title, "visible": True, "disabled": False,
        }],
    }
    value.update(changes)
    return value


def test_legacy_postcondition_failure_is_ambiguous_not_safe_to_retry() -> None:
    outcome = parse_npc_open_outcome(
        result_ok=False,
        message=json.dumps({"ok": False, "message": "npc_open_postcondition_failed"}),
        client_id="client-a",
    )
    assert outcome.status is NpcOpenStatus.ACK_PENDING
    assert outcome.dispatched
    explicit = parse_npc_open_outcome(
        result_ok=False,
        message=json.dumps({"outcome": "NOT_ISSUED", "mutationIssued": False}),
        client_id="client-a",
    )
    assert explicit.status is NpcOpenStatus.NOT_ISSUED
    assert not explicit.dispatched
    for outcome, mutation in (
        ("not_issued", False), ("UNKNOWN", False), ("CONFIRMED", False),
        ("NOT_ISSUED", True),
    ):
        contradictory = parse_npc_open_outcome(
            result_ok=False,
            message=json.dumps({"outcome": outcome, "mutationIssued": mutation}),
            client_id="client-a",
        )
        assert contradictory.status is NpcOpenStatus.ACK_PENDING
        assert contradictory.dispatched


def test_pending_allows_zero_endpoint_and_persists_canonical_route_ref() -> None:
    staged = replace(pending(), npc_id="0")
    restored = restore_pending_npc_open(serialize_pending_npc_open(staged))
    assert restored == staged
    assert restored is not None and restored.route_ref == "398"


def test_pre_route_ref_schema_two_restores_reconciliation_only() -> None:
    raw = serialize_pending_npc_open(pending())
    raw["schema"] = 2
    raw.pop("route_ref")
    raw.pop("route_ref_authority")
    restored = restore_pending_npc_open(raw)
    assert restored is not None
    assert restored.route_ref is None
    assert restored.route_ref_authority == "legacy_checkpoint_missing"


@pytest.mark.parametrize("schema", [True, False, 1.0, 2.0, "1", None])
def test_checkpoint_rejects_non_integer_schema_markers(schema: object) -> None:
    raw = serialize_pending_npc_open(pending())
    raw["schema"] = schema
    with pytest.raises(ValueError, match="invalid pending NPC open checkpoint"):
        restore_pending_npc_open(raw)


def test_npc_open_payload_rejects_noncanonical_route_ref() -> None:
    metadata = {
        "expected_snapshot_id": "area-npcs-test",
        "expected_location_id": "102",
        "npc_id": "0",
        "expected_route_ref": "0398",
        "expected_name": "Торговец Богдан",
    }
    assert npc_open_command_payload(metadata) is None
    metadata["expected_route_ref"] = "398"
    assert npc_open_command_payload(metadata)["expectedRouteRef"] == "398"


def test_q269_settle_requires_exact_fresh_identity_and_npc_page() -> None:
    staged = pending()
    accepted = settle_npc_open_snapshot(
        staged, dialog(), client_id="client-a", profile_id="profile-a", tab_id=42, now=102.0,
    )
    assert accepted.status is NpcOpenSettleStatus.ACCEPT
    for snapshot, identity in (
        (dialog(generatedAt=99.5), ("client-a", "profile-a", 42)),
        (dialog(href="https://evil.example/npc.php"), ("client-a", "profile-a", 42)),
        (dialog(href="https://3kingdoms.ru/main.php"), ("client-a", "profile-a", 42)),
        (dialog(questActions=[]), ("client-a", "profile-a", 42)),
        (dialog(), ("client-b", "profile-a", 42)),
    ):
        decision = settle_npc_open_snapshot(
            staged, snapshot, client_id=identity[0], profile_id=identity[1], tab_id=identity[2], now=102.0,
        )
        assert decision.status is not NpcOpenSettleStatus.ACCEPT


def test_exact_empty_npc_dialog_is_a_local_outcome() -> None:
    staged = pending()
    empty = dialog(questActions=[], dialogActions=[])

    decision = settle_npc_open_snapshot(
        staged, empty, client_id="client-a", profile_id="profile-a",
        tab_id=42, now=102.0,
    )

    assert decision.status is NpcOpenSettleStatus.EMPTY
    assert decision.reason == "npc_open_exact_quest_action_unavailable"


def test_exact_npc_with_only_unrelated_actions_is_a_local_outcome() -> None:
    staged = pending()
    unrelated = dialog(
        questActions=[{
            "questId": "999", "npcId": "771", "action": "open",
            "title": "Магазин", "visible": True, "disabled": False,
        }],
        dialogActions=[],
    )

    decision = settle_npc_open_snapshot(
        staged, unrelated, client_id="client-a", profile_id="profile-a",
        tab_id=42, now=102.0,
    )

    assert decision.status is NpcOpenSettleStatus.EMPTY
    assert decision.reason == "npc_open_exact_quest_action_unavailable"


def test_npc_open_accepts_exact_already_opened_quest_dialog() -> None:
    staged = pending()
    direct_dialog = dialog(
        questActions=[],
        dialogActions=[{
            "questId": "269", "npcId": "771", "action": "answer",
            "ref": "401", "text": "Что случилось?",
            "visible": True, "disabled": False,
        }],
    )

    decision = settle_npc_open_snapshot(
        staged, direct_dialog,
        client_id="client-a", profile_id="profile-a", tab_id=42, now=102.0,
    )

    assert decision.status is NpcOpenSettleStatus.ACCEPT
    assert decision.reason == "npc_open_exact_dialog_confirmed"


def test_checkpoint_marks_direct_dialog_open_without_reissuing_open(tmp_path) -> None:
    state = tmp_path / "chain.json"
    chain = QuestChainRuntime(state_path=state)
    chain.stage_accepted_ref(quest())
    staged = pending()
    chain.stage_npc_open(staged)

    chain.settle_npc_open(staged, quest_opened=True)

    restored = QuestChainRuntime(state_path=state)
    assert restored.pending_npc_open is None
    assert restored.pending_npc_dialog is not None
    assert restored.pending_npc_dialog.quest_opened is True


def test_exact_snapshot_at_or_after_deadline_never_settles() -> None:
    staged = pending()
    for now, generated_at in ((120.0, 119.0), (121.0, 101.0), (119.0, 119.5)):
        decision = settle_npc_open_snapshot(
            staged, dialog(generatedAt=generated_at),
            client_id="client-a", profile_id="profile-a", tab_id=42, now=now,
        )
        assert decision.status is not NpcOpenSettleStatus.ACCEPT
    expired = settle_npc_open_snapshot(
        staged, dialog(), client_id="client-a", profile_id="profile-a", tab_id=42, now=120.0,
    )
    assert expired.status is NpcOpenSettleStatus.STOP_EXPIRED


def test_checkpoint_atomically_transitions_open_to_dialog(tmp_path) -> None:
    state = tmp_path / "chain.json"
    chain = QuestChainRuntime(state_path=state)
    chain.stage_accepted_ref(quest())
    staged = pending()
    chain.stage_npc_open(staged)
    restored = QuestChainRuntime(state_path=state)
    assert restore_pending_npc_open(serialize_pending_npc_open(staged)) == staged
    assert restored.pending_npc_open == staged
    restored.settle_npc_open(staged)
    final = QuestChainRuntime(state_path=state)
    assert final.pending_npc_open is None
    assert final.pending_npc_dialog == staged


def test_settle_persistence_failure_rolls_back_and_restart_restores_without_action(tmp_path) -> None:
    chain = QuestChainRuntime(state_path=tmp_path / "chain.json")
    chain.stage_accepted_ref(quest())
    staged = pending()
    chain.stage_npc_open(staged)
    chain._persist = lambda: (_ for _ in ()).throw(OSError("disk"))  # type: ignore[method-assign]
    with pytest.raises(OSError, match="disk"):
        chain.settle_npc_open(staged)
    assert chain.pending_npc_open == staged
    assert chain.pending_npc_dialog is None

    intake = QuestIntakeRuntime()
    restored = intake.restore_npc_open(staged, confirmed=False)
    assert restored.phase is QuestAcceptPhase.NPC_LOOKUP
    assert intake.pending is not None


@pytest.mark.parametrize("confirmed", [False, True])
def test_restart_restores_exact_acceptance_and_active_proof_finishes_without_npc_reissue(
    tmp_path, confirmed: bool,
) -> None:
    state = tmp_path / f"chain-{confirmed}.json"
    chain = QuestChainRuntime(state_path=state)
    ref = quest()
    chain.stage_accepted_ref(ref)
    staged = pending()
    chain.stage_npc_open(staged)
    if confirmed:
        chain.settle_npc_open(staged)

    director = QuestDirectorRuntime(chain_state_path=state)
    assert director.pending_accept == ref
    assert director.intake_queue == (ref,)
    intake = QuestIntakeRuntime()
    restored = intake.restore_npc_open(staged, confirmed=confirmed)
    assert restored.phase is (
        QuestAcceptPhase.NPC_DIALOG if confirmed else QuestAcceptPhase.NPC_LOOKUP
    )
    assert intake.matches_pending(ref)

    director.begin_active_refresh()
    director.ingest_active_page({
        "loadStatus": "loaded", "mode": "started", "currentPage": 0,
        "pageCount": 1, "hasNextPage": False, "truncated": False,
        "items": [{
            "id": ref.id, "title": ref.title, "status": "active",
            "objective": "Убивая Волков, получите трофей.",
            "navigation": [
                {"text": "Волков", "target": "Волк [5]"},
                {"text": "След Велета", "target": "След Велета"},
            ],
            "progress": None,
        }],
    })
    director.acknowledge_accept(ref.id)
    assert director.pending_accept is None
    assert director.chain.pending_npc_open is None
    assert director.chain.pending_npc_dialog is None
    assert director.chain.lease is not None


def test_malformed_or_foreign_checkpoint_fails_closed() -> None:
    raw = serialize_pending_npc_open(pending())
    raw["game_origin"] = "https://evil.example"
    with pytest.raises(ValueError, match="invalid pending NPC open"):
        restore_pending_npc_open(raw)
