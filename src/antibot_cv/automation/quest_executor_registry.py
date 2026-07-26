"""Fail-closed capability registry for semantic quest executors.

The registry is deliberately independent from production runtimes.  It only
binds an already-evaluated semantic atom to one executor; it never parses quest
text, chooses quest completion, or performs a mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, runtime_checkable

from .quest_execution_model import MutationIntent
from .quest_plan_evaluator import EvaluationStatus, PlanEvaluation
from .quest_plan_model import QuestLeaf


class ExecutorSelectionError(RuntimeError):
    """Base class for fail-closed executor selection failures."""


class ExecutorNotFoundError(ExecutorSelectionError):
    """Raised when no registered executor accepts an actionable atom."""


class ExecutorAmbiguityError(ExecutorSelectionError):
    """Raised when more than one executor accepts the same atom."""


class NonActionableEvaluationError(ExecutorSelectionError):
    """Raised when dispatch is attempted for a non-actionable evaluation."""


@runtime_checkable
class QuestExecutor(Protocol):
    """Unified boundary implemented by quest capability adapters.

    ``plan_intent`` may return a typed mutation or route intent from the
    execution model.  The registry intentionally treats it as opaque: only the
    action boundary may translate and execute that intent.
    """

    @property
    def executor_id(self) -> str:
        """Stable capability identifier used in durable checkpoints."""

    def can_execute(self, atom: QuestLeaf) -> bool:
        """Return whether this executor owns the exact semantic atom."""

    def plan_intent(
        self, atom: QuestLeaf, *, context: object | None = None
    ) -> MutationIntent:
        """Plan, but do not execute, the next typed intent."""

    def reconcile(
        self, atom: QuestLeaf, *, evidence: object, context: object | None = None,
    ) -> object:
        """Reconcile authoritative evidence after a planned transition."""

    def checkpoint(self) -> Mapping[str, object]:
        """Return JSON-compatible executor state."""

    def restore(self, payload: Mapping[str, object]) -> None:
        """Restore previously checkpointed executor state fail-closed."""


def _normalized_executor_id(executor: QuestExecutor) -> str:
    value = executor.executor_id
    if not isinstance(value, str) or not value.strip():
        raise ValueError("executor_id must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True, init=False)
class QuestExecutorRegistry:
    """Immutable registry with deterministic, exclusive atom ownership."""

    _executors: tuple[QuestExecutor, ...]

    def __init__(self, executors: Iterable[QuestExecutor] = ()) -> None:
        entries = tuple(executors)
        identifiers = tuple(_normalized_executor_id(item) for item in entries)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("executor_id values must be unique")
        # Canonical ordering makes construction from sets or registries stable;
        # selection still rejects multiple matches rather than using priority.
        ordered = tuple(item for _, item in sorted(zip(identifiers, entries), key=lambda pair: pair[0]))
        object.__setattr__(self, "_executors", ordered)

    @property
    def executors(self) -> tuple[QuestExecutor, ...]:
        return self._executors

    def select(self, atom: QuestLeaf) -> QuestExecutor:
        matches = tuple(executor for executor in self._executors if executor.can_execute(atom))
        if not matches:
            raise ExecutorNotFoundError(
                f"no quest executor accepts requirement {atom.requirement_id!r}"
            )
        if len(matches) != 1:
            names = ", ".join(executor.executor_id for executor in matches)
            raise ExecutorAmbiguityError(
                f"multiple quest executors accept requirement {atom.requirement_id!r}: {names}"
            )
        return matches[0]

    def select_evaluation(self, evaluation: PlanEvaluation) -> QuestExecutor:
        """Select only the unique executor for an ACTIONABLE next atom."""

        if evaluation.status is not EvaluationStatus.ACTIONABLE or evaluation.next_atom is None:
            raise NonActionableEvaluationError(
                f"quest evaluation is not dispatchable: {evaluation.status.value}"
            )
        return self.select(evaluation.next_atom)


__all__ = [
    "ExecutorAmbiguityError",
    "ExecutorNotFoundError",
    "ExecutorSelectionError",
    "NonActionableEvaluationError",
    "QuestExecutor",
    "QuestExecutorRegistry",
]
