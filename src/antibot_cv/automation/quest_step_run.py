"""Durable, fail-closed cursor for one semantic quest step.

The cursor records a mutation *before* it reaches an action executor.  A staged
mutation is never considered issuable again after a restart; reconciliation
must move it forward using newer authoritative evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping

from src.antibot_cv.automation.checkpoint import checkpoint_lock, write_json_checkpoint


STEP_RUN_SCHEMA_VERSION = 1
STEP_RUN_JOURNAL_SCHEMA_VERSION = 1


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _exact_keys(payload: Mapping[str, Any], expected: set[str], name: str) -> None:
    if set(payload) != expected:
        raise ValueError(f"malformed {name}")


class StepRunPhase(str, Enum):
    PLANNED = "planned"
    STAGED = "staged"
    ACK_PENDING = "ack_pending"
    SETTLED = "settled"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class StepActorIdentity:
    client_id: str
    profile_id: str
    tab_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_id", _text(self.client_id, "client_id"))
        object.__setattr__(self, "profile_id", _text(self.profile_id, "profile_id"))
        object.__setattr__(self, "tab_id", _text(self.tab_id, "tab_id"))


@dataclass(frozen=True, slots=True)
class StagedMutation:
    mutation_id: str
    action_kind: str
    payload_fingerprint: str
    staged_from_revision: int
    intent_payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mutation_id", _text(self.mutation_id, "mutation_id"))
        object.__setattr__(self, "action_kind", _text(self.action_kind, "action_kind"))
        object.__setattr__(
            self, "payload_fingerprint", _text(self.payload_fingerprint, "payload_fingerprint")
        )
        _integer(self.staged_from_revision, "staged_from_revision")
        if self.intent_payload is not None:
            try:
                encoded = json.dumps(
                    self.intent_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                decoded = json.loads(encoded)
            except (TypeError, ValueError) as exc:
                raise ValueError("intent_payload must be JSON-compatible") from exc
            if not isinstance(decoded, dict):
                raise ValueError("intent_payload must be a mapping")
            object.__setattr__(self, "intent_payload", decoded)


@dataclass(frozen=True, slots=True)
class StepRun:
    capability_version: str
    quest_id: str
    plan_fingerprint: str
    step_id: str
    requirement_id: str
    executor_kind: str
    actor: StepActorIdentity
    baseline_revision: int
    retry_budget: int
    phase: StepRunPhase = StepRunPhase.PLANNED
    staged_mutation: StagedMutation | None = None
    settled_revision: int | None = None
    journal_revision: int = 0
    schema_version: int = STEP_RUN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != STEP_RUN_SCHEMA_VERSION
        ):
            raise ValueError("unsupported step run schema version")
        for name in (
            "capability_version", "quest_id", "plan_fingerprint", "step_id",
            "requirement_id", "executor_kind",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.actor, StepActorIdentity):
            raise TypeError("actor must be StepActorIdentity")
        _integer(self.baseline_revision, "baseline_revision")
        _integer(self.retry_budget, "retry_budget")
        _integer(self.journal_revision, "journal_revision")
        if not isinstance(self.phase, StepRunPhase):
            raise TypeError("phase must be StepRunPhase")
        if self.settled_revision is not None:
            _integer(self.settled_revision, "settled_revision")
        has_mutation = self.staged_mutation is not None
        if has_mutation != (self.phase in {StepRunPhase.STAGED, StepRunPhase.ACK_PENDING}):
            raise ValueError("staged mutation does not match step phase")
        if self.phase is StepRunPhase.SETTLED:
            if self.settled_revision is None or self.settled_revision <= self.baseline_revision:
                raise ValueError("settled step requires a newer revision")
        elif self.settled_revision is not None:
            raise ValueError("only a settled step may have settled_revision")

    @property
    def may_issue_mutation(self) -> bool:
        return self.phase is StepRunPhase.PLANNED and self.staged_mutation is None

    def stage(self, mutation: StagedMutation) -> "StepRun":
        if not self.may_issue_mutation:
            raise RuntimeError("step mutation is not issuable")
        if self.retry_budget == 0:
            raise RuntimeError("step retry budget is exhausted")
        if mutation.staged_from_revision != self.baseline_revision:
            raise ValueError("mutation baseline revision mismatch")
        return replace(
            self, phase=StepRunPhase.STAGED, staged_mutation=mutation,
            retry_budget=self.retry_budget - 1,
        )

    def mark_ack_pending(self, mutation_id: str) -> "StepRun":
        if self.phase is not StepRunPhase.STAGED or self.staged_mutation is None:
            raise RuntimeError("no staged mutation to acknowledge")
        if self.staged_mutation.mutation_id != mutation_id:
            raise ValueError("foreign mutation acknowledgement")
        return replace(self, phase=StepRunPhase.ACK_PENDING)

    def settle(self, *, authoritative_revision: int) -> "StepRun":
        if self.phase is not StepRunPhase.ACK_PENDING:
            raise RuntimeError("step is not awaiting reconciliation")
        revision = _integer(authoritative_revision, "authoritative_revision")
        if revision <= self.baseline_revision:
            raise ValueError("settlement evidence is not newer than baseline")
        return replace(
            self, phase=StepRunPhase.SETTLED, staged_mutation=None,
            settled_revision=revision,
        )

    def block(self) -> "StepRun":
        if self.phase in {StepRunPhase.SETTLED, StepRunPhase.STAGED, StepRunPhase.ACK_PENDING}:
            raise RuntimeError("cannot block a committed or settled mutation")
        return replace(self, phase=StepRunPhase.BLOCKED)

    def checkpoint(self) -> dict[str, Any]:
        return serialize_step_run(self)

    @classmethod
    def restore(cls, payload: Mapping[str, Any]) -> "StepRun":
        return restore_step_run(payload)


_RUN_KEYS = {
    "schema_version", "capability_version", "quest_id", "plan_fingerprint", "step_id",
    "requirement_id", "executor_kind", "actor", "baseline_revision", "retry_budget",
    "phase", "staged_mutation", "settled_revision", "journal_revision",
}


def serialize_step_run(run: StepRun) -> dict[str, Any]:
    mutation = run.staged_mutation
    return {
        "schema_version": run.schema_version,
        "capability_version": run.capability_version,
        "quest_id": run.quest_id,
        "plan_fingerprint": run.plan_fingerprint,
        "step_id": run.step_id,
        "requirement_id": run.requirement_id,
        "executor_kind": run.executor_kind,
        "actor": {
            "client_id": run.actor.client_id,
            "profile_id": run.actor.profile_id,
            "tab_id": run.actor.tab_id,
        },
        "baseline_revision": run.baseline_revision,
        "retry_budget": run.retry_budget,
        "phase": run.phase.value,
        "staged_mutation": None if mutation is None else {
            "mutation_id": mutation.mutation_id,
            "action_kind": mutation.action_kind,
            "payload_fingerprint": mutation.payload_fingerprint,
            "staged_from_revision": mutation.staged_from_revision,
            "intent_payload": mutation.intent_payload,
        },
        "settled_revision": run.settled_revision,
        "journal_revision": run.journal_revision,
    }


def restore_step_run(payload: Mapping[str, Any]) -> StepRun:
    if not isinstance(payload, Mapping):
        raise ValueError("malformed step run")
    _exact_keys(payload, _RUN_KEYS, "step run")
    actor_payload = payload["actor"]
    if not isinstance(actor_payload, Mapping):
        raise ValueError("malformed step actor")
    _exact_keys(actor_payload, {"client_id", "profile_id", "tab_id"}, "step actor")
    mutation_payload = payload["staged_mutation"]
    mutation = None
    if mutation_payload is not None:
        if not isinstance(mutation_payload, Mapping):
            raise ValueError("malformed staged mutation")
        _exact_keys(
            mutation_payload,
            {
                "mutation_id", "action_kind", "payload_fingerprint",
                "staged_from_revision", "intent_payload",
            },
            "staged mutation",
        )
        mutation = StagedMutation(**dict(mutation_payload))
    try:
        phase = StepRunPhase(payload["phase"])
    except (TypeError, ValueError) as exc:
        raise ValueError("unsupported step run phase") from exc
    return StepRun(
        schema_version=payload["schema_version"],
        capability_version=payload["capability_version"],
        quest_id=payload["quest_id"],
        plan_fingerprint=payload["plan_fingerprint"],
        step_id=payload["step_id"],
        requirement_id=payload["requirement_id"],
        executor_kind=payload["executor_kind"],
        actor=StepActorIdentity(**dict(actor_payload)),
        baseline_revision=payload["baseline_revision"],
        retry_budget=payload["retry_budget"],
        phase=phase,
        staged_mutation=mutation,
        settled_revision=payload["settled_revision"],
        journal_revision=payload["journal_revision"],
    )


class StepRunJournalConflict(RuntimeError):
    """The durable cursor changed since the caller observed it."""


class StepRunJournal:
    """Small locked JSON CAS store for a single ``StepRun``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, *, actor: StepActorIdentity | None = None) -> StepRun | None:
        with checkpoint_lock(self.path):
            run = self._load_locked()
        if run is not None and actor is not None and run.actor != actor:
            raise ValueError("foreign step run actor")
        return run

    def compare_and_swap(
        self, *, expected_revision: int | None, replacement: StepRun,
        actor: StepActorIdentity,
    ) -> StepRun:
        if replacement.actor != actor:
            raise ValueError("foreign replacement actor")
        expected = None if expected_revision is None else _integer(
            expected_revision, "expected_revision"
        )
        with checkpoint_lock(self.path):
            current = self._load_locked()
            if current is not None and current.actor != actor:
                raise ValueError("foreign persisted step run actor")
            current_revision = None if current is None else current.journal_revision
            if current_revision != expected:
                raise StepRunJournalConflict("step run journal revision changed")
            next_revision = 0 if expected is None else expected + 1
            stored = replace(replacement, journal_revision=next_revision)
            write_json_checkpoint(
                self.path,
                {
                    "journal_schema_version": STEP_RUN_JOURNAL_SCHEMA_VERSION,
                    "step_run": serialize_step_run(stored),
                },
                lock_held=True,
            )
            return stored

    def _load_locked(self) -> StepRun | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("malformed step run journal") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("malformed step run journal")
        _exact_keys(payload, {"journal_schema_version", "step_run"}, "step run journal")
        journal_schema = payload["journal_schema_version"]
        if (
            isinstance(journal_schema, bool)
            or not isinstance(journal_schema, int)
            or journal_schema != STEP_RUN_JOURNAL_SCHEMA_VERSION
        ):
            raise ValueError("unsupported step run journal schema")
        return restore_step_run(payload["step_run"])
