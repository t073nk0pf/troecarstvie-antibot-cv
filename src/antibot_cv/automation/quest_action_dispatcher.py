"""The single mutation boundary for staged semantic quest intents.

The dispatcher performs a durable claim before delegating to ``ActionExecutor``.
It contains no quest parsing or capability decisions.
"""

from __future__ import annotations

import time
from typing import Callable, Protocol

from .action_live_sink import ActionRequest
from .action_registry import action_domain
from .quest_engine_coordinator import QuestEngineCoordinator
from .quest_execution_model import MutationIntent
from .quest_step_run import StepActorIdentity


class ActionRequestExecutor(Protocol):
    def execute(self, request: ActionRequest) -> bool: ...


def action_request_from_intent(intent: MutationIntent) -> ActionRequest:
    """Translate a typed intent without weakening its dry-run declaration."""

    metadata = dict(intent.metadata)
    metadata.update({
        "quest_requirement_id": intent.requirement_id,
        "quest_step_fingerprint": intent.step_fingerprint,
        "quest_idempotency_key": intent.idempotency_key,
        "quest_intent_fingerprint": intent.fingerprint,
    })
    if intent.route_lease is not None:
        metadata["quest_route_lease"] = intent.route_lease.canonical_data()
        metadata["quest_route_lease_fingerprint"] = intent.route_lease.fingerprint
    return ActionRequest(
        action_type=intent.action_type,
        cycle_id=intent.cycle_id,
        battle_id=intent.battle_id,
        dry_run=intent.dry_run,
        metadata=metadata,
    )


class QuestActionDispatcher:
    """Claim once, then use the repository's guarded action boundary."""

    def __init__(
        self,
        *,
        coordinator: QuestEngineCoordinator,
        action_executor: ActionRequestExecutor,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._coordinator = coordinator
        self._action_executor = action_executor
        self._now = now

    def dispatch(self, *, actor: StepActorIdentity, intent: MutationIntent) -> bool:
        if action_domain(intent.action_type) is None:
            raise ValueError("quest intent action type is not registered")
        lease = intent.route_lease
        if lease is None:
            raise ValueError("quest dispatch requires a route lease")
        reason = lease.coherence_reason(
            now=self._now(),
            actor_id=lease.actor_id,
            client_id=actor.client_id,
            profile_id=actor.profile_id,
            tab_id=actor.tab_id,
            quest_id=lease.quest_id,
            step_fingerprint=intent.step_fingerprint,
            revision=lease.revision,
            max_snapshot_age_s=lease.expires_at - lease.generated_at,
        )
        if reason is not None:
            raise ValueError(reason)
        # ACK_PENDING means mutation-possible, not success.  Persisting it before
        # the sink call makes crashes and delivery timeouts zero-reissue.
        self._coordinator.claim_dispatch(actor=actor, intent=intent)
        return bool(self._action_executor.execute(action_request_from_intent(intent)))


__all__ = [
    "ActionRequestExecutor", "QuestActionDispatcher", "action_request_from_intent",
]
