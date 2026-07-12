"""Fail-closed death/revive recovery policy.

This module is deliberately a decision layer.  It does not click, navigate,
send requests, or spend currency.  A caller must execute a returned
``REVIVE`` action and provide a new, fresh snapshot before recovery can
continue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import time
from typing import Callable, Iterable


class RecoveryDecision(str, Enum):
    NOT_DEAD = "NOT_DEAD"
    REVIVE = "REVIVE"
    WAIT_CONFIRMATION = "WAIT_CONFIRMATION"
    RESTORE_CHECKPOINT = "RESTORE_CHECKPOINT"
    COMPLETE = "COMPLETE"
    STOP_UNSAFE = "STOP_UNSAFE"


@dataclass(frozen=True)
class ReviveOption:
    """A revive option as observed on the page, never an instruction to click."""

    option_id: str
    free: bool | None = None
    safe: bool | None = None
    available: bool | None = None


@dataclass(frozen=True)
class RecoverySnapshot:
    """One page observation and its capture time (monotonic clock seconds)."""

    snapshot_id: str
    captured_at: float
    is_dead: bool | None
    revive_options: tuple[ReviveOption, ...] = ()
    activity: str | None = None
    location: str | None = None
    quest: str | None = None

    @classmethod
    def from_options(
        cls, *, snapshot_id: str, captured_at: float, is_dead: bool | None,
        revive_options: Iterable[ReviveOption] = (), activity: str | None = None,
        location: str | None = None, quest: str | None = None,
    ) -> "RecoverySnapshot":
        return cls(snapshot_id, captured_at, is_dead, tuple(revive_options), activity, location, quest)


@dataclass(frozen=True)
class RecoveryCheckpoint:
    """The last known safe work position, not a command to restore it."""

    activity: str | None
    location: str | None
    quest: str | None
    snapshot_id: str = ""

    @property
    def complete(self) -> bool:
        return all(isinstance(value, str) and bool(value.strip()) for value in (self.activity, self.location))


@dataclass
class DeathCounter:
    """Bounded, idempotent death counter keyed by an observed snapshot/event."""

    count: int = 0
    _recorded_ids: set[str] = field(default_factory=set, repr=False)

    def record(self, event_id: str) -> int:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be a non-empty string")
        if event_id not in self._recorded_ids:
            self._recorded_ids.add(event_id)
            self.count += 1
        return self.count


@dataclass(frozen=True)
class RecoveryResult:
    decision: RecoveryDecision
    reason: str
    option_id: str | None = None
    checkpoint: RecoveryCheckpoint | None = None


@dataclass
class DeathRecoveryPolicy:
    """Purely local state machine for safe death recovery."""

    max_deaths: int
    free_revive_allowlist: tuple[str, ...]
    snapshot_max_age_seconds: float = 5.0
    now: Callable[[], float] = time.monotonic
    deaths: DeathCounter = field(default_factory=DeathCounter)
    _pending_snapshot_id: str | None = field(default=None, init=False, repr=False)
    _restored_snapshot_id: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.free_revive_allowlist = tuple(self.free_revive_allowlist)

    def record_death(self, event_id: str) -> int:
        """Record an observed death exactly once; does not make a decision."""
        return self.deaths.record(event_id)

    def checkpoint(self, snapshot: RecoverySnapshot) -> RecoveryCheckpoint:
        """Capture prior work only when all checkpoint fields are known."""
        return RecoveryCheckpoint(snapshot.activity, snapshot.location, snapshot.quest, snapshot.snapshot_id)

    def decide(
        self,
        snapshot: RecoverySnapshot,
        *,
        checkpoint: RecoveryCheckpoint | None = None,
        revived: bool | None = None,
    ) -> RecoveryResult:
        """Return one safe decision for the supplied observation.

        ``revived`` is an explicit observation from a later page snapshot.  A
        truthy value alone is insufficient: the snapshot must be fresh and
        report ``is_dead=False``.
        """
        invalid = self._validate(snapshot)
        if invalid:
            return RecoveryResult(RecoveryDecision.STOP_UNSAFE, invalid)
        if self.deaths.count < 0:
            return RecoveryResult(RecoveryDecision.STOP_UNSAFE, "invalid_death_counter")
        if self._restored_snapshot_id == snapshot.snapshot_id and snapshot.is_dead is False:
            return RecoveryResult(RecoveryDecision.COMPLETE, "recovery_already_completed", checkpoint=checkpoint)

        if self._pending_snapshot_id is None and snapshot.is_dead is False:
            return RecoveryResult(RecoveryDecision.NOT_DEAD, "player_alive")
        if self.deaths.count > self.max_deaths:
            return RecoveryResult(RecoveryDecision.STOP_UNSAFE, "max_deaths_reached")

        if self._pending_snapshot_id is not None:
            if snapshot.snapshot_id == self._pending_snapshot_id:
                return RecoveryResult(RecoveryDecision.WAIT_CONFIRMATION, "revive_confirmation_required")
            if revived is not True or snapshot.is_dead is not False:
                return RecoveryResult(RecoveryDecision.WAIT_CONFIRMATION, "revive_not_confirmed")
            self._pending_snapshot_id = None
            if checkpoint is None or not checkpoint.complete:
                return RecoveryResult(RecoveryDecision.STOP_UNSAFE, "incomplete_checkpoint")
            self._restored_snapshot_id = snapshot.snapshot_id
            return RecoveryResult(RecoveryDecision.RESTORE_CHECKPOINT, "revive_confirmed", checkpoint=checkpoint)

        if snapshot.is_dead is not True:
            return RecoveryResult(RecoveryDecision.STOP_UNSAFE, "unknown_death_state")

        option = self._safe_free_option(snapshot.revive_options)
        if option is None:
            return RecoveryResult(RecoveryDecision.STOP_UNSAFE, "no_allowlisted_free_safe_revive")
        self._pending_snapshot_id = snapshot.snapshot_id
        return RecoveryResult(RecoveryDecision.REVIVE, "allowlisted_free_safe_revive", option_id=option.option_id)

    def _validate(self, snapshot: RecoverySnapshot) -> str | None:
        if not isinstance(snapshot, RecoverySnapshot) or not snapshot.snapshot_id:
            return "invalid_snapshot"
        if (
            not isinstance(snapshot.captured_at, (int, float))
            or isinstance(snapshot.captured_at, bool)
            or not math.isfinite(snapshot.captured_at)
        ):
            return "invalid_snapshot_timestamp"
        age = self.now() - snapshot.captured_at
        if age < 0 or age > self.snapshot_max_age_seconds:
            return "stale_snapshot"
        if self.max_deaths < 0 or self.snapshot_max_age_seconds <= 0:
            return "invalid_policy_configuration"
        return None

    def _safe_free_option(self, options: Iterable[ReviveOption]) -> ReviveOption | None:
        for option in options:
            if (
                isinstance(option, ReviveOption)
                and option.option_id in self.free_revive_allowlist
                and option.free is True
                and option.safe is True
                and option.available is True
            ):
                return option
        return None


# Short aliases keep the integration contract discoverable.
Decision = RecoveryDecision
Snapshot = RecoverySnapshot
Checkpoint = RecoveryCheckpoint
Policy = DeathRecoveryPolicy
DeathRecovery = DeathRecoveryPolicy
