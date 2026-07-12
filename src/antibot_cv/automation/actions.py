from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol

from src.antibot_cv.automation.browser_injector import global_browser_injector
from src.antibot_cv.automation.safety import SafetyGuard
from src.antibot_cv.automation.session import SessionState
from src.antibot_cv.viewport.coordinates import Point


@dataclass(frozen=True)
class ActionRequest:
    action_type: str
    frame_point: Point | None = None
    screen_point: Point | None = None
    end_frame_point: Point | None = None
    end_screen_point: Point | None = None
    cycle_id: int = 0
    battle_id: int | None = None
    dry_run: bool = True
    is_viewport_move: bool = False
    viewport_moves_this_search: int = 0
    scan_direction: str | None = None
    metadata: dict[str, object] | None = None


class ActionSink(Protocol):
    def execute(self, request: ActionRequest) -> bool:
        ...


class DryRunActionSink:
    def __init__(self, logger: object | None = None) -> None:
        self.logger = logger
        self.requests: list[ActionRequest] = []

    def execute(self, request: ActionRequest) -> bool:
        self.requests.append(request)
        _log_action(self.logger, "intended_action", request)
        return True


class ReplayActionSink(DryRunActionSink):
    def execute(self, request: ActionRequest) -> bool:
        self.requests.append(request)
        _log_action(self.logger, "replay_intended_action", request)
        return True


class BlockedActionSink:
    def __init__(self, logger: object | None = None, reason: str = "blocked") -> None:
        self.logger = logger
        self.reason = reason

    def execute(self, request: ActionRequest) -> bool:
        _log_action(self.logger, "action_blocked", request, block_reason=self.reason)
        return False


class LiveMacActionSink:
    def __init__(self, logger: object | None = None, *, browser_client_id: str | None = None) -> None:
        self.logger = logger
        self.browser_client_id = browser_client_id
        self.last_ambiguous_action: str | None = None

    def _execute_injector(
        self,
        injector: object,
        command: str,
        payload: dict[str, object] | None = None,
        *,
        timeout_s: float = 2.5,
        client_id_override: str | None = None,
    ) -> object:
        kwargs: dict[str, object] = {"timeout_s": timeout_s}
        client_id = client_id_override or self.browser_client_id
        if client_id:
            kwargs["client_id"] = client_id
        return injector.execute(command, payload, **kwargs)  # type: ignore[attr-defined]

    def execute(self, request: ActionRequest) -> bool:
        self.last_ambiguous_action = None
        if request.action_type == "attack_visible_target":
            injector = global_browser_injector()
            metadata = dict(request.metadata or {})
            payload = {
                "confirmed": int(metadata.get("confirmed", 1) or 0),
                "margin": int(metadata.get("margin", 35) or 0),
            }
            names = metadata.get("names")
            if isinstance(names, list):
                payload["names"] = names
            allowed_levels = metadata.get("allowed_levels")
            if isinstance(allowed_levels, list):
                payload["allowedLevels"] = allowed_levels
            payload["verifyTimeoutMs"] = 3500
            payload["commandTimeoutMs"] = 6500
            result = self._execute_injector(injector, "attack_visible_bot", payload, timeout_s=7.0)
            result_metadata = dict(metadata)
            result_metadata["injector_message"] = _compact_injector_message(result.message)
            result_metadata["injector_client_id"] = result.client_id
            if result.ok:
                try:
                    parsed = json.loads(result.message)
                    target = parsed.get("target") if isinstance(parsed, dict) else None
                    if isinstance(target, dict):
                        result_metadata["bot_id"] = target.get("botId")
                        result_metadata["target_name"] = target.get("name")
                        result_metadata["target_level"] = target.get("level")
                        result_metadata["target_level_source"] = target.get("levelSource")
                        result_metadata["target_world_x"] = target.get("x")
                        result_metadata["target_world_y"] = target.get("y")
                        result_metadata["target_screen_x"] = target.get("screenX")
                        result_metadata["target_screen_y"] = target.get("screenY")
                except Exception:
                    pass
                logged_request = _copy_request(request, metadata=result_metadata)
                _log_action(self.logger, "attack_visible_target_requested", logged_request, dry_run=False)
                return True
            logged_request = _copy_request(request, metadata=result_metadata)
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_attack_visible_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type in {"click_ability_4", "click_combat_slot"}:
            metadata = dict(request.metadata or {})
            if metadata.get("use_js_skill"):
                raw_slot = metadata.get("skill_slot", metadata.get("slot_index", 4))
                slot = int(raw_slot) if raw_slot is not None else 4
                payload: dict[str, object] = {"slot": slot}
                if "pre_click_delay_ms" in metadata:
                    payload["preClickDelayMs"] = metadata.get("pre_click_delay_ms")
                if "click_hold_ms" in metadata:
                    payload["clickHoldMs"] = metadata.get("click_hold_ms")
                payload["verifyTimeoutMs"] = 900
                payload["commandTimeoutMs"] = 4000
                result = self._execute_injector(
                    global_browser_injector(),
                    "use_skill_slot",
                    payload,
                    timeout_s=4.5,
                )
                result_metadata = dict(metadata)
                result_metadata["injector_message"] = _compact_injector_message(result.message)
                result_metadata["injector_client_id"] = result.client_id
                result_metadata["skill_slot"] = slot
                if result.ok:
                    try:
                        parsed = json.loads(result.message)
                        if isinstance(parsed, dict):
                            result_metadata["injector_result_message"] = parsed.get("message")
                            result_metadata["fight_path"] = parsed.get("fightPath")
                            result_metadata["fight_href"] = parsed.get("fightHref")
                            ability = parsed.get("ability")
                            if isinstance(ability, dict):
                                result_metadata["ability_id"] = ability.get("id")
                                result_metadata["ability_name"] = ability.get("name")
                                result_metadata["ability_slot"] = ability.get("slot")
                    except json.JSONDecodeError:
                        pass
                    event_type = f"{request.action_type}_js"
                    _log_action(self.logger, event_type, _copy_request(request, metadata=result_metadata), dry_run=False)
                    return True
                _log_action(
                    self.logger,
                    "action_blocked",
                    _copy_request(request, metadata=result_metadata),
                    block_reason=f"injector_use_skill_failed:{_compact_injector_message(result.message)}",
                )
                return False

        if request.action_type == "use_battle_item":
            metadata = dict(request.metadata or {})
            payload: dict[str, object] = {
                "kind": str(metadata.get("kind") or ""),
                "slots": metadata.get("slots", []),
                "names": metadata.get("names", []),
            }
            if "item_id" in metadata:
                payload["itemId"] = metadata.get("item_id")
            if "pre_click_delay_ms" in metadata:
                payload["preClickDelayMs"] = metadata.get("pre_click_delay_ms")
            if "click_hold_ms" in metadata:
                payload["clickHoldMs"] = metadata.get("click_hold_ms")
            result = self._execute_injector(
                global_browser_injector(),
                "use_battle_item",
                payload,
                timeout_s=2.5,
            )
            result_metadata = dict(metadata)
            result_metadata["injector_message"] = _compact_injector_message(result.message)
            result_metadata["injector_client_id"] = result.client_id
            parsed: dict[str, object] | None = None
            try:
                candidate = json.loads(result.message)
                parsed = candidate if isinstance(candidate, dict) else None
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                result_metadata["injector_result_message"] = parsed.get("message")
                result_metadata["fight_path"] = parsed.get("fightPath")
                result_metadata["fight_href"] = parsed.get("fightHref")
                item = parsed.get("item")
                if isinstance(item, dict):
                    result_metadata["item_id"] = item.get("id")
                    result_metadata["item_name"] = item.get("name")
                    result_metadata["item_slot"] = item.get("slot")
                result_metadata["method"] = parsed.get("method")
                result_metadata["slot"] = parsed.get("slot")
                result_metadata["evidence"] = parsed.get("evidence")
            if result.ok:
                _log_action(self.logger, "use_battle_item_js", _copy_request(request, metadata=result_metadata), dry_run=False)
                return True
            if parsed is not None and parsed.get("message") == "battle_item_use_ambiguous":
                self.last_ambiguous_action = "use_battle_item"
                _log_action(
                    self.logger,
                    "use_battle_item_ambiguous",
                    _copy_request(request, metadata=result_metadata),
                    dry_run=False,
                )
                return False
            _log_action(
                self.logger,
                "action_blocked",
                _copy_request(request, metadata=result_metadata),
                block_reason=f"injector_use_battle_item_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "revive_free":
            metadata = dict(request.metadata or {})
            result = self._execute_injector(
                global_browser_injector(),
                "revive_free",
                {
                    "expectedCharacter": metadata.get("expected_character", ""),
                    "verifyDelayMs": metadata.get("verify_delay_ms", 1000),
                },
                timeout_s=4.0,
            )
            result_metadata = dict(metadata)
            result_metadata["injector_message"] = _compact_injector_message(result.message)
            result_metadata["injector_client_id"] = result.client_id
            parsed: dict[str, object] | None = None
            try:
                candidate = json.loads(result.message)
                parsed = candidate if isinstance(candidate, dict) else None
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                result_metadata["injector_result_message"] = parsed.get("message")
                result_metadata["submitted"] = parsed.get("submitted")
                result_metadata["confirmed"] = parsed.get("confirmed")
                result_metadata["option"] = parsed.get("option")
            if result.ok and parsed is not None and parsed.get("submitted") is True:
                _log_action(
                    self.logger,
                    "revive_free_submitted",
                    _copy_request(request, metadata=result_metadata),
                    dry_run=False,
                )
                return True
            _log_action(
                self.logger,
                "action_blocked",
                _copy_request(request, metadata=result_metadata),
                block_reason=f"injector_revive_free_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "close_resurrection_notice":
            metadata = dict(request.metadata or {})
            result = self._execute_injector(
                global_browser_injector(),
                "close_resurrection_notice",
                {"verifyDelayMs": metadata.get("verify_delay_ms", 250)},
                timeout_s=2.5,
            )
            result_metadata = dict(metadata)
            result_metadata["injector_message"] = _compact_injector_message(result.message)
            result_metadata["injector_client_id"] = result.client_id
            parsed: dict[str, object] | None = None
            try:
                candidate = json.loads(result.message)
                parsed = candidate if isinstance(candidate, dict) else None
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                result_metadata["injector_result_message"] = parsed.get("message")
                result_metadata["closed"] = parsed.get("closed")
                result_metadata["confirmed"] = parsed.get("confirmed")
            if result.ok and parsed is not None and parsed.get("confirmed") is True:
                _log_action(
                    self.logger,
                    "resurrection_notice_closed",
                    _copy_request(request, metadata=result_metadata),
                    dry_run=False,
                )
                return True
            _log_action(
                self.logger,
                "action_blocked",
                _copy_request(request, metadata=result_metadata),
                block_reason=f"injector_close_resurrection_notice_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type in {"click_exit", "click_hunt"}:
            metadata = dict(request.metadata or {})
            if metadata.get("use_js_open_hunt"):
                result = self._execute_injector(
                    global_browser_injector(),
                    "open_hunt",
                    {"verifyTimeoutMs": 2000, "commandTimeoutMs": 5000},
                    timeout_s=5.5,
                )
                result_metadata = dict(metadata)
                result_metadata["injector_message"] = _compact_injector_message(result.message)
                result_metadata["injector_client_id"] = result.client_id
                request = _copy_request(request, metadata=result_metadata)
                if result.ok:
                    _log_action(self.logger, f"{request.action_type}_js_open_hunt", request, dry_run=False)
                    return True
                if request.screen_point is None:
                    _log_action(
                        self.logger,
                        "action_blocked",
                        request,
                        block_reason=f"injector_open_hunt_failed:{_compact_injector_message(result.message)}",
                    )
                    return False

        if request.action_type == "open_hunt":
            injector = global_browser_injector()
            result = self._execute_injector(
                injector,
                "open_hunt",
                {"verifyTimeoutMs": 2000, "commandTimeoutMs": 5000},
                timeout_s=5.5,
            )
            if result.ok:
                metadata = dict(request.metadata or {})
                metadata["injector_message"] = _compact_injector_message(result.message)
                metadata["injector_client_id"] = result.client_id
                request = ActionRequest(
                    request.action_type,
                    frame_point=request.frame_point,
                    screen_point=request.screen_point,
                    end_frame_point=request.end_frame_point,
                    end_screen_point=request.end_screen_point,
                    cycle_id=request.cycle_id,
                    battle_id=request.battle_id,
                    dry_run=request.dry_run,
                    is_viewport_move=request.is_viewport_move,
                    viewport_moves_this_search=request.viewport_moves_this_search,
                    scan_direction=request.scan_direction,
                    metadata=metadata,
                )
                _log_action(self.logger, "open_hunt_requested", request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                request,
                block_reason=f"injector_open_hunt_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "open_quests":
            result = self._execute_injector(
                global_browser_injector(),
                "open_quests",
                {"verifyTimeoutMs": 2000, "commandTimeoutMs": 5000},
                timeout_s=5.5,
            )
            metadata = dict(request.metadata or {})
            metadata["injector_message"] = _compact_injector_message(result.message)
            metadata["injector_client_id"] = result.client_id
            logged_request = _copy_request(request, metadata=metadata)
            if result.ok:
                _log_action(self.logger, "open_quests_requested", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_open_quests_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "open_quest_navigator":
            metadata = dict(request.metadata or {})
            result = self._execute_injector(
                global_browser_injector(),
                "open_quest_navigator",
                {"target": metadata.get("target", "")},
                timeout_s=2.5,
            )
            result_metadata = {
                **metadata,
                "injector_message": _compact_injector_message(result.message),
                "injector_client_id": result.client_id,
            }
            logged_request = _copy_request(request, metadata=result_metadata)
            if result.ok:
                _log_action(self.logger, "quest_navigator_opened", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_open_quest_navigator_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "open_location_navigator":
            metadata = dict(request.metadata or {})
            result = self._execute_injector(
                global_browser_injector(),
                "open_location_navigator",
                {},
                timeout_s=2.5,
            )
            result_metadata = {
                **metadata,
                "injector_message": _compact_injector_message(result.message),
                "injector_client_id": result.client_id,
            }
            logged_request = _copy_request(request, metadata=result_metadata)
            if result.ok:
                _log_action(self.logger, "location_navigator_opened", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_open_location_navigator_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "navigator_select_target":
            metadata = dict(request.metadata or {})
            navigator_client_id = str(metadata.get("navigator_client_id") or "")
            target = str(metadata.get("target") or "").strip()
            if not navigator_client_id or not target:
                reason = "navigator_client_missing" if not navigator_client_id else "navigator_target_missing"
                _log_action(self.logger, "action_blocked", request, block_reason=reason)
                return False
            result = self._execute_injector(
                global_browser_injector(),
                "navigator_select_target",
                {
                    "target": target,
                    "kind": "location",
                    "searchDelayMs": metadata.get("search_delay_ms", 250),
                    "routeDelayMs": metadata.get("route_delay_ms", 350),
                },
                timeout_s=4.0,
                client_id_override=navigator_client_id,
            )
            result_metadata = {
                **metadata,
                "injector_message": _compact_injector_message(result.message),
                "injector_client_id": result.client_id,
            }
            logged_request = _copy_request(request, metadata=result_metadata)
            parsed: dict[str, object] | None = None
            try:
                candidate = json.loads(result.message)
                parsed = candidate if isinstance(candidate, dict) else None
            except json.JSONDecodeError:
                parsed = None
            if result.ok and parsed is not None and parsed.get("message") in {
                "navigator_target_selected",
                "navigator_target_already_selected",
            }:
                _log_action(self.logger, "navigator_target_selected", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_navigator_select_target_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "navigator_go":
            metadata = dict(request.metadata or {})
            navigator_client_id = str(metadata.get("navigator_client_id") or "")
            if not navigator_client_id:
                _log_action(self.logger, "action_blocked", request, block_reason="navigator_client_missing")
                return False
            result = self._execute_injector(
                global_browser_injector(),
                "navigator_go",
                {"expectedTarget": metadata.get("target", "")},
                timeout_s=3.0,
                client_id_override=navigator_client_id,
            )
            result_metadata = {
                **metadata,
                "injector_message": _compact_injector_message(result.message),
                "injector_client_id": result.client_id,
            }
            logged_request = _copy_request(request, metadata=result_metadata)
            parsed: dict[str, object] | None = None
            try:
                candidate = json.loads(result.message)
                parsed = candidate if isinstance(candidate, dict) else None
            except json.JSONDecodeError:
                parsed = None
            if result.ok and parsed is not None and (
                parsed.get("submitted") is True or parsed.get("message") == "navigator_already_at_target"
            ):
                _log_action(self.logger, "navigator_go_requested", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_navigator_go_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "viewport_move":
            metadata = dict(request.metadata or {})
            if metadata.get("use_js_hunt_direction"):
                direction = request.scan_direction or str(metadata.get("scan_direction") or "")
                result = self._execute_injector(
                    global_browser_injector(),
                    "hunt_move_direction",
                    {"direction": direction, "margin": int(metadata.get("margin", 35) or 0)},
                    timeout_s=2.5,
                )
                result_metadata = dict(metadata)
                result_metadata["injector_message"] = _compact_injector_message(result.message)
                result_metadata["injector_client_id"] = result.client_id
                if result.ok:
                    _log_action(
                        self.logger,
                        "viewport_move_js_hunt_direction",
                        _copy_request(request, metadata=result_metadata),
                        dry_run=False,
                    )
                    return True
                _log_action(
                    self.logger,
                    "action_blocked",
                    _copy_request(request, metadata=result_metadata),
                    block_reason=f"injector_hunt_move_direction_failed:{_compact_injector_message(result.message)}",
                )
                return False

        if request.action_type == "use_recovery_items":
            metadata = dict(request.metadata or {})
            timeout_s = float(metadata.get("timeout_s", 6.0) or 6.0)
            threshold = float(metadata.get("use_when_below_percent", 90) or 90)
            max_uses_per_resource = max(1, int(metadata.get("max_uses_per_resource", 1) or 1))
            inventory_open_delay_ms = max(0, int(metadata.get("inventory_open_delay_ms", 1500) or 1500))
            open_timeout_s = max(0.1, timeout_s, inventory_open_delay_ms / 1000 + 5.5)
            between_items_delay_s = max(0.0, int(metadata.get("between_items_delay_ms", 0) or 0) / 1000)
            confirm_delay_s = max(0.0, int(metadata.get("confirm_delay_ms", 700) or 0) / 1000)
            injector = global_browser_injector()
            result_metadata = dict(metadata)
            attempts: list[dict[str, object]] = []
            resource_results: list[dict[str, object]] = []
            kinds = (
                ("health", "health_names", "health_restore_percent", "healthPercent", "health_use_when_below_percent"),
                ("prowess", "prowess_names", "prowess_restore_percent", "prowessPercent", "prowess_use_when_below_percent"),
            )
            for kind, names_key, restore_key, percent_key, threshold_key in kinds:
                kind_threshold = float(metadata.get(threshold_key, threshold) or threshold)
                restore_percent = max(0.0, float(metadata.get(restore_key, 0) or 0))
                kind_ok = False
                kind_reason = "not_attempted"
                last_percent: float | None = None
                for use_index in range(max_uses_per_resource):
                    open_started_at = time.monotonic()
                    open_result = self._execute_injector(
                        injector,
                        "open_recovery_item",
                        {
                            "kind": kind,
                            "names": metadata.get(names_key, []),
                            "useWhenBelowPercent": kind_threshold,
                            "useIfResourcesMissing": bool(metadata.get("use_if_resources_missing", False)),
                            "forceUse": bool(metadata.get("force_use", False)),
                            "inventoryOpenDelayMs": inventory_open_delay_ms,
                        },
                        timeout_s=open_timeout_s,
                    )
                    open_elapsed_ms = int((time.monotonic() - open_started_at) * 1000)
                    attempt: dict[str, object] = {
                        "kind": kind,
                        "use_index": use_index + 1,
                        "open_ok": open_result.ok,
                        "open_message": _compact_injector_message(open_result.message),
                        "open_client_id": open_result.client_id,
                        "open_elapsed_ms": open_elapsed_ms,
                        "open_timeout_s": open_timeout_s,
                    }
                    try:
                        parsed = json.loads(open_result.message)
                        if isinstance(parsed, dict):
                            attempt["open_result"] = _compact_recovery_result(parsed)
                            if parsed.get("message") == "recovery_item_not_needed":
                                percent = parsed.get("percent")
                                if not isinstance(percent, bool) and percent is not None:
                                    try:
                                        last_percent = float(percent)
                                    except (TypeError, ValueError):
                                        last_percent = None
                                attempts.append(attempt)
                                kind_ok = True
                                kind_reason = "not_needed"
                                break
                            if parsed.get("message") != "recovery_item_clicked":
                                attempts.append(attempt)
                                kind_reason = str(parsed.get("message") or "open_failed")
                                break
                            percent_before = _resource_percent_from_open_result(parsed, percent_key)
                            if percent_before is not None:
                                attempt["percent_before"] = percent_before
                            if restore_percent > 0:
                                attempt["restore_percent"] = restore_percent
                            if parsed.get("requiresConfirm") is False:
                                attempt["confirm_skipped"] = "not_required"
                                if between_items_delay_s:
                                    time.sleep(between_items_delay_s)
                                refresh_result = _refresh_resource_source_after_use(injector)
                                if refresh_result is not None:
                                    attempt["resource_refresh_ok"] = refresh_result.ok
                                    attempt["resource_refresh_message"] = _compact_injector_message(
                                        refresh_result.message
                                    )
                                percent_after = _wait_resource_percent_after_use(injector, percent_key, percent_before)
                                if percent_after is not None:
                                    last_percent = percent_after
                                    attempt["percent_after"] = percent_after
                                    attempt["threshold"] = kind_threshold
                                attempts.append(attempt)
                                if percent_after is not None and percent_after >= kind_threshold:
                                    kind_ok = True
                                    kind_reason = "threshold_reached"
                                    break
                                if use_index + 1 >= max_uses_per_resource:
                                    kind_reason = "max_uses_reached"
                                    break
                                continue
                    except json.JSONDecodeError:
                        attempts.append(attempt)
                        kind_reason = "open_result_json_error"
                        break

                    if confirm_delay_s:
                        time.sleep(confirm_delay_s)
                    confirm_started_at = time.monotonic()
                    confirm_result = self._execute_injector(
                        injector,
                        "confirm_action_form",
                        {},
                        timeout_s=max(0.1, timeout_s),
                    )
                    attempt["confirm_elapsed_ms"] = int((time.monotonic() - confirm_started_at) * 1000)
                    attempt["confirm_ok"] = confirm_result.ok
                    attempt["confirm_message"] = _compact_injector_message(confirm_result.message)
                    attempt["confirm_client_id"] = confirm_result.client_id
                    if confirm_result.ok:
                        if between_items_delay_s:
                            time.sleep(between_items_delay_s)
                        refresh_result = _refresh_resource_source_after_use(injector)
                        if refresh_result is not None:
                            attempt["resource_refresh_ok"] = refresh_result.ok
                            attempt["resource_refresh_message"] = _compact_injector_message(
                                refresh_result.message
                            )
                        percent_after = _wait_resource_percent_after_use(
                            injector,
                            percent_key,
                            attempt.get("percent_before"),
                        )
                        if percent_after is not None:
                            last_percent = percent_after
                            attempt["percent_after"] = percent_after
                            attempt["threshold"] = kind_threshold
                    attempts.append(attempt)
                    if attempt.get("percent_after") is not None and float(attempt["percent_after"]) >= kind_threshold:
                        kind_ok = True
                        kind_reason = "threshold_reached"
                        break
                    if not confirm_result.ok or use_index + 1 >= max_uses_per_resource:
                        kind_reason = "confirm_failed" if not confirm_result.ok else "max_uses_reached"
                        break
                resource_results.append(
                    {
                        "kind": kind,
                        "ok": kind_ok,
                        "reason": kind_reason,
                        "threshold": kind_threshold,
                        "last_percent": last_percent,
                    }
                )

            overall_ok = all(bool(item["ok"]) for item in resource_results)
            if overall_ok and bool(metadata.get("open_hunt_after", True)):
                hunt_result = self._execute_injector(
                    injector,
                    "open_hunt",
                    {"verifyTimeoutMs": 2000, "commandTimeoutMs": 5000},
                    timeout_s=5.5,
                )
                result_metadata["open_hunt_ok"] = hunt_result.ok
                result_metadata["open_hunt_message"] = _compact_injector_message(hunt_result.message)
                result_metadata["open_hunt_client_id"] = hunt_result.client_id
            elif bool(metadata.get("open_hunt_after", True)):
                result_metadata["open_hunt_skipped"] = "recovery_failed"

            result_metadata["recovery_item_attempts"] = attempts
            result_metadata["recovery_resource_results"] = resource_results
            if overall_ok:
                _log_action(self.logger, "recovery_items_js", _copy_request(request, metadata=result_metadata), dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                _copy_request(request, metadata=result_metadata),
                block_reason="injector_recovery_items_failed",
            )
            return False

        if request.action_type == "open_url":
            _log_action(self.logger, "action_blocked", request, block_reason="live_js_only_unsupported_action:open_url")
            return False

        if request.action_type == "browser_back":
            _log_action(self.logger, "action_blocked", request, block_reason="live_js_only_unsupported_action:browser_back")
            return False

        _log_action(self.logger, "action_blocked", request, block_reason=f"live_js_only_unsupported_action:{request.action_type}")
        return False


class ActionExecutor:
    def __init__(
        self,
        *,
        guard: SafetyGuard,
        session: SessionState,
        sink: ActionSink,
        logger: object | None = None,
    ) -> None:
        self.guard = guard
        self.session = session
        self.sink = sink
        self.logger = logger

    def execute(self, request: ActionRequest) -> bool:
        decision = self.guard.allow_action(
            request.action_type,
            completed_cycles=self.session.completed_cycles,
            requested_cycles=self.session.requested_cycles,
            is_viewport_move=request.is_viewport_move,
            viewport_moves_this_search=request.viewport_moves_this_search,
        )
        if not decision.allowed:
            _log_action(self.logger, "action_blocked", request, block_reason=decision.reason)
            return False

        if request.action_type == "click_ability_4" and not self.session.can_use_ability4():
            _log_action(self.logger, "action_blocked", request, block_reason="ability4_already_used")
            return False
        if request.action_type == "click_attack" and not self.session.can_click_attack():
            _log_action(self.logger, "action_blocked", request, block_reason="attack_retry_limit")
            return False
        if request.action_type == "click_exit" and not self.session.can_click_exit():
            _log_action(self.logger, "action_blocked", request, block_reason="exit_already_clicked")
            return False
        if request.action_type == "click_hunt" and not self.session.can_click_hunt():
            _log_action(self.logger, "action_blocked", request, block_reason="hunt_already_clicked")
            return False

        succeeded = self.sink.execute(request)
        if succeeded:
            self.guard.record_action()
            self.session.total_actions += 1
            if request.is_viewport_move:
                self.session.viewport_moves += 1
            if request.action_type == "click_ability_4":
                self.session.mark_ability4()
            elif request.action_type == "click_combat_slot":
                self.session.mark_combat()
            elif request.action_type == "click_attack":
                self.session.mark_attack()
            elif request.action_type == "click_exit":
                self.session.mark_exit()
            elif request.action_type == "click_hunt":
                self.session.mark_hunt()
        return succeeded


def _compact_injector_message(message: object, *, max_length: int = 1800) -> str:
    raw = str(message or "")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return raw[:max_length]
    if not isinstance(parsed, dict):
        return raw[:max_length]
    compact_keys = (
        "ok",
        "message",
        "slot",
        "kind",
        "fightPath",
        "fightHref",
        "method",
        "args",
        "ability",
        "abilityAfter",
        "item",
        "evidence",
        "target",
        "direction",
        "submitted",
        "confirmed",
        "option",
        "pageKind",
    )
    compact = {key: parsed[key] for key in compact_keys if key in parsed}
    if not compact:
        compact = {"message": parsed.get("message", "injector_result")}
    serialized = json.dumps(compact, ensure_ascii=False, separators=(",", ":"), default=str)
    return serialized[:max_length]


def _compact_recovery_result(parsed: dict[str, object]) -> dict[str, object]:
    compact: dict[str, object] = {}
    for key in ("ok", "message", "kind", "method", "requiresConfirm", "inventoryWaitMs", "percent"):
        if key in parsed:
            compact[key] = parsed[key]
    item = parsed.get("item")
    if isinstance(item, dict):
        compact["item"] = {
            key: item.get(key)
            for key in ("id", "aid", "artikulId", "artAltTitle", "title", "count", "path")
            if item.get(key) not in (None, "")
        }
    fallback = parsed.get("fallbackReason")
    if isinstance(fallback, dict):
        compact["fallbackReason"] = {
            key: fallback.get(key)
            for key in ("ok", "message", "status", "error")
            if fallback.get(key) not in (None, "")
        }
    resources = parsed.get("resources")
    if isinstance(resources, dict):
        compact["resources"] = {
            key: resources.get(key)
            for key in ("ok", "healthPercent", "prowessPercent")
            if resources.get(key) is not None
        }
    return compact


def _copy_request(request: ActionRequest, *, metadata: dict[str, object]) -> ActionRequest:
    return ActionRequest(
        request.action_type,
        frame_point=request.frame_point,
        screen_point=request.screen_point,
        end_frame_point=request.end_frame_point,
        end_screen_point=request.end_screen_point,
        cycle_id=request.cycle_id,
        battle_id=request.battle_id,
        dry_run=request.dry_run,
        is_viewport_move=request.is_viewport_move,
        viewport_moves_this_search=request.viewport_moves_this_search,
        scan_direction=request.scan_direction,
        metadata=metadata,
    )


def _resource_percent_from_open_result(parsed: dict[str, object], percent_key: str) -> float | None:
    resources = parsed.get("resources")
    if not isinstance(resources, dict):
        return None
    value = resources.get(percent_key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resource_percent_from_snapshot(injector: object, percent_key: str) -> float | None:
    try:
        result = injector.execute("resource_snapshot", timeout_s=2.5)  # type: ignore[attr-defined]
    except Exception:
        return None
    if not getattr(result, "ok", False):
        return None
    try:
        parsed = json.loads(str(getattr(result, "message", "")))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get(percent_key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _wait_resource_percent_after_use(
    injector: object,
    percent_key: str,
    previous_percent: object | None,
    *,
    timeout_s: float = 3.0,
    interval_s: float = 0.5,
) -> float | None:
    deadline = time.monotonic() + max(0.0, timeout_s)
    try:
        previous = None if previous_percent is None else float(previous_percent)
    except (TypeError, ValueError):
        previous = None
    last_seen: float | None = None
    while True:
        current = _resource_percent_from_snapshot(injector, percent_key)
        if current is not None:
            last_seen = current
            if previous is not None and current > previous:
                return current
            if previous is None:
                return current
        if time.monotonic() >= deadline:
            return last_seen
        time.sleep(max(0.05, interval_s))


def _refresh_resource_source_after_use(injector: object) -> object | None:
    try:
        return injector.execute("resource_refresh", timeout_s=2.5)  # type: ignore[attr-defined]
    except Exception:
        return None


def _log_action(logger: object | None, event_type: str, request: ActionRequest, **extra: object) -> None:
    if logger is None or not hasattr(logger, "log_event"):
        return
    frame_point = request.frame_point
    screen_point = request.screen_point
    fields = {
        "action_type": request.action_type,
        "cycle_id": request.cycle_id,
        "battle_id": request.battle_id,
        "x_frame": None if frame_point is None else frame_point.x,
        "y_frame": None if frame_point is None else frame_point.y,
        "x_screen": None if screen_point is None else screen_point.x,
        "y_screen": None if screen_point is None else screen_point.y,
        "x2_frame": None if request.end_frame_point is None else request.end_frame_point.x,
        "y2_frame": None if request.end_frame_point is None else request.end_frame_point.y,
        "x2_screen": None if request.end_screen_point is None else request.end_screen_point.x,
        "y2_screen": None if request.end_screen_point is None else request.end_screen_point.y,
        "dry_run": request.dry_run,
        "scan_direction": request.scan_direction,
    }
    fields.update(request.metadata or {})
    fields.update(extra)
    logger.log_event(event_type, **fields)
