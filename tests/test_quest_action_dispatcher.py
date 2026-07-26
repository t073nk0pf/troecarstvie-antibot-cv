from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from src.antibot_cv.automation.action_live_sink import ActionRequest
from src.antibot_cv.automation.quest_action_dispatcher import (
    QuestActionDispatcher,
    action_request_from_intent,
)
from src.antibot_cv.automation.quest_engine_coordinator import QuestCoordinationError
from src.antibot_cv.automation.quest_execution_model import MutationIntent, RouteKind, RouteLease
from src.antibot_cv.automation.quest_step_run import (
    StagedMutation,
    StepActorIdentity,
    StepRun,
    StepRunJournal,
    StepRunPhase,
)


@dataclass
class RecordingExecutor:
    result: bool = True
    requests: list[ActionRequest] = field(default_factory=list)

    def execute(self, request: ActionRequest) -> bool:
        self.requests.append(request)
        return self.result


class ClaimCoordinator:
    def __init__(self, journal: StepRunJournal, actor: StepActorIdentity) -> None:
        self.journal = journal
        self.actor = actor

    def claim_dispatch(self, *, actor: StepActorIdentity, intent: MutationIntent) -> StepRun:
        run = self.journal.load(actor=actor)
        assert run is not None and run.staged_mutation is not None
        mutation = run.staged_mutation
        if run.phase is not StepRunPhase.STAGED:
            raise QuestCoordinationError("staged mutation is not dispatchable")
        if (
            mutation.mutation_id != intent.idempotency_key
            or mutation.action_kind != intent.action_type
            or mutation.payload_fingerprint != intent.fingerprint
        ):
            raise QuestCoordinationError("foreign mutation dispatch claim")
        return self.journal.compare_and_swap(
            expected_revision=run.journal_revision,
            replacement=run.mark_ack_pending(intent.idempotency_key),
            actor=actor,
        )


def _fixture(tmp_path):
    actor = StepActorIdentity("client-1", "profile-1", "tab-1")
    lease = RouteLease(
        RouteKind.QUEST_LOCATION, "actor-1", actor.client_id, actor.profile_id,
        actor.tab_id, "q1", "step-1", "4", 9.0, 9.5, 20.0,
    )
    intent = MutationIntent(
        "open_location_navigator", "req-1", "step-1", "mutation-1",
        route_lease=lease,
    )
    planned = StepRun(
        "quest_engine_coordinator_v1", "q1", "plan-1", "step-1", "req-1",
        "quest.visit", actor, 4, 1,
    )
    staged = planned.stage(StagedMutation(
        intent.idempotency_key, intent.action_type, intent.fingerprint, 4,
    ))
    journal = StepRunJournal(tmp_path / "run.json")
    journal.compare_and_swap(expected_revision=None, replacement=staged, actor=actor)
    return actor, intent, journal


def test_translation_preserves_dry_run_and_identity() -> None:
    intent = MutationIntent(
        "open_location_navigator", "req-1", "step-1", "mutation-1", dry_run=True,
        metadata={"location": "Площадь"},
    )
    request = action_request_from_intent(intent)
    assert request.dry_run is True
    assert request.action_type == "open_location_navigator"
    assert request.metadata["quest_intent_fingerprint"] == intent.fingerprint
    assert request.metadata["quest_requirement_id"] == "req-1"


def test_dispatch_claims_before_one_and_only_one_sink_call(tmp_path) -> None:
    actor, intent, journal = _fixture(tmp_path)
    sink = RecordingExecutor()
    dispatcher = QuestActionDispatcher(
        coordinator=ClaimCoordinator(journal, actor),  # type: ignore[arg-type]
        action_executor=sink,
        now=lambda: 10.0,
    )

    assert dispatcher.dispatch(actor=actor, intent=intent) is True
    assert journal.load(actor=actor).phase is StepRunPhase.ACK_PENDING  # type: ignore[union-attr]
    assert len(sink.requests) == 1

    with pytest.raises(QuestCoordinationError, match="not dispatchable"):
        dispatcher.dispatch(actor=actor, intent=intent)
    assert len(sink.requests) == 1


def test_failed_or_ambiguous_delivery_remains_zero_reissue(tmp_path) -> None:
    actor, intent, journal = _fixture(tmp_path)
    sink = RecordingExecutor(result=False)
    dispatcher = QuestActionDispatcher(
        coordinator=ClaimCoordinator(journal, actor),  # type: ignore[arg-type]
        action_executor=sink,
        now=lambda: 10.0,
    )

    assert dispatcher.dispatch(actor=actor, intent=intent) is False
    restored = journal.load(actor=actor)
    assert restored is not None and restored.phase is StepRunPhase.ACK_PENDING
    with pytest.raises(QuestCoordinationError):
        dispatcher.dispatch(actor=actor, intent=intent)
    assert len(sink.requests) == 1


def test_unknown_action_or_expired_lease_is_rejected_before_claim(tmp_path) -> None:
    actor, intent, journal = _fixture(tmp_path)
    sink = RecordingExecutor()
    unknown = MutationIntent(
        "semantic_unknown", intent.requirement_id, intent.step_fingerprint,
        intent.idempotency_key, route_lease=intent.route_lease,
    )
    dispatcher = QuestActionDispatcher(
        coordinator=ClaimCoordinator(journal, actor),  # type: ignore[arg-type]
        action_executor=sink,
        now=lambda: 30.0,
    )
    with pytest.raises(ValueError, match="not registered"):
        dispatcher.dispatch(actor=actor, intent=unknown)
    with pytest.raises(ValueError, match="expired"):
        dispatcher.dispatch(actor=actor, intent=intent)
    assert journal.load(actor=actor).phase is StepRunPhase.STAGED  # type: ignore[union-attr]
    assert sink.requests == []
