from __future__ import annotations

import pytest

from src.antibot_cv.automation.quest_active_catalog_navigation import (
    ActiveCatalogSnapshotEvidence,
    make_pending_active_catalog_navigation,
    restore_pending_active_catalog_navigation,
    serialize_pending_active_catalog_navigation,
    settle_active_catalog_snapshot,
)
from src.antibot_cv.automation.quest_catalog_navigation import CatalogSettleStatus
from src.antibot_cv.automation.quest_catalog_navigation import make_pending_catalog_navigation
from src.antibot_cv.automation.quest_chain_runtime import (
    QuestChainCheckpointConflict,
    QuestChainRuntime,
)
from src.antibot_cv.automation.quest_director_policy import QuestRef


def _pending(*, current_href="https://3kingdoms.ru/main.php", baseline_revision=""):
    return make_pending_active_catalog_navigation(
        client_id="client-a", profile_id="profile-a", tab_id=7, page=1,
        current_href=current_href, baseline_snapshot_id="before",
        baseline_generated_at=999.0, baseline_revision=baseline_revision,
        issued_at=1000.0, settle_timeout_s=20,
    )


def _evidence(**changes):
    values = dict(
        client_id="client-a", profile_id="profile-a", tab_id=7,
        snapshot_id="after", generated_at=1000.1, load_status="loaded",
        truncated=False, page_kind="quests", mode="started", page=1,
        href="https://3kingdoms.ru/user_quest.php?mode=started&page=1", revision="",
    )
    values.update(changes)
    return ActiveCatalogSnapshotEvidence(**values)


def test_active_navigation_round_trip_restart_and_exact_clear(tmp_path) -> None:
    path = tmp_path / "chain.json"
    pending = _pending()
    chain = QuestChainRuntime(state_path=path)
    chain.stage_active_catalog_navigation(pending)
    restored = QuestChainRuntime(state_path=path)
    assert restored.pending_active_catalog_navigation == pending
    with pytest.raises(RuntimeError):
        restored.clear_active_catalog_navigation(_pending(current_href="https://3kingdoms.ru/area.php"))
    restored.clear_active_catalog_navigation(pending)
    assert not path.exists()
    raw = serialize_pending_active_catalog_navigation(pending)
    assert restore_pending_active_catalog_navigation(raw) == pending
    with pytest.raises(ValueError):
        restore_pending_active_catalog_navigation({**raw, "mode": "avail"})


def test_stage_and_clear_write_failures_roll_memory_back(tmp_path, monkeypatch) -> None:
    path = tmp_path / "chain.json"
    pending = _pending()
    chain = QuestChainRuntime(state_path=path)
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    with pytest.raises(OSError):
        chain.stage_active_catalog_navigation(pending)
    assert chain.pending_active_catalog_navigation is None
    monkeypatch.undo()
    chain.stage_accepted_ref(QuestRef("7", "Quest 7", "accept-7", "Wilds", ("Frank",), 0))
    chain.stage_active_catalog_navigation(pending)
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_chain_runtime.write_json_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    with pytest.raises(OSError):
        chain.clear_active_catalog_navigation(pending)
    assert chain.pending_active_catalog_navigation == pending


def test_stage_conflict_is_fail_closed() -> None:
    chain = QuestChainRuntime()
    chain.stage_active_catalog_navigation(_pending())
    with pytest.raises(RuntimeError):
        chain.stage_catalog_navigation(make_pending_catalog_navigation(
            client_id="client-a", profile_id="profile-a", tab_id=7, page=1,
            current_href="https://3kingdoms.ru/main.php", baseline_snapshot_id="before",
            baseline_generated_at=999.0, issued_at=1000.0,
        ))


def test_checkpoint_conflict_restores_durable_winner_for_stage_and_cleanup(tmp_path) -> None:
    path = tmp_path / "chain.json"
    stale = QuestChainRuntime(state_path=path)
    winner = QuestChainRuntime(state_path=path)
    pending = _pending()
    winner.stage_active_catalog_navigation(pending)
    with pytest.raises(QuestChainCheckpointConflict):
        stale.stage_active_catalog_navigation(_pending(current_href="https://3kingdoms.ru/area.php"))
    assert stale.pending_active_catalog_navigation is None

    stale_cleanup = QuestChainRuntime(state_path=path)
    winner.clear_active_catalog_navigation(pending)
    with pytest.raises(QuestChainCheckpointConflict):
        stale_cleanup.clear_active_catalog_navigation(pending)
    assert stale_cleanup.pending_active_catalog_navigation == pending


def test_stale_dom_with_new_snapshot_id_is_rejected_when_already_open() -> None:
    pending = _pending(
        current_href="https://3kingdoms.ru/user_quest.php?mode=started&page=1",
        baseline_revision="doc-1",
    )
    assert settle_active_catalog_snapshot(
        pending, _evidence(snapshot_id="new-wrapper", revision="doc-1"), now=1001.0,
    ).status is CatalogSettleStatus.WAIT
    assert settle_active_catalog_snapshot(
        pending, _evidence(snapshot_id="new-wrapper", revision="doc-2"), now=1001.0,
    ).status is CatalogSettleStatus.ACCEPT


@pytest.mark.parametrize("change", [
    {"client_id": "other"}, {"profile_id": "other"}, {"tab_id": 8},
    {"mode": "avail"}, {"page": 0},
    {"href": "https://3kingdoms.ru/user_quest.php?mode=started&page=0"},
    {"generated_at": 999.9},
])
def test_active_settle_requires_exact_identity_page_href_mode_and_time(change) -> None:
    result = settle_active_catalog_snapshot(_pending(), _evidence(**change), now=1001.0)
    expected = CatalogSettleStatus.STOP_IDENTITY if set(change) <= {"client_id", "profile_id", "tab_id"} else CatalogSettleStatus.WAIT
    assert result.status is expected
