"""Public action API and safety-gated response adapter.

Live browser mutation is implemented by :mod:`action_live_sink`; domain action
ownership lives in the action registry.  Keeping this module small preserves
legacy imports while preventing new domain behavior from accumulating here.
"""

from __future__ import annotations

import time

from src.antibot_cv.automation import action_live_sink as _live
from src.antibot_cv.automation.action_live_sink import (
    ActionDiagnosticCode,
    ActionExecutionResult,
    ActionExecutionStatus,
    ActionExecutor,
    ActionRequest,
    ActionSink,
    TypedActionSink,
    BlockedActionSink,
    DryRunActionSink,
    ReplayActionSink,
    _compact_injector_message,
    _compact_recovery_result,
    _copy_request,
    _log_action,
    _parse_injector_dict,
    _route_confirmation_reason,
)
from src.antibot_cv.automation.action_registry import action_domain
from src.antibot_cv.automation.browser_injector import global_browser_injector


class LiveMacActionSink(_live.LiveMacActionSink):
    """Compatibility facade around the physical live-action boundary.

    Synchronising these dependencies keeps existing monkeypatch-based callers
    compatible without allowing domain modules to import this public facade.
    """

    def execute(self, request: ActionRequest) -> bool:
        return self.execute_outcome(request).issued

    def execute_outcome(self, request: ActionRequest) -> ActionExecutionResult:
        _live.global_browser_injector = global_browser_injector
        _live.time = time
        # Resolve ownership at the router boundary. Unknown actions remain the
        # sink's responsibility so its established blocked-event contract holds.
        action_domain(request.action_type)
        return super().execute_outcome(request)


__all__ = [
    "ActionDiagnosticCode",
    "ActionExecutionResult",
    "ActionExecutionStatus",
    "ActionExecutor",
    "ActionRequest",
    "ActionSink",
    "TypedActionSink",
    "BlockedActionSink",
    "DryRunActionSink",
    "LiveMacActionSink",
    "ReplayActionSink",
]
