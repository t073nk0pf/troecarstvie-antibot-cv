from __future__ import annotations

import argparse
from dataclasses import replace
import json
from types import SimpleNamespace
import time
import threading

import pytest

from src.antibot_cv.automation.browser_injector import InjectorResult
from src.antibot_cv.automation.controller_cli import command_recover_legacy_npc_open
from src.antibot_cv.automation.quest_chain_runtime import (
    QuestChainCheckpointConflict,
    QuestChainRuntime,
)
from src.antibot_cv.automation.quest_chain_runtime import QuarantinedQuest
from src.antibot_cv.automation.checkpoint import checkpoint_lock
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_director_runtime import QuestDirectorRuntime
from src.antibot_cv.automation.quest_npc_legacy_recovery import (
    evaluate_legacy_npc_recovery,
    load_legacy_npc_open_event,
)
from src.antibot_cv.automation.quest_intake_quarantine import make_intake_quarantine
from src.antibot_cv.automation.quest_npc_open_navigation import (
    LEGACY_NPC_OPEN_CAPABILITY,
    LEGACY_STAGE_SOURCE,
    restore_pending_npc_open,
    serialize_pending_npc_open,
)
from src.antibot_cv.automation.quest_available_eligibility import UnsupportedAvailableQuest


CLIENT = "chrome-profile-37433a8ded0b-tab-770945261-session-87ea83cbc9a64609"
NEW_CLIENT = "chrome-profile-37433a8ded0b-tab-770945261-session-1111111111111111"
PROFILE = "chrome-profile-37433a8ded0b"
TAB = 770945261


def quest() -> QuestRef:
    return QuestRef(
        "269", "Вступление в Чёрную лигу", None, "След Велета",
        ("чёрного мага Вагарда",), 0,
    )


def event(ts: float) -> dict[str, object]:
    return {
        "event_type": "action_blocked", "action_type": "open_exact_npc",
        "block_reason": 'injector_open_exact_npc_failed:{"ok":false,"message":"npc_open_postcondition_failed"}',
        "dry_run": False, "ts_wall": ts, "injector_client_id": CLIENT,
        "quest_id": "269", "expected_dialog_name": "чёрного мага Вагарда",
        "expected_name": "Башня Вагарда", "npc_id": "10",
        "expected_location_id": "132", "expected_snapshot_id": "area-npcs-mrojmsjx-o",
    }


def write_events(path, *items) -> None:
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items), encoding="utf-8")


def snapshot(generated_at: float, **updates) -> dict[str, object]:
    ref = quest()
    value = {
        "ok": True, "truncated": False, "snapshotId": "npc-dialog-recovery",
        "generatedAt": generated_at, "pageKind": "npc",
        "href": "https://3kingdoms.ru/npc.php?f_id=10", "expectedName": ref.giver_names[0],
        "expectedNpcId": "10", "identityMatches": True, "npcId": "10",
        "matchingHeaders": ["Чёрный маг Вагард"],
        "questActions": [{
            "questId": "269", "npcId": "10", "action": "open", "title": ref.title,
            "visible": True, "disabled": False,
        }],
    }
    value.update(updates)
    return value


def test_real_q269_legacy_event_requires_unique_exact_record_and_fresh_dialog(tmp_path) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, {"event_type": "other"}, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=CLIENT)
    proof = evaluate_legacy_npc_recovery(
        parsed, quest(),
        client={"client_id": CLIENT, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=CLIENT, result_client_id=CLIENT,
        snapshot=snapshot(issued + 21), now=issued + 22,
    )
    assert proof.eligible and proof.pending is not None
    assert proof.pending.npc_name == "Башня Вагарда"
    assert proof.pending.giver_name == "чёрного мага Вагарда"
    assert proof.pending.capability_version == LEGACY_NPC_OPEN_CAPABILITY
    assert proof.pending.stage_source == LEGACY_STAGE_SOURCE
    assert proof.pending.area_timestamp_source == "derived_event_issued_at"
    assert restore_pending_npc_open(serialize_pending_npc_open(proof.pending)) == proof.pending
    chain = QuestChainRuntime()
    chain.stage_accepted_ref(quest())
    with pytest.raises(RuntimeError, match="cannot be staged"):
        chain.stage_npc_open(proof.pending)
    state = tmp_path / "fabricated-open.json"
    persisted = QuestChainRuntime(state_path=state)
    persisted.stage_accepted_ref(quest())
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["pending_npc_open"] = serialize_pending_npc_open(proof.pending)
    state.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be restored"):
        QuestChainRuntime(state_path=state)


def test_reload_session_is_eligible_only_on_same_exact_profile_and_tab(tmp_path) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=NEW_CLIENT)

    proof = evaluate_legacy_npc_recovery(
        parsed, quest(),
        client={"client_id": NEW_CLIENT, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=NEW_CLIENT, result_client_id=NEW_CLIENT,
        snapshot=snapshot(issued + 21), now=issued + 22,
    )

    assert proof.eligible and proof.pending is not None
    assert parsed.client_id == CLIENT and parsed.session_id == "87ea83cbc9a64609"
    assert proof.pending.client_id == NEW_CLIENT


@pytest.mark.parametrize("current", [
    "chrome-profile-deadbeef0000-tab-770945261-session-1111111111111111",
    "chrome-profile-37433a8ded0b-tab-770945262-session-1111111111111111",
])
def test_reload_session_rejects_different_profile_or_tab(tmp_path, current) -> None:
    path = tmp_path / "events.jsonl"
    write_events(path, event(time.time() - 30))
    with pytest.raises(ValueError, match="transport identity"):
        load_legacy_npc_open_event(path, expected_ref=quest(), client_id=current)


def test_reload_session_rejects_result_client_mismatch(tmp_path) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=NEW_CLIENT)

    proof = evaluate_legacy_npc_recovery(
        parsed, quest(),
        client={"client_id": CLIENT, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=NEW_CLIENT, result_client_id=CLIENT,
        snapshot=snapshot(issued + 21), now=issued + 22,
    )

    assert not proof.eligible
    assert proof.reason == "legacy_npc_recovery_identity_mismatch"


@pytest.mark.parametrize("malformed", [None, {}, "event"])
def test_evaluator_malformed_event_is_ineligible_not_exception(malformed) -> None:
    proof = evaluate_legacy_npc_recovery(
        malformed, quest(),  # type: ignore[arg-type]
        client={"client_id": NEW_CLIENT, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=NEW_CLIENT, result_client_id=NEW_CLIENT,
        snapshot={}, now=time.time(),
    )
    assert not proof.eligible and proof.reason == "legacy_npc_recovery_proof_invalid"


@pytest.mark.parametrize("malformed", [None, {}, "quest"])
def test_evaluator_malformed_ref_is_ineligible_not_exception(tmp_path, malformed) -> None:
    path = tmp_path / "events.jsonl"
    write_events(path, event(time.time() - 30))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=NEW_CLIENT)
    proof = evaluate_legacy_npc_recovery(
        parsed, malformed,  # type: ignore[arg-type]
        client={}, current_client_id=NEW_CLIENT, result_client_id=NEW_CLIENT,
        snapshot={}, now=time.time(),
    )
    assert not proof.eligible and proof.reason == "legacy_npc_recovery_proof_invalid"


@pytest.mark.parametrize("tamper", [
    {"quest_id": "268"},
    {"giver_name": "другой NPC"},
])
def test_evaluator_rejects_event_quest_or_giver_tamper(tmp_path, tamper) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=NEW_CLIENT)

    proof = evaluate_legacy_npc_recovery(
        replace(parsed, **tamper), quest(),
        client={"client_id": NEW_CLIENT, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=NEW_CLIENT, result_client_id=NEW_CLIENT,
        snapshot=snapshot(issued + 21), now=issued + 22,
    )

    assert not proof.eligible and proof.reason == "legacy_npc_recovery_identity_mismatch"


@pytest.mark.parametrize(("target", "updates"), [
    ("event", {"client_id": "bad-client"}),
    ("event", {"profile_id": ""}),
    ("event", {"tab_id": False}),
    ("event", {"quest_id": "0"}),
    ("event", {"npc_id": ""}),
    ("event", {"location_id": "x"}),
    ("event", {"giver_name": "x" * 181}),
    ("event", {"npc_name": "x" * 181}),
    ("event", {"area_snapshot_id": "invalid-snapshot"}),
    ("event", {"area_snapshot_id": "area-npcs-" + "x" * 111}),
    ("event", {"issued_at": float("nan")}),
    ("ref", {"id": ""}),
    ("ref", {"title": ""}),
    ("ref", {"title": "x" * 501}),
    ("ref", {"location": None}),
    ("ref", {"location": "x" * 181}),
    ("ref", {"catalog_page": None}),
    ("ref", {"catalog_page": False}),
    ("ref", {"catalog_page": -1}),
    ("ref", {"accept_ref": "invalid"}),
    ("ref", {"accept_ref": "0"}),
])
def test_evaluator_typed_invalid_event_and_ref_matrix(tmp_path, target, updates) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=NEW_CLIENT)
    candidate_event = replace(parsed, **updates) if target == "event" else parsed
    candidate_ref = replace(quest(), **updates) if target == "ref" else quest()

    proof = evaluate_legacy_npc_recovery(
        candidate_event, candidate_ref,
        client={"client_id": NEW_CLIENT, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=NEW_CLIENT, result_client_id=NEW_CLIENT,
        snapshot=snapshot(issued + 21), now=issued + 22,
    )

    assert not proof.eligible and proof.reason == "legacy_npc_recovery_proof_invalid"


@pytest.mark.parametrize("error", [TypeError("typed"), ValueError("value")])
def test_evaluator_pending_constructor_failure_is_stable_ineligible(
    tmp_path, monkeypatch, error,
) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=NEW_CLIENT)
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_npc_legacy_recovery.make_pending_npc_open",
        lambda **kwargs: (_ for _ in ()).throw(error),
    )

    proof = evaluate_legacy_npc_recovery(
        parsed, quest(),
        client={"client_id": NEW_CLIENT, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=NEW_CLIENT, result_client_id=NEW_CLIENT,
        snapshot=snapshot(issued + 21), now=issued + 22,
    )

    assert not proof.eligible and proof.reason == "legacy_npc_recovery_pending_invalid"


@pytest.mark.parametrize("ref", [
    QuestRef("269", "Вступление в Чёрную лигу", None, "След Велета", (), 0),
    QuestRef("269", "Вступление в Чёрную лигу", None, "След Велета", ("один", "два"), 0),
])
def test_evaluator_requires_one_exact_giver_ref(tmp_path, ref) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=NEW_CLIENT)

    proof = evaluate_legacy_npc_recovery(
        parsed, ref,
        client={"client_id": NEW_CLIENT, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=NEW_CLIENT, result_client_id=NEW_CLIENT,
        snapshot=snapshot(issued + 21), now=issued + 22,
    )

    assert not proof.eligible and proof.reason == "legacy_npc_recovery_proof_invalid"


@pytest.mark.parametrize("identity", [
    {"client_id": CLIENT, "profile_id": PROFILE, "tab_id": TAB},
    {"client_id": NEW_CLIENT, "profile_id": "chrome-profile-deadbeef0000", "tab_id": TAB},
    {"client_id": NEW_CLIENT, "profile_id": PROFILE, "tab_id": TAB + 1},
])
def test_evaluator_rejects_snapshot_client_profile_or_tab_mismatch(tmp_path, identity) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=NEW_CLIENT)

    proof = evaluate_legacy_npc_recovery(
        parsed, quest(), client=identity,
        current_client_id=NEW_CLIENT, result_client_id=NEW_CLIENT,
        snapshot=snapshot(issued + 21), now=issued + 22,
    )

    assert not proof.eligible and proof.reason == "legacy_npc_recovery_identity_mismatch"


def test_evaluator_rejects_malformed_current_session(tmp_path) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=NEW_CLIENT)
    malformed = f"{PROFILE}-tab-{TAB}-session-not-hex"

    proof = evaluate_legacy_npc_recovery(
        parsed, quest(), client={"client_id": malformed, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=malformed, result_client_id=malformed,
        snapshot=snapshot(issued + 21), now=issued + 22,
    )

    assert not proof.eligible and proof.reason == "legacy_npc_recovery_identity_mismatch"


@pytest.mark.parametrize("change", [
    {"dry_run": True}, {"injector_client_id": "wrong"}, {"quest_id": "268"},
    {"block_reason": "injector_open_exact_npc_failed:other"},
])
def test_legacy_event_malformed_or_wrong_identity_fails_closed(tmp_path, change) -> None:
    raw = event(time.time() - 30)
    raw.update(change)
    path = tmp_path / "events.jsonl"
    write_events(path, raw)
    with pytest.raises(ValueError):
        load_legacy_npc_open_event(path, expected_ref=quest(), client_id=CLIENT)


def test_multiple_events_and_wrong_current_proof_are_ineligible(tmp_path) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued), event(issued))
    with pytest.raises(ValueError, match="unique"):
        load_legacy_npc_open_event(path, expected_ref=quest(), client_id=CLIENT)
    parsed_path = tmp_path / "one.jsonl"
    write_events(parsed_path, event(issued))
    parsed = load_legacy_npc_open_event(parsed_path, expected_ref=quest(), client_id=CLIENT)
    proof = evaluate_legacy_npc_recovery(
        parsed, quest(), client={"client_id": CLIENT, "profile_id": PROFILE, "tab_id": TAB},
        current_client_id=CLIENT, result_client_id=CLIENT,
        snapshot=snapshot(issued + 21, questActions=[]), now=issued + 22,
    )
    assert not proof.eligible


def test_current_snapshot_must_be_recent_and_not_from_future(tmp_path) -> None:
    issued = time.time() - 30
    path = tmp_path / "events.jsonl"
    write_events(path, event(issued))
    parsed = load_legacy_npc_open_event(path, expected_ref=quest(), client_id=CLIENT)
    for generated, now in ((issued + 21, issued + 27), (issued + 29, issued + 28)):
        proof = evaluate_legacy_npc_recovery(
            parsed, quest(), client={"client_id": CLIENT, "profile_id": PROFILE, "tab_id": TAB},
            current_client_id=CLIENT, result_client_id=CLIENT,
            snapshot=snapshot(generated), now=now,
        )
        assert not proof.eligible and proof.reason == "legacy_npc_recovery_snapshot_stale"


class FakeInjector:
    def __init__(self, payload, on_execute=None, *, result_client_id=CLIENT, profile_id=PROFILE, tab_id=TAB):
        self.payload = payload
        self.on_execute = on_execute
        self.result_client_id = result_client_id
        self.profile_id = profile_id
        self.tab_id = tab_id
        self.calls = []

    def start(self):
        pass

    def execute(self, command, payload=None, **kwargs):
        self.calls.append((command, payload, kwargs))
        if self.on_execute is not None:
            self.on_execute()
        return InjectorResult(True, json.dumps(self.payload, ensure_ascii=False), self.result_client_id)

    def client_snapshot(self, client_id):
        return {"client_id": client_id, "profile_id": self.profile_id, "tab_id": self.tab_id}


def cli_setup(
    tmp_path, monkeypatch, *, unrelated_evidence=False, on_execute=None,
    current_client_id=CLIENT, result_client_id=None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    issued = time.time() - 30
    events = tmp_path / "events.jsonl"
    write_events(events, event(issued))
    state = tmp_path / "quest_chains" / "v3g45.json"
    chain = QuestChainRuntime(state_path=state)
    chain.stage_accepted_ref(quest())
    if unrelated_evidence:
        chain.intake_quarantines = (make_intake_quarantine(
            QuestRef("236", "Other", None, "Elsewhere", ("Other giver",), 0),
            reason="other_reason", capability_version="quest_intake_v1", recorded_at=time.time(),
        ),)
        chain.quarantines = (QuarantinedQuest(
            "263", "Other objective", "f" * 64, "other_reason", "objective_router_v2", time.time(),
        ),)
        chain._persist()
    injector = FakeInjector(
        snapshot(time.time() - 1), on_execute=on_execute,
        result_client_id=result_client_id or current_client_id,
    )
    monkeypatch.setattr(
        "src.antibot_cv.automation.controller_cli.load_config",
        lambda path: SimpleNamespace(runs_dir=str(tmp_path), leveling=SimpleNamespace(required_character_name="v3g45")),
    )
    monkeypatch.setattr(
        "src.antibot_cv.automation.browser_injector.global_browser_injector", lambda: injector,
    )
    args = argparse.Namespace(config="config.json", client_id=current_client_id, events=str(events), timeout=1.0, apply=False)
    return state, args, injector


def test_cli_reload_session_same_tab_is_eligible_but_result_alias_is_rejected(
    tmp_path, monkeypatch, capsys,
) -> None:
    _, args, _ = cli_setup(tmp_path, monkeypatch, current_client_id=NEW_CLIENT)
    assert command_recover_legacy_npc_open(args) == 0
    assert json.loads(capsys.readouterr().out)["eligible"] is True

    _, args, _ = cli_setup(
        tmp_path / "mismatch", monkeypatch,
        current_client_id=NEW_CLIENT, result_client_id=CLIENT,
    )
    assert command_recover_legacy_npc_open(args) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["reason"] == "legacy_npc_recovery_identity_mismatch"


def test_cli_apply_after_reload_persists_new_session_client_id(
    tmp_path, monkeypatch, capsys,
) -> None:
    state, args, _ = cli_setup(tmp_path, monkeypatch, current_client_id=NEW_CLIENT)
    args.apply = True

    assert command_recover_legacy_npc_open(args) == 0

    assert json.loads(capsys.readouterr().out)["applied"] is True
    pending = QuestChainRuntime(state_path=state).pending_npc_dialog
    assert pending is not None
    assert pending.client_id == NEW_CLIENT
    assert pending.profile_id == PROFILE and pending.tab_id == TAB


@pytest.mark.parametrize("current", [
    "chrome-profile-deadbeef0000-tab-770945261-session-1111111111111111",
    "chrome-profile-37433a8ded0b-tab-770945262-session-1111111111111111",
])
def test_cli_reload_session_rejects_different_profile_or_tab_before_snapshot(
    tmp_path, monkeypatch, capsys, current,
) -> None:
    _, args, injector = cli_setup(tmp_path, monkeypatch, current_client_id=current)

    assert command_recover_legacy_npc_open(args) == 2

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["reason"] == "legacy_npc_recovery_input_invalid"
    assert injector.calls == []


def test_cli_plan_is_zero_write_and_apply_restores_restart_contract(tmp_path, monkeypatch, capsys) -> None:
    state, args, injector = cli_setup(tmp_path, monkeypatch, unrelated_evidence=True)
    assert command_recover_legacy_npc_open(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["mode"] == "plan" and receipt["would_write_pending_npc_dialog"] is True
    assert QuestChainRuntime(state_path=state).pending_npc_dialog is None
    assert injector.calls[0][0] == "npc_dialog_snapshot"

    args.apply = True
    assert command_recover_legacy_npc_open(args) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True
    restored = QuestDirectorRuntime(chain_state_path=state)
    assert restored.pending_accept == quest()
    assert restored.chain.pending_npc_dialog is not None
    assert [item.quest_id for item in restored.chain.intake_quarantines] == ["236"]
    assert [item.quest_id for item in restored.chain.quarantines] == ["263"]


def test_cli_apply_write_failure_rolls_back(tmp_path, monkeypatch, capsys) -> None:
    state, args, _ = cli_setup(tmp_path, monkeypatch)
    args.apply = True
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
    )
    assert command_recover_legacy_npc_open(args) == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["reason"] == "legacy_npc_recovery_checkpoint_write_failed"
    assert QuestChainRuntime(state_path=state).pending_npc_dialog is None


def test_cli_cas_rejects_intervening_ordinary_chain_write(tmp_path, monkeypatch, capsys) -> None:
    holder = {}

    def intervene():
        external = QuestChainRuntime(state_path=holder["state"])
        external.replace_unsupported_available_entries((UnsupportedAvailableQuest(
            "97", "Unsupported", 0, "Elsewhere", (), ("multiple_givers",), "multiple_givers",
        ),))

    state, args, _ = cli_setup(tmp_path, monkeypatch, on_execute=intervene)
    holder["state"] = state
    args.apply = True
    assert command_recover_legacy_npc_open(args) == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["reason"] == "legacy_npc_recovery_checkpoint_write_failed"
    latest = QuestChainRuntime(state_path=state)
    assert latest.pending_npc_dialog is None
    assert [item.quest_id for item in latest.unsupported_available_entries] == ["97"]


def test_stale_ordinary_writer_cannot_overwrite_recovery_and_fresh_writer_preserves_it(
    tmp_path, monkeypatch, capsys,
) -> None:
    state, args, _ = cli_setup(tmp_path, monkeypatch)
    stale = QuestChainRuntime(state_path=state)
    args.apply = True
    assert command_recover_legacy_npc_open(args) == 0
    capsys.readouterr()
    unsupported = UnsupportedAvailableQuest(
        "97", "Unsupported", 0, "Elsewhere", (), ("multiple_givers",), "multiple_givers",
    )
    with pytest.raises(QuestChainCheckpointConflict):
        stale.replace_unsupported_available_entries((unsupported,))
    after_conflict = QuestChainRuntime(state_path=state)
    assert after_conflict.pending_npc_dialog is not None
    assert after_conflict.unsupported_available_entries == ()

    fresh = QuestChainRuntime(state_path=state)
    fresh.replace_unsupported_available_entries((unsupported,))
    final = QuestChainRuntime(state_path=state)
    assert final.pending_npc_dialog is not None
    assert final.unsupported_available_entries == (unsupported,)


def test_all_ordinary_writes_use_baseline_cas_for_absence_updates_and_unlink(tmp_path) -> None:
    state = tmp_path / "ordinary-cas.json"
    first = QuestChainRuntime(state_path=state)
    stale_absent = QuestChainRuntime(state_path=state)
    one = UnsupportedAvailableQuest(
        "97", "One", 0, "Elsewhere", (), ("multiple_givers",), "multiple_givers",
    )
    two = UnsupportedAvailableQuest(
        "98", "Two", 0, "Elsewhere", (), ("missing_location",), "missing_location",
    )
    first.replace_unsupported_available_entries((one,))
    with pytest.raises(QuestChainCheckpointConflict):
        stale_absent.replace_unsupported_available_entries((two,))
    assert stale_absent.unsupported_available_entries == ()
    with pytest.raises(QuestChainCheckpointConflict):
        stale_absent.stage_accepted_ref(quest())
    assert stale_absent.pending_accepted_ref is None
    assert QuestChainRuntime(state_path=state).unsupported_available_entries == (one,)

    stale_before_unlink = QuestChainRuntime(state_path=state)
    first.replace_unsupported_available_entries(())
    assert not state.exists()
    with pytest.raises(QuestChainCheckpointConflict):
        stale_before_unlink.replace_unsupported_available_entries((two,))
    assert stale_before_unlink.unsupported_available_entries == (one,)
    assert not state.exists()


def test_ordinary_chain_write_waits_for_shared_checkpoint_lock(tmp_path) -> None:
    state = tmp_path / "chain.json"
    QuestChainRuntime(state_path=state).stage_accepted_ref(quest())
    finished = threading.Event()

    def ordinary_write():
        runtime = QuestChainRuntime(state_path=state)
        runtime.replace_unsupported_available_entries((UnsupportedAvailableQuest(
            "97", "Unsupported", 0, "Elsewhere", (), ("multiple_givers",), "multiple_givers",
        ),))
        finished.set()

    with checkpoint_lock(state):
        worker = threading.Thread(target=ordinary_write)
        worker.start()
        assert not finished.wait(0.05)
    worker.join(timeout=2)
    assert finished.is_set()
