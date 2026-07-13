from __future__ import annotations

import json
import time

from src.antibot_cv.automation.actions import DryRunActionSink
from src.antibot_cv.automation.browser_injector import InjectorResult
from src.antibot_cv.automation.config import AutomationConfig, to_plain_dict
from src.antibot_cv.automation.controller import AutomationController
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.detection.resources import ResourceBarStatus, ResourceStatus
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger
from src.antibot_cv.telemetry.m1_recovery import M1_RECOVERY_PHASES, assess_m1_recovery
from tests.conftest import blank_frame


def _controller_for_recovery(test_config: AutomationConfig) -> tuple[AutomationController, DryRunActionSink]:
    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": False,
        "max_deaths_per_session": 3,
        "route_settle_ms": 1,
    }
    logger = InMemoryEventLogger(dry_run=False)
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=logger,
        browser_client_id="parent-client",
    )
    sink = DryRunActionSink(logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller.current_page_kind = "area"
    controller.current_location_name = "Курганы бренности"
    controller._last_alive_location_name = "Курганы бренности"
    controller._route_destination_name = "Порт Барбуса"
    return controller, sink


def _run_route_leg(
    controller: AutomationController,
    *,
    current_location_id: str,
    destination_location_id: str,
) -> None:
    controller._navigator_client_id = "navigator-client"
    controller._navigator_client_bound_monotonic = time.monotonic() - 5
    controller._handle_navigator_pending()
    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING, {
        "destination_id": controller._route_destination_id,
        "submitted_from": controller._route_step_submitted_from_id,
        "events": [event["event_type"] for event in controller.logger.events[-12:]],
    }
    controller._handle_navigator_pending()
    assert controller.state_machine.state is GameState.ROUTE_RECOVERY

    controller._route_go_submitted_monotonic = time.monotonic() - 1
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "sections": {
            "location": {"data": {"pageKind": "area", "semanticName": None}},
            "battle": {"data": {"rawHasFight": False, "hasFight": False}},
        },
    }
    transition = {
        "locId": destination_location_id,
        "name": f"location-{destination_location_id}",
    }
    route_snapshots = iter(
        [
            {
                "ok": True,
                "currentLocationId": current_location_id,
                "targetLocationId": destination_location_id,
                "foundPath": [destination_location_id],
                "nextTransition": transition,
                "timerReady": False,
                "timerRemainingMs": 5000,
            },
            {
                "ok": True,
                "currentLocationId": current_location_id,
                "targetLocationId": destination_location_id,
                "foundPath": [destination_location_id],
                "nextTransition": transition,
                "timerReady": False,
                "timerRemainingMs": 4000,
            },
            {
                "ok": True,
                "currentLocationId": current_location_id,
                "targetLocationId": destination_location_id,
                "foundPath": [destination_location_id],
                "nextTransition": transition,
                "timerReady": False,
                "timerRemainingMs": 3000,
            },
            {
                "ok": True,
                "currentLocationId": current_location_id,
                "targetLocationId": destination_location_id,
                "foundPath": [destination_location_id],
                "nextTransition": transition,
                "timerReady": True,
                "timerRemainingMs": 0,
            },
            {
                "ok": True,
                "currentLocationId": current_location_id,
                "targetLocationId": destination_location_id,
                "foundPath": [destination_location_id],
                "nextTransition": transition,
                "timerReady": True,
                "timerRemainingMs": 0,
            },
            {
                "ok": True,
                "currentLocationId": destination_location_id,
                "targetLocationId": "0",
                "foundPath": [],
                "nextTransition": None,
                "timerReady": True,
                "timerRemainingMs": 0,
            },
        ]
    )
    controller._location_route_snapshot_via_injector = lambda: next(route_snapshots)

    controller._handle_route_recovery()
    controller._handle_route_recovery()
    controller._handle_route_recovery()
    controller._handle_route_recovery()
    controller._handle_route_recovery()
    controller._handle_route_recovery()


def _advance_through_recovery_routes(controller: AutomationController, monkeypatch) -> None:
    assert controller._handle_leveling_death(
        {"dead": True, "freeReviveAvailable": True, "freeReviveOptionCount": 1}
    ) is True

    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "snapshotId": "alive-after-revive",
        "sections": {
            "player": {"data": {"name": "v3g45", "level": 5, "xpPercent": 20}},
            "deathRevive": {
                "data": {
                    "dead": False,
                    "freeReviveAvailable": False,
                    "resurrectionNoticeAvailable": True,
                }
            },
            "location": {"data": {"pageKind": "area", "semanticName": "Город Барбус"}},
        },
    }
    assert controller._observe_death_guard(force=True) is True

    controller.current_location_name = "Город Барбус"
    assert controller._handle_leveling_death(
        {"dead": False, "freeReviveAvailable": False, "resurrectionNoticeAvailable": False}
    ) is True

    controller._resource_status_via_injector = lambda: ResourceStatus(
        health=ResourceBarStatus("health", True, 100.0, 1.0),
        prowess=ResourceBarStatus("prowess", True, 100.0, 1.0),
    )
    controller._navigator_client_ids_for_parent = lambda: set()
    controller._find_navigator_client = lambda: {"client_id": "navigator-client"}

    class FakeInjector:
        def execute(self, command, *, timeout_s, client_id):
            assert command == "navigator_snapshot"
            assert timeout_s == 2.5
            assert client_id == "navigator-client"
            return InjectorResult(
                True,
                json.dumps(
                    {
                        "snapshotId": f"navigator-{controller._navigator_target_name}",
                        "generatedAt": time.time(),
                        "href": "https://3kingdoms.ru/navigator.php?name=x",
                        "target": controller._navigator_target_name,
                        "currentLocation": False,
                        "hasRoute": True,
                        "routeTransitions": 1,
                        "visibleGoButtonCount": 1,
                    }
                ),
                client_id=client_id,
            )

    import src.antibot_cv.automation.browser_injector as browser_injector_module

    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: FakeInjector())
    controller._last_rest_check_monotonic = None
    controller._handle_post_revive_recovery(blank_frame())
    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING, {
        "destination_id": controller._route_destination_id,
        "submitted_from": controller._route_step_submitted_from_id,
        "route_kind": controller._route_recovery_kind,
        "events": [event["event_type"] for event in controller.logger.events[-20:]],
    }

    _run_route_leg(
        controller,
        current_location_id="109",
        destination_location_id="110",
    )
    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING, {
        "destination_id": controller._route_destination_id,
        "submitted_from": controller._route_step_submitted_from_id,
        "route_kind": controller._route_recovery_kind,
        "events": [event["event_type"] for event in controller.logger.events[-20:]],
    }
    _run_route_leg(
        controller,
        current_location_id="110",
        destination_location_id="200",
    )


def test_m1_recovery_emits_one_ordered_offline_evidence_chain(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, sink = _controller_for_recovery(test_config)

    _advance_through_recovery_routes(controller, monkeypatch)

    recovery_events = [
        event for event in controller.logger.events if event["event_type"] in M1_RECOVERY_PHASES
    ]
    assessment = assess_m1_recovery(recovery_events)

    assert [request.action_type for request in sink.requests] == [
        "revive_free",
        "close_resurrection_notice",
        "open_location_navigator",
        "navigator_select_target",
        "navigator_go",
        "location_route_step",
        "open_location_navigator",
        "navigator_select_target",
        "navigator_go",
        "location_route_step",
        "open_hunt",
    ]
    assert [event["event_type"] for event in recovery_events] == list(M1_RECOVERY_PHASES)
    assert assessment["offline_ready"] is True
    assert assessment["complete_attempts"] == 1
    assert assessment["consecutive_complete_attempts"] == 1


def test_m1_recovery_does_not_complete_when_final_hunt_is_rejected(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, _ = _controller_for_recovery(test_config)
    execute = controller.action_executor.execute
    controller.action_executor.execute = lambda request: (
        False if request.action_type == "open_hunt" else execute(request)
    )

    _advance_through_recovery_routes(controller, monkeypatch)

    event_types = [event["event_type"] for event in controller.logger.events]
    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "quest_refresh_return_failed"
    assert "hunt_opened" not in event_types
    assert "death_recovery_completed" not in event_types


def test_m1_runtime_rejects_out_of_order_phase_before_terminal(
    test_config: AutomationConfig,
) -> None:
    controller, _ = _controller_for_recovery(test_config)
    controller._active_recovery_id = "out-of-order"
    controller._recovery_phase_events.clear()

    assert controller._log_recovery_phase("death_detected") is True
    assert controller._log_recovery_phase("revive_confirmed") is False
    assert controller._log_recovery_phase("revive_requested") is True
    for phase in M1_RECOVERY_PHASES[3:-1]:
        controller._log_recovery_phase(phase)

    assert controller._complete_death_recovery_evidence("test") is False
    assert not any(
        event["event_type"] == "death_recovery_completed"
        for event in controller.logger.events
    )


def test_resurrection_notice_does_not_confirm_revive_while_still_dead(
    test_config: AutomationConfig,
) -> None:
    controller, sink = _controller_for_recovery(test_config)
    assert controller._handle_leveling_death(
        {"dead": True, "freeReviveAvailable": True, "freeReviveOptionCount": 1}
    ) is True
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "snapshotId": "still-dead",
        "sections": {
            "deathRevive": {
                "data": {
                    "dead": True,
                    "freeReviveAvailable": True,
                    "freeReviveOptionCount": 1,
                    "resurrectionNoticeAvailable": True,
                }
            },
            "location": {"data": {"pageKind": "battle", "semanticName": None}},
        },
    }

    assert controller._observe_death_guard(force=True) is True

    assert [request.action_type for request in sink.requests] == ["revive_free"]
    event_types = [event["event_type"] for event in controller.logger.events]
    assert "revive_confirmed" not in event_types
    assert "notice_closed" not in event_types
