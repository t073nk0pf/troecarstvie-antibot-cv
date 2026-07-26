from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import pytest

from src.antibot_cv.automation.quest_executor_registry import (
    ExecutorAmbiguityError,
    ExecutorNotFoundError,
    NonActionableEvaluationError,
    QuestExecutor,
    QuestExecutorRegistry,
)
from src.antibot_cv.automation.quest_plan_evaluator import EvaluationStatus, PlanEvaluation
from src.antibot_cv.automation.quest_plan_model import Kill, TurnIn


@dataclass
class FakeExecutor:
    executor_id: str
    accepted_type: type[object]
    state: dict[str, object] = field(default_factory=dict)

    def can_execute(self, atom: object) -> bool:
        return isinstance(atom, self.accepted_type)

    def plan_intent(self, atom: object, *, context: object | None = None) -> object:
        return (atom, context)

    def reconcile(
        self, atom: object, *, evidence: object, context: object | None = None,
    ) -> object:
        return (atom, evidence)

    def checkpoint(self) -> Mapping[str, object]:
        return dict(self.state)

    def restore(self, payload: Mapping[str, object]) -> None:
        self.state = dict(payload)


def _kill() -> Kill:
    return Kill("кабан", 1)


def _evaluation(status: EvaluationStatus, *, atom: Kill | None) -> PlanEvaluation:
    return PlanEvaluation(status, atom, (), (), "fixture")


def test_registry_is_immutable_and_selects_unique_executor_deterministically() -> None:
    turn_in = FakeExecutor("turn_in", TurnIn)
    combat = FakeExecutor("combat", Kill)
    registry = QuestExecutorRegistry((turn_in, combat))

    assert registry.executors == (combat, turn_in)
    assert registry.select(_kill()) is combat
    assert isinstance(combat, QuestExecutor)
    with pytest.raises((AttributeError, TypeError)):
        registry.executors = ()  # type: ignore[misc]


def test_registry_fails_closed_when_no_executor_accepts_atom() -> None:
    registry = QuestExecutorRegistry((FakeExecutor("turn_in", TurnIn),))

    with pytest.raises(ExecutorNotFoundError, match=_kill().requirement_id):
        registry.select(_kill())


def test_registry_fails_closed_on_ambiguous_ownership_instead_of_using_priority() -> None:
    registry = QuestExecutorRegistry(
        (FakeExecutor("combat.b", Kill), FakeExecutor("combat.a", Kill))
    )

    with pytest.raises(
        ExecutorAmbiguityError,
        match=r"combat\.a, combat\.b",
    ):
        registry.select(_kill())


@pytest.mark.parametrize(
    ("status", "atom"),
    [
        (EvaluationStatus.SATISFIED, None),
        (EvaluationStatus.BLOCKED, None),
        (EvaluationStatus.UNSAFE, None),
        (EvaluationStatus.UNKNOWN, None),
        (EvaluationStatus.ACTIONABLE, None),
    ],
)
def test_registry_rejects_non_actionable_or_incomplete_evaluation(
    status: EvaluationStatus,
    atom: Kill | None,
) -> None:
    registry = QuestExecutorRegistry((FakeExecutor("combat", Kill),))

    with pytest.raises(NonActionableEvaluationError):
        registry.select_evaluation(_evaluation(status, atom=atom))


def test_registry_selects_from_actionable_evaluation_only() -> None:
    combat = FakeExecutor("combat", Kill)
    registry = QuestExecutorRegistry((combat,))

    assert registry.select_evaluation(
        _evaluation(EvaluationStatus.ACTIONABLE, atom=_kill())
    ) is combat


@pytest.mark.parametrize("executor_id", ["", "   "])
def test_registry_rejects_invalid_executor_identity(executor_id: str) -> None:
    with pytest.raises(ValueError, match="executor_id"):
        QuestExecutorRegistry((FakeExecutor(executor_id, Kill),))


def test_registry_rejects_duplicate_executor_identity() -> None:
    with pytest.raises(ValueError, match="unique"):
        QuestExecutorRegistry(
            (FakeExecutor("combat", Kill), FakeExecutor("combat", TurnIn))
        )
