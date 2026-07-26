from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from src.antibot_cv.automation.actions import DryRunActionSink
from src.antibot_cv.automation.browser_injector import InjectorResult
from src.antibot_cv.automation.config import AutomationConfig, to_plain_dict
from src.antibot_cv.automation.controller import AutomationController
from src.antibot_cv.automation.death_recovery import RecoveryCheckpoint
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_policy import QuestIntent
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.automation.route_recovery_policy import (
    ROUTE_ABSOLUTE_BUDGET_MS,
    RouteRecoveryCadence,
)
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger


def _controller(test_config: AutomationConfig) -> tuple[AutomationController, DryRunActionSink]:
    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "enabled": True, "navigator_timeout_ms": 1000}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="parent-client",
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    return controller, sink


def _route_phase() -> SimpleNamespace:
    return SimpleNamespace(value="ROUTE")


def test_route_recovery_cadence_backs_off_only_for_unchanged_fingerprint() -> None:
    cadence = RouteRecoveryCadence(max_unchanged=3)
    assert cadence.observe(("snapshot-1", "area", "A"), 10.0) is True
    assert cadence.next_poll_at == 10.5
    assert cadence.observe(("snapshot-1", "area", "A"), 10.5) is False
    assert cadence.next_poll_at == 11.5
    assert cadence.observe(("snapshot-2", "area", "B"), 11.5) is True
    assert cadence.interval_s == 0.5
    assert cadence.unchanged_count == 0


def test_route_budget_is_absolute_and_never_scales_with_transition_count(test_config) -> None:
    controller, _ = _controller(test_config)
    controller._route_expected_transitions = 1
    single_step_budget = controller._route_recovery_timeout_ms()
    controller._route_expected_transitions = 50
    assert controller._route_recovery_timeout_ms() == single_step_budget
    assert 60_000 <= single_step_budget <= ROUTE_ABSOLUTE_BUDGET_MS


def test_rehydrated_arrived_route_preserves_original_absolute_deadline(
    test_config: AutomationConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    controller, sink = _controller(test_config)
    controller.state_machine.state = GameState.NAVIGATOR_PENDING
    controller._navigator_target_name = "Порт Барбуса"
    controller._navigator_opened_monotonic = time.monotonic()
    controller._navigator_client_id = "navigator-client"
    controller._navigator_requires_target_selection = False
    controller._route_recovery_kind = "quest_location"
    controller._route_rehydrate_attempts = 1
    original_started = time.monotonic() - 50
    original_deadline = time.monotonic() + 10
    controller._route_started_monotonic = original_started
    controller._route_deadline_monotonic = original_deadline
    controller._find_navigator_client = lambda: {"client_id": "navigator-client"}
    _bind_exact_location_evidence(controller, target="Порт Барбуса")

    class ArrivedInjector:
        def execute(self, command, *, timeout_s, client_id):
            return InjectorResult(True, json.dumps({
                "snapshotId": "arrived", "generatedAt": time.time(),
                "href": "https://3kingdoms.ru/navigator.php", "target": "Порт Барбуса",
                "currentLocation": True, "hasRoute": False, "routeTransitions": 0,
                "visibleGoButtonCount": 0,
            }), client_id=client_id)

    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: ArrivedInjector())
    controller._handle_navigator_pending()

    assert [request.action_type for request in sink.requests] == ["open_area"]
    assert controller._route_started_monotonic == original_started
    assert controller._route_deadline_monotonic == original_deadline


def _bind_exact_location_evidence(
    controller: AutomationController,
    *,
    target: str = "Дикий предел",
) -> None:
    objective = SimpleNamespace(
        quest_id="91",
        quest_title="Охота",
        fingerprint="fresh-fingerprint",
        navigator_label=target,
        monster=SimpleNamespace(name="волколак"),
    )
    controller._quest_director = SimpleNamespace(
        active_objective=objective,
        chain=SimpleNamespace(
            lease=SimpleNamespace(
                quest_id="91",
                quest_title="Охота",
                current_fingerprint="fresh-fingerprint",
            )
        ),
    )
    controller._active_quest_id = "91"
    controller._quest_route_locations = (target,)
    controller._quest_target_routes = {"волколак": (target,)}
    controller._quest_route_link_label = target


def test_deadline_expiring_inside_blocking_navigator_snapshot_has_zero_mutation(
    test_config: AutomationConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    controller, sink = _controller(test_config)
    controller.state_machine.state = GameState.NAVIGATOR_PENDING
    controller._navigator_target_name = "Порт Барбуса"
    controller._navigator_opened_monotonic = time.monotonic()
    controller._navigator_client_id = "navigator-client"
    controller._navigator_requires_target_selection = False
    controller._route_deadline_monotonic = time.monotonic() + 0.005
    controller._find_navigator_client = lambda: {"client_id": "navigator-client"}

    class SlowInjector:
        def execute(self, command, *, timeout_s, client_id):
            assert command == "navigator_snapshot"
            time.sleep(0.015)
            return InjectorResult(True, json.dumps({"ok": True}), client_id=client_id)

    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: SlowInjector())
    controller._handle_navigator_pending()

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "navigator_snapshot_deadline_exhausted"
    assert sink.requests == []


def test_wrong_navigator_snapshot_client_stops_before_route_mutation(
    test_config: AutomationConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    controller, sink = _controller(test_config)
    controller.state_machine.state = GameState.NAVIGATOR_PENDING
    controller._navigator_target_name = "Порт Барбуса"
    controller._navigator_opened_monotonic = time.monotonic()
    controller._navigator_client_id = "navigator-client"
    controller._navigator_requires_target_selection = False
    controller._find_navigator_client = lambda: {"client_id": "navigator-client"}

    class WrongClientInjector:
        def execute(self, command, *, timeout_s, client_id):
            assert command == "navigator_snapshot"
            return InjectorResult(
                True,
                json.dumps(
                    {
                        "hasRoute": True,
                        "isCurrentLocation": False,
                        "goUrl": "/area.php?go=77",
                    }
                ),
                client_id="other-navigator-client",
            )

    monkeypatch.setattr(
        browser_injector_module,
        "global_browser_injector",
        lambda: WrongClientInjector(),
    )
    controller._handle_navigator_pending()

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "navigator_snapshot_client_mismatch"
    assert sink.requests == []


def test_blocking_navigator_go_deadline_stops_before_area_handoff(
    test_config: AutomationConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    controller, _ = _controller(test_config)
    _bind_exact_location_evidence(controller)
    controller.state_machine.state = GameState.NAVIGATOR_PENDING
    controller._navigator_target_name = "Дикий предел"
    controller._navigator_opened_monotonic = time.monotonic()
    controller._navigator_client_id = "navigator-client"
    controller._navigator_requires_target_selection = False
    controller._route_recovery_kind = "quest_location"
    controller._route_deadline_monotonic = time.monotonic() + 0.005
    controller._find_navigator_client = lambda: {"client_id": "navigator-client"}

    class RouteInjector:
        def execute(self, command, *, timeout_s, client_id):
            assert command == "navigator_snapshot"
            return InjectorResult(
                True,
                json.dumps(
                    {
                        "snapshotId": "route-1",
                        "generatedAt": time.time(),
                        "href": "https://3kingdoms.ru/navigator.php",
                        "target": "Дикий предел",
                        "currentLocation": False,
                        "hasRoute": True,
                        "routeTransitions": 1,
                        "visibleGoButtonCount": 1,
                    }
                ),
                client_id=client_id,
            )

    class SlowActionExecutor:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request) -> bool:
            self.requests.append(request)
            if request.action_type == "navigator_go":
                time.sleep(0.015)
            return True

    executor = SlowActionExecutor()
    controller.action_executor = executor
    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: RouteInjector())

    controller._handle_navigator_pending()

    assert [request.action_type for request in executor.requests] == ["navigator_go"]
    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "navigator_go_deadline_exhausted"


def test_legacy_quest_location_without_director_stops_before_open_navigator(
    test_config: AutomationConfig,
) -> None:
    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "auto_navigate_quest_targets": True,
        "autonomous_quest_director": False,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_page_kind = "quests"
    controller._quest_refresh_requested_monotonic = time.monotonic()
    controller._update_quest_target_names(
        {
            "items": [
                {
                    "objective": "Уничтожьте волколака.",
                    "navigation": [{"text": "Дикий предел"}],
                }
            ]
        }
    )
    controller._quest_policy_intent = QuestIntent.NAVIGATE
    controller.state_machine.state = GameState.QUEST_REFRESH_PENDING

    controller._handle_quest_refresh()

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "quest_location_route_binding_mismatch"
    assert sink.requests == []


@pytest.mark.parametrize(
    ("kind", "expected_reason"),
    (
        ("quest_accept", "route_arrival_binding:accept_phase_mismatch"),
        ("quest_dialogue", "route_arrival_binding:lease_missing"),
        ("quest_turn_in", "route_arrival_binding:lease_missing"),
    ),
)
def test_coordinator_arrival_mismatch_stops_before_phase_mutation_or_action(
    test_config: AutomationConfig,
    kind: str,
    expected_reason: str,
) -> None:
    controller, sink = _controller(test_config)
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller._route_recovery_kind = kind
    controller._route_destination_name = "Туманные луга"
    controller.current_location_name = "Туманные луга"
    controller._quest_director = SimpleNamespace(
        pending_accept=None,
        chain=SimpleNamespace(lease=None, pending_accepted_ref=None),
        active_route_plan=None,
        active_objective=None,
    )
    controller._quest_intake.pending = None
    controller._quest_dialogue.pending = None
    controller._quest_turn_in.pending = None

    assert controller._finish_route_arrival("test_arrival") is True
    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == expected_reason
    assert sink.requests == []


def test_accept_ambiguous_giver_fails_before_director_mutation(
    test_config: AutomationConfig,
) -> None:
    controller, sink = _controller(test_config)
    calls: list[str] = []
    controller._quest_director = SimpleNamespace(begin_accept=lambda quest_id: calls.append(quest_id))
    quest = QuestRef("31", "Поручение", location="Туманные луга", giver_names=("Алхимик", "Староста"))

    assert controller._begin_quest_acceptance(quest) is True
    assert calls == []
    assert controller.last_error_reason == "quest_accept_giver_missing_or_ambiguous"
    assert sink.requests == []


def test_stale_director_owned_quest_location_has_zero_mutation(
    test_config: AutomationConfig,
) -> None:
    controller, sink = _controller(test_config)
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller._route_recovery_kind = "quest_location"
    controller._route_destination_name = "Дикий предел"
    controller.current_location_name = "Дикий предел"
    controller._active_quest_id = "91"
    controller._quest_route_locations = ("Дикий предел",)
    controller._quest_target_routes = {"волколак": ("Дикий предел",)}
    controller._quest_route_link_label = "Дикий предел"
    objective = SimpleNamespace(
        quest_id="91",
        quest_title="Охота",
        fingerprint="fresh-fingerprint",
        navigator_label="Дикий предел",
        monster=SimpleNamespace(name="волколак"),
    )
    controller._quest_director = SimpleNamespace(
        active_objective=objective,
        chain=SimpleNamespace(
            lease=SimpleNamespace(quest_id="91", quest_title="Охота", current_fingerprint="stale-fingerprint")
        ),
    )

    assert controller._finish_route_arrival("stale_arrival") is True
    assert controller.last_error_reason == "route_arrival_binding:quest_location_identity_mismatch"
    assert sink.requests == []


def test_wrong_location_snapshot_client_cannot_trigger_rehydrate(
    test_config: AutomationConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    controller, sink = _controller(test_config)

    class WrongClientInjector:
        def execute(self, command, *, timeout_s, client_id):
            assert command == "location_route_snapshot"
            return InjectorResult(True, json.dumps({"ok": True}), client_id="other-client")

    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: WrongClientInjector())
    assert controller._location_route_snapshot_via_injector() is None
    assert controller._route_rehydrate_attempts == 0
    assert sink.requests == []


def test_post_revive_quest_resume_mismatch_stops_before_navigation(
    test_config: AutomationConfig,
) -> None:
    controller, sink = _controller(test_config)
    controller.state_machine.state = GameState.POST_REVIVE_RECOVERY
    controller.current_location_name = "Курганы бренности"
    controller._death_checkpoint = RecoveryCheckpoint(
        activity=GameState.ROUTE_RECOVERY.value,
        location="Курганы бренности",
        quest="91",
        snapshot_id="death-1",
    )
    controller._route_resume_target_name = "Туманные луга"
    controller._route_resume_recovery_kind = "quest_dialogue"
    controller._quest_director = SimpleNamespace(chain=SimpleNamespace(lease=None))
    controller._quest_dialogue.pending = None

    assert controller._resume_after_post_revive_recovery("resources_confirmed") is True
    assert controller.last_error_reason == "post_revive_route_binding:lease_missing"
    assert sink.requests == []
