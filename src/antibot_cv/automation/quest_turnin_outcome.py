from __future__ import annotations

from enum import Enum


class QuestTurnInStartOutcome(str, Enum):
    """Explicit production result of attempting to start quest turn-in."""

    STARTED = "started"
    LOCAL_BLOCKED = "local_blocked"
    NOT_APPLICABLE = "not_applicable"
    STOPPED = "stopped"


__all__ = ["QuestTurnInStartOutcome"]
