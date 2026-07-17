"""Controller-facing route-only coordinator for ordered NPC handoffs."""

from __future__ import annotations

import json
import time

from src.antibot_cv.automation.quest_ordered_npc_handoff import (
    OrderedHandoffStatus,
    make_ordered_handoff_cursor,
    parse_ordered_npc_handoff,
)
from src.antibot_cv.automation.runtime_helpers import same_location_name, snapshot_epoch_seconds


class QuestOrderedHandoffCoordinatorMixin:
    """Stage one first-clause route and observe arrival without NPC actions."""

    def _init_quest_ordered_handoff(self) -> None:
        self._ordered_handoff_active_snapshot_id: str | None = None
        self._ordered_handoff_active_snapshot_generated_at: float | None = None

    def _begin_ordered_npc_handoff(self, entry) -> bool:
        director = self._quest_director
        if director is None or director.chain.lease is None:
            return self._stop_leveling_unsafe("ordered_handoff_lease_missing")
        plan = parse_ordered_npc_handoff(entry)
        if plan.status is not OrderedHandoffStatus.READY:
            return self._defer_active_quest(
                entry.id, f"quest_dialogue_ordered_handoff:{plan.reason}",
            )
        restored = director.chain.ordered_handoff_cursor
        if restored is not None:
            if (
                restored.quest_id != entry.id or restored.quest_title != entry.title
                or restored.fingerprint != plan.fingerprint
                or restored.requirements != plan.requirements
                or restored.lease_fingerprint != director.chain.lease.current_fingerprint
            ):
                return self._stop_leveling_unsafe("ordered_handoff_restored_identity_mismatch")
            # A restart never reissues the navigator mutation.  It may only
            # reconcile an exact already-arrived area via read-only telemetry.
            if same_location_name(self.current_location_name, restored.target_location):
                return self._observe_ordered_handoff_arrival(restored, "ordered_handoff_restart_arrived")
            self.logger.log_event(
                "quest_ordered_handoff_restart_wait",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                quest_id=restored.quest_id,
                target=restored.target_location,
                cursor_ordinal=restored.ordinal,
                mutation_reissued=False,
            )
            return True

        identity = self._ordered_handoff_browser_identity()
        snapshot_id = self._ordered_handoff_active_snapshot_id
        generated_at = self._ordered_handoff_active_snapshot_generated_at
        if identity is None or not snapshot_id or generated_at is None:
            return self._stop_leveling_unsafe("ordered_handoff_causal_baseline_missing")
        staged_at = max(time.time(), generated_at + 0.001)
        try:
            cursor = make_ordered_handoff_cursor(
                plan,
                lease_fingerprint=director.chain.lease.current_fingerprint,
                active_catalog_revision=director.active_catalog.revision,
                active_snapshot_id=snapshot_id,
                active_snapshot_generated_at=generated_at,
                client_id=identity[0], profile_id=identity[1], tab_id=identity[2],
                staged_at=staged_at,
            )
            director.chain.stage_ordered_handoff(cursor)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._stop_leveling_unsafe(f"ordered_handoff_stage:{exc}")
        self.logger.log_event(
            "quest_ordered_handoff_route_staged",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=cursor.quest_id,
            quest_title=cursor.quest_title,
            fingerprint=cursor.fingerprint,
            target=cursor.target_location,
            navigation_label=cursor.target_navigation_label,
            cursor_ordinal=cursor.ordinal,
            active_catalog_revision=cursor.active_catalog_revision,
        )
        if same_location_name(self.current_location_name, cursor.target_location):
            return self._observe_ordered_handoff_arrival(cursor, "ordered_handoff_already_arrived")
        return self._start_location_route(
            cursor.target_location,
            kind="quest_ordered_handoff",
            reason="quest_ordered_handoff_route",
        )

    def _on_ordered_handoff_route_arrived(self, reason: str) -> bool:
        director = self._quest_director
        cursor = director.chain.ordered_handoff_cursor if director is not None else None
        if cursor is None:
            return self._stop_leveling_unsafe("ordered_handoff_cursor_missing")
        if not same_location_name(self.current_location_name, cursor.target_location):
            return self._stop_leveling_unsafe("ordered_handoff_arrival_location_mismatch")
        return self._observe_ordered_handoff_arrival(cursor, reason)

    def _observe_ordered_handoff_arrival(self, cursor, reason: str) -> bool:
        """Read fresh exact NPC names; deliberately keep cursor unchanged."""

        identity = self._ordered_handoff_browser_identity()
        if identity != (cursor.client_id, cursor.profile_id, cursor.tab_id):
            return self._stop_leveling_unsafe("ordered_handoff_arrival_client_mismatch")
        if self.current_page_kind != "area":
            self.logger.log_event(
                "quest_ordered_handoff_area_wait",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                quest_id=cursor.quest_id,
                page_kind=self.current_page_kind,
                cursor_ordinal=cursor.ordinal,
            )
            timeout_s = max(
                5.0,
                min(120.0, self.config.leveling.quest_refresh_timeout_ms / 1000 * 3),
            )
            if time.time() >= cursor.staged_at + timeout_s:
                return self._defer_active_quest(
                    cursor.quest_id,
                    "quest_ordered_handoff_area_wait_expired",
                )
            return True
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        result = global_browser_injector().execute(
            "area_npc_snapshot", {}, timeout_s=2.5, client_id=cursor.client_id,
        )
        if result.client_id != cursor.client_id or not result.ok:
            return self._stop_leveling_unsafe("ordered_handoff_area_snapshot_failed")
        try:
            snapshot = json.loads(result.message)
        except (TypeError, json.JSONDecodeError):
            return self._stop_leveling_unsafe("ordered_handoff_area_snapshot_invalid")
        generated_at = snapshot_epoch_seconds(snapshot.get("generatedAt")) if isinstance(snapshot, dict) else None
        location = snapshot.get("location") if isinstance(snapshot, dict) else None
        items = snapshot.get("items") if isinstance(snapshot, dict) else None
        if (
            not isinstance(snapshot, dict) or snapshot.get("message") != "area_npc_snapshot"
            or snapshot.get("pageKind") != "area" or snapshot.get("truncated") is not False
            or generated_at is None or generated_at <= cursor.staged_at
            or time.time() - generated_at > 5.0
            or not isinstance(location, dict)
            or not same_location_name(location.get("name"), cursor.target_location)
            or not isinstance(items, list)
        ):
            return self._stop_leveling_unsafe("ordered_handoff_area_snapshot_not_fresh_exact")
        exact_names = tuple(
            item.get("name") for item in items
            if isinstance(item, dict) and isinstance(item.get("name"), str)
            and item.get("name") == item.get("name").strip() and item.get("name")
        )
        self.logger.log_event(
            "quest_ordered_handoff_npc_discovery",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=cursor.quest_id,
            quest_title=cursor.quest_title,
            target=cursor.target_location,
            npc_mention=cursor.requirements[0].npc_mention,
            exact_npc_names=list(exact_names),
            snapshot_id=snapshot.get("snapshotId"),
            cursor_ordinal=cursor.ordinal,
            cursor_advanced=False,
            reason=reason,
        )
        # This coordinator deliberately has no NPC mutation executor yet.
        # One exact, fresh discovery is enough evidence to skip the unsupported
        # step; retaining the cursor would repeat the same arrival forever.
        deferred = self._defer_active_quest(
            cursor.quest_id,
            "quest_dialogue_ordered_handoff_executor_unavailable",
        )
        if self.state_machine.state.value == "STOPPED":
            return deferred
        self._clear_location_route_tracking()
        from src.antibot_cv.automation.state_machine import GameState

        if self.state_machine.state is not GameState.QUEST_REFRESH_PENDING:
            self._safe_transition(
                GameState.QUEST_REFRESH_PENDING,
                reason="ordered_handoff_unsupported_skipped",
            )
        return deferred

    def _ordered_handoff_browser_identity(self) -> tuple[str, str, int] | None:
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        client_id = self._last_state_snapshot_client_id or self.browser_client_id or ""
        client = global_browser_injector().client_snapshot(client_id) if client_id else {}
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        replay = self.config.dry_run or self.action_executor.sink.__class__.__name__ in {
            "DryRunActionSink", "ReplayActionSink",
        }
        if replay:
            client_id = client_id or "replay-client"
            profile_id = profile_id or "replay-profile"
            tab_id = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else 0
        if not client_id or not profile_id or not isinstance(tab_id, int) or isinstance(tab_id, bool):
            return None
        return client_id, profile_id, tab_id
