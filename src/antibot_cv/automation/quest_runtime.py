from __future__ import annotations

import time

from src.antibot_cv.automation.actions import ActionRequest
from src.antibot_cv.automation.quest_policy import (
    Quest as PolicyQuest,
    QuestDecision as PolicyQuestDecision,
    QuestIdentity,
    QuestIntent,
    QuestObjectiveKind,
    QuestPolicy,
    QuestProgress,
    QuestSnapshot as PolicyQuestSnapshot,
    QuestStatus,
)
from src.antibot_cv.automation.runtime_helpers import (
    clean_quest_route_label as _clean_quest_route_label,
    extract_quest_combat_targets as _extract_quest_combat_targets,
    normalized_phrase_matches as _normalized_phrase_matches,
    snapshot_epoch_seconds as _snapshot_epoch_seconds,
)
from src.antibot_cv.automation.state_machine import GameState


class QuestRuntimeMixin:
    """Own quest observations, decisions, refreshes, and route handoff."""

    def _init_quest_runtime(self) -> None:
        self._quest_origin_location_name: str | None = None
        self._quest_target_names: tuple[str, ...] = ()
        self._quest_route_locations: tuple[str, ...] = ()
        self._quest_target_routes: dict[str, tuple[str, ...]] = {}
        self._next_quest_refresh_cycle = (
            0
            if self.config.leveling.auto_navigate_quest_targets
            else max(0, int(self.config.leveling.quest_refresh_every_cycles))
        )
        self._quest_refresh_requested_monotonic: float | None = None
        self._quest_policy_intent: QuestIntent | None = None
        self._active_quest_id: str | None = None
        self._last_quest_policy_key: tuple[object, ...] | None = None

    def _evaluate_quest_policy(
        self,
        state_snapshot: object,
        quest_section: dict[str, object],
        quest_data: dict[str, object],
    ) -> PolicyQuestDecision:
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        client_id = self._last_state_snapshot_client_id or self.browser_client_id or ""
        client = global_browser_injector().client_snapshot(client_id)
        profile_id = str(client.get("profile_id") or "")
        tab_id = client.get("tab_id")
        identity = (
            QuestIdentity(client_id, profile_id, int(tab_id))
            if client_id and profile_id and isinstance(tab_id, int) and not isinstance(tab_id, bool)
            else None
        )
        if identity is not None and self._bound_browser_profile_id is None and self._bound_browser_tab_id is None:
            self._bound_browser_profile_id = identity.profile_id
            self._bound_browser_tab_id = identity.tab_id
        expected_identity = (
            QuestIdentity(
                self.browser_client_id or client_id,
                self._bound_browser_profile_id,
                self._bound_browser_tab_id,
            )
            if self._bound_browser_profile_id and self._bound_browser_tab_id is not None
            else None
        )
        raw_items = quest_data.get("items")
        quests: list[PolicyQuest] = []
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    quests.append(PolicyQuest(None, None))
                    continue
                objective = str(item.get("objective") or "").strip()
                target_names = _extract_quest_combat_targets(
                    objective,
                    configured_names=self.config.target.allowed_names,
                )
                locations: list[str] = []
                navigation = item.get("navigation")
                if isinstance(navigation, list):
                    for entry in navigation:
                        if not isinstance(entry, dict):
                            continue
                        location_name = _clean_quest_route_label(entry.get("text") or entry.get("title"))
                        if location_name and location_name not in locations:
                            locations.append(location_name)
                status_text = str(item.get("status") or "").strip().casefold()
                try:
                    status = QuestStatus(status_text)
                except ValueError:
                    status = QuestStatus.UNKNOWN
                kind_text = str(item.get("objectiveKind") or "").strip().casefold()
                kind = (
                    QuestObjectiveKind.COMBAT
                    if target_names and kind_text == "combat"
                    else QuestObjectiveKind.UNKNOWN
                )
                quests.append(
                    PolicyQuest(
                        id=str(item.get("id") or "").strip() or None,
                        title=str(item.get("title") or "").strip() or None,
                        target_mobs=target_names,
                        locations=tuple(locations),
                        status=status,
                        objective_kind=kind,
                        progress=self._quest_progress_from_item(item),
                    )
                )
        snapshot_mapping = state_snapshot if isinstance(state_snapshot, dict) else {}
        source = quest_section.get("source")
        source_href = source.get("href") if isinstance(source, dict) else None
        observed = PolicyQuestSnapshot(
            status=str(quest_data.get("loadStatus") or ""),
            quests=tuple(quests),
            current_location=(
                str(quest_data.get("currentLocation") or "").strip()
                or self._quest_origin_location_name
            ),
            snapshot_id=str(snapshot_mapping.get("snapshotId") or quest_data.get("snapshotId") or "") or None,
            generated_at=_snapshot_epoch_seconds(
                snapshot_mapping.get("generatedAt") or quest_data.get("generatedAt")
            ),
            client_id=client_id or None,
            profile_id=profile_id or None,
            tab_id=int(tab_id) if isinstance(tab_id, int) and not isinstance(tab_id, bool) else None,
            href=str(quest_data.get("href") or source_href or "") or None,
            page_kind=str(quest_data.get("pageKind") or self.current_page_kind or "") or None,
        )
        policy = QuestPolicy(
            self._bound_character_name or self.current_character_name or "",
            require_active_quest=self.config.leveling.auto_navigate_quest_targets,
            require_route_location=self.config.leveling.auto_navigate_quest_targets,
            expected_identity=expected_identity,
            max_snapshot_age_s=max(1.0, self.config.leveling.snapshot_stale_timeout_ms / 1000),
        )
        decision = policy.decide(
            character_name=self.current_character_name,
            current_level=self.current_level,
            current_xp=self.current_xp_percent,
            goal_level=self.config.leveling.target_level,
            quest_state=observed,
        )
        self._apply_quest_policy_decision(decision)
        return decision

    def _apply_quest_policy_decision(self, decision: PolicyQuestDecision) -> None:
        key = (
            decision.intent.value,
            decision.reason,
            decision.quest_id,
            decision.target_mobs,
            decision.locations,
            decision.progress,
            decision.snapshot_id,
        )
        if key != self._last_quest_policy_key:
            self._last_quest_policy_key = key
            self.logger.log_event(
                "quest_policy_decision",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                intent=decision.intent.value,
                reason=decision.reason,
                quest_id=decision.quest_id,
                quest_title=decision.quest_title,
                target_names=list(decision.target_mobs),
                route_locations=list(decision.locations),
                progress_current=decision.progress.current,
                progress_required=decision.progress.required,
                objective_complete=decision.progress.complete,
                progress_evidence=decision.progress.evidence,
                snapshot_id=decision.snapshot_id,
            )
        self._quest_policy_intent = decision.intent
        if decision.intent is QuestIntent.OBJECTIVE_COMPLETE:
            self._active_quest_id = decision.quest_id
            self._quest_target_names = ()
            self._quest_route_locations = decision.locations
            self._quest_target_routes = {}
            return
        if decision.intent not in {QuestIntent.SELECT_QUEST, QuestIntent.NAVIGATE}:
            self._active_quest_id = None
            self._quest_target_names = ()
            self._quest_route_locations = ()
            self._quest_target_routes = {}
            return
        self._active_quest_id = decision.quest_id
        self._quest_target_names = decision.target_mobs
        self._quest_route_locations = decision.locations
        self._quest_target_routes = {
            target: decision.locations
            for target in decision.target_mobs
        }

    @staticmethod
    def _quest_progress_from_item(item: dict[str, object]) -> QuestProgress:
        raw = item.get("progress")
        progress = raw if isinstance(raw, dict) else {}
        current = progress.get("current")
        required = progress.get("required")
        current_value = current if isinstance(current, int) and not isinstance(current, bool) and current >= 0 else None
        required_value = required if isinstance(required, int) and not isinstance(required, bool) and required > 0 else None
        complete = progress.get("complete") is True or (
            current_value is not None and required_value is not None and current_value >= required_value
        )
        evidence = str(progress.get("evidence") or "").strip() or None
        return QuestProgress(current_value, required_value, complete, evidence)

    def _update_quest_target_names(self, quest_data: object) -> None:
        if not isinstance(quest_data, dict):
            return
        items = quest_data.get("items")
        if not isinstance(items, list):
            return
        names: list[str] = []
        route_locations: list[str] = []
        target_routes: dict[str, tuple[str, ...]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            objective = str(item.get("objective") or "").strip()
            navigation = item.get("navigation")
            if not objective:
                continue
            item_routes: list[str] = []
            if isinstance(navigation, list):
                for entry in navigation:
                    if not isinstance(entry, dict):
                        continue
                    location = _clean_quest_route_label(entry.get("text") or entry.get("title"))
                    if not location:
                        continue
                    if location not in item_routes:
                        item_routes.append(location)
                    if location not in route_locations:
                        route_locations.append(location)
            item_targets = _extract_quest_combat_targets(
                objective,
                configured_names=self.config.target.allowed_names,
            )
            for name in item_targets:
                if name not in names:
                    names.append(name)
                target_routes[name] = tuple(item_routes)
        updated_names = tuple(names)
        updated_routes = tuple(route_locations)
        routes_changed = updated_routes != self._quest_route_locations or target_routes != self._quest_target_routes
        if updated_names != self._quest_target_names or routes_changed:
            self._quest_target_names = updated_names
            self._quest_route_locations = updated_routes
            self._quest_target_routes = target_routes
            self.logger.log_event(
                "quest_targets_observed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                target_names=list(self._quest_target_names),
                route_locations=list(self._quest_route_locations),
            )

    def _select_quest_route_target(self) -> str | None:
        configured = str(self.config.leveling.target_location_name or "").strip()
        if configured:
            matches = [
                location
                for location in self._quest_route_locations
                if _normalized_phrase_matches(location, configured)
            ]
            return matches[0] if len(matches) == 1 else None
        for target in self._quest_target_names:
            routes = self._quest_target_routes.get(target, ())
            if len(routes) == 1:
                return routes[0]
        if len(self._quest_route_locations) == 1:
            return self._quest_route_locations[0]
        return None

    def _effective_target_names(self) -> tuple[str, ...]:
        configured = tuple(name for name in self.config.target.allowed_names if name)
        if configured:
            return configured
        if self.config.leveling.auto_navigate_quest_targets:
            return self._quest_target_names
        return ()

    def _maybe_start_quest_refresh(self) -> bool:
        config = self.config.leveling
        if not config.enabled or int(config.quest_refresh_every_cycles) <= 0:
            return False
        if self.session.completed_cycles < self._next_quest_refresh_cycle:
            return False
        if self.current_page_kind == "quests":
            self._quest_refresh_requested_monotonic = time.monotonic()
            self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason="quest_page_already_open")
            return True
        if self.current_page_kind not in {"hunt", "area", "main"}:
            return False
        request = ActionRequest(
            "open_quests",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": "periodic_quest_refresh"},
        )
        if not self.action_executor.execute(request):
            if self.config.leveling.auto_navigate_quest_targets:
                return self._stop_leveling_unsafe("quest_refresh_open_failed")
            interval = max(1, int(config.quest_refresh_every_cycles))
            self._next_quest_refresh_cycle = self.session.completed_cycles + interval
            self.logger.log_event(
                "quest_refresh_skipped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="quest_control_missing_optional",
                next_refresh_cycle=self._next_quest_refresh_cycle,
            )
            return False
        self._quest_refresh_requested_monotonic = time.monotonic()
        self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason="periodic_quest_refresh")
        return True

    def _handle_quest_refresh(self) -> None:
        started = self._quest_refresh_requested_monotonic or time.monotonic()
        if self.current_page_kind == "quests":
            if self.config.leveling.auto_navigate_quest_targets:
                if self._quest_policy_intent is QuestIntent.OBJECTIVE_COMPLETE:
                    self._stop_leveling_unsafe("quest_objective_complete_turn_in_pending")
                    return
                if self._quest_policy_intent is QuestIntent.SELECT_QUEST:
                    self._finish_quest_refresh_to_hunt("quest_target_already_local")
                    return
                if self._quest_policy_intent is not QuestIntent.NAVIGATE:
                    self._stop_leveling_unsafe("quest_policy_not_ready_for_navigation")
                    return
                target = self._select_quest_route_target()
                if not target:
                    self.logger.log_event(
                        "quest_route_skipped",
                        state=self.state_machine.state.value,
                        cycle_id=self.session.cycle_id,
                        reason="route_target_missing_or_ambiguous",
                        route_locations=list(self._quest_route_locations),
                    )
                    self._stop_leveling_unsafe("quest_route_target_missing_or_ambiguous")
                    return
                request = ActionRequest(
                    "open_quest_navigator",
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    dry_run=self.config.dry_run,
                    metadata={"reason": "quest_location_route", "target": target},
                )
                if not self.action_executor.execute(request):
                    self._stop_leveling_unsafe("quest_navigator_open_failed")
                    return
                self._navigator_target_name = target
                self._navigator_target_kind = "location"
                self._navigator_opened_monotonic = time.monotonic()
                self._navigator_client_id = None
                self._navigator_requires_target_selection = False
                self._route_recovery_kind = "quest_location"
                self._safe_transition(GameState.NAVIGATOR_PENDING, reason="quest_navigator_opened")
                return
            self._finish_quest_refresh_to_hunt("quest_refresh_complete")
            return
        timeout_ms = max(1000, int(self.config.leveling.quest_refresh_timeout_ms))
        if (time.monotonic() - started) * 1000 >= timeout_ms:
            self._stop_leveling_unsafe("quest_refresh_timeout")

    def _finish_quest_refresh_to_hunt(self, reason: str) -> bool:
        request = ActionRequest(
            "open_hunt",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": reason},
        )
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe("quest_refresh_return_failed")
        interval = max(1, int(self.config.leveling.quest_refresh_every_cycles))
        self._next_quest_refresh_cycle = self.session.completed_cycles + interval
        self._quest_refresh_requested_monotonic = None
        self._navigator_target_name = None
        self._navigator_target_kind = "location"
        self._navigator_opened_monotonic = None
        self._navigator_client_id = None
        self._navigator_existing_client_ids.clear()
        self._navigator_requires_target_selection = False
        self._route_recovery_kind = None
        self._route_go_submitted_monotonic = None
        self._route_destination_name = None
        self._route_destination_id = None
        self._route_expected_transitions = None
        self._route_step_submitted_from_id = None
        self._route_step_submitted_monotonic = None
        self._route_resume_target_name = None
        self._reset_location_context()
        self._search_pause_until_monotonic = (
            time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
        )
        self._safe_transition(GameState.LOCATION_SEARCH, reason=reason)
        if self.state_machine.state is GameState.LOCATION_SEARCH:
            self._log_recovery_phase("hunt_opened", reason=reason)
            self._complete_death_recovery_evidence(reason)
        return True
