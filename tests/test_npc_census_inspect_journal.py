from __future__ import annotations

import os
import pytest

from src.antibot_cv.automation.npc_census_inspect_journal import (
    CensusJournalCommitUncertain,
    NpcCensusInspectJournal,
    PendingCensusInspection,
)
from src.antibot_cv.automation.npc_census_live import CensusInspectContract


def contract(endpoint: str = "0") -> CensusInspectContract:
    return CensusInspectContract(
        snapshot_id="area-npcs-epoch-1", location_id="102",
        endpoint_id=endpoint, route_ref="398", endpoint_name="Npc",
        observation_epoch="epoch", observation_revision=1,
        generated_at="2026-07-20T00:00:00Z",
    )


def test_journal_persists_exact_pending_contract_and_clears_with_cas(tmp_path) -> None:
    path = tmp_path / "pending.json"
    record = PendingCensusInspection("profile", 17, contract())
    journal = NpcCensusInspectJournal(path)
    journal.stage(record)

    restored = NpcCensusInspectJournal(path)
    restored.load()
    assert restored.get(("profile", 17)) == record
    assert restored.clear_exact(PendingCensusInspection("profile", 17, contract("1"))) is False
    assert restored.get(("profile", 17)) == record
    assert restored.clear_exact(record) is True


def test_journal_rejects_contract_substitution(tmp_path) -> None:
    journal = NpcCensusInspectJournal(tmp_path / "pending.json")
    journal.stage(PendingCensusInspection("profile", 17, contract()))
    with pytest.raises(ValueError, match="CAS conflict"):
        journal.stage(PendingCensusInspection("profile", 17, contract("1")))


def test_journal_rejects_oversized_or_weakly_typed_input(tmp_path) -> None:
    path = tmp_path / "pending.json"
    path.write_bytes(b" " * 262_145)
    with pytest.raises(ValueError, match="hard cap"):
        NpcCensusInspectJournal(path).load()

    path.write_text('{"schemaVersion":true,"records":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        NpcCensusInspectJournal(path).load()

    journal = NpcCensusInspectJournal(path)
    with pytest.raises(ValueError, match="record is invalid"):
        journal.stage(PendingCensusInspection("x" * 257, 17, contract()))
    with pytest.raises(ValueError, match="record is invalid"):
        journal.stage(PendingCensusInspection("profile", 9_007_199_254_740_992, contract()))


def test_journal_memory_changes_only_after_durable_save(monkeypatch, tmp_path) -> None:
    journal = NpcCensusInspectJournal(tmp_path / "pending.json")
    first = PendingCensusInspection("profile", 17, contract())

    monkeypatch.setattr(journal, "_save", lambda _records: (_ for _ in ()).throw(OSError("fsync")))
    with pytest.raises(OSError, match="fsync"):
        journal.stage(first)
    assert journal.get(first.actor_key) is None

    monkeypatch.undo()
    journal.stage(first)
    monkeypatch.setattr(journal, "_save", lambda _records: (_ for _ in ()).throw(OSError("fsync")))
    with pytest.raises(OSError, match="fsync"):
        journal.clear_exact(first)
    assert journal.get(first.actor_key) == first


def test_directory_fsync_failure_after_replace_is_explicit_commit_uncertain(
    monkeypatch, tmp_path,
) -> None:
    path = tmp_path / "pending.json"
    journal = NpcCensusInspectJournal(path)
    record = PendingCensusInspection("profile", 17, contract())
    real_fsync = os.fsync
    calls = 0

    def fail_directory_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls % 2 == 0:
            raise OSError("directory fsync")
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory_fsync)
    with pytest.raises(CensusJournalCommitUncertain):
        journal.stage(record)
    restored = NpcCensusInspectJournal(path)
    restored.load()
    assert journal.get(record.actor_key) == record
    assert restored.get(record.actor_key) == record

    pre_clear_bytes = path.read_bytes()
    with pytest.raises(CensusJournalCommitUncertain):
        journal.clear_exact(record)
    # Current-process state stays fail-closed even though replace is visible.
    assert journal.get(record.actor_key) == record
    # Model a crash rollback of the non-durable directory entry.
    path.write_bytes(pre_clear_bytes)
    restored.load()
    assert restored.get(record.actor_key) == record
