from __future__ import annotations

import argparse
import json
import time
from types import SimpleNamespace

import pytest

from src.antibot_cv.automation.browser_injector import InjectorResult
from src.antibot_cv.automation.controller_cli import command_recover_catalog_navigation
from src.antibot_cv.automation.quest_available_eligibility import classify_available_quest_refs
from src.antibot_cv.automation.quest_catalog_navigation import make_pending_catalog_navigation
from src.antibot_cv.automation.quest_catalog_recovery import evaluate_catalog_recovery
from src.antibot_cv.automation.quest_chain_runtime import QuestChainRuntime
from src.antibot_cv.automation.quest_director_policy import QuestRef


def _pending(*, issued_at: float = 1000.0):
    return make_pending_catalog_navigation(
        client_id="client-a", profile_id="profile-a", tab_id=42, page=2,
        current_href="https://3kingdoms.ru/main.php", baseline_snapshot_id="before",
        baseline_generated_at=issued_at - 1, issued_at=issued_at, settle_timeout_s=20,
    )


def _snapshot(pending, **data_overrides):
    generated_at = data_overrides.pop("generatedAt", pending.deadline + 1)
    snapshot_id = data_overrides.pop("snapshotId", "after")
    data = {
        "loadStatus": "loaded", "truncated": False, "pageKind": "quests",
        "mode": "avail", "currentPage": pending.page, "href": pending.destination,
        "snapshotId": snapshot_id, "generatedAt": generated_at,
        **data_overrides,
    }
    return {
        "snapshotId": snapshot_id,
        "generatedAt": generated_at,
        "sections": {"quests": {"data": data}},
    }


def _proof(pending, *, snapshot=None, now=1030.0, result_client_id="client-a", client=None):
    return evaluate_catalog_recovery(
        pending,
        client=client or {"client_id": "client-a", "profile_id": "profile-a", "tab_id": 42},
        result_client_id=result_client_id,
        snapshot=_snapshot(pending) if snapshot is None else snapshot,
        now=now,
    )


def test_expired_catalog_recovery_accepts_only_exact_post_stop_proof() -> None:
    proof = _proof(_pending())
    assert proof.eligible is True
    assert proof.reason == "catalog_recovery_exact_post_stop_proof"


def test_catalog_recovery_requires_snapshot_at_or_after_deadline() -> None:
    pending = _pending()
    before = _proof(
        pending,
        snapshot=_snapshot(pending, generatedAt=pending.deadline - 0.001),
    )
    exact = _proof(
        pending,
        snapshot=_snapshot(pending, generatedAt=pending.deadline),
    )
    assert before.eligible is False
    assert exact.eligible is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"result_client_id": "client-b"},
        {"client": {"client_id": "client-a", "profile_id": "profile-b", "tab_id": 42}},
        {"client": {"client_id": "client-a", "profile_id": "profile-a", "tab_id": 43}},
        {"client": {"client_id": "client-a", "profile_id": "", "tab_id": 42}},
        {"client": {"client_id": "client-b", "profile_id": "profile-a", "tab_id": 42}},
        {"client": {"client_id": "", "profile_id": "profile-a", "tab_id": 42}},
    ],
)
def test_catalog_recovery_rejects_wrong_or_missing_identity(kwargs) -> None:
    assert _proof(_pending(), **kwargs).eligible is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"currentPage": 1},
        {"mode": "started"},
        {"href": "https://3kingdoms.ru/user_quest.php?mode=avail&page=1"},
        {"href": "/user_quest.php?mode=avail&page=2"},
        {"loadStatus": "not_loaded"},
        {"truncated": True},
        {"pageKind": "area"},
    ],
)
def test_catalog_recovery_rejects_wrong_catalog_shape(overrides) -> None:
    pending = _pending()
    assert _proof(pending, snapshot=_snapshot(pending, **overrides)).eligible is False


@pytest.mark.parametrize(
    "href",
    [
        "http://3kingdoms.ru/user_quest.php?mode=avail&page=2",
        "https://www.3kingdoms.ru/user_quest.php?mode=avail&page=2",
        "https://3kingdoms.ru.evil.test/user_quest.php?mode=avail&page=2",
        "https://3kingdoms.ru:443/user_quest.php?mode=avail&page=2",
        "https://3KINGDOMS.ru/user_quest.php?mode=avail&page=2",
        "https://user@3kingdoms.ru/user_quest.php?mode=avail&page=2",
        "https://example.com/user_quest.php?mode=avail&page=2",
    ],
)
def test_catalog_recovery_rejects_noncanonical_game_origin(href) -> None:
    pending = _pending()
    assert _proof(pending, snapshot=_snapshot(pending, href=href)).eligible is False


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        {"snapshotId": "after", "generatedAt": 1021.0, "sections": {}},
    ],
)
def test_catalog_recovery_rejects_missing_or_malformed_snapshot(snapshot) -> None:
    pending = _pending()
    actual = "not-an-object" if snapshot is None else snapshot
    assert _proof(pending, snapshot=actual).eligible is False


@pytest.mark.parametrize("generated_at", [999.0, float("nan"), float("inf"), float("-inf")])
def test_catalog_recovery_rejects_stale_or_nonfinite_snapshot(generated_at) -> None:
    pending = _pending()
    assert _proof(pending, snapshot=_snapshot(pending, generatedAt=generated_at)).eligible is False


def test_catalog_recovery_rejects_baseline_snapshot_and_not_expired_stage() -> None:
    pending = _pending()
    assert _proof(pending, snapshot=_snapshot(pending, snapshotId="before")).eligible is False
    assert _proof(pending, now=1019.0).eligible is False
    assert _proof(pending, now=float("nan")).eligible is False


class _FakeInjector:
    def __init__(self, pending) -> None:
        self.pending = pending
        self.calls = []

    def start(self) -> None:
        return None

    def execute(self, command, payload=None, *, timeout_s=2.5, client_id=None):
        self.calls.append((command, payload, timeout_s, client_id))
        return InjectorResult(True, json.dumps(_snapshot(self.pending)), "client-a")

    def client_snapshot(self, client_id):
        return {"client_id": client_id, "profile_id": "profile-a", "tab_id": 42}


def _cli_setup(tmp_path, monkeypatch, *, keep_other_evidence=False):
    now = time.time()
    pending = _pending(issued_at=now - 21)
    path = tmp_path / "quest_chains" / "v3g45.json"
    chain = QuestChainRuntime(state_path=path)
    if keep_other_evidence:
        item = classify_available_quest_refs((
            QuestRef("91", "Incomplete", location="Wilds", giver_names=(), catalog_page=0),
        )).unsupported[0]
        chain.replace_unsupported_available_entries((item,))
    chain.stage_catalog_navigation(pending)
    injector = _FakeInjector(pending)
    config = SimpleNamespace(
        runs_dir=str(tmp_path),
        leveling=SimpleNamespace(required_character_name="v3g45"),
    )
    monkeypatch.setattr("src.antibot_cv.automation.controller_cli.load_config", lambda path: config)
    monkeypatch.setattr("src.antibot_cv.automation.browser_injector.global_browser_injector", lambda: injector)
    args = argparse.Namespace(config="config.json", client_id="client-a", timeout=1.0, apply=False)
    return path, pending, injector, args


def test_recovery_cli_defaults_to_zero_write_plan(tmp_path, monkeypatch, capsys) -> None:
    path, pending, injector, args = _cli_setup(tmp_path, monkeypatch)

    assert command_recover_catalog_navigation(args) == 0

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["mode"] == "plan" and receipt["would_clear"] is True
    assert receipt["applied"] is False
    assert QuestChainRuntime(state_path=path).pending_catalog_navigation == pending
    assert injector.calls[0][0] == "state_snapshot"


def test_recovery_cli_apply_clears_only_exact_stage(tmp_path, monkeypatch, capsys) -> None:
    path, pending, injector, args = _cli_setup(tmp_path, monkeypatch)
    args.apply = True

    assert command_recover_catalog_navigation(args) == 0

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["applied"] is True
    assert not path.exists()


def test_recovery_cli_persistence_failure_keeps_pending(tmp_path, monkeypatch, capsys) -> None:
    path, pending, injector, args = _cli_setup(tmp_path, monkeypatch, keep_other_evidence=True)
    args.apply = True

    def fail_write(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr("src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint", fail_write)
    assert command_recover_catalog_navigation(args) == 2

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["reason"] == "catalog_recovery_checkpoint_write_failed"
    assert QuestChainRuntime(state_path=path).pending_catalog_navigation == pending
