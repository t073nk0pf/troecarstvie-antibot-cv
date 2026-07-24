from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest

from src.antibot_cv.automation.actions import (
    ActionDiagnosticCode, ActionExecutionResult, ActionExecutionStatus, ActionExecutor, ActionRequest,
    BlockedActionSink, DryRunActionSink, LiveMacActionSink,
)
from src.antibot_cv.automation.actions import _log_action, _route_confirmation_reason
from src.antibot_cv.automation.browser_injector import InjectorResult
from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.automation.safety import SafetyGuard
from src.antibot_cv.automation.session import MAX_HUNT_CLICKS_PER_CYCLE, SessionState
from src.antibot_cv.automation.live_action_service import LiveActionService
from src.antibot_cv.automation.mutation_lease import (
    MutationLeaseCoordinator,
    MutationLeaseMode,
    MutationTarget,
)
from src.antibot_cv.automation.npc_census_inspect_journal import (
    NpcCensusInspectJournal, PendingCensusInspection,
)
from src.antibot_cv.automation.npc_census_live import CensusInspectContract
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger
from src.antibot_cv.viewport.coordinates import Point


class FailingSink:
    def execute(self, request: ActionRequest) -> bool:
        raise AssertionError("live sink must not be reached")


class DelegatingSink:
    def __init__(self, target: LiveMacActionSink) -> None:
        self.target = target

    def execute(self, request: ActionRequest) -> bool:
        return self.target.execute(request)


class DelegatingDryRunSink(DryRunActionSink):
    def __init__(self, target: LiveMacActionSink) -> None:
        super().__init__()
        self.target = target

    def execute(self, request: ActionRequest) -> bool:
        return self.target.execute(request)


def test_live_quest_item_acknowledgement_is_transport_success(monkeypatch) -> None:
    calls: list[str] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            **kwargs: object,
        ) -> InjectorResult:
            calls.append(command)
            if command == "use_quest_item":
                return InjectorResult(
                    True,
                    '{"ok":true,"message":"quest_item_use_acknowledged"}',
                    "client",
                )
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr(
        "src.antibot_cv.automation.actions.global_browser_injector",
        lambda: FakeInjector(),
    )
    sink = LiveMacActionSink(logger)

    ok = sink.execute(ActionRequest(
        "use_quest_item",
        metadata={"expected_name": "Точильный камень", "confirm_delay_ms": 0},
        dry_run=False,
    ))

    assert ok
    assert calls == ["use_quest_item"]


def test_live_quest_inventory_inspection_does_not_reopen_hunt_before_validation(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class FakeInjector:
        def client_snapshot(self, client_id: str | None = None) -> dict[str, object]:
            return {"client_id": client_id, "profile_id": "profile-a", "tab_id": 17}

        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            **kwargs: object,
        ) -> InjectorResult:
            calls.append(command)
            if command != "inventory_snapshot":
                raise AssertionError(command)
            return InjectorResult(
                True,
                json.dumps(
                    {
                        "ok": True,
                        "category": "quest",
                        "categoryConfirmed": False,
                        "truncated": False,
                        "items": [],
                    }
                ),
                "client",
            )

    monkeypatch.setattr(
        "src.antibot_cv.automation.actions.global_browser_injector",
        lambda: FakeInjector(),
    )
    logger = InMemoryEventLogger(dry_run=False)
    sink = LiveMacActionSink(logger)

    assert sink.execute(ActionRequest("inspect_quest_inventory", dry_run=False))
    assert calls == ["inventory_snapshot"]
    assert logger.events[-1]["event_type"] == "quest_inventory_inspected"
    assert "open_hunt_ok" not in logger.events[-1]


def test_live_semantic_inventory_snapshot_binds_transport_identity_and_request_baseline(
    monkeypatch,
) -> None:
    class FakeInjector:
        def client_snapshot(self, client_id: str | None = None) -> dict[str, object]:
            return {"client_id": client_id, "profile_id": "profile-a", "tab_id": 17}

        def execute(self, *args: object, **kwargs: object) -> InjectorResult:
            return InjectorResult(True, json.dumps({
                "ok": True, "category": "quest", "categoryConfirmed": True,
                "snapshotId": "inventory-1", "generatedAt": "2026-07-18T10:00:00Z",
                "revision": 999, "items": [],
                "clientId": "untrusted-page-client",
            }), "transport-client")

    injector = FakeInjector()
    monkeypatch.setattr(
        "src.antibot_cv.automation.actions.global_browser_injector", lambda: injector,
    )
    sink = LiveMacActionSink(InMemoryEventLogger(dry_run=False))

    assert sink.execute(ActionRequest(
        "inspect_quest_inventory", dry_run=False,
        metadata={"causal_baseline": "catalog-base", "minimum_revision": 7},
    ))
    assert sink.last_quest_inventory_snapshot == {
        "ok": True, "category": "quest", "categoryConfirmed": True,
        "snapshotId": "inventory-1", "generatedAt": "2026-07-18T10:00:00Z",
        "revision": 999, "items": [], "clientId": "transport-client",
        "profileId": "profile-a", "tabId": "17", "causalBaseline": "catalog-base",
    }


def test_live_semantic_inventory_snapshot_rejects_missing_transport_identity(
    monkeypatch,
) -> None:
    class FakeInjector:
        def client_snapshot(self, client_id: str | None = None) -> dict[str, object]:
            return {"client_id": client_id, "profile_id": "", "tab_id": None}

        def execute(self, *args: object, **kwargs: object) -> InjectorResult:
            return InjectorResult(True, json.dumps({
                "ok": True, "category": "quest", "categoryConfirmed": True,
                "snapshotId": "inventory-1", "generatedAt": "2026-07-18T10:00:00Z",
                "revision": 1, "items": [],
            }), "transport-client")

    monkeypatch.setattr(
        "src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector(),
    )
    sink = LiveMacActionSink(InMemoryEventLogger(dry_run=False))

    assert not sink.execute(ActionRequest(
        "inspect_quest_inventory", dry_run=False,
        metadata={"causal_baseline": "catalog-base", "minimum_revision": 7},
    ))
    assert sink.last_quest_inventory_snapshot is None


def test_live_semantic_inventory_snapshot_requires_strict_revision_advance(
    monkeypatch,
) -> None:
    revisions = iter((7, 8))

    class FakeInjector:
        def client_snapshot(self, client_id: str | None = None) -> dict[str, object]:
            return {"client_id": client_id, "profile_id": "profile-a", "tab_id": 17}

        def execute(self, *args: object, **kwargs: object) -> InjectorResult:
            revision = next(revisions)
            return InjectorResult(True, json.dumps({
                "ok": True, "category": "quest", "categoryConfirmed": True,
                "snapshotId": f"inventory-{revision}",
                "generatedAt": "2026-07-18T10:00:00Z",
                "revision": revision, "items": [],
            }), "transport-client")

    monkeypatch.setattr(
        "src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector(),
    )
    sink = LiveMacActionSink(InMemoryEventLogger(dry_run=False))
    request = ActionRequest(
        "inspect_quest_inventory", dry_run=False,
        metadata={"causal_baseline": "catalog-base", "minimum_revision": 7},
    )

    assert not sink.execute(request)
    assert sink.last_quest_inventory_snapshot is None
    assert sink.execute(request)
    assert sink.last_quest_inventory_snapshot["revision"] == 8


def test_dry_run_no_live_click(test_config: AutomationConfig) -> None:
    session = SessionState(requested_cycles=3)
    guard = SafetyGuard(test_config)
    sink = DryRunActionSink()
    executor = ActionExecutor(guard=guard, session=session, sink=sink)
    ok = executor.execute(ActionRequest("click_target", frame_point=Point(1, 2), screen_point=Point(3, 4), dry_run=True))
    assert ok is True
    assert len(sink.requests) == 1
    assert session.total_actions == 1


def test_dry_run_request_collection_is_bounded() -> None:
    sink = DryRunActionSink(max_requests=2)
    for index in range(4):
        sink.execute(ActionRequest(f"action-{index}"))
    assert [request.action_type for request in sink.requests] == ["action-2", "action-3"]


def test_session_battle_and_cycle_tracking_collections_are_reclaimed() -> None:
    session = SessionState(requested_cycles=2)
    session.new_battle()
    session.mark_ability4()
    session.mark_attack()
    session.mark_exit()
    session.mark_hunt()
    session.complete_cycle()

    assert session.ability_used_battle_ids == set()
    assert session.exit_clicked_battle_ids == set()
    assert session.attack_click_counts_by_battle_id == {}
    assert session.hunt_click_counts_by_cycle_id == {}


def test_physical_dry_run_boundary_blocks_malformed_live_sink_wiring(
    test_config: AutomationConfig, monkeypatch,
) -> None:
    sink = LiveMacActionSink(browser_client_id="client-a")
    calls: list[ActionRequest] = []
    monkeypatch.setattr(sink, "execute", lambda request: calls.append(request) or True)
    guard = SafetyGuard(test_config)
    executor = ActionExecutor(
        guard=guard, session=SessionState(requested_cycles=1), sink=sink,
    )

    assert executor.execute(ActionRequest("open_area", dry_run=False)) is False
    assert calls == []
    assert executor.session.total_actions == 0


def test_physical_dry_run_boundary_blocks_live_sink_adapter(
    test_config: AutomationConfig, monkeypatch,
) -> None:
    live_sink = LiveMacActionSink(browser_client_id="client-a")
    calls: list[ActionRequest] = []
    monkeypatch.setattr(live_sink, "execute", lambda request: calls.append(request) or True)
    for adapter in (DelegatingSink(live_sink), DelegatingDryRunSink(live_sink)):
        executor = ActionExecutor(
            guard=SafetyGuard(test_config),
            session=SessionState(requested_cycles=1),
            sink=adapter,
        )
        assert executor.execute(ActionRequest("open_area", dry_run=False)) is False
        assert executor.session.total_actions == 0
    assert calls == []


def test_live_action_service_persists_rate_limit_per_client(
    test_config: AutomationConfig,
) -> None:
    config = replace(
        test_config,
        dry_run=False,
        safety=replace(test_config.safety, max_actions_per_minute=2),
    )
    sinks: dict[str, DryRunActionSink] = {}
    service = LiveActionService(
        lambda: config,
        sink_factory=lambda client_id: sinks.setdefault(client_id, DryRunActionSink()),
    )
    request = ActionRequest("open_area", dry_run=False)

    assert service.execute("client-a", request) is True
    assert service.execute("client-a", request) is True
    assert service.execute("client-a", request) is False
    assert len(sinks["client-a"].requests) == 2
    assert service.counters("client-a")["rate_actions"] == 2
    assert service.execute("client-b", request) is True


def test_live_action_service_emergency_and_error_latches_persist_between_requests(
    test_config: AutomationConfig,
) -> None:
    config = replace(test_config, dry_run=False)
    sink = DryRunActionSink()
    service = LiveActionService(lambda: config, sink_factory=lambda _client_id: sink)
    request = ActionRequest("open_area", dry_run=False)

    assert service.execute("client-a", request) is True
    service.record_error("client-a")
    service.emergency_stop("client-a")
    assert service.execute("client-a", request) is False
    assert len(sink.requests) == 1
    assert service.counters("client-a") == {
        "total_actions": 1,
        "rate_actions": 1,
        "emergency_stopped": True,
        "consecutive_errors": 1,
    }


def test_live_action_service_preserves_safety_context_across_transport_reconnect(
    test_config: AutomationConfig,
) -> None:
    config = replace(test_config, dry_run=False)
    logical_ids = {"old-client": ("profile-a", 17), "new-client": ("profile-a", 17)}
    sinks: dict[str, DryRunActionSink] = {}
    service = LiveActionService(
        lambda: config,
        sink_factory=lambda client_id: sinks.setdefault(client_id, DryRunActionSink()),
        identity_factory=logical_ids.get,
    )
    request = ActionRequest("open_area", dry_run=False)

    assert service.execute("old-client", request) is True
    service.record_error("old-client")
    service.emergency_stop("old-client")
    assert service.execute("new-client", request) is False
    assert "new-client" in sinks
    assert sinks["new-client"].requests == []
    assert service.counters("new-client") == {
        "total_actions": 1,
        "rate_actions": 1,
        "emergency_stopped": True,
        "consecutive_errors": 1,
    }


def test_live_action_service_cannot_mutate_tab_owned_by_controller_run(
    test_config: AutomationConfig,
) -> None:
    config = replace(test_config, dry_run=False)
    coordinator = MutationLeaseCoordinator()
    target = MutationTarget("profile-a", 17)
    run_lease = coordinator.try_acquire(
        target, "controller:run-1", MutationLeaseMode.EXCLUSIVE_RUN,
    )
    sink = DryRunActionSink()
    service = LiveActionService(
        lambda: config,
        sink_factory=lambda _client_id: sink,
        identity_factory=lambda _client_id: ("profile-a", 17),
        mutation_coordinator=coordinator,
    )

    assert run_lease is not None
    assert service.execute("client-a", ActionRequest("open_area", dry_run=False)) is False
    assert sink.requests == []


def test_fenced_service_retains_issued_lease_until_exact_reconciliation(
    test_config: AutomationConfig,
) -> None:
    config = replace(test_config, dry_run=False)
    coordinator = MutationLeaseCoordinator()
    sink = DryRunActionSink()
    service = LiveActionService(
        lambda: config,
        sink_factory=lambda _client_id: sink,
        identity_factory=lambda _client_id: ("profile-a", 17),
        mutation_coordinator=coordinator,
    )

    result = service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
    )
    assert result.execution.status is ActionExecutionStatus.ISSUED
    assert result.lease is not None
    assert sink.requests[0].metadata["mutation_fence"] == {
        "profile_id": "profile-a",
        "tab_id": 17,
        "actor_generation": result.lease.actor_generation,
        "fencing_token": result.lease.fencing_token,
    }
    assert coordinator.holder(MutationTarget("profile-a", 17)) == result.lease
    blocked = service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
    )
    assert blocked.execution.status is ActionExecutionStatus.NOT_ISSUED
    assert len(sink.requests) == 1
    assert service.reconcile_fenced(result.lease)
    assert not service.reconcile_fenced(result.lease)


def test_action_execution_outcome_distinguishes_pre_sink_reject_from_unknown_delivery(
    test_config: AutomationConfig,
) -> None:
    rejected_sink = LiveMacActionSink()
    rejected = ActionExecutor(
        guard=SafetyGuard(test_config),
        session=SessionState(requested_cycles=1),
        sink=rejected_sink,
    ).execute_outcome(ActionRequest("open_area", dry_run=True))
    assert rejected.status is ActionExecutionStatus.NOT_ISSUED

    unknown = ActionExecutor(
        guard=SafetyGuard(replace(test_config, dry_run=False)),
        session=SessionState(requested_cycles=1),
        sink=FailingSink(),
    ).execute_outcome(ActionRequest("click_target", dry_run=False))
    assert unknown.status is ActionExecutionStatus.DELIVERY_UNKNOWN


def test_live_sink_builds_exact_parent_child_navigator_authority() -> None:
    class Injector:
        def __init__(self) -> None:
            self.calls = []

        def client_snapshot(self, client_id):
            return {
                "client_id": client_id, "profile_id": "profile-a",
                "tab_id": 23, "opener_tab_id": 17,
            }

        def execute(self, command, payload=None, **kwargs):
            self.calls.append((command, dict(payload or {}), kwargs))
            return InjectorResult(True, "{}", client_id=kwargs.get("client_id"))

    injector = Injector()
    sink = LiveMacActionSink(browser_client_id="parent-client")
    sink._active_mutation_fence = {
        "profile_id": "profile-a", "tab_id": 17,
        "actor_generation": 1, "fencing_token": 7,
    }
    result = sink._execute_injector(
        injector, "navigator_go", {"expectedTarget": "Дикий предел"},
        client_id_override="child-client",
    )

    assert result.ok is True
    assert injector.calls[0][1]["mutationTarget"] == {
        "kind": "opener_child", "profile_id": "profile-a",
        "tab_id": 23, "opener_tab_id": 17,
    }


def test_live_sink_rejects_foreign_navigator_child_before_transport() -> None:
    class Injector:
        def __init__(self) -> None:
            self.calls = []

        def client_snapshot(self, client_id):
            return {
                "client_id": client_id, "profile_id": "profile-a",
                "tab_id": 23, "opener_tab_id": 99,
            }

        def execute(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("foreign child must not reach transport")

    injector = Injector()
    sink = LiveMacActionSink(browser_client_id="parent-client")
    sink._active_mutation_fence = {
        "profile_id": "profile-a", "tab_id": 17,
        "actor_generation": 1, "fencing_token": 7,
    }
    result = sink._execute_injector(
        injector, "navigator_select_target", {"target": "Дикий предел"},
        client_id_override="child-client",
    )

    assert result.ok is False
    assert result.message == "mutation_child_authority_mismatch"
    assert injector.calls == []


def test_typed_sink_outcome_preserves_known_rejection_and_legacy_bool_compatibility(
    test_config: AutomationConfig,
) -> None:
    executor = ActionExecutor(
        guard=SafetyGuard(replace(test_config, dry_run=False)),
        session=SessionState(requested_cycles=1),
        sink=BlockedActionSink(reason="known_pre_mutation_rejection"),
    )

    request = ActionRequest("open_area", dry_run=False)
    assert executor.execute_outcome(request).status is ActionExecutionStatus.NOT_ISSUED
    assert executor.execute(request) is False


def test_live_sink_conservatively_classifies_legacy_false_and_accepts_typed_rejection(
    monkeypatch,
) -> None:
    from src.antibot_cv.automation import action_combat_handlers

    sink = LiveMacActionSink()
    monkeypatch.setattr(action_combat_handlers, "handle_action", lambda *_args: False)
    result = sink.execute_outcome(ActionRequest("click_target", dry_run=False))
    assert result.status is ActionExecutionStatus.DELIVERY_UNKNOWN
    assert sink.execute(ActionRequest("click_target", dry_run=False)) is False

    monkeypatch.setattr(
        action_combat_handlers,
        "handle_action",
        lambda *_args: ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED),
    )
    result = sink.execute_outcome(ActionRequest("click_target", dry_run=False))
    assert result.status is ActionExecutionStatus.NOT_ISSUED


def test_fenced_service_retains_delivery_unknown_and_legacy_endpoint_cannot_reissue(
    test_config: AutomationConfig,
) -> None:
    coordinator = MutationLeaseCoordinator()
    service = LiveActionService(
        lambda: replace(test_config, dry_run=False),
        sink_factory=lambda _client_id: FailingSink(),
        identity_factory=lambda _client_id: ("profile-a", 17),
        mutation_coordinator=coordinator,
    )

    first = service.execute_fenced(
        "client-a", ActionRequest("click_target", dry_run=False),
    )
    assert first.execution.status is ActionExecutionStatus.DELIVERY_UNKNOWN
    assert first.lease is not None
    assert service.retained_fenced("client-a", "click_target") == first.lease
    assert service.retained_fenced("client-a", "open_area") is None
    assert service.execute(
        "client-a", ActionRequest("click_target", dry_run=False),
    ) is False
    assert coordinator.holder(MutationTarget("profile-a", 17)) == first.lease


def test_retained_unknown_is_retrievable_after_transport_reconnect(
    test_config: AutomationConfig,
) -> None:
    coordinator = MutationLeaseCoordinator()
    service = LiveActionService(
        lambda: replace(test_config, dry_run=False),
        sink_factory=lambda _client_id: FailingSink(),
        identity_factory=lambda client_id: (
            ("profile-a", 17) if client_id in {"old-client", "new-client"} else None
        ),
        mutation_coordinator=coordinator,
    )

    first = service.execute_fenced(
        "old-client", ActionRequest("click_target", dry_run=False),
    )

    assert first.execution.status is ActionExecutionStatus.DELIVERY_UNKNOWN
    assert service.retained_fenced("new-client", "click_target") == first.lease
    assert service.execute_fenced(
        "new-client", ActionRequest("click_target", dry_run=False),
    ).execution.status is ActionExecutionStatus.NOT_ISSUED


def test_fenced_claim_callback_stages_before_sink_and_failure_releases(
    test_config: AutomationConfig,
) -> None:
    coordinator = MutationLeaseCoordinator()
    staged = []

    class StageCheckingSink(DryRunActionSink):
        def execute_outcome(self, request):
            assert staged
            return super().execute_outcome(request)

    sink = StageCheckingSink()
    service = LiveActionService(
        lambda: replace(test_config, dry_run=False),
        sink_factory=lambda _client_id: sink,
        identity_factory=lambda _client_id: ("profile-a", 17),
        mutation_coordinator=coordinator,
    )
    result = service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
        on_claimed=staged.append,
    )
    assert result.execution.status is ActionExecutionStatus.ISSUED
    assert len(sink.requests) == 1
    assert result.lease is not None
    assert service.reconcile_fenced(result.lease)

    rejected = service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
        on_claimed=lambda _lease: (_ for _ in ()).throw(RuntimeError("stage failed")),
    )
    assert rejected.execution.status is ActionExecutionStatus.NOT_ISSUED
    assert coordinator.holder(MutationTarget("profile-a", 17)) is None
    assert len(sink.requests) == 1


def test_fenced_uncertain_claim_retains_barrier_without_calling_sink(
    test_config: AutomationConfig,
) -> None:
    coordinator = MutationLeaseCoordinator()
    sink = DryRunActionSink()
    service = LiveActionService(
        lambda: replace(test_config, dry_run=False),
        sink_factory=lambda _client_id: sink,
        identity_factory=lambda _client_id: ("profile-a", 17),
        mutation_coordinator=coordinator,
    )

    class CommitUncertain(OSError):
        retain_mutation_lease = True

    result = service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
        on_claimed=lambda _lease: (_ for _ in ()).throw(CommitUncertain()),
    )

    assert result.execution.status is ActionExecutionStatus.DELIVERY_UNKNOWN
    assert result.lease is not None
    assert sink.requests == []
    assert coordinator.holder(MutationTarget("profile-a", 17)) == result.lease
    assert service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
    ).execution.status is ActionExecutionStatus.NOT_ISSUED


def test_fenced_uncertain_not_issued_rollback_retains_barrier(
    test_config: AutomationConfig,
) -> None:
    coordinator = MutationLeaseCoordinator()

    class RejectingSink(DryRunActionSink):
        def execute_outcome(self, request):
            return ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED)

    sink = RejectingSink()
    service = LiveActionService(
        lambda: replace(test_config, dry_run=False),
        sink_factory=lambda _client_id: sink,
        identity_factory=lambda _client_id: ("profile-a", 17),
        mutation_coordinator=coordinator,
    )

    class CommitUncertain(OSError):
        retain_mutation_lease = True

    result = service.execute_fenced(
        "client-a", ActionRequest("inspect_exact_npc", dry_run=False),
        on_not_issued=lambda _lease: (_ for _ in ()).throw(CommitUncertain()),
    )

    assert result.execution.status is ActionExecutionStatus.DELIVERY_UNKNOWN
    assert result.lease is not None
    assert coordinator.holder(MutationTarget("profile-a", 17)) == result.lease
    assert service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
    ).execution.status is ActionExecutionStatus.NOT_ISSUED


def test_real_census_journal_uncertain_rollback_retains_global_barrier(
    test_config: AutomationConfig, monkeypatch, tmp_path,
) -> None:
    coordinator = MutationLeaseCoordinator()

    class RejectingSink(DryRunActionSink):
        def execute_outcome(self, request):
            return ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED)

    service = LiveActionService(
        lambda: replace(test_config, dry_run=False),
        sink_factory=lambda _client_id: RejectingSink(),
        identity_factory=lambda _client_id: ("profile-a", 17),
        mutation_coordinator=coordinator,
    )
    journal = NpcCensusInspectJournal(tmp_path / "inspect.json")
    record = PendingCensusInspection(
        "profile-a", 17,
        CensusInspectContract(
            snapshot_id="area-npcs-epoch-1", location_id="102",
            endpoint_id="0", route_ref="398", endpoint_name="Npc",
            observation_epoch="epoch", observation_revision=1,
            generated_at="2026-07-20T00:00:00Z",
        ),
    )
    real_fsync = os.fsync
    calls = 0

    def fail_rollback_directory_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("rollback directory fsync")
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_rollback_directory_fsync)
    result = service.execute_fenced(
        "client-a", ActionRequest("inspect_exact_npc", dry_run=False),
        on_claimed=lambda _lease: journal.stage(record),
        on_not_issued=lambda _lease: journal.clear_exact(record),
    )

    assert result.execution.status is ActionExecutionStatus.DELIVERY_UNKNOWN
    assert result.lease is not None
    assert journal.get(record.actor_key) == record
    assert coordinator.holder(MutationTarget("profile-a", 17)) == result.lease
    assert service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
    ).execution.status is ActionExecutionStatus.NOT_ISSUED


def test_real_census_journal_pre_replace_rollback_failure_retains_barrier(
    test_config: AutomationConfig, monkeypatch, tmp_path,
) -> None:
    coordinator = MutationLeaseCoordinator()

    class RejectingSink(DryRunActionSink):
        def execute_outcome(self, request):
            return ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED)

    service = LiveActionService(
        lambda: replace(test_config, dry_run=False),
        sink_factory=lambda _client_id: RejectingSink(),
        identity_factory=lambda _client_id: ("profile-a", 17),
        mutation_coordinator=coordinator,
    )
    journal = NpcCensusInspectJournal(tmp_path / "inspect.json")
    record = PendingCensusInspection(
        "profile-a", 17,
        CensusInspectContract(
            snapshot_id="area-npcs-epoch-1", location_id="102",
            endpoint_id="0", route_ref="398", endpoint_name="Npc",
            observation_epoch="epoch", observation_revision=1,
            generated_at="2026-07-20T00:00:00Z",
        ),
    )
    real_fsync = os.fsync
    calls = 0

    def fail_rollback_file_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("rollback file fsync")
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_rollback_file_fsync)
    result = service.execute_fenced(
        "client-a", ActionRequest("inspect_exact_npc", dry_run=False),
        on_claimed=lambda _lease: journal.stage(record),
        on_not_issued=lambda _lease: journal.clear_exact(record),
    )

    assert result.execution.status is ActionExecutionStatus.DELIVERY_UNKNOWN
    assert result.lease is not None
    assert journal.get(record.actor_key) == record
    restored = NpcCensusInspectJournal(journal.path)
    restored.load()
    assert restored.get(record.actor_key) == record
    assert coordinator.holder(MutationTarget("profile-a", 17)) == result.lease
    assert service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
    ).execution.status is ActionExecutionStatus.NOT_ISSUED


def test_fenced_not_issued_rolls_back_exact_staged_claim_for_fresh_retry(
    test_config: AutomationConfig,
) -> None:
    coordinator = MutationLeaseCoordinator()
    staged = {}

    class ToggleSink(DryRunActionSink):
        reject = True

        def execute_outcome(self, request):
            if self.reject:
                return ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED)
            return super().execute_outcome(request)

    sink = ToggleSink()
    service = LiveActionService(
        lambda: replace(test_config, dry_run=False),
        sink_factory=lambda _client_id: sink,
        identity_factory=lambda _client_id: ("profile-a", 17),
        mutation_coordinator=coordinator,
    )

    def stage(lease):
        if staged and staged.get(lease.target) != lease:
            raise RuntimeError("stale staged claim")
        staged[lease.target] = lease

    def rollback(lease):
        if staged.get(lease.target) == lease:
            staged.pop(lease.target)

    first = service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
        on_claimed=stage, on_not_issued=rollback,
    )
    assert first.execution.status is ActionExecutionStatus.NOT_ISSUED
    assert staged == {}
    assert coordinator.holder(MutationTarget("profile-a", 17)) is None

    sink.reject = False
    second = service.execute_fenced(
        "client-a", ActionRequest("open_area", dry_run=False),
        on_claimed=stage, on_not_issued=rollback,
    )
    assert second.execution.status is ActionExecutionStatus.ISSUED
    assert second.lease is not None and staged[second.lease.target] == second.lease


def test_action_executor_rejects_mismatched_semantic_combat_binding(
    test_config: AutomationConfig,
) -> None:
    sink = DryRunActionSink()
    executor = ActionExecutor(
        guard=SafetyGuard(test_config),
        session=SessionState(requested_cycles=1),
        sink=sink,
    )

    assert executor.execute(ActionRequest(
        "attack_visible_target",
        dry_run=True,
        metadata={
            "semantic_authoritative": True,
            "names": ["Гигантская оса"],
            "semantic_binding": {
                "target": "Непобедимый кабан",
                "requirement_id": "req_" + "a" * 64,
                "plan_fingerprint": "b" * 64,
            },
        },
    )) is False
    assert sink.requests == []


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
                "targetSpecs": [{"name": "Волк", "level": 3}],
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

    ok = sink.execute(
        ActionRequest(
            "attack_visible_target",
            metadata={
                "confirmed": 1,
                "margin": 35,
                "allowed_levels": [3],
                "target_specs": [{"name": "Волк", "level": 3}],
            },
            dry_run=False,
        )
    )

    assert ok
    assert logger.events[-1]["event_type"] == "attack_visible_target_requested"
    assert logger.events[-1]["target_level"] == 3


def test_live_ability_without_exact_mutation_binding_is_blocked(monkeypatch) -> None:
    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            raise AssertionError("missing binding must not reach injector")

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(ActionRequest("click_ability_4", metadata={"use_js_skill": True, "skill_slot": 4}, dry_run=False))

    assert not ok
    assert logger.events[-1]["event_type"] == "action_blocked"
    assert logger.events[-1]["block_reason"] == "skill_mutation_binding_missing"


def test_live_combat_slot_without_exact_mutation_binding_is_blocked(monkeypatch) -> None:
    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            required_version: str | None = None,
        ) -> InjectorResult:
            raise AssertionError("missing binding must not reach injector")

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger)

    ok = sink.execute(ActionRequest("click_combat_slot", metadata={"use_js_skill": True, "slot_index": 4}, dry_run=False))

    assert not ok
    assert logger.events[-1]["event_type"] == "action_blocked"


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
                            "returnObservation": {
                                "raw": {"type": "promise"},
                                "promise": {
                                    "status": "resolved",
                                    "result": {"type": "string", "length": 14},
                                },
                            },
                            "beforePlayerStanceState": [
                                {"path": "model.player.position", "type": "string", "value": "front"}
                            ],
                            "afterPlayerStanceState": [
                                {"path": "model.player.position", "type": "string", "value": "back"}
                            ],
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
        ActionRequest(
            "click_combat_slot",
            metadata={
                "use_js_skill": True,
                "skill_slot": 2,
                "expected_skill_id": -10,
                "expected_skill_name": "test",
                "expected_skill_slot": 2,
                "expected_battle_identity": "fight.php|battle:7|opp:42",
                "expected_battle_snapshot_id": "battle-1",
                "expected_battle_observation_token": "page-token-1",
            },
            dry_run=False,
        )
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
    assert '\\"length\\":14' in serialized
    assert "stance-applied" not in serialized
    assert "model.player.position" in serialized


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
                return InjectorResult(True, '{"ok":true,"message":"hunt_opened"}', "client")
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
    assert "open_hunt" in calls
    assert logger.events[-1]["event_type"] == "action_blocked"
    assert logger.events[-1]["open_hunt_ok"] is True
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
                "quest_id": "355",
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
            {
                "target": "Кабан-секач [5]",
                "linkLabel": "Кабанов-секачей",
                "expectedQuestId": "355",
            },
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
                    "commandTimeoutMs": 2600,
                },
                3.1,
        ),
        ("navigator_go", "child-client", {"expectedTarget": "Дикий предел"}, 3.0),
        (
            "location_route_step",
            "parent-client",
            {"expectedCurrentLocationId": "102", "navigationDelayMs": 75},
            3.0,
        ),
    ]


def test_live_quest_navigator_uses_generic_navigator_only_for_missing_quest_link(monkeypatch) -> None:
    calls: list[str] = []

    class FakeInjector:
        def execute(self, command, payload=None, *, timeout_s=2.5, client_id=None) -> InjectorResult:
            calls.append(command)
            if command == "open_quest_navigator":
                return InjectorResult(False, '{"ok":false,"message":"quest_navigator_link_missing"}', client_id)
            if command == "open_location_navigator":
                return InjectorResult(True, '{"ok":true,"message":"location_navigator_opened"}', client_id)
            raise AssertionError(command)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(
        ActionRequest(
            "open_quest_navigator",
            metadata={"target": "Гигантская оса [2]", "quest_id": "31"},
            dry_run=False,
        )
    )

    assert calls == ["open_quest_navigator", "open_location_navigator"]
    assert logger.events[-1]["event_type"] == "quest_navigator_opened"
    assert logger.events[-1]["navigator_fallback"] == "location_navigator"


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
            return InjectorResult(
                True,
                '{"ok":true,"outcome":"CONFIRMED","mutationIssued":true,"message":"quest_catalog_opened_confirmed"}',
                client_id,
            )

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(ActionRequest("open_quest_catalog", metadata={"page": 2}, dry_run=False))
    assert not sink.execute(ActionRequest("open_quest_catalog", metadata={"page": "2"}, dry_run=False))
    assert not sink.execute(ActionRequest("open_quest_catalog", metadata={"page": 101}, dry_run=False))
    assert calls == [
        (
            "open_quest_catalog",
            {"page": 2, "verifyTimeoutMs": 5000, "commandTimeoutMs": 8000},
            8.5,
            "parent-client",
        )
    ]


def test_live_open_quest_catalog_preserves_ack_pending_without_retry(monkeypatch) -> None:
    calls: list[tuple[dict[str, object], float]] = []

    class FakeInjector:
        def execute(self, command, payload=None, *, timeout_s=2.5, client_id=None):
            calls.append((dict(payload or {}), timeout_s))
            return InjectorResult(
                True,
                '{"ok":true,"outcome":"ACK_PENDING","mutationIssued":true,"message":"quest_catalog_open_unconfirmed"}',
                client_id,
            )

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr(
        "src.antibot_cv.automation.actions.global_browser_injector",
        lambda: FakeInjector(),
    )
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(
        ActionRequest("open_quest_catalog", metadata={"page": 0}, dry_run=False)
    ) is True
    assert calls == [
        ({"page": 0, "verifyTimeoutMs": 5000, "commandTimeoutMs": 8000}, 8.5)
    ]
    assert logger.events[-1]["event_type"] == "open_quest_catalog_requested"
    assert sink.last_catalog_navigation_outcome is not None
    assert sink.last_catalog_navigation_outcome.status.value == "ACK_PENDING"
    assert sink.last_catalog_navigation_outcome.client_id == "parent-client"


def test_live_open_quest_catalog_does_not_fabricate_missing_result_client(monkeypatch) -> None:
    class FakeInjector:
        def execute(self, command, payload=None, *, timeout_s=2.5, client_id=None):
            return InjectorResult(
                True,
                '{"ok":true,"outcome":"ACK_PENDING","mutationIssued":true,"destination":"/user_quest.php?mode=avail&page=0","message":"quest_catalog_open_unconfirmed"}',
                None,
            )

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert sink.execute(ActionRequest("open_quest_catalog", metadata={"page": 0}, dry_run=False))
    assert sink.last_catalog_navigation_outcome is not None
    assert sink.last_catalog_navigation_outcome.client_id == ""


def test_live_open_quest_catalog_not_issued_is_blocked(monkeypatch) -> None:
    class FakeInjector:
        def execute(self, command, payload=None, *, timeout_s=2.5, client_id=None):
            return InjectorResult(False, "injector_delivery_timeout", client_id)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")

    assert not sink.execute(ActionRequest("open_quest_catalog", metadata={"page": 0}, dry_run=False))
    assert logger.events[-1]["event_type"] == "action_blocked"
    assert sink.last_catalog_navigation_outcome is not None
    assert sink.last_catalog_navigation_outcome.status.value == "NOT_ISSUED"


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
            return InjectorResult(True, json.dumps({
                "outcome": "CONFIRMED", "mutationIssued": True,
                "message": "quest_active_opened_confirmed", "shellLoaded": True,
                "destination": "/user_quest.php?mode=started&page=1",
            }), client_id)

    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(InMemoryEventLogger(dry_run=False), browser_client_id="parent-client")

    assert sink.execute(ActionRequest("open_active_quest_page", metadata={"page": 1}, dry_run=False))
    assert sink.last_active_catalog_navigation_outcome.status.value == "CONFIRMED"
    assert not sink.execute(ActionRequest("open_active_quest_page", metadata={"page": "1"}, dry_run=False))
    assert not sink.execute(ActionRequest("open_active_quest_page", metadata={"page": 101}, dry_run=False))
    assert calls == [(
        "open_active_quest_page",
        {"page": 1, "verifyTimeoutMs": 5000, "commandTimeoutMs": 7000},
        7.5,
        "parent-client",
    )]


@pytest.mark.parametrize(
    ("payload", "expected_execute", "expected_status"),
    [
        ({"outcome": "ACK_PENDING", "mutationIssued": True,
          "message": "quest_active_open_unconfirmed"}, True, "ACK_PENDING"),
        ({"outcome": "NOT_ISSUED", "mutationIssued": False,
          "message": "quest_active_main_content_missing"}, False, "NOT_ISSUED"),
    ],
)
def test_live_active_navigation_preserves_issued_ambiguity(
    monkeypatch, payload, expected_execute, expected_status,
) -> None:
    payload = {
        **payload, "destination": "/user_quest.php?mode=started&page=1",
        "shellLoaded": False,
    }

    class FakeInjector:
        def execute(self, command, payload=None, *, timeout_s=2.5, client_id=None):
            return InjectorResult(expected_execute, json.dumps(payload_result), client_id)

    payload_result = payload
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(InMemoryEventLogger(dry_run=False), browser_client_id="parent-client")
    assert sink.execute(ActionRequest(
        "open_active_quest_page", metadata={"page": 1}, dry_run=False,
    )) is expected_execute
    assert sink.last_active_catalog_navigation_outcome.status.value == expected_status


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
            return InjectorResult(True, json.dumps({
                "outcome": "CONFIRMED", "mutationIssued": True,
                "destination": "https://3kingdoms.ru/npc.php?f_id=6",
                "issuedAt": "2026-07-17T00:00:00Z",
                "message": "npc_opened_confirmed",
            }), client_id)

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")
    valid = {
        "expected_snapshot_id": "area-npcs-mrj-1",
        "expected_location_id": "125",
        "npc_id": "6",
        "expected_route_ref": "398",
        "expected_name": "Моряк Кентур",
    }

    assert sink.execute(ActionRequest("open_exact_npc", metadata=valid, dry_run=False))
    assert sink.execute(
        ActionRequest("open_exact_npc", metadata={**valid, "npc_id": "0"}, dry_run=False)
    )
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
                "expectedRouteRef": "398",
                "expectedName": "Моряк Кентур",
                "expectedDialogName": "Моряк Кентур",
                "verifyTimeoutMs": 2500,
                "commandTimeoutMs": 5500,
            },
            6.0,
            "parent-client",
        ),
        (
            "open_exact_npc",
            {
                "expectedSnapshotId": "area-npcs-mrj-1",
                "expectedLocationId": "125",
                "npcId": "0",
                "expectedRouteRef": "398",
                "expectedName": "Моряк Кентур",
                "expectedDialogName": "Моряк Кентур",
                "verifyTimeoutMs": 2500,
                "commandTimeoutMs": 5500,
            },
            6.0,
            "parent-client",
        ),
    ]


def test_live_inspect_exact_npc_uses_same_bound_endpoint_without_result_guess(monkeypatch) -> None:
    calls = []

    class FakeInjector:
        def execute(self, command, payload=None, *, timeout_s=2.5, client_id=None):
            calls.append((command, dict(payload or {}), client_id))
            return InjectorResult(True, json.dumps({
                "outcome": "CONFIRMED", "mutationIssued": True,
                "destination": "https://3kingdoms.ru/npc.php",
                "message": "npc_opened_confirmed",
            }), client_id)

    monkeypatch.setattr(
        "src.antibot_cv.automation.actions.global_browser_injector",
        lambda: FakeInjector(),
    )
    sink = LiveMacActionSink(
        InMemoryEventLogger(dry_run=False), browser_client_id="parent-client",
    )
    result = sink.execute_outcome(ActionRequest(
        "inspect_exact_npc", dry_run=False,
        metadata={
            "expected_snapshot_id": "area-npcs-mrj-1",
            "expected_location_id": "102", "npc_id": "0",
            "expected_route_ref": "398", "expected_name": "Палатка Вилены",
        },
    ))

    assert result.status is ActionExecutionStatus.ISSUED
    assert calls[0][0] == "inspect_exact_npc"
    assert calls[0][1]["expectedName"] == "Палатка Вилены"
    assert calls[0][1]["expectedDialogName"] == "Палатка Вилены"
    assert "expectedNpcInstanceId" not in calls[0][1]


@pytest.mark.parametrize(
    ("injector_ok", "message", "expected_status", "expected_code"),
    [
        (
            True,
            '{"outcome":"CONFIRMED","mutationIssued":true,'
            '"destination":"https://3kingdoms.ru/npc.php?f_id=6"}',
            ActionExecutionStatus.ISSUED,
            "npc_open_confirmed",
        ),
        (
            False,
            "injector_ack_timeout",
            ActionExecutionStatus.DELIVERY_UNKNOWN,
            "injector_ack_timeout",
        ),
        (
            False,
            "injector_delivery_timeout",
            ActionExecutionStatus.NOT_ISSUED,
            "injector_delivery_timeout",
        ),
        (
            False,
            '{"outcome":"NOT_ISSUED","mutationIssued":false,'
            '"message":"npc_route_ref_mismatch"}',
            ActionExecutionStatus.NOT_ISSUED,
            "npc_route_ref_mismatch",
        ),
        (
            False,
            "npc_route_ref_mismatch",
            ActionExecutionStatus.DELIVERY_UNKNOWN,
            "npc_open_ack_pending",
        ),
        (
            False,
            '{"outcome":"NOT_ISSUED","mutationIssued":true,'
            '"message":"npc_route_ref_mismatch"}',
            ActionExecutionStatus.DELIVERY_UNKNOWN,
            "npc_open_ack_pending",
        ),
        (
            False,
            '{"outcome":"NOT_ISSUED","mutationIssued":false,'
            '"message":"https://3kingdoms.ru/npc.php?secret=raw"}',
            ActionExecutionStatus.NOT_ISSUED,
            "npc_open_not_issued",
        ),
    ],
)
def test_live_open_exact_npc_preserves_safe_typed_transport_diagnostic(
    monkeypatch, injector_ok, message, expected_status, expected_code,
) -> None:
    class FakeInjector:
        def execute(self, command, payload=None, *, timeout_s=2.5, client_id=None):
            return InjectorResult(injector_ok, message, client_id)

    monkeypatch.setattr(
        "src.antibot_cv.automation.actions.global_browser_injector",
        lambda: FakeInjector(),
    )
    sink = LiveMacActionSink(
        InMemoryEventLogger(dry_run=False), browser_client_id="parent-client",
    )
    result = sink.execute_outcome(ActionRequest(
        "open_exact_npc",
        metadata={
            "expected_snapshot_id": "area-npcs-mrj-1",
            "expected_location_id": "125",
            "npc_id": "6",
            "expected_route_ref": "398",
            "expected_name": "Моряк Кентур",
        },
        dry_run=False,
    ))

    assert result.status is expected_status
    assert result.diagnostic_code == expected_code


def test_action_execution_result_closes_diagnostic_and_status_contract() -> None:
    result = ActionExecutionResult(
        ActionExecutionStatus.DELIVERY_UNKNOWN,
        "https://3kingdoms.ru/npc.php?secret=raw\n" + "x" * 5000,
    )
    assert result.diagnostic_code is ActionDiagnosticCode.UNSPECIFIED
    with pytest.raises(TypeError):
        ActionExecutionResult("delivery_unknown", ActionDiagnosticCode.UNSPECIFIED)


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
        "expected_name": "Марилио",
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
                "expectedName": "Марилио",
                "questId": "246",
                "expectedTitle": "Хворь скакунов",
                    "action": "open",
                    "expectedRef": None,
                    "expectedPointId": None,
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
                "expectedName": "Марилио",
                "questId": "246",
                "expectedTitle": "Хворь скакунов",
                    "action": "answer",
                    "expectedRef": "3441",
                    "expectedPointId": None,
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
                "expectedName": "Марилио",
                "questId": "246",
                "expectedTitle": "Хворь скакунов",
                    "action": "accept",
                    "expectedRef": None,
                    "expectedPointId": None,
                    "expectedText": "Взять задание",
            },
            3.0,
            "parent-client",
        ),
    ]


def test_q304_vilena_resulting_identity_reaches_guarded_live_transport(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object], str | None]] = []

    class FakeInjector:
        def execute(
            self,
            command: str,
            payload: dict[str, object] | None = None,
            *,
            timeout_s: float = 2.5,
            client_id: str | None = None,
        ) -> InjectorResult:
            calls.append((command, dict(payload or {}), client_id))
            if command == "open_exact_npc":
                return InjectorResult(True, json.dumps({
                    "outcome": "CONFIRMED",
                    "mutationIssued": True,
                    "destination": "https://3kingdoms.ru/npc.php?f_id=4",
                    "issuedAt": "2026-07-19T00:00:00Z",
                    "message": "npc_opened_confirmed",
                }), client_id)
            return InjectorResult(True, '{"message":"npc_quest_action_submitted"}', client_id)

    monkeypatch.setattr(
        "src.antibot_cv.automation.actions.global_browser_injector",
        lambda: FakeInjector(),
    )
    config = AutomationConfig.from_dict({"dry_run": False, "max_cycles": 3})
    session = SessionState(requested_cycles=3)
    executor = ActionExecutor(
        guard=SafetyGuard(config),
        session=session,
        sink=LiveMacActionSink(
            InMemoryEventLogger(dry_run=False), browser_client_id="vilena-client",
        ),
    )

    assert executor.execute(ActionRequest(
        "open_exact_npc",
        dry_run=False,
        metadata={
            "expected_snapshot_id": "area-npcs-q304-vilena",
            "expected_location_id": "128",
            "npc_id": "4",
            "expected_route_ref": "398",
            "expected_name": "Палатка Вилены",
            "expected_dialog_name": "Колдунья Вилена",
            "expected_npc_instance_id": "110",
        },
    ))
    assert executor.execute(ActionRequest(
        "npc_quest_action",
        dry_run=False,
        metadata={
            "expected_snapshot_id": "npc-dialog-q304-vilena",
            "npc_id": "4",
            "expected_npc_instance_id": "110",
            "expected_name": "Колдунья Вилена",
            "quest_id": "304",
            "expected_title": "Цветочная болезнь",
            "action": "open",
        },
    ))

    assert session.total_actions == 2
    assert calls == [
        (
            "open_exact_npc",
            {
                "expectedSnapshotId": "area-npcs-q304-vilena",
                "expectedLocationId": "128",
                "npcId": "4",
                "expectedRouteRef": "398",
                "expectedName": "Палатка Вилены",
                "expectedDialogName": "Колдунья Вилена",
                "expectedNpcInstanceId": "110",
                "verifyTimeoutMs": 2500,
                "commandTimeoutMs": 5500,
            },
            "vilena-client",
        ),
        (
        "npc_quest_action",
        {
            "expectedSnapshotId": "npc-dialog-q304-vilena",
            "npcId": "4",
            "expectedNpcInstanceId": "110",
            "expectedName": "Колдунья Вилена",
            "questId": "304",
            "expectedTitle": "Цветочная болезнь",
            "action": "open",
            "expectedRef": None,
            "expectedPointId": None,
            "expectedText": None,
        },
        "vilena-client",
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
                "search_delay_ms": 1425,
                "route_delay_ms": 950,
                "command_timeout_ms": 4375,
                "retry_delay_ms": 0,
                "retry_limit": 1,
            },
            dry_run=False,
        )
    )

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][2]["commandTimeoutMs"] == 4375
    assert calls[0][3] == 4.875
    assert sleeps == []
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


def test_live_enter_instance_requires_bound_snapshot(monkeypatch) -> None:
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
            return InjectorResult(
                True,
                '{"ok":true,"message":"instance_entry_submitted","submitted":true}',
                "parent-client",
            )

    logger = InMemoryEventLogger(dry_run=False)
    monkeypatch.setattr("src.antibot_cv.automation.actions.global_browser_injector", lambda: FakeInjector())
    sink = LiveMacActionSink(logger, browser_client_id="parent-client")
    assert not sink.execute(ActionRequest("enter_instance", metadata={"expected_name": "Огненный провал"}, dry_run=False))
    assert calls == []
    assert sink.execute(
        ActionRequest(
            "enter_instance",
            metadata={"expected_name": "Огненный провал", "expected_snapshot_id": "instance-1"},
            dry_run=False,
        )
    )
    assert calls == [
        (
            "enter_instance",
            {"expectedName": "Огненный провал", "expectedSnapshotId": "instance-1", "navigationDelayMs": 75},
            3.5,
        )
    ]
    assert logger.events[-1]["event_type"] == "instance_entry_requested"


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
