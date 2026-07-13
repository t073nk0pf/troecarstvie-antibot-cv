from __future__ import annotations

import time

from src.antibot_cv.automation.actions import ActionRequest
from src.antibot_cv.automation.death_recovery import (
    RecoveryCheckpoint,
    RecoveryDecision,
    RecoverySnapshot,
    ReviveOption,
)
from src.antibot_cv.automation.leveling_policy import (
    Death as LevelingDeath,
    Inventory as LevelingInventory,
    Intent as LevelingIntent,
    LevelingPolicy,
    Location as LevelingLocation,
    ObservedPlayer as LevelingPlayer,
    Quests as LevelingQuests,
)
from src.antibot_cv.automation.quest_policy import QuestIntent
from src.antibot_cv.automation.runtime_helpers import (
    is_semantic_location_name as _is_semantic_location_name,
    navigator_target_kind as _navigator_target_kind,
    optional_float as _optional_float,
    optional_int as _optional_int,
    same_location_name as _same_location_name,
)
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.telemetry.m1_recovery import M1_RECOVERY_PHASES


class LevelingRuntimeMixin:
    def _update_location_tracking(
        self,
        location: object,
        death_data: object,
        player: object,
    ) -> None:
        if not isinstance(location, dict):
            self.current_page_kind = None
            self.current_location_name = None
            return
        self.current_page_kind = str(location.get("pageKind") or "").strip() or None
        self.current_location_name = str(location.get("semanticName") or "").strip() or None
        dead = death_data.get("dead") if isinstance(death_data, dict) else None
        hp_percent = _optional_float(player.get("hpPercent")) if isinstance(player, dict) else None
        alive = dead is False or (dead is not True and hp_percent is not None and hp_percent > 0)
        recovery_active = self._death_latched or self.state_machine.state in {
            GameState.DEAD,
            GameState.REVIVE_PENDING,
        }
        if (
            alive
            and not recovery_active
            and self.current_page_kind == "area"
            and self.current_location_name
        ):
            self._last_alive_location_name = self.current_location_name
            self._quest_origin_location_name = self.current_location_name

    def _bind_or_reject_character(self, observed_name: str) -> bool:
        name = str(observed_name or "").strip()
        if not name:
            return False
        expected_name = str(self._bound_character_name or "").strip()
        if expected_name and name.casefold() != expected_name.casefold():
            self.last_error_reason = f"character_mismatch:{name}"
            self.logger.log_event(
                "session_stopped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="character_mismatch",
                expected_character=expected_name,
                observed_character=name,
            )
            self._safe_transition(GameState.STOPPED, reason="character_mismatch")
            return True
        if self._bound_character_name is None:
            self._bound_character_name = name
            self.logger.log_event(
                "character_bound",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                character_name=name,
                browser_client_id=self.browser_client_id,
            )
        return False

    def _observe_leveling_goal(self) -> bool:
        leveling = self.config.leveling
        if self.config.dry_run or not leveling.enabled:
            return False
        snapshot = self._state_snapshot_via_injector()
        if isinstance(snapshot, dict):
            self._current_state_snapshot_id = str(snapshot.get("snapshotId") or "") or None
        freshness_base = self._last_state_snapshot_success_monotonic or self._leveling_started_monotonic
        snapshot_age_ms = (time.monotonic() - freshness_base) * 1000
        if snapshot_age_ms >= max(1000, int(leveling.snapshot_stale_timeout_ms)):
            return self._stop_leveling_unsafe("leveling_state_snapshot_stale")
        sections = snapshot.get("sections") if isinstance(snapshot, dict) else None
        player_section = sections.get("player") if isinstance(sections, dict) else None
        player = player_section.get("data") if isinstance(player_section, dict) else None
        quest_section = sections.get("quests") if isinstance(sections, dict) else None
        quest_data = quest_section.get("data") if isinstance(quest_section, dict) else None
        death_section = sections.get("deathRevive") if isinstance(sections, dict) else None
        death_data = death_section.get("data") if isinstance(death_section, dict) else None
        location_section = sections.get("location") if isinstance(sections, dict) else None
        location = location_section.get("data") if isinstance(location_section, dict) else None
        self._update_location_tracking(location, death_data, player)
        battle_section = sections.get("battle") if isinstance(sections, dict) else None
        battle_data = battle_section.get("data") if isinstance(battle_section, dict) else None
        level = _optional_int(player.get("level")) if isinstance(player, dict) else None
        name = str(player.get("name") or "").strip() if isinstance(player, dict) else ""
        xp_percent = _optional_float(player.get("xpPercent")) if isinstance(player, dict) else None
        if level is None or not name:
            if self._handle_leveling_death(death_data):
                return True
            last_valid = self._last_valid_player_snapshot_monotonic or self._leveling_started_monotonic
            stale_ms = (time.monotonic() - last_valid) * 1000
            if stale_ms >= max(1000, int(leveling.snapshot_stale_timeout_ms)):
                self.last_error_reason = "leveling_player_snapshot_stale"
                self.logger.log_event(
                    "session_stopped",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    reason=self.last_error_reason,
                    page_kind=self.current_page_kind,
                    stale_ms=stale_ms,
                )
                self._safe_transition(GameState.STOPPED, reason=self.last_error_reason)
                return True
            return False
        if self._last_state_snapshot_success_monotonic is not None:
            self._last_valid_player_snapshot_monotonic = self._last_state_snapshot_success_monotonic
        self.current_character_name = name
        self.current_level = level
        self.current_xp_percent = xp_percent
        observation = (name, level, xp_percent, self.current_page_kind, self.current_location_name)
        if observation != self._last_player_observation:
            self._last_player_observation = observation
            self.logger.log_event(
                "player_observed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                character_name=name,
                level=level,
                xp_percent=xp_percent,
                page_kind=self.current_page_kind,
                location_name=self.current_location_name,
            )
        if self._bind_or_reject_character(name):
            return True
        target_level = _optional_int(leveling.target_level)
        if target_level is not None and level >= target_level:
            self.logger.log_event(
                "goal_level_reached",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                character_name=name,
                current_level=level,
                target_level=target_level,
                xp_percent=xp_percent,
            )
            self._safe_transition(GameState.STOPPED, reason="goal_level_reached")
            return True
        if (
            isinstance(quest_section, dict)
            and isinstance(quest_data, dict)
            and quest_data.get("loadStatus") == "loaded"
        ):
            quest_decision = self._evaluate_quest_policy(snapshot, quest_section, quest_data)
            if (
                self.config.leveling.auto_navigate_quest_targets
                and quest_decision.intent is QuestIntent.STOP_UNSAFE
            ):
                return self._stop_leveling_unsafe(f"quest_policy:{quest_decision.reason}")
        if (
            (isinstance(death_data, dict) and death_data.get("dead") is True)
            or self.state_machine.state in {GameState.DEAD, GameState.REVIVE_PENDING}
        ):
            if self._handle_leveling_death(death_data):
                return True
        if self.current_page_kind == "battle" or (
            isinstance(battle_data, dict)
            and (battle_data.get("rawHasFight") is True or battle_data.get("hasFight") is True)
        ):
            reason = (
                "battle_resolution_pending"
                if isinstance(battle_data, dict) and battle_data.get("finished") is True
                else "battle_in_progress"
            )
            self._record_leveling_wait(reason)
            return False
        if self.state_machine.state in {
            GameState.POST_REVIVE_RECOVERY,
            GameState.QUEST_REFRESH_PENDING,
            GameState.NAVIGATOR_PENDING,
            GameState.ROUTE_RECOVERY,
        }:
            reason = (
                "post_revive_resource_recovery"
                if self.state_machine.state is GameState.POST_REVIVE_RECOVERY
                else "navigation_in_progress"
            )
            self._record_leveling_wait(reason)
            return False
        if self._maybe_start_configured_location_route():
            return True
        decision = self._record_leveling_decision(player, death_data, sections)
        if decision is not None and decision.intent is LevelingIntent.STOP_UNSAFE:
            return self._stop_leveling_unsafe(f"leveling_policy:{decision.reason}")
        return self._handle_leveling_death(death_data)

    def _maybe_start_configured_location_route(self) -> bool:
        config = self.config.leveling
        target = str(config.target_location_name or "").strip()
        if (
            not config.enabled
            or config.auto_navigate_quest_targets
            or not target
            or self.state_machine.state not in {GameState.LOCATION_SEARCH, GameState.VIEWPORT_SCAN}
            or _same_location_name(self.current_location_name, target)
            or _same_location_name(self._configured_route_completed_target, target)
        ):
            return False
        if (
            self.current_page_kind == "hunt"
            and not self.current_location_name
            and not self._last_alive_location_name
        ):
            request = ActionRequest(
                "open_area",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={"reason": "capture_configured_route_checkpoint", "target": target},
            )
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("configured_location_area_open_failed")
            self._search_pause_until_monotonic = time.monotonic() + 0.5
            self.logger.log_event(
                "configured_location_route_preparing",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                page_kind=self.current_page_kind,
                target=target,
                reason="capture_configured_route_checkpoint",
            )
            return True
        if self.current_page_kind not in {"area", "hunt", "main"}:
            if self.current_page_kind not in {"quests", "inventory", "shop", "statistics"}:
                return False
            request = ActionRequest(
                "open_hunt",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={"reason": "prepare_configured_location_route", "target": target},
            )
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("configured_location_area_open_failed")
            self._search_pause_until_monotonic = time.monotonic() + 0.5
            self.logger.log_event(
                "configured_location_route_preparing",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                page_kind=self.current_page_kind,
                target=target,
            )
            return True
        request = ActionRequest(
            "open_location_navigator",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "reason": "configured_location_route",
                "target": target,
                "current_location": self.current_location_name,
            },
        )
        self._navigator_existing_client_ids = self._navigator_client_ids_for_parent()
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe("configured_location_navigator_open_failed")
        self._navigator_target_name = target
        self._navigator_target_kind = _navigator_target_kind(target)
        self._navigator_opened_monotonic = time.monotonic()
        self._navigator_client_id = None
        self._navigator_client_bound_monotonic = None
        self._navigator_requires_target_selection = True
        self._route_recovery_kind = "configured_location"
        self._route_go_submitted_monotonic = None
        self._safe_transition(GameState.NAVIGATOR_PENDING, reason="configured_location_navigator_opened")
        self.logger.log_event(
            "configured_location_route_started",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            current_location=self.current_location_name,
            target=target,
        )
        return True

    def _record_leveling_wait(self, reason: str) -> None:
        if (self.last_leveling_intent, self.last_leveling_reason) == (LevelingIntent.WAIT.value, reason):
            return
        self.last_leveling_intent = LevelingIntent.WAIT.value
        self.last_leveling_reason = reason
        self.logger.log_event(
            "leveling_decision",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            intent=LevelingIntent.WAIT.value,
            reason=reason,
            item=None,
            character_name=self.current_character_name,
            current_level=self.current_level,
            goal_level=self.config.leveling.target_level,
        )

    def _record_leveling_decision(
        self,
        player: object,
        death_data: object,
        sections: object,
    ) -> object | None:
        if not isinstance(player, dict) or self.current_level is None or not self.current_character_name:
            return None
        hp_percent = _optional_float(player.get("hpPercent"))
        prowess_percent = _optional_float(player.get("prowessPercent"))
        dead = death_data.get("dead") if isinstance(death_data, dict) else None
        alive = False if dead is True else True if dead is False else None
        if alive is None and self.current_page_kind == "hunt" and isinstance(sections, dict):
            hunt_section = sections.get("hunt")
            hunt_data = hunt_section.get("data") if isinstance(hunt_section, dict) else None
            if isinstance(hunt_data, dict) and hunt_data.get("hasHunt") is True:
                alive = True
        target_location = str(self.config.leveling.target_location_name or "").strip()
        configured_route_resolved = bool(
            target_location
            and _same_location_name(self._configured_route_completed_target, target_location)
        )
        policy_location_name = self.current_location_name
        if (
            not policy_location_name
            and self.current_page_kind == "hunt"
            and configured_route_resolved
        ):
            policy_location_name = self._last_alive_location_name
        page_kind_known = bool(self.current_page_kind)
        location_safe: bool | None = True if page_kind_known else None
        if target_location:
            if configured_route_resolved and policy_location_name:
                location_safe = True
            else:
                location_safe = (
                    None
                    if not policy_location_name
                    else _same_location_name(policy_location_name, target_location)
                )
        inventory_items: dict[str, int] | None = None
        shop_section = sections.get("shopInventory") if isinstance(sections, dict) else None
        shop_data = shop_section.get("data") if isinstance(shop_section, dict) else None
        raw_items = shop_data.get("items") if isinstance(shop_data, dict) else None
        if isinstance(raw_items, list):
            inventory_items = {}
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                count = _optional_int(item.get("count")) or 0
                if title:
                    inventory_items[title] = max(inventory_items.get(title, 0), count)
        policy = LevelingPolicy(
            required_character=self._bound_character_name or self.current_character_name,
            target_level=int(self.config.leveling.target_level or self.current_level + 1),
            # A zero-death session may run until the first observed death, at
            # which point DeathRecoveryPolicy stops before revival. Positive
            # limits stop farming as soon as the recovered count reaches the
            # configured cap; do not allow an extra attack with ``+ 1``.
            max_deaths=max(1, int(self.config.leveling.max_deaths_per_session)),
            hp_threshold=max(0.0, min(1.0, self.config.resources.health_min_percent / 100)),
            prowess_threshold=max(0.0, min(1.0, self.config.resources.prowess_min_percent / 100)),
            hp_item_allowlist=tuple(self.config.item_recovery.health_names),
            prowess_item_allowlist=tuple(self.config.item_recovery.prowess_names),
        )
        decision = policy.decide(
            LevelingPlayer(
                character=self.current_character_name,
                level=self.current_level,
                hp=None if hp_percent is None else int(round(hp_percent * 100)),
                max_hp=10000 if hp_percent is not None else None,
                prowess=None if prowess_percent is None else int(round(prowess_percent * 100)),
                max_prowess=10000 if prowess_percent is not None else None,
                alive=alive,
            ),
            LevelingLocation(
                name=policy_location_name,
                safe=location_safe,
                in_hunt=None if not page_kind_known else self.current_page_kind == "hunt",
                hunt_open=None if not page_kind_known else self.current_page_kind == "hunt",
                ready=None if not page_kind_known else True,
            ),
            LevelingDeath(
                deaths=self.deaths_observed,
                can_free_revive=(
                    death_data.get("freeReviveAvailable") is True
                    if isinstance(death_data, dict)
                    else None
                ),
            ),
            LevelingQuests(complete=False),
            LevelingInventory(inventory_items),
        )
        if (decision.intent.value, decision.reason) != (self.last_leveling_intent, self.last_leveling_reason):
            self.last_leveling_intent = decision.intent.value
            self.last_leveling_reason = decision.reason
            self.logger.log_event(
                "leveling_decision",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                intent=decision.intent.value,
                reason=decision.reason,
                item=decision.item,
                character_name=self.current_character_name,
                current_level=self.current_level,
                goal_level=self.config.leveling.target_level,
            )
        return decision

    def _death_recovery_snapshot(self, death_data: dict[str, object], dead: bool | None) -> RecoverySnapshot:
        free_count = _optional_int(death_data.get("freeReviveOptionCount"))
        options: tuple[ReviveOption, ...] = ()
        if death_data.get("freeReviveAvailable") is True and free_count in {None, 1}:
            options = (ReviveOption("explicit_free_revive", free=True, safe=True, available=True),)
        state_marker = "dead" if dead is True else "alive" if dead is False else "unknown"
        snapshot_id = self._current_state_snapshot_id or (
            f"controller:{self.session.cycle_id}:{self.deaths_observed}:{state_marker}"
        )
        return RecoverySnapshot(
            snapshot_id=snapshot_id,
            captured_at=time.monotonic(),
            is_dead=dead,
            revive_options=options,
            activity=self.state_machine.state.value,
            location=self._confirmed_recovery_location(),
            quest=self._active_quest_id,
        )

    def _record_recovery_decision(self, decision: object) -> None:
        self.logger.log_event(
            "death_recovery_decision",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            decision=getattr(getattr(decision, "decision", None), "value", None),
            reason=getattr(decision, "reason", None),
            option_id=getattr(decision, "option_id", None),
            deaths_observed=self.deaths_observed,
        )

    def _handle_leveling_death(self, death_data: object) -> bool:
        if not isinstance(death_data, dict):
            return self.state_machine.state in {GameState.DEAD, GameState.REVIVE_PENDING}
        dead = death_data.get("dead") if isinstance(death_data.get("dead"), bool) else None
        if dead is True:
            if not self._death_latched:
                if self.state_machine.state in {GameState.ROUTE_RECOVERY, GameState.NAVIGATOR_PENDING}:
                    route_target = self._route_destination_name or self._navigator_target_name
                    if route_target and not self._route_resume_target_name:
                        self._route_resume_target_name = route_target
                previous_activity = self.state_machine.state.value
                previous_location = self._confirmed_recovery_location()
                event_id = self._current_state_snapshot_id or f"death:{self.session.cycle_id}:{self.deaths_observed + 1}"
                self._death_latched = True
                self._active_recovery_id = event_id
                self._recovery_phase_events.clear()
                self.deaths_observed += 1
                self._death_recovery_policy.record_death(event_id)
                self._death_checkpoint = RecoveryCheckpoint(
                    activity=previous_activity,
                    location=previous_location,
                    quest=self._active_quest_id,
                    snapshot_id=event_id,
                )
                self._revive_attempted_for_current_death = False
                self._revive_requested_monotonic = None
                self.session.reset_cycle_attempt()
                self.logger.log_event(
                    "character_death_observed",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    deaths_observed=self.deaths_observed,
                    max_deaths=self.config.leveling.max_deaths_per_session,
                    checkpoint_activity=previous_activity,
                    checkpoint_location=previous_location,
                    checkpoint_quest=self._active_quest_id,
                    recovery_id=event_id,
                )
                self._log_recovery_phase(
                    "death_detected",
                    checkpoint_activity=previous_activity,
                    checkpoint_location=previous_location,
                    checkpoint_quest=self._active_quest_id,
                )
            recovery = self._death_recovery_policy.decide(
                self._death_recovery_snapshot(death_data, True),
                checkpoint=self._death_checkpoint,
            )
            self._record_recovery_decision(recovery)
            if recovery.decision is RecoveryDecision.STOP_UNSAFE:
                return self._stop_leveling_unsafe(f"death_recovery:{recovery.reason}")
            if self.state_machine.state not in {GameState.DEAD, GameState.REVIVE_PENDING}:
                self._safe_transition(GameState.DEAD, reason="character_death_observed")
            if self.state_machine.state == GameState.REVIVE_PENDING:
                timeout_ms = max(1000, int(self.config.leveling.revive_verify_timeout_ms))
                requested_at = self._revive_requested_monotonic or time.monotonic()
                if (time.monotonic() - requested_at) * 1000 >= timeout_ms:
                    return self._stop_leveling_unsafe("free_revive_not_confirmed")
                return True
            if self._revive_attempted_for_current_death or recovery.decision is RecoveryDecision.WAIT_CONFIRMATION:
                return True
            if recovery.decision is not RecoveryDecision.REVIVE:
                return self._stop_leveling_unsafe(f"death_recovery_unexpected:{recovery.reason}")
            if not self.config.leveling.free_revive_only:
                return self._stop_leveling_unsafe("non_free_revive_policy_forbidden")
            request = ActionRequest(
                "revive_free",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "expected_character": self._bound_character_name
                    or self.config.leveling.required_character_name,
                    "verify_delay_ms": 1000,
                    "revive_option_id": recovery.option_id,
                    "recovery_id": self._active_recovery_id,
                },
            )
            self._revive_attempted_for_current_death = True
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("free_revive_action_rejected")
            self._log_recovery_phase("revive_requested", revive_option_id=recovery.option_id)
            self._revive_requested_monotonic = time.monotonic()
            self._safe_transition(GameState.REVIVE_PENDING, reason="free_revive_submitted")
            return True
        if self.state_machine.state == GameState.REVIVE_PENDING:
            if dead is not False:
                timeout_ms = max(1000, int(self.config.leveling.revive_verify_timeout_ms))
                requested_at = self._revive_requested_monotonic or time.monotonic()
                if (time.monotonic() - requested_at) * 1000 >= timeout_ms:
                    return self._stop_leveling_unsafe("free_revive_result_ambiguous")
                return True
            recovery = self._death_recovery_policy.decide(
                self._death_recovery_snapshot(death_data, False),
                checkpoint=self._death_checkpoint,
                revived=True,
            )
            self._record_recovery_decision(recovery)
            if recovery.decision is RecoveryDecision.STOP_UNSAFE:
                return self._stop_leveling_unsafe(f"death_recovery:{recovery.reason}")
            if recovery.decision in {RecoveryDecision.RESTORE_CHECKPOINT, RecoveryDecision.COMPLETE}:
                self._log_recovery_phase("revive_confirmed", reason="free_revive_confirmed")
                return self._complete_revive_recovery("free_revive_confirmed")
            return True
        if self.state_machine.state == GameState.DEAD and dead is False:
            self._log_recovery_phase("revive_confirmed", reason="external_revive_confirmed")
            return self._complete_revive_recovery("external_revive_confirmed")
        if dead is False:
            self._death_latched = False
        return False

    def _complete_revive_recovery(self, reason: str) -> bool:
        self._log_recovery_phase("revive_confirmed", reason=reason)
        self._death_latched = False
        self._revive_attempted_for_current_death = False
        self._revive_requested_monotonic = None
        checkpoint_location = (
            str(self._death_checkpoint.location or "").strip()
            if self._death_checkpoint is not None
            else ""
        )
        if not _is_semantic_location_name(checkpoint_location):
            return self._stop_leveling_unsafe("post_revive_checkpoint_location_unconfirmed")
        self._post_revive_recovery_started_monotonic = time.monotonic()
        self._post_revive_recovery_attempts = 0
        self._post_revive_resume_reason = reason
        self._safe_transition(GameState.POST_REVIVE_RECOVERY, reason=reason)
        self.logger.log_event(
            "post_revive_resource_recovery_started",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            deaths_observed=self.deaths_observed,
            checkpoint_location=checkpoint_location,
            checkpoint_activity=self._death_checkpoint.activity if self._death_checkpoint else None,
        )
        return True

    def _resume_after_post_revive_recovery(self, reason: str) -> bool:
        checkpoint_location = (
            str(self._death_checkpoint.location or "").strip()
            if self._death_checkpoint is not None
            else ""
        )
        if not _is_semantic_location_name(checkpoint_location):
            return self._stop_leveling_unsafe("post_revive_checkpoint_location_unconfirmed")
        self._post_revive_recovery_started_monotonic = None
        self._post_revive_recovery_attempts = 0
        self._post_revive_resume_reason = None
        self._safe_transition(GameState.ROUTE_RECOVERY, reason="post_revive_resources_confirmed")
        at_checkpoint = _same_location_name(self.current_location_name, checkpoint_location)
        if not at_checkpoint:
            self._start_location_route(
                checkpoint_location,
                kind="post_revive_location",
                reason="post_revive_location_route",
            )
            if self.state_machine.state is not GameState.NAVIGATOR_PENDING:
                return True
            self.logger.log_event(
                "post_revive_route_recovery_started",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                deaths_observed=self.deaths_observed,
                checkpoint_activity=self._death_checkpoint.activity if self._death_checkpoint else None,
                checkpoint_location=checkpoint_location,
                checkpoint_quest=self._death_checkpoint.quest if self._death_checkpoint else None,
                route_kind=self._route_recovery_kind,
            )
            return True
        self._log_recovery_phase(
            "checkpoint_arrived",
            location_name=self.current_location_name,
            reason="post_revive_checkpoint_already_current",
        )
        if self._route_resume_target_name and not _same_location_name(
            self.current_location_name,
            self._route_resume_target_name,
        ):
            return self._start_location_route(
                self._route_resume_target_name,
                kind="post_revive_route_resume",
                reason="post_revive_route_resume",
            )
        if (
            self.config.leveling.auto_navigate_quest_targets
            and self._death_checkpoint is not None
            and self._death_checkpoint.quest
        ):
            request = ActionRequest(
                "open_quests",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "reason": "post_revive_quest_route_recovery",
                    "checkpoint_quest": self._death_checkpoint.quest,
                    "checkpoint_location": self._death_checkpoint.location,
                },
            )
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("post_revive_quest_open_failed")
            self._quest_refresh_requested_monotonic = time.monotonic()
            self._safe_transition(GameState.QUEST_REFRESH_PENDING, reason="post_revive_quest_opened")
            self.logger.log_event(
                "post_revive_route_recovery_started",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                deaths_observed=self.deaths_observed,
                checkpoint_activity=self._death_checkpoint.activity,
                checkpoint_location=self._death_checkpoint.location,
                checkpoint_quest=self._death_checkpoint.quest,
            )
            return True
        self._log_recovery_phase(
            "original_destination_arrived",
            location_name=self.current_location_name,
            reason="post_revive_original_destination_is_current",
        )
        request = ActionRequest(
            "open_hunt",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": "post_revive_route_recovery"},
        )
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe("post_revive_hunt_open_failed")
        self._reset_location_context()
        self._search_pause_until_monotonic = (
            time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
        )
        self._safe_transition(GameState.LOCATION_SEARCH, reason="post_revive_hunt_opened")
        if self.state_machine.state is GameState.LOCATION_SEARCH:
            self._log_recovery_phase("hunt_opened", reason="post_revive_hunt_opened")
            self._complete_death_recovery_evidence(reason)
        self.logger.log_event(
            "post_revive_route_recovered",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            deaths_observed=self.deaths_observed,
            reason=reason,
        )
        return True

    def _log_recovery_phase(self, event_type: str, **fields: object) -> bool:
        recovery_id = str(self._active_recovery_id or "").strip()
        if not recovery_id or event_type not in M1_RECOVERY_PHASES:
            return False
        if event_type in self._recovery_phase_events:
            return False
        next_index = len(self._recovery_phase_events)
        expected = M1_RECOVERY_PHASES[next_index] if next_index < len(M1_RECOVERY_PHASES) else None
        if event_type != expected:
            self.logger.log_event(
                "death_recovery_phase_rejected",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                recovery_id=recovery_id,
                phase=event_type,
                expected_phase=expected,
            )
            return False
        self._recovery_phase_events.append(event_type)
        self.logger.log_event(
            event_type,
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            recovery_id=recovery_id,
            deaths_observed=self.deaths_observed,
            **fields,
        )
        return True

    def _complete_death_recovery_evidence(self, reason: str) -> bool:
        recovery_id = str(self._active_recovery_id or "").strip()
        if not recovery_id:
            return False
        missing = [phase for phase in M1_RECOVERY_PHASES[:-1] if phase not in self._recovery_phase_events]
        if missing:
            self.logger.log_event(
                "death_recovery_evidence_incomplete",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                recovery_id=recovery_id,
                reason=reason,
                missing_phases=missing,
                observed_phases=[phase for phase in M1_RECOVERY_PHASES if phase in self._recovery_phase_events],
            )
            return False
        completed = self._log_recovery_phase("death_recovery_completed", reason=reason)
        if completed:
            self._active_recovery_id = None
            self._recovery_phase_events.clear()
            max_deaths = max(0, int(self.config.leveling.max_deaths_per_session))
            if max_deaths > 0 and self.deaths_observed >= max_deaths:
                self.logger.log_event(
                    "death_recovery_limit_reached",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    deaths_observed=self.deaths_observed,
                    max_deaths=max_deaths,
                    reason="max_deaths_recovered",
                )
                self._safe_transition(GameState.STOPPED, reason="max_deaths_recovered")
        return completed

    def _confirmed_recovery_location(self) -> str | None:
        for candidate in (
            self._last_alive_location_name,
            self.current_location_name,
            self._quest_origin_location_name,
        ):
            value = str(candidate or "").strip()
            if _is_semantic_location_name(value):
                return value
        return None

    def _stop_leveling_unsafe(self, reason: str) -> bool:
        self.last_error_reason = reason
        self.logger.log_event(
            "session_stopped",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            reason=reason,
            character_name=self.current_character_name,
            current_level=self.current_level,
            deaths_observed=self.deaths_observed,
        )
        self._safe_transition(GameState.STOPPED, reason=reason)
        return True

    def _effective_target_levels(self) -> tuple[int, ...]:
        configured = tuple(int(level) for level in self.config.target.allowed_levels if int(level) > 0)
        if configured:
            return configured
        if not self.config.leveling.enabled or self.current_level is None:
            return ()
        levels = {
            self.current_level + int(offset)
            for offset in self.config.leveling.auto_target_level_offsets
            if self.current_level + int(offset) > 0
        }
        return tuple(sorted(levels))
