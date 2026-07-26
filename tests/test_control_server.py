from __future__ import annotations

import json
import threading

from src.antibot_cv.automation.browser_injector import CURRENT_BRIDGE_VERSION, BrowserInjectorServer, InjectorResult
from src.antibot_cv.automation.actions import (
    ActionDiagnosticCode,
    ActionExecutionResult,
    ActionExecutionStatus,
)
from src.antibot_cv.automation.control_server import AutomationControlApi, ControlRun
from src.antibot_cv.automation.live_action_service import FencedActionResult
from src.antibot_cv.automation.mutation_lease import (
    ActorKey, MutationLease, MutationLeaseCoordinator, MutationLeaseMode,
)
from src.antibot_cv.automation.npc_census_inspect_journal import (
    NpcCensusInspectJournal, PendingCensusInspection,
)
from src.antibot_cv.automation.npc_census_live import CensusInspectContract


class FakeThread:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class BarrierLiveActions:
    def __init__(self, status=ActionExecutionStatus.ISSUED) -> None:
        self.lease = MutationLease(
            ActorKey("profile-a", 17), "test", MutationLeaseMode.TRANSIENT, 1, 1,
        )
        self.pending = False
        self.reconciled = False
        self.status = status
        self.dispatch_calls = 0

    def execute_fenced(
        self, _client_id, _request, *, on_claimed=None, on_not_issued=None,
    ):
        if self.pending:
            return FencedActionResult(
                ActionExecutionResult(ActionExecutionStatus.NOT_ISSUED), None,
            )
        self.pending = True
        self.dispatch_calls += 1
        if on_claimed is not None:
            on_claimed(self.lease)
        if self.status is ActionExecutionStatus.NOT_ISSUED:
            if on_not_issued is not None:
                on_not_issued(self.lease)
            self.pending = False
            return FencedActionResult(ActionExecutionResult(self.status), None)
        return FencedActionResult(
            ActionExecutionResult(self.status), self.lease,
        )

    def retained_fenced(self, _client_id, _action_type):
        return self.lease if self.pending else None

    def execute(self, client_id, request):
        result = self.execute_fenced(client_id, request)
        if result.execution.status is not ActionExecutionStatus.ISSUED or result.lease is None:
            return False
        return self.reconcile_fenced(result.lease)

    def reconcile_fenced(self, lease):
        assert lease == self.lease and self.pending
        self.pending = False
        self.reconciled = True
        return True

    def counters(self, _client_id):
        return {"total_actions": 1}


def test_status_with_client_id_is_not_global_running() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0))
    api._runs["client-a"] = ControlRun(  # noqa: SLF001 - focused state-regression test.
        client_id="client-a",
        thread=FakeThread(True),  # type: ignore[arg-type]
        stop_event=threading.Event(),
        started_at=1.0,
        last_status={"state": "BATTLE_ACTIVE", "completed_cycles": 1, "requested_cycles": 1000},
    )

    assert api.status()["running"] is True
    assert api.status("client-a")["running"] is True
    assert api.status("client-b")["running"] is False
    assert api.status("client-b")["last_status"] == {}


def test_control_api_live_requires_explicit_true() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0))

    default_options = api._options_from_payload({}, browser_client_id="client-a")  # noqa: SLF001
    live_options = api._options_from_payload({"live": True}, browser_client_id="client-a")  # noqa: SLF001

    assert default_options.live is False
    assert live_options.live is True


def test_control_api_forwards_autonomous_quest_director_override() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0))

    options = api._options_from_payload(  # noqa: SLF001
        {"autonomousQuestDirector": True, "pinnedQuestId": "246"},
        browser_client_id="client-a",
    )

    assert options.runtime_overrides["autonomousQuestDirector"] is True
    assert options.runtime_overrides["pinnedQuestId"] == "246"


def test_control_api_rejects_string_live_flag() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0))

    status, payload = api.start({"live": "false"})

    assert status == 400
    assert payload["error"] == "invalid_live_flag"


def test_control_api_rejects_live_run_without_server_live_capability() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0), allow_live=False)

    status, payload = api.start({"live": True})

    assert status == 403
    assert payload["error"] == "live_server_not_authorized"


def test_location_route_keeps_mutation_barrier_until_authoritative_after_snapshot() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"locId": "2", "name": "B"}},
        {"currentLocationId": "2", "location": {"semanticName": "B"}},
    ))

    def location_route(_client_id):
        snapshot = next(snapshots)
        if snapshot["currentLocationId"] == "2":
            blocked = api.live_actions.execute_fenced("client-a", object())
            assert blocked.execution.status is ActionExecutionStatus.NOT_ISSUED
        return 200, {"snapshot": snapshot}

    api.location_route = location_route
    status, payload = api.location_route_step({
        "clientId": "client-a", "verifyDelayMs": 300,
    })

    assert status == 200 and payload["verified"] is True
    assert api.live_actions.reconciled is True


def test_location_route_retains_barrier_after_wrong_destination() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"locId": "2", "name": "B"}},
        {"currentLocationId": "999", "location": {"semanticName": "Wrong"}},
    ))
    api.location_route = lambda _client_id: (200, {"snapshot": next(snapshots)})

    status, payload = api.location_route_step({
        "clientId": "client-a", "verifyDelayMs": 300,
    })

    assert status == 502 and payload["verified"] is False
    assert api.live_actions.pending is True
    assert api.live_actions.reconciled is False


def test_location_route_does_not_allow_name_to_override_conflicting_id() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"locId": "2", "name": "B"}},
        {"currentLocationId": "999", "location": {"semanticName": "B"}},
    ))
    api.location_route = lambda _client_id: (200, {"snapshot": next(snapshots)})

    status, payload = api.location_route_step({
        "clientId": "client-a", "verifyDelayMs": 300,
    })

    assert status == 502 and payload["verified"] is False
    assert api.live_actions.pending is True


def test_location_route_delivery_unknown_reconciles_only_from_exact_after_evidence() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"locId": "2", "name": "B"}},
        {"currentLocationId": "2", "location": {"semanticName": "B"}},
    ))
    api.location_route = lambda _client_id: (200, {"snapshot": next(snapshots)})

    status, payload = api.location_route_step({
        "clientId": "client-a", "verifyDelayMs": 300,
    })

    assert status == 200 and payload["delivery_status"] == "delivery_unknown"
    assert api.live_actions.reconciled is True


def test_location_route_retry_uses_original_baseline_without_reissue() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"locId": "2", "name": "B"}},
        {"currentLocationId": "", "location": {"semanticName": ""}},
        {"currentLocationId": "2", "location": {"semanticName": "B"}},
    ))
    api.location_route = lambda _client_id: (200, {"snapshot": next(snapshots)})
    request = {"clientId": "client-a", "verifyDelayMs": 300}

    first_status, first = api.location_route_step(request)
    second_status, second = api.location_route_step(request)

    assert first_status == 502 and first["verified"] is False
    assert second_status == 200 and second["verified"] is True
    assert api.live_actions.dispatch_calls == 1
    assert api.live_actions.reconciled is True


def test_location_route_not_issued_rolls_back_record_for_fresh_claim() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.NOT_ISSUED)
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"locId": "2", "name": "B"}},
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"locId": "2", "name": "B"}},
        {"currentLocationId": "2", "location": {"semanticName": "B"}},
    ))
    api.location_route = lambda _client_id: (200, {"snapshot": next(snapshots)})
    request = {"clientId": "client-a", "verifyDelayMs": 300}

    first_status, _ = api.location_route_step(request)
    assert first_status == 409
    assert api._route_reconciliations == {}

    api.live_actions.status = ActionExecutionStatus.ISSUED
    second_status, second = api.location_route_step(request)

    assert second_status == 200 and second["verified"] is True
    assert api.live_actions.dispatch_calls == 2
    assert api.live_actions.reconciled is True


def test_location_route_missing_record_before_settle_does_not_release_lease() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"locId": "2", "name": "B"}},
        {"currentLocationId": "2", "location": {"semanticName": "B"}},
    ))

    def location_route(_client_id):
        snapshot = next(snapshots)
        if snapshot["currentLocationId"] == "2":
            api._route_reconciliations.clear()
        return 200, {"snapshot": snapshot}

    api.location_route = location_route
    status, _ = api.location_route_step({"clientId": "client-a", "verifyDelayMs": 300})

    assert status == 502
    assert api.live_actions.pending is True
    assert api.live_actions.reconciled is False


def test_location_route_name_fallback_requires_absent_expected_id() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"name": "B"}},
        {"currentLocationId": "", "location": {"semanticName": "B"}},
    ))
    api.location_route = lambda _client_id: (200, {"snapshot": next(snapshots)})

    status, payload = api.location_route_step({
        "clientId": "client-a", "verifyDelayMs": 300,
    })

    assert status == 200 and payload["verification_method"] == "semantic_name"
    assert api.live_actions.reconciled is True


def test_location_route_expected_id_with_missing_after_id_retains_barrier() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"currentLocationId": "1", "location": {"semanticName": "A"},
         "nextTransition": {"locId": "2", "name": "B"}},
        {"currentLocationId": "", "location": {"semanticName": "B"}},
    ))
    api.location_route = lambda _client_id: (200, {"snapshot": next(snapshots)})

    status, payload = api.location_route_step({
        "clientId": "client-a", "verifyDelayMs": 300,
    })

    assert status == 502 and payload["verified"] is False
    assert api.live_actions.pending is True


def test_open_exact_npc_keeps_barrier_until_authoritative_dialog_snapshot() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)

    def npc_dialog(_client_id, _expected_name, _expected_npc_id, _expected_instance_id):
        blocked = api.live_actions.execute_fenced("client-a", object())
        assert blocked.execution.status is ActionExecutionStatus.NOT_ISSUED
        return 200, {"snapshot": {"identityMatches": True, "npcId": "4"}}

    api.npc_dialog = npc_dialog
    status, payload = api.open_exact_npc({
        "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
    })

    assert status == 200 and payload["ok"] is True
    assert payload["diagnostic_code"] == "unspecified"
    assert api.live_actions.reconciled is True


def _census_inspect_request() -> dict[str, object]:
    return {
        "clientId": "client-a", "expectedSnapshotId": "area-npcs-epoch-1",
        "expectedLocationId": "102", "npcId": 0, "expectedRouteRef": "398",
        "expectedName": "Торговец Богдан",
        "expectedObservationEpoch": "epoch", "expectedObservationRevision": 1,
        "expectedGeneratedAt": "2026-07-20T19:03:01.000Z",
    }


def _census_dialog(*, npc_id: object = "0", resulting_name: object = "Торговец Богдан"):
    return {
        "ok": True, "message": "npc_dialog_snapshot", "pageKind": "npc",
        "truncated": False, "identityMatches": True, "npcId": npc_id,
        "npcInstanceId": "12", "resultingName": resulting_name,
        "observationEpoch": "epoch", "observationRevision": 2,
        "snapshotId": "npc-dialog-epoch-2",
        "generatedAt": "2026-07-20T19:03:02.000Z",
    }


def _configure_census_journal(api, tmp_path) -> None:
    api._npc_inspect_journal = NpcCensusInspectJournal(tmp_path / "inspect.json")
    api._npc_inspect_leases = {}
    api._logical_client_key = lambda _client_id: ("profile-a", 17)


def test_census_inspect_reconciles_exact_confirmed_zero_endpoint(tmp_path) -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api._lock = threading.RLock()
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)
    _configure_census_journal(api, tmp_path)
    api.npc_dialog = lambda *_args: (200, {"snapshot": _census_dialog()})

    status, payload = api.inspect_exact_npc(_census_inspect_request())

    assert status == 200 and payload["ok"] is True
    assert payload["dialog"]["resultingName"] == "Торговец Богдан"
    assert api.live_actions.dispatch_calls == 1
    assert api.live_actions.reconciled is False
    assert api._npc_inspect_journal.get(("profile-a", 17)).phase == "reconciled"


def test_census_inspect_binds_proxy_to_numeric_endpoint_and_discovers_result_name(tmp_path) -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api._lock = threading.RLock()
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)
    _configure_census_journal(api, tmp_path)
    observed = []

    def npc_dialog(*args):
        observed.append(args)
        return 200, {"snapshot": _census_dialog()}

    api.npc_dialog = npc_dialog
    assert api.inspect_exact_npc(_census_inspect_request())[0] == 200
    assert observed == [("client-a", None, "0", None)]


def test_census_pending_can_abort_only_on_newer_same_area_authority(tmp_path) -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api._lock = threading.RLock()
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    _configure_census_journal(api, tmp_path)
    api.npc_dialog = lambda *_args: (200, {"snapshot": _census_dialog(npc_id="999")})
    assert api.inspect_exact_npc(_census_inspect_request())[0] == 502
    api.area_npcs = lambda *_args: (200, {"snapshot": {
        "ok": True, "message": "area_npc_snapshot", "pageKind": "area",
        "truncated": False, "snapshotId": "area-npcs-next-2",
        "observationEpoch": "next", "observationRevision": 2,
        "generatedAt": "2026-07-20T19:04:01.000Z",
        "location": {"id": "102", "name": "Городская площадь Арсы"},
        "items": [],
    }})

    status, payload = api.open_area({"clientId": "client-a"})

    assert status == 409
    assert payload["error"] == "npc_census_pending_aborted_on_new_area_authority"
    assert api._npc_inspect_journal.get(("profile-a", 17)) is None
    assert api.live_actions.reconciled is True


def test_census_pending_does_not_abort_on_foreign_area_authority(tmp_path) -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api._lock = threading.RLock()
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    _configure_census_journal(api, tmp_path)
    api.npc_dialog = lambda *_args: (200, {"snapshot": _census_dialog(npc_id="999")})
    assert api.inspect_exact_npc(_census_inspect_request())[0] == 502
    api.area_npcs = lambda *_args: (200, {"snapshot": {
        "ok": True, "message": "area_npc_snapshot", "pageKind": "area",
        "truncated": False, "snapshotId": "area-npcs-next-2",
        "observationEpoch": "next", "observationRevision": 2,
        "generatedAt": "2026-07-20T19:04:01.000Z",
        "location": {"id": "999", "name": "Чужая локация"}, "items": [],
    }})

    status, payload = api.open_area({"clientId": "client-a"})

    assert status == 409 and payload["error"] == "npc_census_reconciliation_pending"
    assert api._npc_inspect_journal.get(("profile-a", 17)) is not None
    assert api.live_actions.reconciled is False


def test_census_inspect_unknown_retries_observation_without_reissue(tmp_path) -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api._lock = threading.RLock()
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    _configure_census_journal(api, tmp_path)
    snapshots = iter((_census_dialog(npc_id="999"), _census_dialog()))
    api.npc_dialog = lambda *_args: (200, {"snapshot": next(snapshots)})

    first_status, _ = api.inspect_exact_npc(_census_inspect_request())
    second_status, second = api.inspect_exact_npc(_census_inspect_request())

    assert first_status == 502
    assert second_status == 200 and second["ok"] is True
    assert second["diagnostic_code"] == "retained_reconciliation"
    assert api.live_actions.dispatch_calls == 1
    assert api.live_actions.reconciled is False
    assert api._npc_inspect_journal.get(("profile-a", 17)).phase == "reconciled"


def test_census_inspect_incomplete_dialogue_cannot_settle_pending(tmp_path) -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api._lock = threading.RLock()
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    _configure_census_journal(api, tmp_path)
    incomplete = _census_dialog()
    for key in (
        "snapshotId", "observationEpoch", "observationRevision", "generatedAt",
    ):
        incomplete.pop(key)
    api.npc_dialog = lambda *_args: (200, {"snapshot": incomplete})

    status, payload = api.inspect_exact_npc(_census_inspect_request())

    assert status == 502 and payload["ok"] is False
    assert api.live_actions.dispatch_calls == 1
    assert api.live_actions.reconciled is False
    assert api._npc_inspect_journal.get(("profile-a", 17)) is not None


def test_census_inspect_retained_contract_cannot_be_substituted(tmp_path) -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api._lock = threading.RLock()
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    _configure_census_journal(api, tmp_path)
    api.npc_dialog = lambda *_args: (200, {"snapshot": _census_dialog(npc_id="999")})
    assert api.inspect_exact_npc(_census_inspect_request())[0] == 502
    changed = {**_census_inspect_request(), "npcId": 13, "expectedRouteRef": "228"}

    status, payload = api.inspect_exact_npc(changed)

    assert status == 409 and payload["error"] == "npc_census_contract_mismatch"
    assert api.live_actions.dispatch_calls == 1
    assert api.live_actions.pending is True


def test_census_inspect_restart_reconciles_durable_record_without_reissue(tmp_path) -> None:
    path = tmp_path / "inspect.json"
    first = AutomationControlApi.__new__(AutomationControlApi)
    first.allow_live = True
    first._lock = threading.RLock()
    first.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    first._resolve_client_id = lambda _value: ("client-a", None)
    first._logical_client_key = lambda _client_id: ("profile-a", 17)
    first._npc_inspect_journal = NpcCensusInspectJournal(path)
    first._npc_inspect_leases = {}
    first.npc_dialog = lambda *_args: (200, {"snapshot": _census_dialog(npc_id="999")})
    assert first.inspect_exact_npc(_census_inspect_request())[0] == 502

    second = AutomationControlApi.__new__(AutomationControlApi)
    second.allow_live = True
    second._lock = threading.RLock()
    second.live_actions = BarrierLiveActions()
    second._resolve_client_id = lambda _value: ("client-a", None)
    second._logical_client_key = lambda _client_id: ("profile-a", 17)
    second._npc_inspect_journal = NpcCensusInspectJournal(path)
    second._npc_inspect_journal.load()
    second._npc_inspect_leases = {}
    second.npc_dialog = lambda *_args: (200, {"snapshot": _census_dialog()})

    status, payload = second.inspect_exact_npc(_census_inspect_request())
    assert status == 200 and payload["ok"] is True
    assert second.live_actions.dispatch_calls == 0
    assert second._npc_inspect_journal.get(("profile-a", 17)).phase == "reconciled"


def test_census_reconciled_tombstone_replays_without_reissue_until_open_area(tmp_path) -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api._lock = threading.RLock()
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.ISSUED)
    api._resolve_client_id = lambda _value: ("client-a", None)
    _configure_census_journal(api, tmp_path)
    api.npc_dialog = lambda *_args: (200, {"snapshot": _census_dialog()})

    assert api.inspect_exact_npc(_census_inspect_request())[0] == 200
    replay_status, replay = api.inspect_exact_npc(_census_inspect_request())
    assert replay_status == 200 and replay["dialog"]["resultingName"] == "Торговец Богдан"
    assert api.live_actions.dispatch_calls == 1
    assert api.live_actions.pending is True

    status, payload = api.open_area({"clientId": "client-a"})
    assert status == 200 and payload["submitted"] is True
    assert api._npc_inspect_journal.get(("profile-a", 17)) is None
    assert api.live_actions.dispatch_calls == 2


def test_census_restart_restores_global_actor_mutation_barrier(tmp_path) -> None:
    path = tmp_path / "inspect.json"
    journal = NpcCensusInspectJournal(path)
    journal.stage(PendingCensusInspection(
        "profile-a", 17,
        CensusInspectContract.from_payload(_census_inspect_request()),
    ))
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.mutation_coordinator = MutationLeaseCoordinator()
    api._npc_inspect_journal = NpcCensusInspectJournal(path)
    api._npc_inspect_journal.load()
    api._npc_inspect_leases = {}

    api._restore_npc_inspect_barriers()

    actor = ActorKey("profile-a", 17)
    restored = api.mutation_coordinator.holder(actor)
    assert restored == api._npc_inspect_leases[("profile-a", 17)]
    assert api.mutation_coordinator.try_acquire(
        actor, "quest:client:open_area", MutationLeaseMode.TRANSIENT,
    ) is None


def test_open_exact_npc_exposes_only_safe_typed_dispatch_diagnostic() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    api.npc_dialog = lambda *_args: (503, {"error": "snapshot_unavailable"})

    original_execute = api.live_actions.execute_fenced

    def execute_with_safe_code(*args, **kwargs):
        result = original_execute(*args, **kwargs)
        return type(result)(
            ActionExecutionResult(
                ActionExecutionStatus.DELIVERY_UNKNOWN,
                ActionDiagnosticCode.INJECTOR_ACK_TIMEOUT,
            ),
            result.lease,
        )

    api.live_actions.execute_fenced = execute_with_safe_code
    status, payload = api.open_exact_npc({
        "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4,
        "expectedRouteRef": 398,
    })

    assert status == 502
    assert payload["delivery_status"] == "delivery_unknown"
    assert payload["diagnostic_code"] == "injector_ack_timeout"
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "npc.php?" not in rendered
    assert "expectedRouteRef" not in rendered


def test_open_exact_npc_normalizes_untrusted_internal_diagnostic_at_http_boundary() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    api.npc_dialog = lambda *_args: (503, {"error": "snapshot_unavailable"})
    original_execute = api.live_actions.execute_fenced

    def execute_with_tampered_result(*args, **kwargs):
        result = original_execute(*args, **kwargs)
        execution = object.__new__(ActionExecutionResult)
        object.__setattr__(execution, "status", ActionExecutionStatus.DELIVERY_UNKNOWN)
        object.__setattr__(
            execution,
            "diagnostic_code",
            "https://3kingdoms.ru/npc.php?secret=raw\n" + "x" * 5000,
        )
        return type(result)(execution, result.lease)

    api.live_actions.execute_fenced = execute_with_tampered_result
    status, payload = api.open_exact_npc({
        "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4,
        "expectedRouteRef": 398,
    })

    assert status == 502
    assert payload["diagnostic_code"] == "unspecified"
    assert "secret" not in json.dumps(payload)


def test_open_exact_npc_retains_barrier_on_dialog_identity_mismatch() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)
    api.npc_dialog = lambda *_args: (
        200, {"snapshot": {"identityMatches": False, "npcId": "4"}},
    )

    status, payload = api.open_exact_npc({
        "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
    })

    assert status == 502 and payload["ok"] is False
    assert api.live_actions.pending is True
    assert api.live_actions.reconciled is False
    blocked = api.live_actions.execute_fenced("client-a", object())
    assert blocked.execution.status is ActionExecutionStatus.NOT_ISSUED


def test_open_exact_npc_rejects_unbound_numeric_dialog_identity() -> None:
    for observed_npc_id in ("999", None, True):
        api = AutomationControlApi.__new__(AutomationControlApi)
        api.allow_live = True
        api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
        api._resolve_client_id = lambda _value: ("client-a", None)
        api.npc_dialog = lambda *_args, value=observed_npc_id: (
            200, {"snapshot": {"identityMatches": True, "npcId": value}},
        )

        status, payload = api.open_exact_npc({
            "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
        })

        assert status == 502 and payload["ok"] is False
        assert api.live_actions.pending is True
        assert api.live_actions.reconciled is False


def test_open_exact_npc_retry_reconciles_retained_unknown_without_reissue() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    snapshots = iter((
        {"identityMatches": True, "npcId": "999"},
        {"identityMatches": True, "npcId": "4"},
    ))
    api.npc_dialog = lambda *_args: (200, {"snapshot": next(snapshots)})
    request = {
        "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
    }

    first_status, _ = api.open_exact_npc(request)
    second_status, second = api.open_exact_npc(request)

    assert first_status == 502
    assert second_status == 200 and second["delivery_status"] == "delivery_unknown"
    assert api.live_actions.dispatch_calls == 1
    assert api.live_actions.reconciled is True


def test_open_exact_npc_retry_cannot_substitute_original_contract() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    observations = []

    def npc_dialog(_client_id, expected_name, expected_npc_id, _expected_instance_id):
        observations.append((expected_name, expected_npc_id))
        return 200, {"snapshot": {"identityMatches": True, "npcId": "999"}}

    api.npc_dialog = npc_dialog
    first_status, _ = api.open_exact_npc({
        "clientId": "client-a", "expectedSnapshotId": "area-1",
        "expectedLocationId": "128", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
    })
    second_status, second = api.open_exact_npc({
        "clientId": "client-a", "expectedSnapshotId": "area-2",
        "expectedLocationId": "999", "expectedName": "Npc999",
        "expectedDialogName": "Npc999", "npcId": 999, "expectedRouteRef": 999,
    })

    assert first_status == 502
    assert second_status == 409
    assert second["error"] == "npc_open_reconciliation_contract_mismatch"
    assert api.live_actions.dispatch_calls == 1
    assert api.live_actions.pending is True
    assert observations == [("Колдунья Вилена", "4")]


def test_open_exact_npc_requires_resulting_instance_when_contract_binds_it() -> None:
    for observed_instance_id in ("109", None, True):
        api = AutomationControlApi.__new__(AutomationControlApi)
        api.allow_live = True
        api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
        api._resolve_client_id = lambda _value: ("client-a", None)
        api.npc_dialog = lambda *_args, value=observed_instance_id: (
            200, {"snapshot": {
                "identityMatches": True, "npcId": "4", "npcInstanceId": value,
            }},
        )

        status, payload = api.open_exact_npc({
            "clientId": "client-a", "expectedSnapshotId": "area-1",
            "expectedLocationId": "128", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
            "expectedNpcInstanceId": 110,
        })

        assert status == 502 and payload["ok"] is False
        assert api.live_actions.pending is True


def test_open_exact_npc_rejects_present_invalid_instance_before_claim() -> None:
    invalid_values = (True, False, 110.0, 0, -1, {}, [], "110x")
    for invalid in invalid_values:
        api = AutomationControlApi.__new__(AutomationControlApi)
        api.allow_live = True
        api.live_actions = BarrierLiveActions()
        api._resolve_client_id = lambda _value: ("client-a", None)
        observations = []
        api.npc_dialog = lambda *_args: observations.append(True)

        status, payload = api.open_exact_npc({
            "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
            "expectedNpcInstanceId": invalid,
        })

        assert status == 400
        assert payload["error"] == "npc_instance_identity_invalid"
        assert api.live_actions.dispatch_calls == 0
        assert api.live_actions.reconciled is False
        assert observations == []


def test_open_exact_npc_absent_instance_remains_explicit_optional_contract() -> None:
    for absent in (None, ""):
        api = AutomationControlApi.__new__(AutomationControlApi)
        api.allow_live = True
        api.live_actions = BarrierLiveActions()
        api._resolve_client_id = lambda _value: ("client-a", None)
        api.npc_dialog = lambda *_args: (
            200, {"snapshot": {"identityMatches": True, "npcId": "4"}},
        )
        request = {
            "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
        }
        if absent is not None:
            request["expectedNpcInstanceId"] = absent

        status, payload = api.open_exact_npc(request)

        assert status == 200 and payload["ok"] is True


def test_open_exact_npc_retained_bound_instance_cannot_be_weakened_on_retry() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api._resolve_client_id = lambda _value: ("client-a", None)
    observations = []
    api.npc_dialog = lambda *_args: observations.append(True) or (
        200, {"snapshot": {"identityMatches": False, "npcId": "4", "npcInstanceId": "109"}},
    )
    bound = {
        "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
        "expectedNpcInstanceId": 110,
    }
    first_status, _ = api.open_exact_npc(bound)
    weakened = dict(bound, expectedNpcInstanceId=True)
    retry_status, retry = api.open_exact_npc(weakened)

    assert first_status == 502
    assert retry_status == 400 and retry["error"] == "npc_instance_identity_invalid"
    assert api.live_actions.dispatch_calls == 1
    assert api.live_actions.pending is True
    assert len(observations) == 1


def test_open_exact_npc_retained_lease_without_record_is_fail_closed() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.DELIVERY_UNKNOWN)
    api.live_actions.pending = True
    api._resolve_client_id = lambda _value: ("client-a", None)
    observations = []
    api.npc_dialog = lambda *_args: observations.append(True)

    status, payload = api.open_exact_npc({
        "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
    })

    assert status == 502
    assert payload["error"] == "npc_open_reconciliation_record_missing"
    assert api.live_actions.dispatch_calls == 0
    assert observations == []


def test_open_exact_npc_not_issued_rolls_back_for_different_fresh_contract() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions(ActionExecutionStatus.NOT_ISSUED)
    api._resolve_client_id = lambda _value: ("client-a", None)

    first_status, _ = api.open_exact_npc({
        "clientId": "client-a", "expectedSnapshotId": "area-1",
        "expectedLocationId": "128", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
    })
    assert first_status == 409
    assert api._npc_open_reconciliations == {}

    api.live_actions.status = ActionExecutionStatus.ISSUED
    api.npc_dialog = lambda *_args: (
        200, {"snapshot": {"identityMatches": True, "npcId": "999"}},
    )
    second_status, second = api.open_exact_npc({
        "clientId": "client-a", "expectedSnapshotId": "area-2",
        "expectedLocationId": "999", "expectedName": "Npc999",
        "expectedDialogName": "Npc999", "npcId": 999, "expectedRouteRef": 999,
    })

    assert second_status == 200 and second["ok"] is True
    assert api.live_actions.dispatch_calls == 2


def test_open_exact_npc_missing_record_before_settle_does_not_release_lease() -> None:
    api = AutomationControlApi.__new__(AutomationControlApi)
    api.allow_live = True
    api.live_actions = BarrierLiveActions()
    api._resolve_client_id = lambda _value: ("client-a", None)

    def npc_dialog(*_args):
        api._npc_open_reconciliations.clear()
        return 200, {"snapshot": {"identityMatches": True, "npcId": "4"}}

    api.npc_dialog = npc_dialog
    status, _ = api.open_exact_npc({
        "clientId": "client-a", "expectedName": "Палатка Вилены",
        "expectedDialogName": "Колдунья Вилена", "npcId": 4, "expectedRouteRef": 398,
    })

    assert status == 502
    assert api.live_actions.pending is True
    assert api.live_actions.reconciled is False


def test_control_api_state_snapshot_uses_existing_injector_client() -> None:
    calls: list[tuple[str, dict, float, str]] = []

    class FakeInjector:
        def client_snapshot(self, client_id):
            return {"client_id": client_id, "client_seen": True, "version_ok": True}

        def execute(self, command, payload, *, timeout_s, client_id):
            calls.append((command, payload, timeout_s, client_id))
            return InjectorResult(
                True,
                json.dumps({"schemaVersion": 1, "sections": {"player": {"status": "available"}}}),
                client_id,
            )

    api = AutomationControlApi(FakeInjector())  # type: ignore[arg-type]
    status, payload = api.handle(
        "GET",
        "/api/state-snapshot",
        {"clientId": ["client-a"], "include": ["player,location"]},
        None,
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["message"] == "state_snapshot"
    assert payload["snapshot"]["schemaVersion"] == 1
    assert calls == [
        ("state_snapshot", {"include": ["player", "location"]}, 5.0, "client-a"),
    ]


def test_control_api_battle_debug_uses_action_service_boundary() -> None:
    class FakeInjector:
        def client_snapshot(self, client_id):
            return {"client_id": client_id, "client_seen": True, "version_ok": True}

    class FakeLiveActions:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, client_id, request):
            self.requests.append((client_id, request))
            return True

        def battle_debug_result(self, client_id):
            assert client_id == "client-a"
            return {"hasFight": True}

    api = AutomationControlApi(FakeInjector())  # type: ignore[arg-type]
    actions = FakeLiveActions()
    api.live_actions = actions  # type: ignore[assignment]

    status, payload = api.battle_debug("client-a")

    assert status == 200
    assert payload == {
        "ok": True,
        "client_id": "client-a",
        "debug": {"hasFight": True},
        "message": "battle_debug",
    }
    assert len(actions.requests) == 1
    assert actions.requests[0][0] == "client-a"
    assert actions.requests[0][1].action_type == "battle_debug"


def test_control_api_treats_new_document_client_as_same_running_tab() -> None:
    injector = BrowserInjectorServer(port=0)
    with injector._lock:  # noqa: SLF001 - focused logical-tab regression test.
        injector._record_client_locked(  # noqa: SLF001
            "old-client",
            CURRENT_BRIDGE_VERSION,
            profile_id="profile-a",
            tab_id=42,
        )
        injector._record_client_locked(  # noqa: SLF001
            "new-client",
            CURRENT_BRIDGE_VERSION,
            profile_id="profile-a",
            tab_id=42,
        )
    api = AutomationControlApi(injector)
    stop_event = threading.Event()
    api._runs["old-client"] = ControlRun(  # noqa: SLF001
        client_id="old-client",
        thread=FakeThread(True),  # type: ignore[arg-type]
        stop_event=stop_event,
        started_at=1.0,
    )

    assert api.status("new-client")["running"] is True
    status, payload = api.start({"clientId": "new-client", "live": False})
    assert status == 409
    assert payload["error"] == "tab_already_running"
    stop_status, _ = api.stop({"clientId": "new-client"})
    assert stop_status == 200
    assert stop_event.is_set()
