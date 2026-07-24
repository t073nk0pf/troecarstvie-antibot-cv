"""Thin controller integration for snapshot-bound quest chat evidence."""

from __future__ import annotations

from dataclasses import replace
import time

from src.antibot_cv.automation.quest_chat_progress import (
    AuthoritativeQuestStep,
    PendingQuestChatRefresh,
    QuestChatProgressTracker,
    QuestChatReconcileState,
    reconcile_chat_refresh,
)
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint


class QuestChatProgressCoordinatorMixin:
    def _init_quest_chat_progress(self) -> None:
        self._quest_chat_progress = QuestChatProgressTracker(
            max_age_s=max(1.0, self.config.leveling.snapshot_stale_timeout_ms / 1000),
        )
        self._quest_chat_refresh_pending: PendingQuestChatRefresh | None = None
        self._quest_chat_authoritative_step: AuthoritativeQuestStep | None = None
        self._quest_chat_terminal_completion_evidence = None

    def _bound_quest_chat_step(self) -> AuthoritativeQuestStep | None:
        director = self._quest_director
        if director is None or director.chain.lease is None:
            return None
        lease = director.chain.lease
        if director.active_catalog.complete:
            matches = [entry for entry in director.active_catalog.result if entry.id == lease.quest_id]
            if len(matches) != 1 or matches[0].title != lease.quest_title:
                self._quest_chat_authoritative_step = None
                return None
            entry = matches[0]
            objective = entry.data.get("objective")
            fingerprint, _ = quest_step_fingerprint(entry)
            if (
                not isinstance(objective, str)
                or not objective.strip()
                or fingerprint is None
                or fingerprint != lease.current_fingerprint
            ):
                self._quest_chat_authoritative_step = None
                return None
            self._quest_chat_authoritative_step = AuthoritativeQuestStep(
                lease.quest_id, lease.quest_title, lease.current_fingerprint, objective
            )
        cached = self._quest_chat_authoritative_step
        if (
            cached is None
            or cached.quest_id != lease.quest_id
            or cached.quest_title != lease.quest_title
            or cached.fingerprint != lease.current_fingerprint
        ):
            return None
        return cached

    def _observe_quest_chat_progress(self, state_snapshot: object, chat_section: object) -> None:
        director = self._quest_director
        step = self._bound_quest_chat_step()
        if director is None or step is None:
            return
        snapshot = state_snapshot if isinstance(state_snapshot, dict) else {}
        evidence = self._quest_chat_progress.observe(
            chat_section,
            snapshot_generated_at=snapshot.get("generatedAt"),
            step=step,
        )
        if evidence is None or self._quest_chat_refresh_pending is not None:
            return
        trigger_snapshot_id = str(snapshot.get("snapshotId") or "").strip()
        if not trigger_snapshot_id:
            return
        now = time.monotonic()
        timeout_s = max(
            8.0,
            3.0 * max(1.0, self.config.leveling.quest_refresh_timeout_ms / 1000),
        )
        self._quest_chat_refresh_pending = PendingQuestChatRefresh(
            evidence,
            director.active_catalog.revision,
            trigger_snapshot_id,
            now,
            now + timeout_s,
        )
        self.logger.log_event(
            "quest_chat_progress_observed",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=evidence.quest_id,
            quest_fingerprint=evidence.fingerprint,
            resource=evidence.resource,
            chat_event_id=evidence.event_id,
            outcome="active_catalog_refresh_pending",
            trigger_revision=director.active_catalog.revision,
            trigger_snapshot_id=trigger_snapshot_id,
        )

    def _reconcile_quest_chat_refresh(self) -> None:
        pending, director = self._quest_chat_refresh_pending, self._quest_director
        if pending is None or director is None or not director.active_catalog.complete:
            return
        result = reconcile_chat_refresh(
            pending,
            director.active_catalog.result,
            completed_revision=director.active_catalog.revision,
        )
        fields = {
            "state": self.state_machine.state.value,
            "cycle_id": self.session.cycle_id,
            "quest_id": pending.evidence.quest_id,
            "resource": pending.evidence.resource,
            "trigger_revision": pending.trigger_revision,
            "completed_revision": director.active_catalog.revision,
            "attempts": pending.attempts,
            "outcome": result.state.value,
            "reason": result.reason,
        }
        if result.state is QuestChatReconcileState.NOT_NEWER:
            self.logger.log_event("quest_chat_progress_reconcile_wait", **fields)
        elif result.state is QuestChatReconcileState.ADVANCED:
            self._quest_chat_refresh_pending = None
            self.logger.log_event("quest_chat_progress_reconciled", **fields)
        elif result.state is QuestChatReconcileState.SAME_STEP:
            # The line confirms one resource requirement, not necessarily the
            # whole (possibly composite) step.  Keep normal objective routing;
            # a dedicated sub-requirement tracker may select the next part.
            self._quest_chat_refresh_pending = None
            if pending.evidence.terminal_collection:
                self._quest_chat_terminal_completion_evidence = pending.evidence
            self.logger.log_event("quest_chat_resource_requirement_confirmed", **fields)
        else:
            self._quest_chat_refresh_pending = None
            self.logger.log_event("quest_chat_progress_reconcile_unsafe", **fields)
            self._stop_leveling_unsafe(f"quest_chat_progress_reconcile:{result.reason}")

    def _handle_pending_quest_chat_refresh(self, *, allow_active_progress: bool = False) -> bool:
        pending = self._quest_chat_refresh_pending
        if pending is None:
            return False
        now = time.monotonic()
        if now >= pending.deadline_monotonic:
            self._quest_chat_refresh_pending = None
            self.logger.log_event(
                "quest_chat_progress_exhausted",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                quest_id=pending.evidence.quest_id,
                resource=pending.evidence.resource,
                attempts=pending.attempts,
                outcome="ttl_exhausted",
            )
            return self._stop_leveling_unsafe("quest_chat_progress_refresh_ttl_exhausted")
        if self._quest_active_snapshot_requested:
            # Outside QUEST_REFRESH_PENDING this remains a hard admission
            # barrier.  Inside the refresh handler, yield so its pagination
            # branch can request page 1..N and eventually reconcile us.
            return not allow_active_progress
        # Item recovery can finish while its scheduled main-frame reload is
        # still settling.  Starting catalogue navigation in that bounded
        # window lets the delayed recovery navigation overwrite the quest
        # page, leaving a mutation-possible ACK_PENDING that must never be
        # reissued.  Reuse the recovery owner's existing settle lease before
        # staging the first catalogue mutation.
        recovery_settle_until = getattr(self, "_search_pause_until_monotonic", None)
        if (
            isinstance(recovery_settle_until, (int, float))
            and not isinstance(recovery_settle_until, bool)
            and now < float(recovery_settle_until)
        ):
            return True
        if now < pending.next_retry_monotonic:
            return True
        self._quest_chat_refresh_pending = replace(
            pending,
            attempts=pending.attempts + 1,
            dedicated_refresh_started=True,
        )
        return self._request_active_quest_snapshot("quest_chat_progress_refresh")
