from __future__ import annotations

import json

from src.antibot_cv.automation.actions import ActionExecutor, ActionRequest, DryRunActionSink, LiveMacActionSink
from src.antibot_cv.automation.actions import _log_action, _route_confirmation_reason
from src.antibot_cv.automation.browser_injector import InjectorResult
from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.automation.safety import SafetyGuard
from src.antibot_cv.automation.session import MAX_HUNT_CLICKS_PER_CYCLE, SessionState
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger
from src.antibot_cv.viewport.coordinates import Point


class FailingSink:
    def execute(self, request: ActionRequest) -> bool:
        raise AssertionError("live sink must not be reached")


def test_dry_run_no_live_click(test_config: AutomationConfig) -> None:
    session = SessionState(requested_cycles=3)
    guard = SafetyGuard(test_config)
    sink = DryRunActionSink()
    executor = ActionExecutor(guard=guard, session=session, sink=sink)
    ok = executor.execute(ActionRequest("click_target", frame_point=Point(1, 2), screen_point=Point(3, 4), dry_run=True))
    assert ok is True
    assert len(sink.requests) == 1
    assert session.total_actions == 1


def test_emergency_stop_blocks_actions(test_config: AutomationConfig) -> None:
    session = SessionState(requested_cycles=3)
    guard = SafetyGuard(test_config)
    guard.emergency_stop()
    executor = ActionExecutor(guard=guard, session=session, sink=FailingSink())
    ok = executor.execute(ActionRequest("click_target", screen_point=Point(3, 4), dry_run=True))
    assert ok is False
    assert session.total_actions == 0


def test_max_actions_blocks(test_config: AutomationConfig) -> None:
    config = test_config.with_overrides()
    session = SessionState(requested_cycles=3)
    guard = SafetyGuard(config)
    for _ in range(config.safety.max_actions_per_minute):
        guard.record_action()
    decision = guard.allow_action("click_target", completed_cycles=0, requested_cycles=3)
    assert decision.allowed is False
    assert decision.reason == "max_actions_per_minute"


def test_consecutive_errors_latch_stop(test_config: AutomationConfig) -> None:
    guard = SafetyGuard(test_config)
    guard.record_error()
    guard.record_error()
    decision = guard.record_error()
    assert decision.allowed is False
    assert guard.emergency_stopped is True


def test_ability_exit_hunt_limits_retries(test_config: AutomationConfig) -> None:
    session = SessionState(requested_cycles=3)
    session.new_battle()
    guard = SafetyGuard(test_config)
    sink = DryRunActionSink()
    executor = ActionExecutor(guard=guard, session=session, sink=sink)
    assert executor.execute(ActionRequest("click_ability_4", screen_point=Point(1, 1), battle_id=1, dry_run=True))
    assert not executor.execute(ActionRequest("click_ability_4", screen_point=Point(1, 1), battle_id=1, dry_run=True))
    assert executor.execute(ActionRequest("click_exit", screen_point=Point(1, 1), battle_id=1, dry_run=True))
    assert not executor.execute(ActionRequest("click_exit", screen_point=Point(1, 1), battle_id=1, dry_run=True))
    for _ in range(MAX_HUNT_CLICKS_PER_CYCLE):
        assert executor.execute(ActionRequest("click_hunt", screen_point=Point(1, 1), cycle_id=0, dry_run=True))
    assert not executor.execute(ActionRequest("click_hunt", screen_point=Point(1, 1), cycle_id=0, dry_run=True))
    assert session.hunt_actions == MAX_HUNT_CLICKS_PER_CYCLE


def test_attack_click_has_bounded_retries(test_config: AutomationConfig) -> None:
    session = SessionState(requested_cycles=3)
    session.new_battle()
    guard = SafetyGuard(test_config)
    sink = DryRunActionSink()
    executor = ActionExecutor(guard=guard, session=session, sink=sink)

    for _ in range(3):
        assert executor.execute(ActionRequest("click_attack", screen_point=Point(1, 1), battle_id=1, dry_run=True))
    assert not executor.execute(ActionRequest("click_attack", screen_point=Point(1, 1), battle_id=1, dry_run=True))
    assert session.attack_actions == 3


def test_log_action_allows_live_dry_run_override() -> None:
    logger = InMemoryEventLogger()
    request = ActionRequest("click_target", screen_point=Point(1, 1), dry_run=True)
    _log_action(logger, "click_target_clicked", request, dry_run=False)
    assert logger.events[-1]["dry_run"] is False


def test_live_mouse_click_is_blocked_in_js_only_mode() -> None:
    logger = InMemoryEventLogger(dry_run=False)
    sink = LiveMacActionSink(logger)

    ok = sink.execute(ActionRequest("click_combat_slot", screen_point=Point(10, 20), dry_run=False))

    assert not ok
    assert logger.events[-1]["event_type"] == "action_blocked"
    assert logger.events[-1]["block_reason"] == "live_js_only_unsupported_action:click_combat_slot"


def test_live_attack_visible_target_passes_allowed_levels(monkeypatch) -> None:
    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            assert command == "attack_visible_bot"
            assert payload == {
                "confirmed": 1,
                "margin": 35,
                "allowedLevels": [3],
                "verifyTimeoutMs": 3500,
                "commandTimeoutMs": 6500,
            }
            assert timeout_s == 7.0
            assert required_version is None
            return InjectorResult(
                True,
                '{"ok":true,"message":"huntAttack","target":{"botId":1675,"name":"mob","level":3,"levelSource":"lvl","x":10,"y":20,"screenX":30,"screenY":40}}',
                "client",
            )

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(ActionRequest("attack_visible_target", metadata={"confirmed": 1, "margin": 35, "allowed_levels": [3]}, dry_run=False))

    assert ok
    assert logger.events[-1]["event_type"] == "attack_visible_target_requested"
    assert logger.events[-1]["target_level"] == 3


def test_live_ability_uses_js_skill_without_screen_point(monkeypatch) -> None:
    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            assert command == "use_skill_slot"
            assert payload == {"slot": 4, "verifyTimeoutMs": 900, "commandTimeoutMs": 4000}
            assert timeout_s == 4.5
            assert required_version is None
            return InjectorResult(
                True,
                '{"ok":true,"message":"useSkill","slot":4,"ability":{"id":-10,"slot":4,"name":"test"}}',
                "client",
            )

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(ActionRequest("click_ability_4", metadata={"use_js_skill": True, "skill_slot": 4}, dry_run=False))

    assert ok
    assert logger.events[-1]["event_type"] == "click_ability_4_js"
    assert logger.events[-1]["ability_id"] == -10
    assert logger.events[-1]["ability_name"] == "test"


def test_live_combat_slot_uses_js_skill_without_screen_point(monkeypatch) -> None:
    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            assert command == "use_skill_slot"
            assert payload == {"slot": 4, "verifyTimeoutMs": 900, "commandTimeoutMs": 4000}
            assert timeout_s == 4.5
            assert required_version is None
            return InjectorResult(True, '{"ok":true,"message":"useSkill","slot":4}', "client")

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(ActionRequest("click_combat_slot", metadata={"use_js_skill": True, "slot_index": 4}, dry_run=False))

    assert ok
    assert logger.events[-1]["event_type"] == "click_combat_slot_js"
    assert logger.events[-1]["skill_slot"] == 4


def test_live_js_combat_logs_compact_injector_evidence(monkeypatch) -> None:
    huge_snapshot = {"candidates": ["x" * 2000 for _ in range(20)]}

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            if command == "use_skill_slot":
                return InjectorResult(
                    True,
                    json.dumps(
                        {
                            "ok": True,
                            "message": "useSkill_confirmed",
                            "slot": 2,
                            "ability": {"id": -10, "slot": 2, "name": "test"},
                            "before": huge_snapshot,
                            "after": huge_snapshot,
                        }
                    ),
                    "client",
                )
            if command == "use_battle_item":
                return InjectorResult(
                    True,
                    json.dumps(
                        {
                            "ok": True,
                            "message": "battle_item_used",
                            "kind": "health",
                            "slot": 5,
                            "item": {"id": 101, "slot": 5, "name": "item"},
                            "method": "useSkill",
                            "evidence": {"confirmed": True, "itemChanged": True},
                            "afterSnapshot": huge_snapshot,
                        }
                    ),
                    "client",
                )
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    assert sink.execute(
        ActionRequest("click_combat_slot", metadata={"use_js_skill": True, "skill_slot": 2}, dry_run=False)
    )
    assert sink.execute(
        ActionRequest(
            "use_battle_item",
            metadata={"kind": "health", "slots": [5], "names": ["item"]},
            dry_run=False,
        )
    )

    serialized = json.dumps(logger.events, ensure_ascii=False)
    assert len(serialized) < 5000
    assert "x" * 2000 not in serialized


def test_live_recovery_logs_compact_failed_inventory_diagnostics(monkeypatch) -> None:
    huge = "x" * 5000

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            assert command == "open_recovery_item"
            return InjectorResult(
                False,
                json.dumps(
                    {
                        "ok": False,
                        "message": "recovery_item_use_failed",
                        "kind": payload["kind"] if payload else "",
                        "item": {
                            "artikulId": "3573417035",
                            "artAltTitle": "Малый бурдюк жизни",
                            "count": 1,
                            "debug": huge,
                        },
                        "fallbackReason": {
                            "message": "useArtifact_rejected",
                            "status": -1,
                            "error": "Призрак не может использовать этот предмет!",
                            "response": huge,
                        },
                        "resources": {
                            "healthPercent": 0,
                            "prowessPercent": 0,
                            "candidates": [huge],
                        },
                    }
                ),
                "client",
            )

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    assert not sink.execute(
        ActionRequest(
            "use_recovery_items",
            metadata={
                "health_names": ["бурдюк жизни"],
                "prowess_names": ["бурдюк удали"],
                "max_uses_per_resource": 1,
                "inventory_open_delay_ms": 0,
                "open_hunt_after": False,
            },
            dry_run=False,
        )
    )

    serialized = json.dumps(logger.events, ensure_ascii=False)
    assert len(serialized) < 6000
    assert huge not in serialized
    attempts = logger.events[-1]["recovery_item_attempts"]
    assert attempts[0]["open_result"]["fallbackReason"]["status"] == -1
    assert attempts[0]["open_result"]["resources"]["healthPercent"] == 0


def test_live_viewport_move_uses_js_hunt_direction(monkeypatch) -> None:
    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            assert command == "hunt_move_direction"
            assert payload == {"direction": "SOUTH", "margin": 35}
            assert required_version is None
            return InjectorResult(True, '{"ok":true,"message":"hunt_direction_moved","direction":"south"}', "client")

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(
        ActionRequest(
            "viewport_move",
            scan_direction="SOUTH",
            metadata={"use_js_hunt_direction": True, "margin": 35},
            is_viewport_move=True,
            dry_run=False,
        )
    )

    assert ok
    assert logger.events[-1]["event_type"] == "viewport_move_js_hunt_direction"
    assert logger.events[-1]["scan_direction"] == "SOUTH"


def test_live_recovery_items_can_use_multiple_burdjuks(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object] | None]] = []

    class FakeInjector:
        def __init__(self) -> None:
            self.open_counts = {"health": 0, "prowess": 0}
            self.resources = {"healthPercent": 20, "prowessPercent": 40}

        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            calls.append((command, payload))
            assert required_version is None
            if command == "open_recovery_item":
                assert payload is not None
                kind = str(payload["kind"])
                self.open_counts[kind] += 1
                resources = dict(self.resources)
                return InjectorResult(
                    True,
                    json.dumps(
                        {
                            "ok": True,
                            "message": "recovery_item_clicked",
                            "kind": kind,
                            "resources": resources,
                            "item": {"artikulId": "111"},
                        }
                    ),
                    "client",
                )
            if command == "confirm_action_form":
                if self.open_counts["health"] > 0 and self.open_counts["prowess"] == 0:
                    self.resources["healthPercent"] += 40
                elif self.open_counts["prowess"] > 0:
                    self.resources["prowessPercent"] += 30
                return InjectorResult(True, '{"ok":true,"message":"action_form_confirmed"}', "client")
            if command == "resource_snapshot":
                return InjectorResult(True, json.dumps(self.resources), "client")
            if command == "open_hunt":
                return InjectorResult(True, '{"ok":true,"message":"open_hunt"}', "client")
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(
        ActionRequest(
            "use_recovery_items",
            metadata={
                "health_names": ["бурдюк жизни"],
                "prowess_names": ["бурдюк удали"],
                "use_when_below_percent": 90,
                "health_restore_percent": 40,
                "prowess_restore_percent": 30,
                "max_uses_per_resource": 4,
                "inventory_open_delay_ms": 1600,
                "confirm_delay_ms": 0,
                "between_items_delay_ms": 0,
            },
            dry_run=False,
        )
    )

    assert ok
    assert [command for command, payload in calls if command == "open_recovery_item" for _ in [payload]] == [
        "open_recovery_item",
        "open_recovery_item",
        "open_recovery_item",
        "open_recovery_item",
    ]
    attempts = logger.events[-1]["recovery_item_attempts"]
    assert [attempt["kind"] for attempt in attempts] == ["health", "health", "prowess", "prowess"]
    assert attempts[1]["percent_after"] == 100
    assert attempts[3]["percent_after"] == 100
    open_payloads = [payload for command, payload in calls if command == "open_recovery_item"]
    assert [payload["inventoryOpenDelayMs"] for payload in open_payloads] == [1600, 1600, 1600, 1600]


def test_live_recovery_items_passes_force_use_to_injector(monkeypatch) -> None:
    payloads: list[dict[str, object]] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            if command == "open_recovery_item":
                assert payload is not None
                payloads.append(payload)
                return InjectorResult(
                    True,
                    json.dumps(
                        {
                            "ok": True,
                            "message": "recovery_item_not_needed",
                            "kind": payload["kind"],
                            "resources": {"healthPercent": 100, "prowessPercent": 100},
                        }
                    ),
                    "client",
                )
            if command == "open_hunt":
                return InjectorResult(True, '{"ok":true,"message":"open_hunt"}', "client")
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(
        ActionRequest(
            "use_recovery_items",
            metadata={
                "health_names": ["бурдюк жизни"],
                "prowess_names": ["бурдюк удали"],
                "use_when_below_percent": 90,
                "force_use": True,
                "open_hunt_after": False,
            },
            dry_run=False,
        )
    )

    assert ok
    assert [payload["forceUse"] for payload in payloads] == [True, True]


def test_live_recovery_items_extends_open_timeout_for_inventory_delay(monkeypatch) -> None:
    open_timeouts: list[float] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            if command == "open_recovery_item":
                open_timeouts.append(timeout_s)
                return InjectorResult(
                    True,
                    json.dumps(
                        {
                            "ok": True,
                            "message": "recovery_item_not_needed",
                            "kind": payload["kind"] if payload else "health",
                            "resources": {"healthPercent": 100, "prowessPercent": 100},
                        }
                    ),
                    "client",
                )
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(
        ActionRequest(
            "use_recovery_items",
            metadata={
                "health_names": ["бурдюк жизни"],
                "prowess_names": ["бурдюк удали"],
                "timeout_s": 1,
                "inventory_open_delay_ms": 5000,
                "open_hunt_after": False,
            },
            dry_run=False,
        )
    )

    assert ok
    assert open_timeouts == [10.5, 10.5]


def test_live_recovery_items_skips_confirm_when_not_required(monkeypatch) -> None:
    calls: list[str] = []
    client_ids: list[str | None] = []

    class FakeInjector:
        def __init__(self) -> None:
            self.resources = {"healthPercent": 100, "prowessPercent": 80}

        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append(command)
            client_ids.append(client_id)
            if command == "open_recovery_item":
                kind = str((payload or {}).get("kind"))
                if kind == "health":
                    return InjectorResult(
                        True,
                        json.dumps(
                            {
                                "ok": True,
                                "message": "recovery_item_not_needed",
                                "kind": "health",
                                "percent": 100,
                                "threshold": 90,
                            }
                        ),
                        "client",
                    )
                return InjectorResult(
                    True,
                    json.dumps(
                        {
                            "ok": True,
                            "message": "recovery_item_clicked",
                            "kind": "prowess",
                            "requiresConfirm": False,
                            "resources": {"healthPercent": 100, "prowessPercent": 80},
                        }
                    ),
                    "client",
                )
            if command == "confirm_action_form":
                raise AssertionError("confirm must be skipped")
            if command == "resource_refresh":
                self.resources["prowessPercent"] = 95
                return InjectorResult(True, '{"ok":true,"message":"main_frame_reload_scheduled"}', "client")
            if command == "resource_snapshot":
                return InjectorResult(True, json.dumps(self.resources), "client")
            return InjectorResult(True, '{"ok":true}', "client")

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="client-a")

    ok = sink.execute(
        ActionRequest(
            "use_recovery_items",
            metadata={
                "health_names": [],
                "prowess_names": ["бурдюк удали"],
                "use_when_below_percent": 90,
                "health_restore_percent": 40,
                "prowess_restore_percent": 30,
                "max_uses_per_resource": 1,
                "confirm_delay_ms": 0,
                "between_items_delay_ms": 0,
                "open_hunt_after": False,
            },
            dry_run=False,
        )
    )

    assert ok
    assert calls.count("open_recovery_item") == 2
    assert "confirm_action_form" not in calls
    assert "resource_snapshot" in calls
    assert client_ids and set(client_ids) == {"client-a"}
    attempts = logger.events[-1]["recovery_item_attempts"]
    assert attempts[-1]["confirm_skipped"] == "not_required"


def test_live_recovery_items_does_not_open_hunt_when_resource_not_confirmed(monkeypatch) -> None:
    calls: list[str] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            calls.append(command)
            if command == "open_recovery_item":
                kind = str((payload or {}).get("kind"))
                if kind == "health":
                    return InjectorResult(
                        True,
                        json.dumps({"ok": True, "message": "recovery_item_not_needed", "kind": "health", "percent": 100}),
                        "client",
                    )
                return InjectorResult(
                    True,
                    json.dumps(
                        {
                            "ok": True,
                            "message": "recovery_item_clicked",
                            "kind": "prowess",
                            "requiresConfirm": False,
                            "resources": {"healthPercent": 100, "prowessPercent": 80},
                        }
                    ),
                    "client",
                )
            if command == "resource_refresh":
                return InjectorResult(True, '{"ok":true,"message":"main_frame_reload_scheduled"}', "client")
            if command == "resource_snapshot":
                return InjectorResult(True, json.dumps({"healthPercent": 100, "prowessPercent": 80}), "client")
            if command == "open_hunt":
                raise AssertionError("hunt must not open before recovery is confirmed")
            return InjectorResult(True, '{"ok":true}', "client")

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(
        ActionRequest(
            "use_recovery_items",
            metadata={
                "health_names": ["бурдюк жизни"],
                "prowess_names": ["бурдюк удали"],
                "use_when_below_percent": 90,
                "max_uses_per_resource": 1,
                "confirm_delay_ms": 0,
                "between_items_delay_ms": 0,
                "open_hunt_after": True,
            },
            dry_run=False,
        )
    )

    assert not ok
    assert "open_hunt" not in calls
    assert logger.events[-1]["event_type"] == "action_blocked"
    assert logger.events[-1]["open_hunt_skipped"] == "recovery_failed"
    assert logger.events[-1]["recovery_resource_results"][-1]["ok"] is False


def test_live_navigator_actions_keep_parent_and_child_clients_separate(monkeypatch) -> None:
    calls: list[tuple[str, str | None, dict[str, object], float]] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, client_id, dict(payload or {}), timeout_s))
            if command in {"open_quest_navigator", "open_location_navigator"}:
                return InjectorResult(True, '{"submitted":true}', client_id)
            if command == "navigator_select_target":
                return InjectorResult(
                    True,
                    '{"message":"navigator_target_selected","selected":true}',
                    client_id,
                )
            if command == "navigator_go":
                return InjectorResult(
                    True,
                    '{"message":"navigator_go_submitted","submitted":true}',
                    client_id,
                )
            if command == "location_route_step":
                return InjectorResult(
                    True,
                    '{"message":"location_route_step_submitted","submitted":true}',
                    client_id,
                )
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(
        ActionRequest(
            "open_quest_navigator",
            metadata={"target": "Дикий предел"},
            dry_run=False,
        )
    )
    assert sink.execute(
        ActionRequest(
            "open_quest_navigator",
            metadata={
                "target": "Кабан-секач [5]",
                "link_label": "Кабанов-секачей",
            },
            dry_run=False,
        )
    )
    assert sink.execute(
        ActionRequest(
            "open_location_navigator",
            metadata={"target": "Курганы бренности"},
            dry_run=False,
        )
    )
    assert sink.execute(
        ActionRequest(
            "navigator_select_target",
            metadata={
                "target": "Бродячий муравей [4]",
                "target_kind": "monster",
                "navigator_client_id": "child-client",
                "search_delay_ms": 250,
                "route_delay_ms": 350,
            },
            dry_run=False,
        )
    )
    assert sink.execute(
        ActionRequest(
            "navigator_go",
            metadata={"target": "Дикий предел", "navigator_client_id": "child-client"},
            dry_run=False,
        )
    )
    assert sink.execute(
        ActionRequest(
            "location_route_step",
            metadata={"expected_current_location_id": "102", "navigation_delay_ms": 75},
            dry_run=False,
        )
    )

    assert calls == [
        ("open_quest_navigator", "parent-client", {"target": "Дикий предел"}, 2.5),
        (
            "open_quest_navigator",
            "parent-client",
            {"target": "Кабан-секач [5]", "linkLabel": "Кабанов-секачей"},
            2.5,
        ),
        ("open_location_navigator", "parent-client", {}, 2.5),
        (
            "navigator_select_target",
            "child-client",
            {
                "target": "Бродячий муравей [4]",
                "kind": "monster",
                "searchDelayMs": 250,
                "routeDelayMs": 350,
                "commandTimeoutMs": 9000,
            },
            10.0,
        ),
        ("navigator_go", "child-client", {"expectedTarget": "Дикий предел"}, 3.0),
        (
            "location_route_step",
            "parent-client",
            {"expectedCurrentLocationId": "102", "navigationDelayMs": 75},
            3.0,
        ),
    ]


def test_live_open_quest_catalog_forwards_only_bounded_integer_page(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object], float, str | None]] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, dict(payload or {}), timeout_s, client_id))
            return InjectorResult(True, '{"message":"quest_catalog_opened_confirmed"}', client_id)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(ActionRequest("open_quest_catalog", metadata={"page": 2}, dry_run=False))
    assert not sink.execute(ActionRequest("open_quest_catalog", metadata={"page": "2"}, dry_run=False))
    assert not sink.execute(ActionRequest("open_quest_catalog", metadata={"page": 101}, dry_run=False))
    assert calls == [
        (
            "open_quest_catalog",
            {"page": 2, "verifyTimeoutMs": 2000, "commandTimeoutMs": 5000},
            5.5,
            "parent-client",
        )
    ]


def test_live_open_active_quest_page_forwards_only_bounded_integer_page(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object], float, str | None]] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, dict(payload or {}), timeout_s, client_id))
            return InjectorResult(True, '{"message":"quest_active_opened_confirmed"}', client_id)

    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(InMemoryEventLogger(dry_run=False), browser_client_id="parent-client")

    assert sink.execute(ActionRequest("open_active_quest_page", metadata={"page": 1}, dry_run=False))
    assert not sink.execute(ActionRequest("open_active_quest_page", metadata={"page": "1"}, dry_run=False))
    assert not sink.execute(ActionRequest("open_active_quest_page", metadata={"page": 101}, dry_run=False))
    assert calls == [(
        "open_active_quest_page",
        {"page": 1, "verifyTimeoutMs": 2000, "commandTimeoutMs": 5000},
        5.5,
        "parent-client",
    )]


def test_live_open_exact_npc_requires_structured_snapshot_bound_identity(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object], float, str | None]] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, dict(payload or {}), timeout_s, client_id))
            return InjectorResult(True, '{"message":"npc_opened_confirmed"}', client_id)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")
    valid = {
        "expected_snapshot_id": "area-npcs-mrj-1",
        "expected_location_id": "125",
        "npc_id": "6",
        "expected_name": "Моряк Кентур",
    }

    assert sink.execute(ActionRequest("open_exact_npc", metadata=valid, dry_run=False))
    assert not sink.execute(ActionRequest("open_exact_npc", metadata={**valid, "npc_id": "6x"}, dry_run=False))
    assert not sink.execute(
        ActionRequest("open_exact_npc", metadata={**valid, "expected_snapshot_id": "wrong"}, dry_run=False)
    )
    assert calls == [
        (
            "open_exact_npc",
            {
                "expectedSnapshotId": "area-npcs-mrj-1",
                "expectedLocationId": "125",
                "npcId": "6",
                "expectedName": "Моряк Кентур",
                "expectedDialogName": "Моряк Кентур",
                "verifyTimeoutMs": 2500,
                "commandTimeoutMs": 5500,
            },
            6.0,
            "parent-client",
        )
    ]


def test_live_npc_quest_action_requires_exact_numeric_quest_contract(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object], float, str | None]] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, dict(payload or {}), timeout_s, client_id))
            return InjectorResult(True, '{"message":"npc_quest_action_submitted"}', client_id)

    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(InMemoryEventLogger(dry_run=False), browser_client_id="parent-client")
    valid = {
        "expected_snapshot_id": "npc-dialog-mrj-1",
        "npc_id": "13",
        "quest_id": "246",
        "expected_title": "Хворь скакунов",
        "action": "open",
    }

    assert sink.execute(ActionRequest("npc_quest_action", metadata=valid, dry_run=False))
    assert not sink.execute(ActionRequest("npc_quest_action", metadata={**valid, "quest_id": "246x"}, dry_run=False))
    assert not sink.execute(ActionRequest("npc_quest_action", metadata={**valid, "action": "accept"}, dry_run=False))
    answer = {
        **valid,
        "action": "answer",
        "expected_ref": "3441",
        "expected_text": "Поклон тебе, почтенный воевода!",
    }
    assert sink.execute(ActionRequest("npc_quest_action", metadata=answer, dry_run=False))
    assert not sink.execute(ActionRequest("npc_quest_action", metadata={**answer, "expected_ref": "bad"}, dry_run=False))
    accept = {**valid, "action": "accept", "expected_text": "Взять задание"}
    assert sink.execute(ActionRequest("npc_quest_action", metadata=accept, dry_run=False))
    assert calls == [
        (
            "npc_quest_action",
            {
                "expectedSnapshotId": "npc-dialog-mrj-1",
                "npcId": "13",
                "questId": "246",
                "expectedTitle": "Хворь скакунов",
                "action": "open",
                "expectedRef": None,
                "expectedText": None,
            },
            3.0,
            "parent-client",
        ),
        (
            "npc_quest_action",
            {
                "expectedSnapshotId": "npc-dialog-mrj-1",
                "npcId": "13",
                "questId": "246",
                "expectedTitle": "Хворь скакунов",
                "action": "answer",
                "expectedRef": "3441",
                "expectedText": "Поклон тебе, почтенный воевода!",
            },
            3.0,
            "parent-client",
        ),
        (
            "npc_quest_action",
            {
                "expectedSnapshotId": "npc-dialog-mrj-1",
                "npcId": "13",
                "questId": "246",
                "expectedTitle": "Хворь скакунов",
                "action": "accept",
                "expectedRef": None,
                "expectedText": "Взять задание",
            },
            3.0,
            "parent-client",
        ),
    ]


def test_live_navigator_retries_once_after_unique_section_lag(monkeypatch) -> None:
    calls: list[tuple[str, str | None, dict[str, object], float]] = []
    sleeps: list[float] = []
    responses = iter(
        [
            InjectorResult(
                False,
                '{"ok":false,"message":"navigator_target_missing_in_section",'
                '"exactCandidateCount":1,"candidateCount":0}',
                "child-client",
            ),
            InjectorResult(
                True,
                '{"ok":true,"message":"navigator_target_selected","selected":true}',
                "child-client",
            ),
        ]
    )

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, client_id, dict(payload or {}), timeout_s))
            return next(responses)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    monkeypatch.setattr("src.antibot_cv.automation.actions.time.sleep", sleeps.append)
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(
        ActionRequest(
            "navigator_select_target",
            metadata={
                "target": "Белая Рысь [6]",
                "target_kind": "monster",
                "navigator_client_id": "child-client",
                "search_delay_ms": 12000,
                "route_delay_ms": 8000,
                "retry_delay_ms": 500,
            },
            dry_run=False,
        )
    )

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][2]["commandTimeoutMs"] == 22000
    assert calls[0][3] == 23.0
    assert sleeps == [0.5]
    assert [event["event_type"] for event in logger.events[-2:]] == [
        "navigator_target_selection_retry",
        "navigator_target_selected",
    ]


def test_live_navigator_retries_once_after_confirmed_target_route_lag(monkeypatch) -> None:
    calls: list[tuple[str, str | None, dict[str, object], float]] = []
    sleeps: list[float] = []
    responses = iter(
        [
            InjectorResult(
                False,
                '{"ok":false,"message":"navigator_route_not_ready",'
                '"after":{"ok":true,"target":"Белая Рысь [6]","hasRoute":false}}',
                "child-client",
            ),
            InjectorResult(
                True,
                '{"ok":true,"message":"navigator_snapshot","target":"Белая Рысь [6]",'
                '"currentLocation":false,"hasRoute":false,"routeTransitions":6}',
                "child-client",
            ),
        ]
    )

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, client_id, dict(payload or {}), timeout_s))
            return next(responses)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    monkeypatch.setattr("src.antibot_cv.automation.actions.time.sleep", sleeps.append)
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(
        ActionRequest(
            "navigator_select_target",
            metadata={
                "target": "Белая Рысь [6]",
                "target_kind": "monster",
                "navigator_client_id": "child-client",
                "search_delay_ms": 12000,
                "route_delay_ms": 8000,
                "retry_delay_ms": 500,
            },
            dry_run=False,
        )
    )

    assert len(calls) == 2
    assert calls[0][0] == "navigator_select_target"
    assert calls[1] == ("navigator_snapshot", "child-client", {}, 2.5)
    assert sleeps == [0.5]
    assert [event["event_type"] for event in logger.events[-2:]] == [
        "navigator_target_selection_retry",
        "navigator_target_selected",
    ]


def test_live_navigator_go_confirms_parent_route_after_child_ack_timeout(monkeypatch) -> None:
    calls: list[tuple[str, str | None, dict[str, object], float]] = []
    parent_snapshots = iter(
        [
            '{"ok":true,"message":"location_route_snapshot","pageKind":"area",'
            '"currentLocationId":"112","targetLocationId":"0","foundPath":[],'
            '"nextTransition":null,"transitionTimerSeconds":0}',
            '{"ok":true,"message":"location_route_snapshot","pageKind":"area",'
            '"currentLocationId":"112","targetLocationId":"125",'
            '"foundPath":["111","110","121","122","123","125"],'
            '"nextTransition":{"id":"12","locId":"111"},"transitionTimerSeconds":0}',
        ]
    )

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, client_id, dict(payload or {}), timeout_s))
            if command == "navigator_go":
                return InjectorResult(False, "injector_ack_timeout", "child-client")
            if command == "location_route_snapshot":
                return InjectorResult(
                    True,
                    next(parent_snapshots),
                    "parent-client",
                )
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(
        ActionRequest(
            "navigator_go",
            metadata={
                "target": "Белая Рысь [6]",
                "navigator_client_id": "child-client",
                "route_transitions": 6,
            },
            dry_run=False,
        )
    )

    assert calls == [
        ("location_route_snapshot", "parent-client", {}, 2.5),
        ("navigator_go", "child-client", {"expectedTarget": "Белая Рысь [6]"}, 3.0),
        ("location_route_snapshot", "parent-client", {}, 2.5),
    ]
    assert logger.events[-1]["event_type"] == "navigator_go_requested"
    assert logger.events[-1]["route_confirmation"] == "parent_route_snapshot_after_ack_timeout"


def test_live_navigator_go_waits_for_delayed_parent_route_after_popup_closes(monkeypatch) -> None:
    snapshots = iter(
        [
            '{"ok":false,"message":"location_route_page_missing","pageKind":"quests"}',
            '{"ok":false,"message":"location_route_page_missing","pageKind":"quests"}',
            '{"ok":true,"message":"location_route_snapshot","pageKind":"area",'
            '"currentLocationId":"102","targetLocationId":"130",'
            '"foundPath":["101","110","130"],'
            '"nextTransition":{"locId":"101"}}',
        ]
    )
    sleeps: list[float] = []

    class FakeInjector:
        def execute(self, command, payload=None, *, timeout_s=2.5, client_id=None):
            if command == "navigator_go":
                return InjectorResult(False, "injector_ack_timeout", "child-client")
            if command == "location_route_snapshot":
                message = next(snapshots)
                return InjectorResult('"ok":true' in message, message, "parent-client")
            raise AssertionError(command)

    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    monkeypatch.setattr("src.antibot_cv.automation.actions.time.sleep", lambda seconds: sleeps.append(seconds))
    sink = LiveMacActionSink(InMemoryEventLogger(dry_run=False), browser_client_id="parent-client")

    assert sink.execute(
        ActionRequest(
            "navigator_go",
            metadata={"target": "Лес призраков", "navigator_client_id": "child-client", "route_transitions": 3},
            dry_run=False,
        )
    )
    assert sleeps == [0.2]
    assert sink.logger.events[-1]["route_confirmation"] == "parent_route_snapshot_after_ack_timeout"


def test_live_navigator_go_rejects_unchanged_parent_route_after_ack_timeout(monkeypatch) -> None:
    route = (
        '{"ok":true,"message":"location_route_snapshot","pageKind":"area",'
        '"currentLocationId":"112","targetLocationId":"125",'
        '"foundPath":["111","110","121","122","123","125"],'
        '"nextTransition":{"id":"12","locId":"111"}}'
    )

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            if command == "location_route_snapshot":
                return InjectorResult(True, route, "parent-client")
            if command == "navigator_go":
                return InjectorResult(False, "injector_ack_timeout", "child-client")
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert not sink.execute(
        ActionRequest(
            "navigator_go",
            metadata={
                "target": "Белая Рысь [6]",
                "navigator_client_id": "child-client",
                "route_transitions": 6,
            },
            dry_run=False,
        )
    )
    assert logger.events[-1]["event_type"] == "action_blocked"
    assert logger.events[-1]["route_confirmation_rejected"] == "parent_route_unchanged_after_go"


def test_live_navigator_go_rejects_disconnected_parent_route_after_ack_timeout(monkeypatch) -> None:
    parent_snapshots = iter(
        [
            '{"ok":true,"message":"location_route_snapshot","pageKind":"area",'
            '"currentLocationId":"112","targetLocationId":"0","foundPath":[],"nextTransition":null}',
            '{"ok":true,"message":"location_route_snapshot","pageKind":"area",'
            '"currentLocationId":"112","targetLocationId":"125",'
            '"foundPath":["999","110","121","122","123","124"],'
            '"nextTransition":{"id":"12","locId":"999"}}',
        ]
    )

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            if command == "location_route_snapshot":
                return InjectorResult(True, next(parent_snapshots), "parent-client")
            if command == "navigator_go":
                return InjectorResult(False, "injector_ack_timeout", "child-client")
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert not sink.execute(
        ActionRequest(
            "navigator_go",
            metadata={
                "target": "Белая Рысь [6]",
                "navigator_client_id": "child-client",
                "route_transitions": 6,
            },
            dry_run=False,
        )
    )
    assert logger.events[-1]["event_type"] == "action_blocked"
    assert logger.events[-1]["route_confirmation_rejected"] == "parent_route_destination_disconnected"


def test_navigator_go_route_confirmation_rejects_zero_target_sentinel() -> None:
    before = {
        "message": "location_route_snapshot",
        "pageKind": "area",
        "currentLocationId": "112",
        "targetLocationId": "125",
        "foundPath": ["125"],
        "nextTransition": {"locId": "125"},
    }
    malformed_after = {
        "message": "location_route_snapshot",
        "pageKind": "area",
        "currentLocationId": "112",
        "targetLocationId": "0",
        "foundPath": ["0"],
        "nextTransition": {"locId": "0"},
    }

    assert (
        _route_confirmation_reason(before, malformed_after, 1)
        == "parent_route_target_missing"
    )


def test_live_open_area_uses_bounded_bridge_command(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object], float]] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, dict(payload or {}), timeout_s))
            return InjectorResult(True, '{"ok":true,"message":"area_opened","opened":true}', "parent-client")

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(ActionRequest("open_area", metadata={"reason": "checkpoint"}, dry_run=False))
    assert calls == [("open_area", {"commandTimeoutMs": 4000}, 5.0)]
    assert logger.events[-1]["event_type"] == "open_area_requested"


def test_live_open_area_confirms_delayed_area_navigation(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object], float]] = []
    sleeps: list[float] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, dict(payload or {}), timeout_s))
            if command == "open_area":
                return InjectorResult(False, '{"ok":false,"message":"area_open_unconfirmed"}', "parent-client")
            if command == "location_route_snapshot":
                return InjectorResult(
                    True,
                    '{"ok":true,"message":"location_route_snapshot","pageKind":"area",'
                    '"location":{"id":"125","semanticName":"Порт безбрежного моря"}}',
                    "parent-client",
                )
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    monkeypatch.setattr("src.antibot_cv.automation.actions.time.sleep", sleeps.append)
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(ActionRequest("open_area", metadata={"reason": "checkpoint"}, dry_run=False))
    assert calls == [
        ("open_area", {"commandTimeoutMs": 4000}, 5.0),
        ("location_route_snapshot", {}, 2.5),
    ]
    assert sleeps == [0.5]
    assert logger.events[-1]["event_type"] == "open_area_requested"
    assert logger.events[-1]["area_confirmation"] == "location_snapshot_after_delayed_navigation"
