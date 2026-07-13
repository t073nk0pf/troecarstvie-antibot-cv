"""Stateful, action-free coordinator for autonomous quest scheduling."""

from __future__ import annotations

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestCatalogAccumulator
from src.antibot_cv.automation.quest_catalog import QuestCatalogAccumulator
from src.antibot_cv.automation.quest_director_policy import (
    QuestDirectorDecision,
    QuestDirectorPolicy,
    QuestDirectorState,
    QuestRef,
    QuestDirectorIntent,
)


class QuestDirectorRuntime:
    """Maintain director observations while an orchestrator performs actions."""

    def __init__(self, *, refresh_every_completed: int = 5, catalog_max_pages: int = 20) -> None:
        self.policy = QuestDirectorPolicy(refresh_every_completed=refresh_every_completed)
        self.catalog = QuestCatalogAccumulator(max_pages=catalog_max_pages)
        self.active_catalog = ActiveQuestCatalogAccumulator(max_pages=catalog_max_pages)
        self.discovery_initialized = False
        self.available_snapshot_fresh = False
        self.active_snapshot_fresh = False
        self.refresh_in_progress = False
        self.completed_since_refresh = 0
        self.active_quests: tuple[QuestRef, ...] = ()
        self.available_quests: tuple[QuestRef, ...] = ()
        self.intake_queue: tuple[QuestRef, ...] = ()
        self.pending_accept: QuestRef | None = None

    def decision(self) -> QuestDirectorDecision:
        if self.pending_accept is not None:
            return QuestDirectorDecision(
                QuestDirectorIntent.WAIT,
                "quest_accept_in_progress",
                quest=self.pending_accept,
                intake_queue=self.intake_queue,
            )
        result = self.policy.decide(self.snapshot())
        self.intake_queue = result.intake_queue
        return result

    def snapshot(self) -> QuestDirectorState:
        return QuestDirectorState(
            discovery_initialized=self.discovery_initialized,
            available_snapshot_fresh=self.available_snapshot_fresh,
            active_snapshot_fresh=self.active_snapshot_fresh,
            refresh_in_progress=self.refresh_in_progress,
            completed_since_refresh=self.completed_since_refresh,
            active_quests=self.active_quests,
            available_quests=self.available_quests,
            intake_queue=self.intake_queue,
        )

    def begin_catalog_refresh(self) -> None:
        if self.pending_accept is not None:
            raise RuntimeError("cannot refresh catalogue during quest acceptance")
        self.catalog.reset()
        self.available_snapshot_fresh = False
        self.active_snapshot_fresh = False
        self.available_quests = ()
        self.intake_queue = ()
        self.refresh_in_progress = True

    def ingest_catalog_page(self, data: object) -> int | None:
        if not self.refresh_in_progress:
            raise RuntimeError("catalogue refresh has not started")
        self.catalog.ingest(data)
        if not self.catalog.complete:
            return self.catalog.next_page
        self.available_quests = self.catalog.director_refs
        self.discovery_initialized = True
        self.available_snapshot_fresh = True
        self.refresh_in_progress = False
        self.completed_since_refresh = 0
        self.intake_queue = self.policy.decide(self.snapshot()).intake_queue
        return None

    def observe_active(self, items: object) -> None:
        if not isinstance(items, list):
            raise ValueError("active quest items must be a list")
        refs: list[QuestRef] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or item.get("status") != "active":
                raise ValueError("active quest snapshot contains an invalid item")
            quest_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if (
                not quest_id
                or not quest_id.isdecimal()
                or int(quest_id) <= 0
                or not title
                or quest_id in seen
            ):
                raise ValueError("active quest identity is missing or duplicated")
            seen.add(quest_id)
            refs.append(QuestRef(quest_id, title))
        self.active_quests = tuple(refs)
        self.active_snapshot_fresh = True

    def begin_active_refresh(self) -> None:
        self.active_catalog.reset()
        self.active_snapshot_fresh = False

    def ingest_active_page(self, data: object) -> int | None:
        result = self.active_catalog.ingest(data)
        if result is None:
            return self.active_catalog.next_page
        self.active_quests = tuple(QuestRef(entry.id, entry.title) for entry in result)
        self.active_snapshot_fresh = True
        return None

    def begin_accept(self, quest_id: str) -> QuestRef:
        if self.pending_accept is not None:
            raise RuntimeError("quest acceptance is already in progress")
        if not self.available_snapshot_fresh or not self.active_snapshot_fresh:
            raise RuntimeError("quest acceptance requires fresh snapshots")
        if not self.intake_queue or self.intake_queue[0].id != quest_id:
            raise RuntimeError("accepted quest does not match intake queue head")
        self.pending_accept = self.intake_queue[0]
        return self.pending_accept

    def invalidate_active_snapshot(self) -> None:
        self.active_snapshot_fresh = False

    def acknowledge_accept(self, quest_id: str) -> None:
        pending = self.pending_accept
        if pending is None or pending.id != quest_id:
            raise RuntimeError("accepted quest does not match pending acceptance")
        if not self.intake_queue or self.intake_queue[0].id != quest_id:
            raise RuntimeError("accepted quest does not match intake queue head")
        if not self.active_snapshot_fresh:
            raise RuntimeError("accepted quest requires fresh active snapshot")
        confirmed = next((quest for quest in self.active_quests if quest.id == quest_id), None)
        if confirmed is None or _normalized_title(confirmed.title) != _normalized_title(pending.title):
            raise RuntimeError("accepted quest is missing from active snapshot")
        self.intake_queue = self.intake_queue[1:]
        self.available_quests = tuple(quest for quest in self.available_quests if quest.id != quest_id)
        self.pending_accept = None

    def mark_quest_completed(self, quest_id: str) -> None:
        if not any(quest.id == quest_id for quest in self.active_quests):
            raise RuntimeError("completed quest was not active")
        self.active_quests = tuple(quest for quest in self.active_quests if quest.id != quest_id)
        self.completed_since_refresh += 1

    def expire_available_snapshot(self) -> None:
        self.available_snapshot_fresh = False


def _normalized_title(value: str) -> str:
    return " ".join(str(value or "").casefold().split())
