import json
from types import MappingProxyType

import pytest

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_active_catalog_navigation import (
    PendingActiveCatalogNavigation,
)
from src.antibot_cv.automation.quest_chain_runtime import (
    QuestChainCheckpointConflict,
    QuestChainRuntime,
)
from src.antibot_cv.automation.quest_local_block_journal import LocalBlockEnsureState
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint


KEY = {
    "quest_id": "31",
    "quest_title": "Quest 31",
    "fingerprint": "exact-step-fingerprint",
    "phase": "turn_in",
    "capability_version": "semantic_turn_in.v1",
}


def _ensure(chain: QuestChainRuntime, authority_id: str, *, reason: str = "npc_missing"):
    return chain.ensure_local_block(
        **KEY, reason=reason, authority_id=authority_id, max_attempts=2,
    )


def _pending_navigation() -> PendingActiveCatalogNavigation:
    return PendingActiveCatalogNavigation(
        "client", "profile", 1, "started", 0,
        "https://3kingdoms.ru/user_quest.php?mode=started&page=0",
        "https://3kingdoms.ru/main.php", "snapshot-1", 10.0, "rev-1",
        11.0, 21.0,
    )


def test_local_block_counts_distinct_authorities_and_reason_is_not_identity(tmp_path) -> None:
    path = tmp_path / "chain.json"
    chain = QuestChainRuntime(state_path=path)

    first = _ensure(chain, "snapshot:rev-1")
    duplicate = _ensure(chain, "snapshot:rev-1", reason="dialog_closed")
    exhausted = _ensure(chain, "snapshot:rev-2", reason="dialog_closed")
    assert (first.state, first.attempts, first.counted) == (
        LocalBlockEnsureState.REFRESH_REQUIRED, 1, True,
    )
    assert (duplicate.state, duplicate.attempts, duplicate.counted) == (
        LocalBlockEnsureState.REFRESH_REQUIRED, 1, False,
    )
    assert (exhausted.state, exhausted.attempts, exhausted.counted) == (
        LocalBlockEnsureState.QUARANTINE_REQUIRED, 2, True,
    )

    restored = QuestChainRuntime(state_path=path)
    assert restored.local_blocks[0].authority_ids == ("snapshot:rev-1", "snapshot:rev-2")
    assert restored.local_blocks[0].reason == "dialog_closed"
    restarted = _ensure(restored, "snapshot:rev-2")
    assert restarted.state is LocalBlockEnsureState.QUARANTINE_REQUIRED
    assert (restarted.attempts, restarted.counted) == (2, False)


def test_restart_before_refresh_dispatch_still_requires_refresh(tmp_path) -> None:
    path = tmp_path / "chain.json"
    chain = QuestChainRuntime(state_path=path)
    assert _ensure(chain, "snapshot:rev-1").state is LocalBlockEnsureState.REFRESH_REQUIRED
    restarted = _ensure(QuestChainRuntime(state_path=path), "snapshot:rev-1")
    assert restarted.state is LocalBlockEnsureState.REFRESH_REQUIRED
    assert restarted.counted is False


def test_staged_navigation_precedes_refresh_but_exhaustion_precedes_staged(tmp_path) -> None:
    chain = QuestChainRuntime(state_path=tmp_path / "chain.json")
    _ensure(chain, "snapshot:rev-1")
    chain.stage_active_catalog_navigation(_pending_navigation())

    restarted = QuestChainRuntime(state_path=tmp_path / "chain.json")
    staged = _ensure(restarted, "snapshot:rev-1")
    exhausted = _ensure(restarted, "snapshot:rev-2")

    assert staged.state is LocalBlockEnsureState.STAGED
    assert staged.counted is False
    assert exhausted.state is LocalBlockEnsureState.QUARANTINE_REQUIRED
    assert exhausted.counted is True


def test_local_block_clear_and_cas_conflict_restore_durable_winner(tmp_path) -> None:
    path = tmp_path / "chain.json"
    stale = QuestChainRuntime(state_path=path)
    winner = QuestChainRuntime(state_path=path)
    _ensure(winner, "snapshot:rev-1")

    with pytest.raises(QuestChainCheckpointConflict):
        _ensure(stale, "snapshot:stale")
    assert stale.local_blocks == ()

    restored = QuestChainRuntime(state_path=path)
    assert restored.clear_local_block(
        KEY["quest_id"], KEY["fingerprint"], phase=KEY["phase"],
        capability_version=KEY["capability_version"],
    ) is True
    assert not path.exists()


@pytest.mark.parametrize("field,value", [
    ("attempts", 2),
    ("authority_ids", ["UPPERCASE"]),
    ("refresh_claimed", "yes"),
])
def test_restore_rejects_malformed_local_block(field, value) -> None:
    raw = {
        "quest_id": "31", "quest_title": "Quest 31",
        "fingerprint": "exact-step-fingerprint", "phase": "turn_in",
        "capability_version": "semantic_turn_in.v1", "reason": "npc_missing",
        "authority_ids": ["snapshot:rev-1"], "attempts": 1,
        "refresh_claimed": False,
    }
    raw[field] = value
    with pytest.raises(ValueError, match="local block"):
        QuestChainRuntime().restore({"local_blocks": [raw]})


def test_restore_enforces_local_block_cap() -> None:
    raw = json.loads(json.dumps({
        "quest_id": "31", "quest_title": "Quest 31", "fingerprint": "one",
        "phase": "turn_in", "capability_version": "semantic_turn_in.v1",
        "reason": "npc_missing", "authority_ids": ["snapshot:rev-1"],
        "attempts": 1, "refresh_claimed": False,
    }))
    second = {**raw, "quest_id": "32", "quest_title": "Quest 32", "fingerprint": "two"}
    with pytest.raises(ValueError, match="local block history"):
        QuestChainRuntime(max_local_blocks=1).restore({"local_blocks": [raw, second]})


def test_legacy_refresh_claim_is_accepted_but_ignored() -> None:
    raw = {
        "quest_id": "31", "quest_title": "Quest 31",
        "fingerprint": "exact-step-fingerprint", "phase": "turn_in",
        "capability_version": "semantic_turn_in.v1", "reason": "npc_missing",
        "authority_ids": ["snapshot:rev-1"], "attempts": 1,
        "refresh_claimed": True,
    }
    chain = QuestChainRuntime()
    chain.restore({"local_blocks": [raw]})
    result = _ensure(chain, "snapshot:rev-1")
    assert result.state is LocalBlockEnsureState.REFRESH_REQUIRED
    assert "refresh_claimed" not in chain.checkpoint()["local_blocks"][0]


def test_quarantine_persist_failure_rolls_back_matching_local_block(monkeypatch) -> None:
    data = MappingProxyType({
        "id": "31", "title": "Quest 31", "status": "active",
        "objective": "Убить Волк [5]",
        "navigation": (MappingProxyType({"text": "Волк [5]", "target": "Волк [5]"}),),
        "progress": None,
    })
    entry = ActiveQuestEntry("31", "Quest 31", data)
    fingerprint, _ = quest_step_fingerprint(entry)
    assert fingerprint is not None
    chain = QuestChainRuntime()
    chain.ensure_local_block(
        "31", "Quest 31", fingerprint, phase="turn_in",
        capability_version="semantic_turn_in.v1", reason="npc_missing",
        authority_id="snapshot:rev-1",
    )
    monkeypatch.setattr(chain, "_persist", lambda: (_ for _ in ()).throw(OSError("disk")))

    with pytest.raises(OSError, match="disk"):
        chain.quarantine_entry(
            entry, reason="turn_in_npc_missing",
            capability_version="semantic_turn_in.v1", recorded_at=10.0,
        )

    assert len(chain.local_blocks) == 1
    assert chain.quarantines == ()
