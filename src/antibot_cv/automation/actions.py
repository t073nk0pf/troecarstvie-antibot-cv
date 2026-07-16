from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol

from src.antibot_cv.automation.action_route_helpers import (
    route_confirmation_reason as _route_confirmation_reason,
)
from src.antibot_cv.automation.browser_injector import global_browser_injector
from src.antibot_cv.automation.navigator_action_runtime import open_location_navigator_fallback
from src.antibot_cv.automation.resource_action_helpers import (
    refresh_resource_source_after_use,
    resource_percent_from_open_result,
    wait_resource_percent_after_use,
)
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
            allowed_bot_ids = metadata.get("allowed_bot_ids")
            if isinstance(allowed_bot_ids, list):
                payload["allowedBotIds"] = allowed_bot_ids
            target_specs = metadata.get("target_specs")
            if isinstance(target_specs, list):
                payload["targetSpecs"] = target_specs
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
        if request.action_type == "enter_instance":
            injector = global_browser_injector()
            metadata = dict(request.metadata or {})
            expected_name = str(metadata.get("expected_name") or "").strip()
            expected_snapshot_id = str(metadata.get("expected_snapshot_id") or "").strip()
            if not expected_name or not expected_snapshot_id:
                _log_action(self.logger, "action_blocked", request, block_reason="instance_identity_missing")
                return False
            result = self._execute_injector(
                injector,
                "enter_instance",
                {
                    "expectedName": expected_name,
                    "expectedSnapshotId": expected_snapshot_id,
                    "navigationDelayMs": max(25, min(250, int(metadata.get("navigation_delay_ms") or 75))),
                },
                timeout_s=3.5,
            )
            parsed = _parse_injector_dict(result.message)
            submitted = bool(
                result.ok
                and isinstance(parsed, dict)
                and parsed.get("message") == "instance_entry_submitted"
                and parsed.get("submitted") is True
            )
            logged_request = _copy_request(
                request,
                metadata={
                    **metadata,
                    "injector_message": _compact_injector_message(result.message),
                    "injector_client_id": result.client_id,
                },
            )
            if submitted:
                _log_action(self.logger, "instance_entry_requested", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_enter_instance_failed:{_compact_injector_message(result.message)}",
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

        if request.action_type == "open_area":
            result = self._execute_injector(
                global_browser_injector(),
                "open_area",
                {"commandTimeoutMs": 4000},
                timeout_s=5.0,
            )
            result_metadata = {
                **dict(request.metadata or {}),
                "injector_message": _compact_injector_message(result.message),
                "injector_client_id": result.client_id,
            }
            logged_request = _copy_request(request, metadata=result_metadata)
            try:
                candidate = json.loads(result.message)
                parsed = candidate if isinstance(candidate, dict) else None
            except json.JSONDecodeError:
                parsed = None
            if result.ok and parsed is not None and parsed.get("message") in {
                "area_opened",
                "area_already_open",
            }:
                _log_action(self.logger, "open_area_requested", logged_request, dry_run=False)
                return True
            if parsed is not None and parsed.get("message") == "area_open_unconfirmed":
                time.sleep(0.5)
                confirmation = self._execute_injector(
                    global_browser_injector(),
                    "location_route_snapshot",
                    {},
                    timeout_s=2.5,
                )
                try:
                    confirmation_candidate = json.loads(confirmation.message)
                    confirmation_parsed = (
                        confirmation_candidate if isinstance(confirmation_candidate, dict) else None
                    )
                except json.JSONDecodeError:
                    confirmation_parsed = None
                location = (
                    confirmation_parsed.get("location")
                    if isinstance(confirmation_parsed, dict)
                    else None
                )
                if (
                    confirmation.ok
                    and isinstance(confirmation_parsed, dict)
                    and confirmation_parsed.get("message") == "location_route_snapshot"
                    and confirmation_parsed.get("pageKind") == "area"
                    and isinstance(location, dict)
                    and bool(str(location.get("id") or "").strip())
                    and bool(str(location.get("semanticName") or "").strip())
                ):
                    confirmed_metadata = {
                        **result_metadata,
                        "area_confirmation": "location_snapshot_after_delayed_navigation",
                        "area_confirmation_message": _compact_injector_message(confirmation.message),
                        "area_confirmation_client_id": confirmation.client_id,
                    }
                    _log_action(
                        self.logger,
                        "open_area_requested",
                        _copy_request(request, metadata=confirmed_metadata),
                        dry_run=False,
                    )
                    return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_open_area_failed:{_compact_injector_message(result.message)}",
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

        if request.action_type == "open_quest_catalog":
            metadata = dict(request.metadata or {})
            raw_page = metadata.get("page", 0)
            page = int(raw_page) if isinstance(raw_page, int) and not isinstance(raw_page, bool) else -1
            if page < 0 or page > 100:
                _log_action(
                    self.logger,
                    "action_blocked",
                    request,
                    block_reason="quest_catalog_page_invalid",
                )
                return False
            result = self._execute_injector(
                global_browser_injector(),
                "open_quest_catalog",
                {"page": page, "verifyTimeoutMs": 2000, "commandTimeoutMs": 5000},
                timeout_s=5.5,
            )
            result_metadata = {
                **metadata,
                "page": page,
                "injector_message": _compact_injector_message(result.message),
                "injector_client_id": result.client_id,
            }
            logged_request = _copy_request(request, metadata=result_metadata)
            if result.ok:
                _log_action(self.logger, "open_quest_catalog_requested", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_open_quest_catalog_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "open_active_quest_page":
            metadata = dict(request.metadata or {})
            raw_page = metadata.get("page", 0)
            page = int(raw_page) if isinstance(raw_page, int) and not isinstance(raw_page, bool) else -1
            if page < 0 or page > 100:
                _log_action(
                    self.logger,
                    "action_blocked",
                    request,
                    block_reason="quest_active_page_invalid",
                )
                return False
            result = self._execute_injector(
                global_browser_injector(),
                "open_active_quest_page",
                {"page": page, "verifyTimeoutMs": 2000, "commandTimeoutMs": 5000},
                timeout_s=5.5,
            )
            result_metadata = {
                **metadata,
                "page": page,
                "injector_message": _compact_injector_message(result.message),
                "injector_client_id": result.client_id,
            }
            logged_request = _copy_request(request, metadata=result_metadata)
            if result.ok:
                _log_action(self.logger, "open_active_quest_page_requested", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_open_active_quest_page_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "open_exact_npc":
            metadata = dict(request.metadata or {})
            expected_snapshot_id = str(metadata.get("expected_snapshot_id") or "").strip()
            expected_location_id = str(metadata.get("expected_location_id") or "").strip()
            npc_id = str(metadata.get("npc_id") or "").strip()
            expected_name = str(metadata.get("expected_name") or "").strip()
            expected_dialog_name = str(metadata.get("expected_dialog_name") or expected_name).strip()
            valid = (
                expected_snapshot_id.startswith("area-npcs-")
                and 0 < len(expected_snapshot_id) <= 120
                and 0 < len(expected_location_id) <= 80
                and npc_id.isdecimal()
                and int(npc_id) > 0
                and 0 < len(expected_name) <= 180
                and 0 < len(expected_dialog_name) <= 180
            )
            if not valid:
                _log_action(self.logger, "action_blocked", request, block_reason="npc_identity_invalid")
                return False
            result = self._execute_injector(
                global_browser_injector(),
                "open_exact_npc",
                {
                    "expectedSnapshotId": expected_snapshot_id,
                    "expectedLocationId": expected_location_id,
                    "npcId": npc_id,
                    "expectedName": expected_name,
                    "expectedDialogName": expected_dialog_name,
                    "verifyTimeoutMs": 2500,
                    "commandTimeoutMs": 5500,
                },
                timeout_s=6.0,
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
            if result.ok and parsed and parsed.get("message") == "npc_opened_confirmed":
                _log_action(self.logger, "exact_npc_opened", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_open_exact_npc_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "npc_quest_action":
            metadata = dict(request.metadata or {})
            expected_snapshot_id = str(metadata.get("expected_snapshot_id") or "").strip()
            npc_id = str(metadata.get("npc_id") or "").strip()
            quest_id = str(metadata.get("quest_id") or "").strip()
            expected_title = str(metadata.get("expected_title") or "").strip()
            action = str(metadata.get("action") or "").strip().lower()
            expected_ref = str(metadata.get("expected_ref") or "").strip()
            expected_point_id = str(metadata.get("expected_point_id") or "").strip()
            expected_text = str(metadata.get("expected_text") or "").strip()
            valid = (
                expected_snapshot_id.startswith("npc-dialog-")
                and 0 < len(expected_snapshot_id) <= 120
                and npc_id.isdecimal()
                and int(npc_id) > 0
                and quest_id.isdecimal()
                and int(quest_id) > 0
                and (action == "done" or 0 < len(expected_title) <= 220)
                and action in {"open", "answer", "accept", "done"}
                and (
                    action == "open"
                    or (
                        0 < len(expected_text) <= 1200
                        and (
                            action == "accept"
                            or (action == "done" and expected_point_id.isdecimal() and int(expected_point_id) > 0)
                            or (expected_ref.isdecimal() and int(expected_ref) > 0)
                        )
                    )
                )
            )
            if not valid:
                _log_action(self.logger, "action_blocked", request, block_reason="npc_quest_action_invalid")
                return False
            result = self._execute_injector(
                global_browser_injector(),
                "npc_quest_action",
                {
                    "expectedSnapshotId": expected_snapshot_id,
                    "npcId": npc_id,
                    "questId": quest_id,
                    "expectedTitle": expected_title,
                    "action": action,
                    "expectedRef": expected_ref if action == "answer" else None,
                    "expectedPointId": expected_point_id if action == "done" else None,
                    "expectedText": expected_text if action in {"answer", "accept", "done"} else None,
                },
                timeout_s=3.0,
            )
            result_metadata = {
                **metadata,
                "injector_message": _compact_injector_message(result.message),
                "injector_client_id": result.client_id,
            }
            logged_request = _copy_request(request, metadata=result_metadata)
            try:
                parsed = json.loads(result.message)
            except json.JSONDecodeError:
                parsed = None
            if result.ok and isinstance(parsed, dict) and parsed.get("message") == "npc_quest_action_submitted":
                _log_action(self.logger, "npc_quest_action_submitted", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_npc_quest_action_failed:{_compact_injector_message(result.message)}",
            )
            return False

        if request.action_type == "open_quest_navigator":
            metadata = dict(request.metadata or {})
            injector = global_browser_injector()
            result = self._execute_injector(
                injector,
                "open_quest_navigator",
                {
                    "target": metadata.get("target", ""),
                    **(
                        {"linkLabel": metadata.get("link_label")}
                        if metadata.get("link_label")
                        else {}
                    ),
                    **(
                        {"expectedQuestId": metadata.get("quest_id")}
                        if metadata.get("quest_id")
                        else {}
                    ),
                },
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
            fallback = open_location_navigator_fallback(
                result,
                metadata=result_metadata,
                execute=lambda: self._execute_injector(injector, "open_location_navigator", {}, timeout_s=2.5),
                compact_message=_compact_injector_message,
            )
            if fallback is not None:
                fallback_request = _copy_request(request, metadata=fallback.metadata)
                if fallback.ok:
                    _log_action(self.logger, "quest_navigator_opened", fallback_request, dry_run=False)
                else:
                    _log_action(self.logger, "action_blocked", fallback_request, block_reason=fallback.block_reason)
                return fallback.ok
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
            injector = global_browser_injector()
            search_delay_ms = max(250, min(15000, int(metadata.get("search_delay_ms", 250) or 250)))
            route_delay_ms = max(100, min(8000, int(metadata.get("route_delay_ms", 350) or 350)))
            command_timeout_ms = max(9000, min(25000, search_delay_ms + route_delay_ms + 2000))
            payload = {
                "target": target,
                "kind": str(metadata.get("target_kind") or "location"),
                "searchDelayMs": search_delay_ms,
                "routeDelayMs": route_delay_ms,
                "commandTimeoutMs": command_timeout_ms,
            }

            def select_target_once():
                return self._execute_injector(
                    injector,
                    "navigator_select_target",
                    payload,
                    timeout_s=(command_timeout_ms / 1000) + 1.0,
                    client_id_override=navigator_client_id,
                )

            result = select_target_once()
            parsed: dict[str, object] | None = None
            try:
                candidate = json.loads(result.message)
                parsed = candidate if isinstance(candidate, dict) else None
            except json.JSONDecodeError:
                parsed = None
            initial_message = _compact_injector_message(result.message)
            retryable_unique_result = (
                parsed is not None
                and parsed.get("message") == "navigator_target_missing_in_section"
                and parsed.get("exactCandidateCount") == 1
                and parsed.get("candidateCount") == 0
            )
            route_snapshot = parsed.get("after") if parsed is not None else None
            retryable_route_result = (
                parsed is not None
                and parsed.get("message") == "navigator_route_not_ready"
                and isinstance(route_snapshot, dict)
                and str(route_snapshot.get("target") or "").strip().casefold() == target.casefold()
            )
            if retryable_unique_result or retryable_route_result:
                retry_delay_ms = max(0, min(2000, int(metadata.get("retry_delay_ms", 500) or 0)))
                retry_request = _copy_request(
                    request,
                    metadata={
                        **metadata,
                        "injector_message": initial_message,
                        "injector_client_id": result.client_id,
                        "retry_delay_ms": retry_delay_ms,
                        "retry_limit": 1,
                    },
                )
                _log_action(self.logger, "navigator_target_selection_retry", retry_request, dry_run=False)
                if retry_delay_ms:
                    time.sleep(retry_delay_ms / 1000)
                if retryable_unique_result:
                    result = select_target_once()
                else:
                    # The site's delayed route render is started by the first
                    # exact candidate click. Re-clicking the candidate can reset
                    # that render, so confirm it with a read-only snapshot.
                    result = self._execute_injector(
                        injector,
                        "navigator_snapshot",
                        {},
                        timeout_s=2.5,
                        client_id_override=navigator_client_id,
                    )
                try:
                    candidate = json.loads(result.message)
                    parsed = candidate if isinstance(candidate, dict) else None
                except json.JSONDecodeError:
                    parsed = None
            result_metadata = {
                **metadata,
                "injector_message": _compact_injector_message(result.message),
                "injector_client_id": result.client_id,
            }
            if initial_message != result_metadata["injector_message"]:
                result_metadata["initial_injector_message"] = initial_message
            logged_request = _copy_request(request, metadata=result_metadata)
            route_snapshot_confirmed = (
                result.ok
                and parsed is not None
                and parsed.get("message") == "navigator_snapshot"
                and str(parsed.get("target") or "").strip().casefold() == target.casefold()
                and (
                    parsed.get("currentLocation") is True
                    or parsed.get("hasRoute") is True
                    or (
                        isinstance(parsed.get("routeTransitions"), int)
                        and not isinstance(parsed.get("routeTransitions"), bool)
                        and int(parsed["routeTransitions"]) > 0
                    )
                )
            )
            if result.ok and parsed is not None and (
                parsed.get("message") in {
                    "navigator_target_selected",
                    "navigator_target_already_selected",
                }
                or route_snapshot_confirmed
            ):
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
            injector = global_browser_injector()
            expected_transitions = metadata.get("route_transitions")
            parent_route_before = None
            parent_route_before_result = None
            if (
                self.browser_client_id
                and isinstance(expected_transitions, int)
                and not isinstance(expected_transitions, bool)
                and expected_transitions > 0
            ):
                parent_route_before_result = self._execute_injector(
                    injector,
                    "location_route_snapshot",
                    {},
                    timeout_s=2.5,
                    client_id_override=self.browser_client_id,
                )
                parent_route_before = _parse_injector_dict(parent_route_before_result.message)
            result = self._execute_injector(
                injector,
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
            if (
                not result.ok
                and result.message == "injector_ack_timeout"
                and self.browser_client_id
            ):
                confirmation = None
                confirmation_parsed = None
                route_confirmation_reason = "parent_route_after_unconfirmed"
                for attempt in range(20):
                    confirmation = self._execute_injector(
                        injector,
                        "location_route_snapshot",
                        {},
                        timeout_s=2.5,
                        client_id_override=self.browser_client_id,
                    )
                    confirmation_parsed = _parse_injector_dict(confirmation.message) if confirmation.ok else None
                    route_confirmation_reason = _route_confirmation_reason(
                        parent_route_before,
                        confirmation_parsed,
                        expected_transitions,
                    )
                    if route_confirmation_reason != "parent_route_after_unconfirmed":
                        break
                    if attempt < 19:
                        time.sleep(0.2)
                assert confirmation is not None
                if route_confirmation_reason in {
                    "confirmed_changed_connected_route",
                    "confirmed_connected_route_after_unconfirmed_before",
                }:
                    confirmed_metadata = {
                        **result_metadata,
                        "route_confirmation": "parent_route_snapshot_after_ack_timeout",
                        "route_confirmation_before_message": _compact_injector_message(
                            parent_route_before_result.message
                            if parent_route_before_result is not None
                            else ""
                        ),
                        "route_confirmation_message": _compact_injector_message(confirmation.message),
                        "route_confirmation_client_id": confirmation.client_id,
                    }
                    _log_action(
                        self.logger,
                        "navigator_go_requested",
                        _copy_request(request, metadata=confirmed_metadata),
                        dry_run=False,
                    )
                    return True
                result_metadata = {
                    **result_metadata,
                    "route_confirmation_rejected": route_confirmation_reason,
                    "route_confirmation_before_message": _compact_injector_message(
                        parent_route_before_result.message
                        if parent_route_before_result is not None
                        else ""
                    ),
                    "route_confirmation_message": _compact_injector_message(confirmation.message),
                    "route_confirmation_client_id": confirmation.client_id,
                }
                logged_request = _copy_request(request, metadata=result_metadata)
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

        if request.action_type == "location_route_step":
            metadata = dict(request.metadata or {})
            result = self._execute_injector(
                global_browser_injector(),
                "location_route_step",
                {
                    "expectedCurrentLocationId": metadata.get("expected_current_location_id", ""),
                    "navigationDelayMs": metadata.get("navigation_delay_ms", 75),
                },
                timeout_s=3.0,
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
            if result.ok and parsed is not None and parsed.get("submitted") is True:
                _log_action(self.logger, "location_route_step_submitted", logged_request, dry_run=False)
                return True
            _log_action(
                self.logger,
                "action_blocked",
                logged_request,
                block_reason=f"injector_location_route_step_failed:{_compact_injector_message(result.message)}",
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
                            percent_before = resource_percent_from_open_result(parsed, percent_key)
                            if percent_before is not None:
                                attempt["percent_before"] = percent_before
                            if restore_percent > 0:
                                attempt["restore_percent"] = restore_percent
                            if parsed.get("requiresConfirm") is False:
                                attempt["confirm_skipped"] = "not_required"
                                if between_items_delay_s:
                                    time.sleep(between_items_delay_s)
                                refresh_result = refresh_resource_source_after_use(
                                    injector,
                                    client_id=self.browser_client_id,
                                )
                                if refresh_result is not None:
                                    attempt["resource_refresh_ok"] = refresh_result.ok
                                    attempt["resource_refresh_message"] = _compact_injector_message(
                                        refresh_result.message
                                    )
                                percent_after = wait_resource_percent_after_use(
                                    injector,
                                    percent_key,
                                    percent_before,
                                    client_id=self.browser_client_id,
                                )
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
                        refresh_result = refresh_resource_source_after_use(
                            injector,
                            client_id=self.browser_client_id,
                        )
                        if refresh_result is not None:
                            attempt["resource_refresh_ok"] = refresh_result.ok
                            attempt["resource_refresh_message"] = _compact_injector_message(
                                refresh_result.message
                            )
                        percent_after = wait_resource_percent_after_use(
                            injector,
                            percent_key,
                            attempt.get("percent_before"),
                            client_id=self.browser_client_id,
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
        "expectedSection",
        "exactCandidateCount",
        "exactCandidateSections",
        "exactCandidateSectionDiagnostics",
        "candidateCount",
        "candidateSections",
        "visibleCandidateCount",
        "contextChanges",
        "inputCount",
        "inputDispatched",
        "section",
        "sectionEvidence",
        "currentLocation",
        "hasRoute",
        "routeTransitions",
        "currentLocationId",
        "targetLocationId",
        "foundPath",
        "transitionTimerSeconds",
        "location",
    )
    compact = {key: parsed[key] for key in compact_keys if key in parsed}
    if not compact:
        compact = {"message": parsed.get("message", "injector_result")}
    serialized = json.dumps(compact, ensure_ascii=False, separators=(",", ":"), default=str)
    return serialized[:max_length]


def _parse_injector_dict(message: object) -> dict[str, object] | None:
    try:
        parsed = json.loads(str(message or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


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
