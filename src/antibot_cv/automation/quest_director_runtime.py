"""Stateful, action-free coordinator for autonomous quest scheduling."""

from __future__ import annotations

from src.antibot_cv.automation.quest_catalog import QuestCatalogAccumulator
from src.antibot_cv.automation.quest_director_policy import (
    QuestDirectorDecision,
    QuestDirectorPolicy,
    QuestDirectorState,
    QuestRef,
)


class QuestDirectorRuntime:
    """Maintain director observations while an orchestrator performs actions."""

    def __init__(self, *, refresh_every_completed: int = 5, catalog_max_pages: int = 20) -> None:
        self.policy = QuestDirectorPolicy(refresh_every_completed=refresh_every_completed)
        self.catalog = QuestCatalogAccumulator(max_pages=catalog_max_pages)
        self.discovery_initialized = False
        self.available_snapshot_fresh = False
        self.active_snapshot_fresh = False
        self.refresh_in_progress = False
        self.completed_since_refresh = 0
        self.active_quests: tuple[QuestRef, ...] = ()
        self.available_quests: tuple[QuestRef, ...] = ()
        self.intake_queue: tuple[QuestRef, ...] = ()

    def decision(self) -> QuestDirectorDecision:
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

    def acknowledge_accept(self, quest_id: str) -> None:
        if not self.intake_queue or self.intake_queue[0].id != quest_id:
            raise RuntimeError("accepted quest does not match intake queue head")
        self.intake_queue = self.intake_queue[1:]
        self.available_quests = tuple(quest for quest in self.available_quests if quest.id != quest_id)

    def mark_quest_completed(self, quest_id: str) -> None:
        if not any(quest.id == quest_id for quest in self.active_quests):
            raise RuntimeError("completed quest was not active")
        self.active_quests = tuple(quest for quest in self.active_quests if quest.id != quest_id)
        self.completed_since_refresh += 1

    def expire_available_snapshot(self) -> None:
        self.available_snapshot_fresh = False
