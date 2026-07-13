from __future__ import annotations

import json
import time

import numpy as np

from src.antibot_cv.automation.actions import ActionRequest
from src.antibot_cv.automation.route_planner import RouteAction, validate_navigator_route
from src.antibot_cv.automation.runtime_constants import JS_VISIBLE_ATTACK_INTERVAL_MS
from src.antibot_cv.automation.runtime_helpers import (
    detect_direction_pad_roi as _detect_direction_pad_roi,
    is_semantic_location_name as _is_semantic_location_name,
    navigator_target_kind as _navigator_target_kind,
    same_location_name as _same_location_name,
    snapshot_main_href as _snapshot_main_href,
)
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.entity_detection.target_locator import LocatedTarget
from src.antibot_cv.viewport.coordinates import Point, Rect
from src.antibot_cv.viewport.scrollbar import ScrollDirection


class NavigationRuntimeMixin:
    def _handle_location_frame(self, frame: np.ndarray) -> None:
        if not self.config.dry_run:
            if not self._resources_allow_search(frame):
                return
            if self._maybe_start_quest_refresh():
                return
            if not self._attack_visible_target_via_injector(frame):
                if not self._handle_live_failed_visible_attack(frame):
                    self._open_hunt_from_game_shell(frame)
            return
        if self._attack_visible_target_via_injector():
            return

        targets = self.target_locator.locate(frame)
        for target in targets:
            self.logger.log_event(
                "target_candidate",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                detector_id="green_label",
                detector_confidence=target.confidence,
                target_id=target.target_id,
                x_frame=target.interaction_point.x,
                y_frame=target.interaction_point.y,
                frame_hash=self._last_frame_hash,
            )
        if targets:
            self._stable_target_frames += 1
            self._current_target = targets[0]
            tracks = self.tracker.update(targets)
            if self._stable_target_frames >= self.config.target.stable_frames:
                track = tracks[0] if tracks else None
                self.session.targets_detected += 1
                self.logger.log_event(
                    "target_acquired",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    detector_confidence=targets[0].confidence,
                    target_id=targets[0].target_id,
                    track_id=None if track is None else track.track_id,
                    frame_hash=self._last_frame_hash,
                )
                self._safe_transition(GameState.TARGET_FOUND)
                self._select_target(targets[0])
                self._stable_target_frames = 0
                self.navigator.reset()
                self._reset_scrollbar_search()
            return

        self._stable_target_frames = 0
        if self.state_machine.state == GameState.LOCATION_SEARCH:
            self._safe_transition(GameState.VIEWPORT_SCAN, reason="no_target")
        self._move_viewport(frame)

    def _handle_navigator_pending(self) -> None:
        started = self._navigator_opened_monotonic or time.monotonic()
        timeout_ms = max(1000, int(self.config.leveling.navigator_timeout_ms))
        child = self._find_navigator_client()
        if child is None:
            if (time.monotonic() - started) * 1000 >= timeout_ms:
                self._stop_leveling_unsafe("navigator_child_timeout")
            return
        self._navigator_client_id = str(child.get("client_id") or "") or None
        if not self._navigator_client_id:
            return
        if self._navigator_requires_target_selection:
            request = ActionRequest(
                "navigator_select_target",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "target": self._navigator_target_name or "",
                    "target_kind": self._navigator_target_kind,
                    "navigator_client_id": self._navigator_client_id,
                    "search_delay_ms": 3000,
                    "route_delay_ms": 500,
                    "reason": self._route_recovery_kind,
                },
            )
            if not self.action_executor.execute(request):
                self._stop_leveling_unsafe("navigator_target_selection_failed_or_ambiguous")
                return
            self._navigator_requires_target_selection = False
            return
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "navigator_snapshot",
                timeout_s=2.5,
                client_id=self._navigator_client_id,
            )
        except Exception as exc:
            if (time.monotonic() - started) * 1000 >= timeout_ms:
                self._stop_leveling_unsafe(f"navigator_snapshot_error:{exc}")
            return
        if not result.ok:
            if (time.monotonic() - started) * 1000 >= timeout_ms:
                self._stop_leveling_unsafe("navigator_snapshot_timeout")
            return
        try:
            snapshot = json.loads(result.message)
        except json.JSONDecodeError:
            snapshot = None
        if not isinstance(snapshot, dict):
            return
        route_decision = validate_navigator_route(
            snapshot,
            self._navigator_target_name or "",
            max_transitions=max(1, int(self.config.leveling.navigator_max_transitions)),
            max_snapshot_age_s=max(1.0, self.config.leveling.navigator_timeout_ms / 1000),
        )
        self.logger.log_event(
            "navigator_route_decision",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            action=route_decision.action.value,
            reason=route_decision.reason,
            target=route_decision.target,
            route_transitions=route_decision.route_transitions,
            snapshot_id=route_decision.snapshot_id,
            navigator_client_id=self._navigator_client_id,
        )
        if route_decision.action is RouteAction.ARRIVED:
            self._finish_route_arrival("navigator_target_current_location")
            return
        if route_decision.action is RouteAction.REFRESH:
            if (time.monotonic() - started) * 1000 >= timeout_ms:
                self._stop_leveling_unsafe(f"navigator_route_refresh_timeout:{route_decision.reason}")
            return
        if route_decision.action is not RouteAction.MOVE:
            self._stop_leveling_unsafe(f"navigator_route_unsafe:{route_decision.reason}")
            return
        request = ActionRequest(
            "navigator_go",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "target": self._navigator_target_name or "",
                "navigator_client_id": self._navigator_client_id,
                "route_transitions": route_decision.route_transitions,
                "route_snapshot_id": route_decision.snapshot_id,
            },
        )
        if not self.action_executor.execute(request):
            self._stop_leveling_unsafe("navigator_go_failed_or_ambiguous")
            return
        now = time.monotonic()
        self._route_go_submitted_monotonic = now
        self._route_destination_name = self._navigator_target_name
        self._route_destination_id = None
        self._route_expected_transitions = route_decision.route_transitions
        self._route_step_submitted_from_id = None
        self._route_step_submitted_monotonic = None
        self._safe_transition(GameState.ROUTE_RECOVERY, reason="navigator_go_submitted")

    def _find_navigator_client(self) -> dict[str, object] | None:
        if not self.browser_client_id:
            return None
        from src.antibot_cv.automation.browser_injector import global_browser_injector

        injector = global_browser_injector()
        parent = injector.client_snapshot(self.browser_client_id)
        parent_tab_id = parent.get("tab_id")
        parent_profile = str(parent.get("profile_id") or "")
        if parent_tab_id is None or not parent_profile:
            return None
        candidates = [
            client
            for client in injector.client_snapshots(within_s=5.0)
            if client.get("client_seen")
            and client.get("version_ok")
            and str(client.get("client_id") or "") not in self._navigator_existing_client_ids
            and client.get("opener_tab_id") == parent_tab_id
            and str(client.get("profile_id") or "") == parent_profile
            and "/navigator.php" in str(client.get("href") or "")
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _navigator_client_ids_for_parent(self) -> set[str]:
        if not self.browser_client_id:
            return set()
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            injector = global_browser_injector()
            parent = injector.client_snapshot(self.browser_client_id)
            parent_tab_id = parent.get("tab_id")
            parent_profile = str(parent.get("profile_id") or "")
            if parent_tab_id is None or not parent_profile:
                return set()
            return {
                str(client.get("client_id"))
                for client in injector.client_snapshots(within_s=5.0)
                if client.get("client_seen")
                and client.get("opener_tab_id") == parent_tab_id
                and str(client.get("profile_id") or "") == parent_profile
                and "/navigator.php" in str(client.get("href") or "")
                and client.get("client_id")
            }
        except Exception:
            return set()

    def _handle_route_recovery(self) -> None:
        submitted = self._route_go_submitted_monotonic
        if submitted is None:
            return
        elapsed_ms = (time.monotonic() - submitted) * 1000
        if elapsed_ms < max(500, int(self.config.leveling.route_settle_ms)):
            return
        snapshot = self._state_snapshot_via_injector(force=True)
        sections = snapshot.get("sections") if isinstance(snapshot, dict) else None
        location_section = sections.get("location") if isinstance(sections, dict) else None
        location = location_section.get("data") if isinstance(location_section, dict) else None
        battle_section = sections.get("battle") if isinstance(sections, dict) else None
        battle = battle_section.get("data") if isinstance(battle_section, dict) else None
        page_kind = str(location.get("pageKind") or "") if isinstance(location, dict) else ""
        destination_name = self._route_destination_name or self._navigator_target_name
        if isinstance(battle, dict) and (
            battle.get("rawHasFight") is True or battle.get("hasFight") is True
        ):
            if destination_name:
                self._route_resume_target_name = destination_name
            self._ensure_battle_context("route_interrupted_by_battle")
            self.session.mark_battle_detected()
            target_state = GameState.WAIT_BATTLE_END if battle.get("finished") is True else GameState.BATTLE_ACTIVE
            self._safe_transition(target_state, reason="route_interrupted_by_battle")
            self.logger.log_event(
                "route_interrupted",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason="battle",
                target=destination_name,
                location_name=self.current_location_name,
            )
            return
        if page_kind in {"area", "hunt", "main"}:
            self.current_page_kind = page_kind
            self.current_location_name = str(location.get("semanticName") or "") or None
            if _same_location_name(self.current_location_name, destination_name):
                self.logger.log_event(
                    "navigator_route_confirmed",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    page_kind=page_kind,
                    location_name=self.current_location_name,
                    target=destination_name,
                    elapsed_ms=elapsed_ms,
                )
                self._finish_route_arrival("navigator_route_confirmed")
                return
        route_snapshot = self._location_route_snapshot_via_injector()
        if isinstance(route_snapshot, dict) and route_snapshot.get("ok") is True:
            current_id = str(route_snapshot.get("currentLocationId") or "").strip()
            live_target_id = str(route_snapshot.get("targetLocationId") or "").strip()
            if live_target_id and live_target_id != "0" and self._route_destination_id is None:
                self._route_destination_id = live_target_id
            if current_id and self._route_destination_id and current_id == self._route_destination_id:
                self._finish_route_arrival("navigator_route_id_confirmed")
                return
            if self._route_step_submitted_from_id and current_id != self._route_step_submitted_from_id:
                self._route_step_submitted_from_id = None
                self._route_step_submitted_monotonic = None
                self._route_go_submitted_monotonic = time.monotonic()
            next_transition = route_snapshot.get("nextTransition")
            found_path = route_snapshot.get("foundPath")
            if (
                route_snapshot.get("timerReady") is True
                and current_id
                and isinstance(next_transition, dict)
                and isinstance(found_path, list)
                and found_path
                and str(next_transition.get("locId") or "") == str(found_path[0])
                and self._route_step_submitted_from_id != current_id
            ):
                request = ActionRequest(
                    "location_route_step",
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    dry_run=self.config.dry_run,
                    metadata={
                        "expected_current_location_id": current_id,
                        "navigation_delay_ms": 75,
                        "target": destination_name,
                        "next_location_id": next_transition.get("locId"),
                        "next_location_name": next_transition.get("name"),
                    },
                )
                if not self.action_executor.execute(request):
                    self._stop_leveling_unsafe("location_route_step_failed_or_ambiguous")
                    return
                self._route_step_submitted_from_id = current_id
                self._route_step_submitted_monotonic = time.monotonic()
                self.logger.log_event(
                    "location_route_step_requested",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    current_location_id=current_id,
                    next_location_id=next_transition.get("locId"),
                    next_location_name=next_transition.get("name"),
                    target=destination_name,
                )
                return
        timeout_ms = max(
            int(self.config.leveling.navigator_timeout_ms),
            int(self.config.leveling.route_settle_ms) + 1000,
        )
        if self._route_expected_transitions is not None:
            timeout_ms = max(timeout_ms, max(1, int(self._route_expected_transitions)) * 15000 + 10000)
        if elapsed_ms >= timeout_ms:
            self._stop_leveling_unsafe("navigator_route_result_unconfirmed")

    def _location_route_snapshot_via_injector(self) -> dict[str, object] | None:
        if self.config.dry_run or not self.browser_client_id:
            return None
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "location_route_snapshot",
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
        except Exception as exc:
            self.logger.log_event(
                "location_route_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason=str(exc),
            )
            return None
        if not result.ok:
            return None
        try:
            payload = json.loads(result.message)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _finish_route_arrival(self, reason: str) -> bool:
        recovery_kind = self._route_recovery_kind
        arrival_target = self._route_destination_name or self._navigator_target_name
        resume_target = self._route_resume_target_name
        if recovery_kind == "post_revive_location":
            self._log_recovery_phase(
                "checkpoint_arrived",
                location_name=self.current_location_name,
                reason=reason,
            )
        if resume_target and not _same_location_name(self.current_location_name, resume_target):
            self._route_resume_target_name = None
            return self._start_location_route(
                resume_target,
                kind="interrupted_route_resume",
                reason="interrupted_route_resume",
            )
        configured_target = str(self.config.leveling.target_location_name or "").strip()
        if configured_target and _same_location_name(arrival_target, configured_target):
            self._configured_route_completed_target = configured_target
            self.logger.log_event(
                "configured_location_route_completed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                target=configured_target,
                location_name=self.current_location_name,
                reason=reason,
            )
        if recovery_kind in {
            "post_revive_location",
            "post_revive_route_resume",
            "interrupted_route_resume",
        }:
            self._log_recovery_phase(
                "original_destination_arrived",
                location_name=self.current_location_name,
                reason=reason,
            )
        return self._finish_quest_refresh_to_hunt(reason)

    def _start_location_route(self, target: str, *, kind: str, reason: str) -> bool:
        target = str(target or "").strip()
        if not _is_semantic_location_name(target):
            return self._stop_leveling_unsafe(f"{reason}_target_invalid")
        request = ActionRequest(
            "open_location_navigator",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={"reason": reason, "target": target, "current_location": self.current_location_name},
        )
        self._navigator_existing_client_ids = self._navigator_client_ids_for_parent()
        if not self.action_executor.execute(request):
            return self._stop_leveling_unsafe(f"{reason}_navigator_open_failed")
        self._navigator_target_name = target
        self._navigator_target_kind = _navigator_target_kind(target)
        self._navigator_opened_monotonic = time.monotonic()
        self._navigator_client_id = None
        self._navigator_requires_target_selection = True
        self._route_recovery_kind = kind
        self._route_go_submitted_monotonic = None
        self._route_destination_name = None
        self._route_destination_id = None
        self._route_expected_transitions = None
        self._route_step_submitted_from_id = None
        self._route_step_submitted_monotonic = None
        self._safe_transition(GameState.NAVIGATOR_PENDING, reason=f"{reason}_navigator_opened")
        return self.state_machine.state is GameState.NAVIGATOR_PENDING

    def _select_target(self, target: LocatedTarget) -> None:
        self._selected_target = target
        self._target_click_attempts = 0
        self._last_attack_click_monotonic = None
        self._click_selected_target()

    def _attack_visible_target_via_injector(self, frame: np.ndarray | None = None) -> bool:
        if self.config.dry_run:
            return False
        if frame is not None and not self._resources_allow_search(frame):
            return False
        now = time.monotonic()
        if self._last_js_visible_attack_monotonic is not None:
            elapsed_ms = (now - self._last_js_visible_attack_monotonic) * 1000
            if elapsed_ms < JS_VISIBLE_ATTACK_INTERVAL_MS:
                return False
        self._last_js_visible_attack_monotonic = now
        allowed_levels = self._effective_target_levels()
        allowed_names = self._effective_target_names()
        if not allowed_levels and not allowed_names:
            self.logger.log_event(
                "js_visible_target_blocked",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="target_filter_missing",
                frame_hash=self._last_frame_hash,
            )
            return False
        metadata: dict[str, object] = {
            "frame_hash": self._last_frame_hash,
            "confirmed": 1,
            "margin": 35,
        }
        if allowed_levels:
            metadata["allowed_levels"] = list(allowed_levels)
        if allowed_names:
            metadata["names"] = list(allowed_names)
        request = ActionRequest(
            "attack_visible_target",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata=metadata,
        )
        self.logger.log_event(
            "js_visible_target_probe",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            frame_hash=self._last_frame_hash,
            allowed_levels=list(allowed_levels),
            allowed_names=list(allowed_names),
        )
        if not self.action_executor.execute(request):
            return False
        self.session.targets_detected += 1
        self._stable_target_frames = 0
        self._selected_target = None
        self._last_target_click_monotonic = now
        if self.state_machine.state in {GameState.LOCATION_SEARCH, GameState.VIEWPORT_SCAN}:
            self._safe_transition(GameState.TARGET_FOUND, reason="js_visible_target_attack")
            self._safe_transition(GameState.TARGET_SELECTED, reason="js_visible_target_attack")
        if self.session.battle_id is None:
            battle_id = self.session.new_battle()
            self._safe_transition(GameState.BATTLE_WAIT, battle_id=battle_id, reason="js_visible_target_attack")
        elif self.state_machine.state != GameState.BATTLE_WAIT and self.state_machine.can_transition(GameState.BATTLE_WAIT):
            self._safe_transition(GameState.BATTLE_WAIT, reason="js_visible_target_attack")
        self.navigator.reset()
        self._reset_scrollbar_search()
        return True

    def _handle_live_failed_visible_attack(self, frame: np.ndarray) -> bool:
        snapshot = self._visible_hunt_snapshot_via_injector()
        if self._live_snapshot_on_fight_page(snapshot):
            self._log_live_page_wait("live_fight_page_waiting", snapshot)
            battle_snapshot = self._battle_snapshot_via_injector(force=True)
            if self._sync_battle_from_snapshot(frame, battle_snapshot):
                return True
            if self._injector_snapshot_has_inactive_battle(battle_snapshot):
                return self._open_hunt_from_game_shell(frame, confirmed_inactive_fight_page=True)
            return True
        if self._live_snapshot_on_hunt_page(snapshot):
            self._log_live_page_wait("live_hunt_target_waiting", snapshot)
            if self._snapshot_visible_target_count(snapshot) <= 0:
                self._move_viewport(frame)
            return True
        if self._live_snapshot_on_area_error(snapshot):
            self._log_live_page_wait("live_area_error_waiting", snapshot)
            return True
        return False

    def _visible_hunt_snapshot_via_injector(self) -> dict[str, object] | None:
        if self.config.dry_run:
            return None
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            allowed_levels = self._effective_target_levels()
            allowed_names = self._effective_target_names()
            result = global_browser_injector().execute(
                "visible_hunt_targets",
                {"margin": 35, "allowedLevels": list(allowed_levels), "names": list(allowed_names)},
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
        except Exception as exc:
            self.logger.log_event(
                "js_visible_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=str(exc),
                frame_hash=self._last_frame_hash,
            )
            return None
        if not result.ok:
            self.logger.log_event(
                "js_visible_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=result.message,
                frame_hash=self._last_frame_hash,
            )
            return None
        try:
            parsed = json.loads(result.message)
        except json.JSONDecodeError:
            self.logger.log_event(
                "js_visible_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason="invalid_json",
                frame_hash=self._last_frame_hash,
            )
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _snapshot_visible_target_count(snapshot: dict[str, object] | None) -> int:
        targets = snapshot.get("targets") if isinstance(snapshot, dict) else None
        return len(targets) if isinstance(targets, list) else 0

    def _live_snapshot_on_fight_page(self, snapshot: dict[str, object] | None) -> bool:
        return "/fight.php" in _snapshot_main_href(snapshot)

    def _live_snapshot_on_hunt_page(self, snapshot: dict[str, object] | None) -> bool:
        if snapshot is None:
            return False
        return bool(snapshot.get("hasHunt")) or "/hunt.php" in _snapshot_main_href(snapshot)

    def _live_snapshot_on_area_error(self, snapshot: dict[str, object] | None) -> bool:
        href = _snapshot_main_href(snapshot)
        return "/area.php" in href and "error=" in href

    def _log_live_page_wait(self, event_type: str, snapshot: dict[str, object] | None, **extra: object) -> None:
        targets = snapshot.get("targets") if isinstance(snapshot, dict) else None
        display_children = snapshot.get("displayChildren") if isinstance(snapshot, dict) else None
        self.logger.log_event(
            event_type,
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            main_href="" if snapshot is None else snapshot.get("mainHref", ""),
            has_hunt=False if snapshot is None else bool(snapshot.get("hasHunt")),
            visible_target_count=len(targets) if isinstance(targets, list) else 0,
            display_child_count=len(display_children) if isinstance(display_children, list) else 0,
            frame_hash=self._last_frame_hash,
            **extra,
        )

    def _click_selected_target(self) -> bool:
        if self._selected_target is None:
            return False
        target = self._selected_target
        point = self._target_click_point(target, self._target_click_attempts)
        screen_point = self.mapper.frame_to_pynput(point)
        request = ActionRequest(
            "click_target",
            frame_point=point,
            screen_point=screen_point,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "click_count": 2 if self.config.target.double_click_to_attack else 1,
                "target_id": target.target_id,
                "frame_hash": self._last_frame_hash,
                "target_click_attempt": self._target_click_attempts,
            },
        )
        self.logger.log_event(
            "target_selected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            target_id=target.target_id,
            x_frame=point.x,
            y_frame=point.y,
            target_click_attempt=self._target_click_attempts,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._last_target_click_monotonic = time.monotonic()
            self._target_click_attempts += 1
            if self.state_machine.state == GameState.TARGET_FOUND:
                self._safe_transition(GameState.TARGET_SELECTED)
            if self.session.battle_id is None and self.state_machine.state == GameState.TARGET_SELECTED:
                battle_id = self.session.new_battle()
                self._safe_transition(GameState.BATTLE_WAIT, battle_id=battle_id)
            return True
        return False

    def _target_click_point(self, target: LocatedTarget, attempt_index: int) -> Point:
        if target.target_id == "green_sprite":
            sprite_offsets = (
                Point(0, 0),
                Point(10, 0),
                Point(-10, 0),
                Point(0, 10),
                Point(0, -10),
                Point(16, 8),
                Point(-16, 8),
                Point(16, -8),
                Point(-16, -8),
            )
            offset = sprite_offsets[min(attempt_index, len(sprite_offsets) - 1)]
            point = Point(target.interaction_point.x + offset.x, target.interaction_point.y + offset.y)
            return Rect(0, 0, self.mapper.roi.width, self.mapper.roi.height).clamp(point)
        if attempt_index == 0:
            return target.interaction_point
        retry_index = attempt_index - 1
        if retry_index >= len(self.config.target.click_retry_offsets):
            return target.interaction_point
        offset = self.config.target.click_retry_offsets[retry_index]
        point = Point(target.label_center.x + offset.dx, target.label_center.y + offset.dy)
        return Rect(0, 0, self.mapper.roi.width, self.mapper.roi.height).clamp(point)

    def _move_viewport(self, frame: np.ndarray) -> None:
        if self._viewport_settle_active():
            return
        self._update_direction_pad_from_frame(frame)
        if self.config.dry_run and self._interleaved_scrollbar_due() and self._move_scrollbar(frame):
            return
        move = self.navigator.next_move()
        if move is None:
            if self.config.dry_run and self._move_scrollbar(frame):
                return
            self.logger.log_event(
                "viewport_search_exhausted",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                recovery_pause_ms=self.config.recovery.viewport_exhausted_pause_ms,
                frame_hash=self._last_frame_hash,
            )
            self.session.mark_recovery()
            self.navigator.reset()
            self._reset_scrollbar_search()
            self._search_pause_until_monotonic = time.monotonic() + self.config.recovery.viewport_exhausted_pause_ms / 1000
            if self.state_machine.state != GameState.LOCATION_SEARCH:
                self._safe_transition(GameState.LOCATION_SEARCH, reason="viewport_search_exhausted")
            return
        screen_point = self.mapper.frame_to_pynput(move.point) if move.point else None
        current_moves = move.move_index - 1 + self._scrollbar_moves
        request = ActionRequest(
            "viewport_move",
            frame_point=move.point,
            screen_point=screen_point,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            is_viewport_move=True,
            viewport_moves_this_search=current_moves,
            scan_direction=move.direction.value,
            metadata={
                "frame_hash": self._last_frame_hash,
                "margin": 35,
                "use_js_hunt_direction": not self.config.dry_run,
            },
        )
        self.logger.log_event(
            "viewport_move",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            scan_direction=move.direction.value,
            x_frame=None if move.point is None else move.point.x,
            y_frame=None if move.point is None else move.point.y,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._direction_moves_since_scrollbar += 1
            self._last_viewport_action_monotonic = time.monotonic()

    def _viewport_settle_active(self) -> bool:
        if self._last_viewport_action_monotonic is None:
            return False
        elapsed_ms = (time.monotonic() - self._last_viewport_action_monotonic) * 1000
        return elapsed_ms < self.config.viewport.settle_ms

    def _update_direction_pad_from_frame(self, frame: np.ndarray) -> None:
        color_roi = _detect_direction_pad_roi(frame)
        if color_roi is not None:
            current = self.navigator.geometry.roi
            if abs(current.x - color_roi.x) > 12 or abs(current.y - color_roi.y) > 12:
                self.navigator.update_roi(color_roi)
                self.logger.log_event(
                    "direction_pad_detected",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    detector_id="direction_pad_color",
                    detector_confidence=0.8,
                    x_frame=color_roi.center.x,
                    y_frame=color_roi.center.y,
                    frame_hash=self._last_frame_hash,
                )
            return
        if self.config.viewport.direction_pad_roi is not None:
            return
        matches = self.registry.match(frame, "direction_pad")
        if not matches:
            return
        self.navigator.update_roi(matches[0].bbox)
        self.logger.log_event(
            "direction_pad_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            detector_id="direction_pad",
            detector_confidence=matches[0].confidence,
            x_frame=matches[0].center.x,
            y_frame=matches[0].center.y,
            frame_hash=self._last_frame_hash,
        )

    def _move_scrollbar(self, frame: np.ndarray) -> bool:
        if not self.config.viewport.scrollbar_enabled:
            return False
        if self._scrollbar_moves >= self.config.viewport.max_scrollbar_moves_per_search:
            return False
        if not self.config.viewport.scrollbar_sequence:
            return False

        direction_name = self.config.viewport.scrollbar_sequence[self._scrollbar_index % len(self.config.viewport.scrollbar_sequence)]
        direction = ScrollDirection[direction_name]
        move = self.scrollbar.next_drag(direction, frame, self.config.viewport.scrollbar_step_px)
        self._scrollbar_index += 1
        if move is None:
            self.logger.log_event(
                "scrollbar_not_found",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                frame_hash=self._last_frame_hash,
            )
            return False

        current_moves = self.navigator.moves + self._scrollbar_moves
        request = ActionRequest(
            "drag_scrollbar",
            frame_point=move.start,
            screen_point=self.mapper.frame_to_pynput(move.start),
            end_frame_point=move.end,
            end_screen_point=self.mapper.frame_to_pynput(move.end),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            is_viewport_move=True,
            viewport_moves_this_search=current_moves,
            scan_direction=f"SCROLL_{move.direction.value}",
            metadata={"detector_confidence": move.confidence, "frame_hash": self._last_frame_hash},
        )
        self.logger.log_event(
            "viewport_scroll",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            scan_direction=f"SCROLL_{move.direction.value}",
            detector_id="scrollbar_thumb",
            detector_confidence=move.confidence,
            x_frame=move.start.x,
            y_frame=move.start.y,
            x2_frame=move.end.x,
            y2_frame=move.end.y,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._scrollbar_moves += 1
            self._direction_moves_since_scrollbar = 0
            self._last_viewport_action_monotonic = time.monotonic()
            return True
        return False

    def _reset_scrollbar_search(self) -> None:
        self._scrollbar_index = 0
        self._scrollbar_moves = 0
        self._direction_moves_since_scrollbar = 0
        self._last_viewport_action_monotonic = None

    def _interleaved_scrollbar_due(self) -> bool:
        every = self.config.viewport.scrollbar_every_direction_moves
        return (
            self.config.viewport.scrollbar_enabled
            and every > 0
            and self._direction_moves_since_scrollbar >= every
            and self._scrollbar_moves < self.config.viewport.max_scrollbar_moves_per_search
        )

    def _default_scrollbar_roi(self) -> Rect | None:
        if self.config.viewport.scrollbar_roi is not None:
            return self.config.viewport.scrollbar_roi
        if self.config.target.search_roi is not None:
            return self.config.target.search_roi
        return None
