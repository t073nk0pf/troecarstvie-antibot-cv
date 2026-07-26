"""Long-lived application boundary for control-server live mutations."""

from __future__ import annotations

from dataclasses import dataclass, replace
import threading
from typing import Callable, Hashable

from src.antibot_cv.automation.actions import (
    ActionExecutionResult,
    ActionExecutionStatus,
    ActionExecutor,
    ActionRequest,
    ActionSink,
    LiveMacActionSink,
)
from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.automation.safety import SafetyDecision, SafetyGuard
from src.antibot_cv.automation.session import SessionState
from src.antibot_cv.automation.mutation_lease import (
    ActorKey,
    MutationLease,
    MutationLeaseCoordinator,
    MutationLeaseMode,
    MutationTarget,
)


@dataclass(frozen=True, slots=True)
class FencedActionResult:
    execution: ActionExecutionResult
    lease: MutationLease | None


@dataclass
class LiveClientActionContext:
    client_id: str
    guard: SafetyGuard
    session: SessionState
    sink: ActionSink
    executor: ActionExecutor
    execution_lock: threading.RLock


class LiveActionService:
    """Own one persistent safety/session/sink context per browser client."""

    def __init__(
        self,
        config_factory: Callable[[], AutomationConfig],
        *,
        sink_factory: Callable[[str], ActionSink] | None = None,
        identity_factory: Callable[[str], Hashable | None] | None = None,
        mutation_coordinator: MutationLeaseCoordinator | None = None,
        logger: object | None = None,
    ) -> None:
        self._config_factory = config_factory
        self._sink_factory = sink_factory or (
            lambda client_id: LiveMacActionSink(
                logger=logger, browser_client_id=client_id,
            )
        )
        self._logger = logger
        self._identity_factory = identity_factory
        self._mutation_coordinator = mutation_coordinator
        self._lock = threading.RLock()
        self._contexts: dict[Hashable, LiveClientActionContext] = {}

    def execute(self, client_id: str, request: ActionRequest) -> bool:
        """Serialize one mutation through the client's persistent executor."""

        normalized = _client_id(client_id)
        if self._mutation_coordinator is not None:
            result = self.execute_fenced(normalized, request)
            if result.execution.status is ActionExecutionStatus.ISSUED:
                assert result.lease is not None
                # Compatibility callers use this boundary only for actions whose
                # bridge acknowledgement is their terminal postcondition.  Multi-
                # phase route/NPC consumers must call execute_fenced directly and
                # reconcile against a newer authoritative snapshot.
                self.reconcile_fenced(result.lease)
                return True
            return False
        with self._lock:
            context = self._context_locked(normalized)
        with context.execution_lock:
            try:
                return context.executor.execute(request)
            except Exception:
                context.guard.record_error()
                return False

    def execute_fenced(
        self,
        client_id: str,
        request: ActionRequest,
        *,
        on_claimed: Callable[[MutationLease], None] | None = None,
        on_not_issued: Callable[[MutationLease], None] | None = None,
    ) -> FencedActionResult:
        """Execute with a monotonic actor fence and retain uncertain ownership."""

        normalized = _client_id(client_id)
        if self._mutation_coordinator is None or self._identity_factory is None:
            return FencedActionResult(
                ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED), None,
            )
        identity = self._identity_factory(normalized)
        if (
            not isinstance(identity, tuple) or len(identity) != 2
            or not isinstance(identity[0], str)
            or isinstance(identity[1], bool) or not isinstance(identity[1], int)
        ):
            return FencedActionResult(
                ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED), None,
            )
        actor = ActorKey(identity[0], identity[1])
        generation = self._mutation_coordinator.bind(actor, normalized)
        lease = self._mutation_coordinator.try_acquire(
            actor, f"quest:{normalized}:{request.action_type}",
            MutationLeaseMode.TRANSIENT, actor_generation=generation,
        )
        if lease is None:
            return FencedActionResult(
                ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED), None,
            )
        if not self._mutation_coordinator.is_current(lease):
            return FencedActionResult(
                ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED), None,
            )
        if on_claimed is not None:
            try:
                on_claimed(lease)
            except Exception as exc:
                if getattr(exc, "retain_mutation_lease", False):
                    return FencedActionResult(
                        ActionExecutionResult(ActionExecutionStatus.DELIVERY_UNKNOWN), lease,
                    )
                self._mutation_coordinator.release(lease)
                return FencedActionResult(
                    ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED), None,
                )
        metadata = dict(request.metadata or {})
        metadata["mutation_fence"] = {
            "profile_id": actor.profile_id,
            "tab_id": actor.tab_id,
            "actor_generation": lease.actor_generation,
            "fencing_token": lease.fencing_token,
        }
        request = replace(request, metadata=metadata)
        with self._lock:
            context = self._context_locked(normalized)
        with context.execution_lock:
            outcome = context.executor.execute_outcome(request)
        if outcome.status is ActionExecutionStatus.NOT_ISSUED:
            if on_not_issued is not None:
                try:
                    on_not_issued(lease)
                except Exception:
                    # A durable staged claim already exists.  Until rollback is
                    # proven, every failure is an uncertain compound outcome.
                    return FencedActionResult(
                        ActionExecutionResult(ActionExecutionStatus.DELIVERY_UNKNOWN), lease,
                    )
            self._mutation_coordinator.release(lease)
            return FencedActionResult(outcome, None)
        return FencedActionResult(outcome, lease)

    def reconcile_fenced(self, lease: MutationLease) -> bool:
        """Release only the exact retained generation/fence after authority settles."""

        if self._mutation_coordinator is None:
            return False
        return self._mutation_coordinator.release(lease)

    def retained_fenced(self, client_id: str, action_type: str) -> MutationLease | None:
        """Return the exact retained lease for observation-only reconciliation.

        This never acquires or advances authority and therefore cannot reissue a
        mutation after an unknown delivery result.
        """

        normalized = _client_id(client_id)
        if self._mutation_coordinator is None or self._identity_factory is None:
            return None
        identity = self._identity_factory(normalized)
        if (
            not isinstance(identity, tuple) or len(identity) != 2
            or not isinstance(identity[0], str)
            or isinstance(identity[1], bool) or not isinstance(identity[1], int)
        ):
            return None
        actor = ActorKey(identity[0], identity[1])
        lease = self._mutation_coordinator.holder(actor)
        capability = str(action_type or "").strip()
        owner_prefix = "quest:"
        owner_suffix = f":{capability}"
        if (
            lease is None or not capability
            or not lease.owner_id.startswith(owner_prefix)
            or not lease.owner_id.endswith(owner_suffix)
        ):
            return None
        return lease if self._mutation_coordinator.is_current(lease) else None

    def emergency_stop(self, client_id: str) -> None:
        with self._lock:
            self._context_locked(_client_id(client_id)).guard.emergency_stop()

    def record_error(self, client_id: str) -> SafetyDecision:
        with self._lock:
            return self._context_locked(_client_id(client_id)).guard.record_error()

    def clear_errors(self, client_id: str) -> None:
        with self._lock:
            self._context_locked(_client_id(client_id)).guard.clear_errors()

    def counters(self, client_id: str) -> dict[str, object]:
        with self._lock:
            context = self._context_locked(_client_id(client_id))
            return {
                "total_actions": context.session.total_actions,
                "rate_actions": len(context.guard.action_times),
                "emergency_stopped": context.guard.emergency_stopped,
                "consecutive_errors": context.guard.consecutive_errors,
            }

    def battle_debug_result(self, client_id: str) -> dict[str, object] | None:
        """Return the last sink-owned battle diagnostic for this client."""

        with self._lock:
            sink = self._context_locked(_client_id(client_id)).sink
            value = getattr(sink, "last_battle_debug", None)
            return dict(value) if isinstance(value, dict) else None

    def _context_locked(self, client_id: str) -> LiveClientActionContext:
        identity = None if self._identity_factory is None else self._identity_factory(client_id)
        context_key: Hashable = ("logical", identity) if identity is not None else ("transport", client_id)
        existing = self._contexts.get(context_key)
        if existing is not None:
            if existing.client_id != client_id:
                sink = self._sink_factory(client_id)
                existing.client_id = client_id
                existing.sink = sink
                existing.executor = ActionExecutor(
                    guard=existing.guard,
                    session=existing.session,
                    sink=sink,
                    logger=self._logger,
                    mutation_fence_validator=(
                        None if self._mutation_coordinator is None
                        else self._mutation_coordinator.matches_fence
                    ),
                )
            return existing
        config = replace(self._config_factory(), dry_run=False)
        guard = SafetyGuard(config)
        guard.set_dry_run(False)
        session = SessionState(requested_cycles=1)
        sink = self._sink_factory(client_id)
        context = LiveClientActionContext(
            client_id,
            guard,
            session,
            sink,
            ActionExecutor(
                guard=guard, session=session, sink=sink, logger=self._logger,
                mutation_fence_validator=(
                    None if self._mutation_coordinator is None
                    else self._mutation_coordinator.matches_fence
                ),
            ),
            threading.RLock(),
        )
        self._contexts[context_key] = context
        return context


def _client_id(value: object) -> str:
    client_id = str(value or "").strip()
    if not client_id or len(client_id) > 120:
        raise ValueError("live action client identity is invalid")
    return client_id
