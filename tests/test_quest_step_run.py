from __future__ import annotations

import json

import pytest

from src.antibot_cv.automation.quest_step_run import (
    StagedMutation,
    StepActorIdentity,
    StepRun,
    StepRunJournal,
    StepRunJournalConflict,
    StepRunPhase,
)


def actor(tab: str = "tab-1") -> StepActorIdentity:
    return StepActorIdentity("client-1", "profile-1", tab)


def planned(**overrides: object) -> StepRun:
    values = {
        "capability_version": "quest_executor.v1",
        "quest_id": "q304",
        "plan_fingerprint": "plan-304",
        "step_id": "step-blood",
        "requirement_id": "req-blood",
        "executor_kind": "combat",
        "actor": actor(),
        "baseline_revision": 12,
        "retry_budget": 2,
    }
    values.update(overrides)
    return StepRun(**values)  # type: ignore[arg-type]


def mutation() -> StagedMutation:
    return StagedMutation("mut-1", "battle_target", "sha256:abc", 12)


def test_stage_restore_has_zero_reissue_semantics() -> None:
    staged = planned().stage(mutation())
    restored = StepRun.restore(staged.checkpoint())

    assert restored == staged
    assert restored.phase is StepRunPhase.STAGED
    assert restored.retry_budget == 1
    assert not restored.may_issue_mutation
    with pytest.raises(RuntimeError, match="not issuable"):
        restored.stage(mutation())


def test_ack_and_settle_require_matching_mutation_and_newer_evidence() -> None:
    staged = planned().stage(mutation())
    with pytest.raises(ValueError, match="foreign"):
        staged.mark_ack_pending("mut-other")
    pending = staged.mark_ack_pending("mut-1")
    with pytest.raises(ValueError, match="not newer"):
        pending.settle(authoritative_revision=12)

    settled = pending.settle(authoritative_revision=13)
    assert settled.phase is StepRunPhase.SETTLED
    assert settled.staged_mutation is None
    assert settled.settled_revision == 13


def test_restore_rejects_partial_unknown_and_inconsistent_payloads() -> None:
    payload = planned().checkpoint()
    del payload["actor"]
    with pytest.raises(ValueError, match="malformed"):
        StepRun.restore(payload)

    payload = planned().checkpoint()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="malformed"):
        StepRun.restore(payload)

    payload = planned().checkpoint()
    payload["phase"] = "staged"
    with pytest.raises(ValueError, match="staged mutation"):
        StepRun.restore(payload)

    payload = planned().checkpoint()
    payload["schema_version"] = True
    with pytest.raises(ValueError, match="schema version"):
        StepRun.restore(payload)


def test_journal_cas_roundtrip_and_conflict(tmp_path) -> None:
    journal = StepRunJournal(tmp_path / "step-run.json")
    initial = journal.compare_and_swap(
        expected_revision=None, replacement=planned(), actor=actor()
    )
    assert initial.journal_revision == 0

    staged = journal.compare_and_swap(
        expected_revision=0, replacement=initial.stage(mutation()), actor=actor()
    )
    assert staged.journal_revision == 1
    assert journal.load(actor=actor()) == staged
    with pytest.raises(StepRunJournalConflict):
        journal.compare_and_swap(
            expected_revision=0, replacement=staged, actor=actor()
        )


def test_journal_rejects_foreign_actor_without_overwrite(tmp_path) -> None:
    path = tmp_path / "step-run.json"
    journal = StepRunJournal(path)
    original = journal.compare_and_swap(
        expected_revision=None, replacement=planned(), actor=actor()
    )
    before = path.read_bytes()

    with pytest.raises(ValueError, match="foreign"):
        journal.compare_and_swap(
            expected_revision=0,
            replacement=planned(actor=actor("tab-2")),
            actor=actor("tab-2"),
        )
    assert path.read_bytes() == before
    assert journal.load(actor=actor()) == original


def test_journal_write_failure_leaves_previous_checkpoint(tmp_path, monkeypatch) -> None:
    path = tmp_path / "step-run.json"
    journal = StepRunJournal(path)
    initial = journal.compare_and_swap(
        expected_revision=None, replacement=planned(), actor=actor()
    )
    before = path.read_bytes()

    def fail_write(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_step_run.write_json_checkpoint", fail_write
    )
    with pytest.raises(OSError, match="disk full"):
        journal.compare_and_swap(
            expected_revision=0, replacement=initial.stage(mutation()), actor=actor()
        )
    assert path.read_bytes() == before


def test_journal_fails_closed_on_malformed_data(tmp_path) -> None:
    path = tmp_path / "step-run.json"
    path.write_text(json.dumps({"journal_schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        StepRunJournal(path).load(actor=actor())
