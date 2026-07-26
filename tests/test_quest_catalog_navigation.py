from __future__ import annotations

import json

import pytest

from src.antibot_cv.automation.quest_catalog_navigation import (
    CatalogNavigationStatus,
    CatalogSettleStatus,
    CatalogSnapshotEvidence,
    make_pending_catalog_navigation,
    parse_catalog_navigation_outcome,
    restore_pending_catalog_navigation,
    serialize_pending_catalog_navigation,
    settle_catalog_snapshot,
)
from src.antibot_cv.automation.quest_chain_runtime import QuestChainRuntime
from src.antibot_cv.automation.quest_director_policy import QuestRef


def _pending(*, issued_at: float = 1000.0):
    return make_pending_catalog_navigation(
        client_id="client-a",
        profile_id="profile-a",
        tab_id=42,
        page=2,
        current_href="https://3kingdoms.ru/main.php",
        baseline_snapshot_id="snapshot-before",
        baseline_generated_at=999.0,
        issued_at=issued_at,
        settle_timeout_s=20,
    )


def _evidence(**overrides):
    values = {
        "client_id": "client-a",
        "profile_id": "profile-a",
        "tab_id": 42,
        "snapshot_id": "snapshot-after",
        "generated_at": 1000.1,
        "load_status": "loaded",
        "truncated": False,
        "page_kind": "quests",
        "mode": "avail",
        "page": 2,
        "href": "https://3kingdoms.ru/user_quest.php?mode=avail&page=2",
    }
    values.update(overrides)
    return CatalogSnapshotEvidence(**values)


def test_pending_catalog_navigation_round_trip_is_strict_and_durable(tmp_path) -> None:
    path = tmp_path / "chain.json"
    pending = _pending()
    chain = QuestChainRuntime(state_path=path)

    chain.stage_catalog_navigation(pending)

    restored = QuestChainRuntime(state_path=path)
    assert restored.pending_catalog_navigation == pending
    restored.clear_catalog_navigation(pending)
    assert not path.exists()
    raw = serialize_pending_catalog_navigation(pending)
    assert restore_pending_catalog_navigation(raw) == pending
    for invalid in ({**raw, "extra": True}, {**raw, "page": "2"}, {**raw, "deadline": 5000.0}):
        with pytest.raises(ValueError):
            restore_pending_catalog_navigation(invalid)


@pytest.mark.parametrize("field", ["baseline_generated_at", "issued_at", "deadline"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_checkpoint_rejects_every_nonfinite_timestamp(field, value) -> None:
    raw = serialize_pending_catalog_navigation(_pending())
    raw[field] = value
    with pytest.raises(ValueError):
        restore_pending_catalog_navigation(raw)


@pytest.mark.parametrize(
    "changes",
    [
        {"baseline_generated_at": 1000.1, "issued_at": 1000.0},
        {"issued_at": 1000.0, "deadline": 1000.0},
        {"issued_at": 1000.0, "deadline": 1120.1},
    ],
)
def test_checkpoint_rejects_invalid_causal_timestamp_order(changes) -> None:
    raw = serialize_pending_catalog_navigation(_pending())
    raw.update(changes)
    with pytest.raises(ValueError):
        restore_pending_catalog_navigation(raw)


@pytest.mark.parametrize(
    "destination",
    [
        "http://3kingdoms.ru/user_quest.php?mode=avail&page=2",
        "https://www.3kingdoms.ru/user_quest.php?mode=avail&page=2",
        "https://3kingdoms.ru.evil.test/user_quest.php?mode=avail&page=2",
        "https://evil3kingdoms.ru/user_quest.php?mode=avail&page=2",
        "https://3kingdoms.ru:443/user_quest.php?mode=avail&page=2",
        "https://3KINGDOMS.ru/user_quest.php?mode=avail&page=2",
        "https://user@3kingdoms.ru/user_quest.php?mode=avail&page=2",
        "https://example.com/user_quest.php?mode=avail&page=2",
    ],
)
def test_pending_checkpoint_rejects_noncanonical_game_origin(destination) -> None:
    raw = serialize_pending_catalog_navigation(_pending())
    raw["destination"] = destination
    with pytest.raises(ValueError):
        restore_pending_catalog_navigation(raw)


def test_pending_creation_rejects_foreign_current_origin() -> None:
    with pytest.raises(ValueError):
        make_pending_catalog_navigation(
            client_id="client-a", profile_id="profile-a", tab_id=42, page=2,
            current_href="https://example.com/main.php", baseline_snapshot_id="before",
            baseline_generated_at=999.0, issued_at=1000.0,
        )


@pytest.mark.parametrize("generated_at", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_snapshot_generated_at_never_accepts(generated_at) -> None:
    decision = settle_catalog_snapshot(
        _pending(), _evidence(generated_at=generated_at), now=1001.0
    )
    assert decision.status is CatalogSettleStatus.WAIT


@pytest.mark.parametrize("now", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_settle_clock_stops_deterministically(now) -> None:
    decision = settle_catalog_snapshot(_pending(), _evidence(), now=now)
    assert decision.status is CatalogSettleStatus.STOP_EXPIRED
    assert decision.reason == "catalog_navigation_clock_invalid"


def test_expired_exact_snapshot_stops_before_accept() -> None:
    decision = settle_catalog_snapshot(_pending(), _evidence(), now=1020.0)
    assert decision.status is CatalogSettleStatus.STOP_EXPIRED


@pytest.mark.parametrize("generated_at", [1002.0, 1020.1])
def test_future_snapshot_timestamp_never_accepts(generated_at) -> None:
    decision = settle_catalog_snapshot(
        _pending(), _evidence(generated_at=generated_at), now=1001.0
    )
    assert decision.status is CatalogSettleStatus.WAIT


def test_catalog_navigation_persistence_failure_rolls_memory_back(tmp_path, monkeypatch) -> None:
    path = tmp_path / "chain.json"
    pending = _pending()
    chain = QuestChainRuntime(state_path=path)

    def fail_write(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr("src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint", fail_write)
    with pytest.raises(OSError):
        chain.stage_catalog_navigation(pending)
    assert chain.pending_catalog_navigation is None
    assert not path.exists()


def test_catalog_navigation_clear_failure_keeps_durable_pending(tmp_path, monkeypatch) -> None:
    path = tmp_path / "chain.json"
    pending = _pending()
    chain = QuestChainRuntime(state_path=path)
    chain.stage_accepted_ref(QuestRef("7", "Quest 7", "accept-7", "Wilds", ("Frank",), 0))
    chain.stage_catalog_navigation(pending)

    def fail_write(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr("src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint", fail_write)
    with pytest.raises(OSError):
        chain.clear_catalog_navigation(pending)
    assert chain.pending_catalog_navigation == pending
    assert QuestChainRuntime(state_path=path).pending_catalog_navigation == pending


@pytest.mark.parametrize(
    ("overrides", "now", "expected"),
    [
        ({}, 1001.0, CatalogSettleStatus.ACCEPT),
        ({"snapshot_id": "snapshot-before"}, 1001.0, CatalogSettleStatus.WAIT),
        ({"generated_at": 999.5}, 1001.0, CatalogSettleStatus.WAIT),
        ({"page": 1}, 1001.0, CatalogSettleStatus.WAIT),
        ({"mode": "started"}, 1001.0, CatalogSettleStatus.WAIT),
        ({"load_status": "not_loaded"}, 1001.0, CatalogSettleStatus.WAIT),
        ({"truncated": True}, 1001.0, CatalogSettleStatus.WAIT),
        ({"href": "https://3kingdoms.ru/user_quest.php?mode=avail&page=1"}, 1001.0, CatalogSettleStatus.WAIT),
        ({"page": 1}, 1020.0, CatalogSettleStatus.STOP_EXPIRED),
        ({"client_id": "client-b"}, 1001.0, CatalogSettleStatus.STOP_IDENTITY),
        ({"profile_id": "profile-b"}, 1001.0, CatalogSettleStatus.STOP_IDENTITY),
        ({"tab_id": 43}, 1001.0, CatalogSettleStatus.STOP_IDENTITY),
    ],
)
def test_catalog_snapshot_settle_matrix(overrides, now, expected) -> None:
    assert settle_catalog_snapshot(_pending(), _evidence(**overrides), now=now).status is expected


def test_bridge_outcome_preserves_ack_pending_and_transport_ambiguity() -> None:
    structured = json.dumps(
        {
            "outcome": "ACK_PENDING",
            "mutationIssued": True,
            "destination": "/user_quest.php?mode=avail&page=2",
            "message": "quest_catalog_open_unconfirmed",
            "before": {"href": "https://3kingdoms.ru/main.php"},
            "after": {"href": "https://3kingdoms.ru/user_quest.php?mode=avail&page=2"},
            "shellLoaded": False,
        }
    )
    outcome = parse_catalog_navigation_outcome(
        result_ok=True, message=structured, client_id="client-a", destination="ignored"
    )
    assert outcome.status is CatalogNavigationStatus.ACK_PENDING
    assert outcome.dispatched is True
    timeout = parse_catalog_navigation_outcome(
        result_ok=False,
        message="injector_ack_timeout",
        client_id="client-a",
        destination="/user_quest.php?mode=avail&page=2",
    )
    assert timeout.status is CatalogNavigationStatus.ACK_PENDING
    delivery = parse_catalog_navigation_outcome(
        result_ok=False,
        message="injector_delivery_timeout",
        client_id="client-a",
        destination="/user_quest.php?mode=avail&page=2",
    )
    assert delivery.status is CatalogNavigationStatus.NOT_ISSUED


@pytest.mark.parametrize(
    ("result_ok", "values", "expected"),
    [
        (False, {"outcome": "CONFIRMED", "mutationIssued": True, "shellLoaded": True}, CatalogNavigationStatus.ACK_PENDING),
        (True, {"outcome": "CONFIRMED", "mutationIssued": True, "shellLoaded": False}, CatalogNavigationStatus.ACK_PENDING),
        (True, {"outcome": "CONFIRMED", "mutationIssued": False, "shellLoaded": True}, CatalogNavigationStatus.NOT_ISSUED),
        (True, {"outcome": "CONFIRMED", "mutationIssued": True, "shellLoaded": True, "destination": "/user_quest.php?mode=avail&page=9"}, CatalogNavigationStatus.ACK_PENDING),
    ],
)
def test_malformed_structured_outcome_never_becomes_confirmed(result_ok, values, expected) -> None:
    payload = {
        "message": "quest_catalog_opened_confirmed",
        "destination": "/user_quest.php?mode=avail&page=2",
        **values,
    }
    outcome = parse_catalog_navigation_outcome(
        result_ok=result_ok,
        message=json.dumps(payload),
        client_id="client-a",
        destination="/user_quest.php?mode=avail&page=2",
    )
    assert outcome.status is expected


def test_already_open_confirmed_requires_exact_destination_and_loaded_shell() -> None:
    outcome = parse_catalog_navigation_outcome(
        result_ok=True,
        message=json.dumps({
            "outcome": "CONFIRMED", "mutationIssued": False,
            "message": "quest_catalog_already_open", "shellLoaded": True,
            "destination": "/user_quest.php?mode=avail&page=2",
        }),
        client_id="client-a",
        destination="/user_quest.php?mode=avail&page=2",
    )
    assert outcome.status is CatalogNavigationStatus.CONFIRMED
