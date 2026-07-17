from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol

from src.antibot_cv.automation.action_route_helpers import (
    route_confirmation_reason as _route_confirmation_reason,
)
from src.antibot_cv.automation.browser_injector import global_browser_injector
from src.antibot_cv.automation.combat_skill_mutation import skill_mutation_payload
from src.antibot_cv.automation.navigator_action_runtime import open_location_navigator_fallback
from src.antibot_cv.automation.quest_catalog_navigation import CatalogNavigationOutcome, parse_catalog_navigation_outcome
from src.antibot_cv.automation.quest_npc_open_navigation import NpcOpenOutcome, npc_open_command_payload, parse_npc_open_outcome
from src.antibot_cv.automation.quest_npc_action_journal import NpcQuestActionOutcome, parse_npc_quest_action_outcome
from src.antibot_cv.automation.resource_action_helpers import (
    refresh_resource_source_after_use,
    resource_percent_from_open_result,
    wait_resource_percent_after_use,
)
from src.antibot_cv.automation.safety import SafetyGuard
from src.antibot_cv.automation.session import SessionState
from src.antibot_cv.automation.bounded_collector import BoundedList
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
    def __init__(self, logger: object | None = None, *, max_requests: int = 2048) -> None:
        self.logger = logger
        self.requests = BoundedList[ActionRequest](max_requests)

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
    def __init__(self, logger: object | None = None, *, browser_client_id: str | None = None,
                 cancellation_event: object | None = None) -> None:
        self.logger = logger
        self.browser_client_id = browser_client_id
        self.cancellation_event = cancellation_event
        self.last_ambiguous_action: str | None = None
        self.last_catalog_navigation_outcome: CatalogNavigationOutcome | None = None
        self.last_npc_open_outcome: NpcOpenOutcome | None = None
        self.last_npc_quest_action_outcome: NpcQuestActionOutcome | None = None
        self.last_quest_inventory_snapshot: dict[str, object] | None = None
        self.last_area_object_result: dict[str, object] | None = None

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
        if self.cancellation_event is not None:
            kwargs["cancellation_event"] = self.cancellation_event
        return injector.execute(command, payload, **kwargs)  # type: ignore[attr-defined]

    def execute(self, request: ActionRequest) -> bool:
        self.last_ambiguous_action = None
        from src.antibot_cv.automation import (
            action_combat_handlers,
            action_navigation_handlers,
            action_quest_handlers,
            action_resource_handlers,
        )
        for handler in (
            action_combat_handlers,
            action_navigation_handlers,
            action_quest_handlers,
            action_resource_handlers,
        ):
            result = handler.handle_action(self, request)
            if result is not None:
                return bool(result)
        _log_action(
            self.logger,
            "action_blocked",
            request,
            block_reason=f"live_js_only_unsupported_action:{request.action_type}",
        )
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
        # Physical boundary: malformed dependency wiring must not turn a
        # dry-run guard (or a dry-run request) into a live macOS/browser
        # mutation merely because the live sink was injected by mistake.
        non_mutating_sink = type(self.sink) in (
            DryRunActionSink, ReplayActionSink, BlockedActionSink,
        )
        if (self.guard.dry_run or request.dry_run) and not non_mutating_sink:
            _log_action(
                self.logger,
                "action_blocked",
                request,
                block_reason="physical_dry_run_boundary",
            )
            return False
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
        "returnObservation", "beforePlayerStanceState", "afterPlayerStanceState",
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
