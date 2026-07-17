"""Pure scheduling policy for autonomous quest intake and execution.

The director does not parse pages or perform actions.  It decides whether the
orchestrator must refresh the global available-quest catalogue, accept the
next quest from a stable intake queue, execute an already active quest, or
fall back to profit farming after a fresh empty catalogue has been observed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class QuestDirectorIntent(str, Enum):
    REFRESH_AVAILABLE = "REFRESH_AVAILABLE"
    REFRESH_ACTIVE = "REFRESH_ACTIVE"
    ACCEPT_QUEST = "ACCEPT_QUEST"
    EXECUTE_ACTIVE = "EXECUTE_ACTIVE"
    PROFIT_FARM = "PROFIT_FARM"
    WAIT = "WAIT"
    STOP_UNSAFE = "STOP_UNSAFE"


@dataclass(frozen=True)
class QuestRef:
    """Stable action-free identity produced by a quest page parser."""

    id: str
    title: str
    accept_ref: str | None = None
    location: str | None = None
    giver_names: tuple[str, ...] = ()
    catalog_page: int | None = None


@dataclass(frozen=True)
class QuestDirectorState:
    """One immutable observation consumed by :class:`QuestDirectorPolicy`.

    ``available_snapshot_fresh`` means that the current empty/non-empty
    available list was loaded successfully after the last refresh request.
    An integration must clear it after enough farming or when the catalogue
    can no longer be considered authoritative; this prevents an empty-list
    refresh loop while still allowing periodic rediscovery.
    """

    discovery_initialized: bool = False
    available_snapshot_fresh: bool = False
    active_snapshot_fresh: bool = False
    refresh_in_progress: bool = False
    completed_since_refresh: int = 0
    active_quests: tuple[QuestRef, ...] = ()
    available_quests: tuple[QuestRef, ...] = ()
    intake_queue: tuple[QuestRef, ...] = ()
    unsupported_available_count: int = 0


@dataclass(frozen=True)
class QuestDirectorDecision:
    intent: QuestDirectorIntent
    reason: str
    quest: QuestRef | None = None
    intake_queue: tuple[QuestRef, ...] = ()


class QuestDirectorPolicy:
    """Choose one deterministic next step for the autonomous quest loop."""

    def __init__(self, *, refresh_every_completed: int = 5, prefer_active_quests: bool = False) -> None:
        self.refresh_every_completed = refresh_every_completed
        self.prefer_active_quests = prefer_active_quests

    def decide(self, state: QuestDirectorState) -> QuestDirectorDecision:
        invalid = self._invalid_reason(state)
        if invalid:
            return QuestDirectorDecision(QuestDirectorIntent.STOP_UNSAFE, invalid)

        fresh_inputs = state.available_snapshot_fresh and state.active_snapshot_fresh
        queue = _merge_intake_queue(
            state.intake_queue if fresh_inputs else (),
            state.available_quests if fresh_inputs else (),
            excluded_ids={quest.id for quest in state.active_quests},
        )

        if state.refresh_in_progress:
            return QuestDirectorDecision(
                QuestDirectorIntent.WAIT,
                "available_refresh_in_progress",
                intake_queue=queue,
            )

        if not state.discovery_initialized:
            return QuestDirectorDecision(
                QuestDirectorIntent.REFRESH_AVAILABLE,
                "initial_available_refresh_required",
                intake_queue=queue,
            )

        if state.completed_since_refresh >= self.refresh_every_completed:
            return QuestDirectorDecision(
                QuestDirectorIntent.REFRESH_AVAILABLE,
                "completed_quest_refresh_interval_reached",
                intake_queue=queue,
            )

        if not state.active_snapshot_fresh:
            return QuestDirectorDecision(
                QuestDirectorIntent.REFRESH_ACTIVE,
                "active_quest_refresh_required",
                intake_queue=queue,
            )

        if self.prefer_active_quests and state.active_quests:
            return QuestDirectorDecision(
                QuestDirectorIntent.EXECUTE_ACTIVE,
                "active_quest_preferred_over_intake",
                quest=state.active_quests[0],
                intake_queue=queue,
            )

        # Intake all newly discovered quests before choosing one to execute.
        # The integration removes the acknowledged head and calls decide again.
        if queue:
            return QuestDirectorDecision(
                QuestDirectorIntent.ACCEPT_QUEST,
                "available_quest_waiting_for_acceptance",
                quest=queue[0],
                intake_queue=queue,
            )

        if state.active_quests:
            return QuestDirectorDecision(
                QuestDirectorIntent.EXECUTE_ACTIVE,
                "active_quest_ready",
                quest=state.active_quests[0],
            )

        if not state.available_snapshot_fresh:
            return QuestDirectorDecision(
                QuestDirectorIntent.REFRESH_AVAILABLE,
                "active_queue_empty_refresh_required",
            )

        if state.unsupported_available_count:
            return QuestDirectorDecision(
                QuestDirectorIntent.WAIT,
                "unsupported_available_quests_present",
            )

        # Farming is permitted only after one fresh observation proves that
        # both the active set and the available/intake sets are empty.
        return QuestDirectorDecision(
            QuestDirectorIntent.PROFIT_FARM,
            "fresh_catalogue_and_active_queue_empty",
        )

    def _invalid_reason(self, state: QuestDirectorState) -> str | None:
        if not isinstance(state, QuestDirectorState):
            return "invalid_director_state"
        if (
            not isinstance(self.refresh_every_completed, int)
            or isinstance(self.refresh_every_completed, bool)
            or self.refresh_every_completed <= 0
        ):
            return "invalid_refresh_interval"
        if (
            not isinstance(state.discovery_initialized, bool)
            or not isinstance(state.available_snapshot_fresh, bool)
            or not isinstance(state.active_snapshot_fresh, bool)
            or not isinstance(state.refresh_in_progress, bool)
        ):
            return "invalid_director_flags"
        if (
            not isinstance(state.completed_since_refresh, int)
            or isinstance(state.completed_since_refresh, bool)
            or state.completed_since_refresh < 0
            or not isinstance(state.unsupported_available_count, int)
            or isinstance(state.unsupported_available_count, bool)
            or state.unsupported_available_count < 0
        ):
            return "invalid_completed_quest_count"
        all_quests = (*state.active_quests, *state.available_quests, *state.intake_queue)
        if any(not _quest_ref_valid(quest) for quest in all_quests):
            return "invalid_quest_reference"
        if _has_duplicate_ids(state.active_quests):
            return "duplicate_active_quest"
        return None


def _merge_intake_queue(
    queued: Iterable[QuestRef],
    available: Iterable[QuestRef],
    *,
    excluded_ids: set[str],
) -> tuple[QuestRef, ...]:
    result: list[QuestRef] = []
    seen = set(excluded_ids)
    for quest in (*tuple(queued), *tuple(available)):
        if quest.id in seen:
            continue
        seen.add(quest.id)
        result.append(quest)
    return tuple(result)


def _quest_ref_valid(quest: object) -> bool:
    return (
        isinstance(quest, QuestRef)
        and isinstance(quest.id, str)
        and bool(quest.id.strip())
        and quest.id == quest.id.strip()
        and isinstance(quest.title, str)
        and bool(quest.title.strip())
        and (quest.accept_ref is None or (isinstance(quest.accept_ref, str) and bool(quest.accept_ref.strip())))
        and (quest.location is None or (isinstance(quest.location, str) and bool(quest.location.strip())))
        and isinstance(quest.giver_names, tuple)
        and all(isinstance(name, str) and bool(name.strip()) for name in quest.giver_names)
        and (
            quest.catalog_page is None
            or (
                isinstance(quest.catalog_page, int)
                and not isinstance(quest.catalog_page, bool)
                and quest.catalog_page >= 0
            )
        )
    )


def _has_duplicate_ids(quests: Iterable[QuestRef]) -> bool:
    ids = [quest.id for quest in quests]
    return len(ids) != len(set(ids))
