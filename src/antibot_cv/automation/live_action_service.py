"""Long-lived application boundary for control-server live mutations."""

from __future__ import annotations

from dataclasses import dataclass, replace
import threading
from typing import Callable, Hashable

from src.antibot_cv.automation.actions import (
    ActionExecutor,
    ActionRequest,
    ActionSink,
    LiveMacActionSink,
)
from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.automation.safety import SafetyDecision, SafetyGuard
from src.antibot_cv.automation.session import SessionState


@dataclass
class LiveClientActionContext:
    client_id: str
    guard: SafetyGuard
    session: SessionState
    sink: ActionSink
    executor: ActionExecutor


class LiveActionService:
    """Own one persistent safety/session/sink context per browser client."""

    def __init__(
        self,
        config_factory: Callable[[], AutomationConfig],
        *,
        sink_factory: Callable[[str], ActionSink] | None = None,
        identity_factory: Callable[[str], Hashable | None] | None = None,
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
        self._lock = threading.RLock()
        self._contexts: dict[Hashable, LiveClientActionContext] = {}

    def execute(self, client_id: str, request: ActionRequest) -> bool:
        """Serialize one mutation through the client's persistent executor."""

        normalized = _client_id(client_id)
        with self._lock:
            context = self._context_locked(normalized)
            try:
                return context.executor.execute(request)
            except Exception:
                context.guard.record_error()
                return False

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
            ),
        )
        self._contexts[context_key] = context
        return context


def _client_id(value: object) -> str:
    client_id = str(value or "").strip()
    if not client_id or len(client_id) > 120:
        raise ValueError("live action client identity is invalid")
    return client_id
