from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from src.antibot_cv.automation.browser_injector import CURRENT_BRIDGE_VERSION, BrowserInjectorServer
from src.antibot_cv.automation.actions import (
    ActionDiagnosticCode,
    ActionExecutionStatus,
    ActionRequest,
)
from src.antibot_cv.automation.config import to_plain_dict
from src.antibot_cv.automation.controller import AutomationRunOptions, load_config, run_automation
from src.antibot_cv.automation.live_action_service import LiveActionService
from src.antibot_cv.automation.mutation_lease import (
    ActorKey,
    MutationLease,
    MutationLeaseCoordinator,
    MutationLeaseMode,
    MutationTarget,
)
from src.antibot_cv.automation.world_registry import WorldRegistry
from src.antibot_cv.automation.npc_census_live import (
    CensusInspectContract,
    inspection_snapshot_matches, newer_same_area_authority,
)
from src.antibot_cv.automation.npc_census_inspect_journal import (
    NpcCensusInspectJournal,
    PendingCensusInspection,
)


@dataclass
class ControlRun:
    client_id: str
    thread: threading.Thread
    stop_event: threading.Event
    started_at: float
    ended_at: float | None = None
    last_error: str | None = None
    last_summary: dict[str, Any] | None = None
    last_status: dict[str, Any] = field(default_factory=dict)
    last_options: dict[str, Any] = field(default_factory=dict)
    mutation_lease: MutationLease | None = None

    @property
    def running(self) -> bool:
        return self.thread.is_alive()


@dataclass(frozen=True, slots=True)
class RouteReconciliationRecord:
    lease: MutationLease
    before_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NpcOpenReconciliationRecord:
    lease: MutationLease
    expected_snapshot_id: str
    expected_location_id: str
    endpoint_npc_id: str
    route_ref: str
    endpoint_name: str
    resulting_dialog_name: str
    resulting_instance_id: str


class AutomationControlService:
    def __init__(
        self,
        injector: BrowserInjectorServer,
        *,
        default_config: str | Path | None = None,
        allow_live: bool = False,
    ) -> None:
        self.injector = injector
        self.default_config = str(default_config) if default_config is not None else "config/automation.local.json"
        self.allow_live = bool(allow_live)
        self.world_registry = WorldRegistry(Path(self.default_config).with_name("world_registry.json"))
        # The page bridge survives control-server restarts.  A wall-clock epoch
        # keeps a freshly started authority newer than any process-local epoch
        # issued by an earlier server, while fencing tokens remain per intent.
        self.mutation_coordinator = MutationLeaseCoordinator(
            generation_seed=int(time.time()),
        )
        self.live_actions = LiveActionService(
            lambda: load_config(self.default_config),
            identity_factory=self._logical_client_key,
            mutation_coordinator=self.mutation_coordinator,
        )
        self._lock = threading.RLock()
        self._runs: dict[str, ControlRun] = {}
        self._route_reconciliations: dict[MutationTarget, RouteReconciliationRecord] = {}
        self._npc_open_reconciliations: dict[MutationTarget, NpcOpenReconciliationRecord] = {}
        census_runs_dir = Path(load_config(self.default_config).runs_dir)
        self._npc_inspect_journal = NpcCensusInspectJournal(
            census_runs_dir / "npc_census_inspect.json",
        )
        self._npc_inspect_journal.load()
        self._npc_inspect_leases: dict[tuple[str, int], MutationLease] = {}
        self._restore_npc_inspect_barriers()

    def _restore_npc_inspect_barriers(self) -> None:
        """Restore global actor ownership for inspections uncertain at restart."""

        for pending in self._npc_inspect_journal.records():
            actor = ActorKey(pending.profile_id, pending.tab_id)
            lease = self.mutation_coordinator.try_acquire(
                actor, "quest:durable-census:inspect_exact_npc",
                MutationLeaseMode.TRANSIENT,
            )
            if lease is None:
                raise ValueError("NPC census durable mutation barrier restore failed")
            self._npc_inspect_leases[pending.actor_key] = lease

    def clients(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._clients_locked()

    def status(self, client_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            selected_client_id = client_id or self._single_running_client_id_locked()
            selected_run = self._run_for_client_locked(selected_client_id) if selected_client_id else None
            running = selected_run.running if selected_run is not None else False
            any_running = any(run.running for run in self._runs.values())
            return {
                "ok": True,
                "running": running,
                "any_running": any_running,
                "selected_client_id": selected_client_id,
                "started_at": None if selected_run is None else selected_run.started_at,
                "ended_at": None if selected_run is None else selected_run.ended_at,
                "last_error": None if selected_run is None else selected_run.last_error,
                "last_summary": None if selected_run is None else selected_run.last_summary,
                "last_status": {} if selected_run is None else dict(selected_run.last_status),
                "last_options": {} if selected_run is None else dict(selected_run.last_options),
                "client": self.injector.client_snapshot(selected_client_id),
                "clients": self._clients_locked(),
                "required_version": CURRENT_BRIDGE_VERSION,
                "live_allowed": self.allow_live,
            }

    def config_snapshot(self) -> dict[str, Any]:
        config_path = self.default_config
        try:
            config = load_config(config_path)
        except Exception as exc:
            return {"ok": False, "config": config_path, "error": str(exc)}
        return {
            "ok": True,
            "config": config_path,
            "live_allowed": self.allow_live,
            "defaults": {
                "max_cycles": config.max_cycles,
                "max_session_minutes": config.max_session_minutes,
                "target_levels": list(config.target.allowed_levels),
                "target_names": list(config.target.allowed_names),
                "health_min_percent": config.resources.health_min_percent,
                "prowess_min_percent": config.resources.prowess_min_percent,
                "recover_to_percent": config.resources.recover_to_percent,
                "item_recovery": to_plain_dict(config.item_recovery),
                "battle_item_recovery": to_plain_dict(config.battle_item_recovery),
                "leveling": to_plain_dict(config.leveling),
            },
        }

    def battle_skills(self, client_id: str | None = None) -> tuple[int, dict[str, Any]]:
        resolved_client_id, error = self._resolve_client_id(client_id)
        if error is not None:
            return 409, error
        result = self.injector.execute("battle_snapshot", timeout_s=2.5, client_id=resolved_client_id)
        payload: dict[str, Any] = {
            "ok": bool(result.ok),
            "client_id": result.client_id,
            "message": result.message,
            "abilities": [],
        }
        try:
            parsed = json.loads(result.message)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            abilities = parsed.get("abilities")
            if isinstance(abilities, list):
                payload["abilities"] = [ability for ability in abilities if isinstance(ability, dict)]
            payload["hasFight"] = parsed.get("hasFight")
            payload["useSkillAvailable"] = parsed.get("useSkillAvailable")
            payload["fightHref"] = parsed.get("fightHref")
        return (200 if result.ok else 502), payload

    def battle_debug(self, client_id: str | None = None) -> tuple[int, dict[str, Any]]:
        resolved_client_id, error = self._resolve_client_id(client_id)
        if error is not None:
            return 409, error
        request = ActionRequest(
            "battle_debug",
            cycle_id=0,
            battle_id=None,
            dry_run=False,
            metadata={"reason": "control_api_battle_debug"},
        )
        dispatched = self.live_actions.execute(resolved_client_id, request)
        debug = self.live_actions.battle_debug_result(resolved_client_id)
        payload = {
            "ok": bool(dispatched and isinstance(debug, dict)),
            "client_id": resolved_client_id,
            "debug": debug,
            "message": "battle_debug" if isinstance(debug, dict) else "battle_debug_unavailable",
        }
        return (200 if payload["ok"] else 502), payload

    def state_snapshot(
        self,
        client_id: str | None = None,
        include: list[str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        resolved_client_id, error = self._resolve_client_id(client_id)
        if error is not None:
            return 409, error
        sections = include or ["player", "location", "deathRevive", "battle", "hunt", "quests", "shopInventory"]
        result = self.injector.execute(
            "state_snapshot",
            {"include": sections},
            timeout_s=5.0,
            client_id=resolved_client_id,
        )
        try:
            snapshot = json.loads(result.message)
        except json.JSONDecodeError:
            snapshot = None
        payload: dict[str, Any] = {
            "ok": bool(result.ok and isinstance(snapshot, dict)),
            "client_id": result.client_id or resolved_client_id,
            "message": "state_snapshot" if isinstance(snapshot, dict) else result.message,
            "snapshot": snapshot if isinstance(snapshot, dict) else None,
        }
        return (200 if payload["ok"] else 502), payload

    def location_route(self, client_id: str | None = None) -> tuple[int, dict[str, Any]]:
        resolved_client_id, error = self._resolve_client_id(client_id)
        if error is not None:
            return 409, error
        result = self.injector.execute(
            "location_route_snapshot",
            timeout_s=5.0,
            client_id=resolved_client_id,
        )
        try:
            snapshot = json.loads(result.message)
        except json.JSONDecodeError:
            snapshot = None
        payload: dict[str, Any] = {
            "ok": bool(result.ok and isinstance(snapshot, dict)),
            "client_id": result.client_id or resolved_client_id,
            "message": "location_route_snapshot" if isinstance(snapshot, dict) else result.message,
            "snapshot": snapshot if isinstance(snapshot, dict) else None,
        }
        if isinstance(snapshot, dict):
            self.world_registry.observe_route(snapshot)
        return (200 if payload["ok"] else 502), payload

    def area_npcs(self, client_id: str | None = None, expected_name: str | None = None) -> tuple[int, dict[str, Any]]:
        return self._structured_snapshot(
            "area_npc_snapshot",
            client_id=client_id,
            payload={"expectedName": str(expected_name or "")[:180]},
        )

    def instance_entrance(
        self,
        client_id: str | None = None,
        expected_name: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return self._structured_snapshot(
            "instance_entrance_snapshot",
            client_id=client_id,
            payload={"expectedName": str(expected_name or "")[:180]},
        )

    def enter_instance(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not self.allow_live:
            return 403, {"ok": False, "error": "live_server_not_authorized"}
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        expected_name = str(payload.get("expectedName") or "").strip()
        expected_snapshot_id = str(payload.get("expectedSnapshotId") or "").strip()
        if not expected_name or not expected_snapshot_id:
            return 400, {"ok": False, "error": "instance_identity_missing"}
        submitted = self.live_actions.execute(
            resolved_client_id,
            ActionRequest(
                "enter_instance",
                dry_run=False,
                metadata={
                    "expected_name": expected_name,
                    "expected_snapshot_id": expected_snapshot_id,
                    "navigation_delay_ms": 75,
                },
            )
        )
        return (200 if submitted else 409), {
            "ok": submitted,
            "submitted": submitted,
            "client_id": resolved_client_id,
            "error": None if submitted else "instance_entry_blocked",
        }

    def npc_dialog(
        self,
        client_id: str | None = None,
        expected_name: str | None = None,
        expected_npc_id: str | int | None = None,
        expected_npc_instance_id: str | int | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return self._structured_snapshot(
            "npc_dialog_snapshot",
            client_id=client_id,
            payload={
                "expectedName": str(expected_name or "")[:180],
                "expectedNpcId": str(
                    expected_npc_id if expected_npc_id is not None else ""
                )[:40],
                "expectedNpcInstanceId": str(
                    expected_npc_instance_id if expected_npc_instance_id is not None else ""
                )[:40],
            },
        )

    def _structured_snapshot(
        self,
        command: str,
        *,
        client_id: str | None,
        payload: dict[str, object],
    ) -> tuple[int, dict[str, Any]]:
        resolved_client_id, error = self._resolve_client_id(client_id)
        if error is not None:
            return 409, error
        if command == "area_npc_snapshot":
            result = self.injector.execute("area_npc_snapshot", payload, timeout_s=3.0, client_id=resolved_client_id)
        elif command == "instance_entrance_snapshot":
            result = self.injector.execute("instance_entrance_snapshot", payload, timeout_s=3.0, client_id=resolved_client_id)
        elif command == "npc_dialog_snapshot":
            result = self.injector.execute("npc_dialog_snapshot", payload, timeout_s=3.0, client_id=resolved_client_id)
        else:
            return 400, {"ok": False, "error": "unsupported_snapshot_command"}
        try:
            snapshot = json.loads(result.message)
        except json.JSONDecodeError:
            snapshot = None
        response: dict[str, Any] = {
            "ok": bool(result.ok and isinstance(snapshot, dict)),
            "client_id": result.client_id or resolved_client_id,
            "message": command if isinstance(snapshot, dict) else result.message,
            "snapshot": snapshot if isinstance(snapshot, dict) else None,
        }
        if isinstance(snapshot, dict):
            if command == "area_npc_snapshot":
                self.world_registry.observe_area_npcs(snapshot)
            elif command == "npc_dialog_snapshot":
                self.world_registry.observe_npc_dialog(snapshot)
            elif command == "instance_entrance_snapshot":
                self.world_registry.observe_instances(snapshot)
        return (200 if response["ok"] else 502), response

    def location_route_step(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not self.allow_live:
            return 403, {
                "ok": False,
                "error": "live_server_not_authorized",
                "message": "Restart control-server with the explicit --live flag.",
            }
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        retained_lease = self.live_actions.retained_fenced(
            resolved_client_id, "location_route_step",
        )
        records = getattr(self, "_route_reconciliations", None)
        if records is None:
            records = {}
            self._route_reconciliations = records
        record = (
            records.get(retained_lease.target)
            if retained_lease is not None else None
        )
        if retained_lease is not None and not (
            isinstance(record, RouteReconciliationRecord)
            and record.lease == retained_lease
        ):
            return 502, {
                "ok": False,
                "submitted": False,
                "verified": False,
                "error": "route_reconciliation_record_missing",
                "client_id": resolved_client_id,
            }
        if isinstance(record, RouteReconciliationRecord) and record.lease == retained_lease:
            before_status = 200
            before_payload = {"snapshot": record.before_snapshot}
            before_snapshot = record.before_snapshot
        else:
            before_status, before_payload = self.location_route(resolved_client_id)
            before_snapshot = before_payload.get("snapshot") if before_status == 200 else None
        if not isinstance(before_snapshot, dict):
            return 409, {
                "ok": False,
                "error": "location_route_snapshot_unavailable",
                "client_id": resolved_client_id,
                "before": before_payload,
            }
        expected_current_location_id = str(
            payload.get("expectedCurrentLocationId")
            or before_snapshot.get("currentLocationId")
            or ""
        ).strip()
        def stage_route_record(lease: MutationLease) -> None:
            lock = getattr(self, "_lock", None)
            if lock is None:
                lock = threading.RLock()
                self._lock = lock
            with lock:
                existing = records.get(lease.target)
                candidate = RouteReconciliationRecord(lease, dict(before_snapshot))
                if existing is not None and existing != candidate:
                    raise RuntimeError("route reconciliation CAS conflict")
                records[lease.target] = candidate

        def rollback_route_record(lease: MutationLease) -> None:
            lock = getattr(self, "_lock", None)
            if lock is None:
                return
            with lock:
                existing = records.get(lease.target)
                if isinstance(existing, RouteReconciliationRecord) and existing.lease == lease:
                    records.pop(lease.target, None)

        dispatch = None if retained_lease is not None else self.live_actions.execute_fenced(
            resolved_client_id,
            ActionRequest(
                "location_route_step",
                dry_run=False,
                metadata={
                    "expected_current_location_id": expected_current_location_id,
                    "navigation_delay_ms": max(25, min(250, int(payload.get("navigationDelayMs") or 75))),
                },
            ),
            on_claimed=stage_route_record,
            on_not_issued=rollback_route_record,
        )
        dispatch_status = (
            ActionExecutionStatus.DELIVERY_UNKNOWN
            if dispatch is None else dispatch.execution.status
        )
        dispatch_lease = retained_lease if dispatch is None else dispatch.lease
        submitted = dispatch_status in {
            ActionExecutionStatus.ISSUED, ActionExecutionStatus.DELIVERY_UNKNOWN,
        }
        if not submitted:
            return 409, {
                "ok": False,
                "error": "location_route_step_blocked",
                "client_id": resolved_client_id,
                "before": _compact_route_snapshot(before_snapshot),
            }
        verify_delay_ms = max(300, min(3000, int(payload.get("verifyDelayMs") or 900)))
        time.sleep(verify_delay_ms / 1000)
        after_status, after_payload = self.location_route(resolved_client_id)
        after_snapshot = after_payload.get("snapshot") if after_status == 200 else None
        before_location_id = str(before_snapshot.get("currentLocationId") or "")
        after_location_id = (
            str(after_snapshot.get("currentLocationId") or "")
            if isinstance(after_snapshot, dict)
            else ""
        )
        before_location = before_snapshot.get("location")
        after_location = after_snapshot.get("location") if isinstance(after_snapshot, dict) else None
        next_transition = before_snapshot.get("nextTransition")
        before_name = (
            str(before_location.get("semanticName") or "").strip()
            if isinstance(before_location, dict)
            else ""
        )
        after_name = (
            str(after_location.get("semanticName") or "").strip()
            if isinstance(after_location, dict)
            else ""
        )
        expected_next_name = (
            str(next_transition.get("name") or "").strip()
            if isinstance(next_transition, dict)
            else ""
        )
        expected_next_location_id = (
            str(next_transition.get("locId") or "").strip()
            if isinstance(next_transition, dict)
            else ""
        )
        verified_by_id = bool(
            expected_next_location_id
            and after_location_id == expected_next_location_id
            and before_location_id != after_location_id
        )
        verified_by_name = bool(
            not expected_next_location_id
            and expected_next_name
            and after_name == expected_next_name
            and before_name != after_name
        )
        verified = verified_by_id or verified_by_name
        settled = verified
        if verified and dispatch_lease is not None:
            settled = False
            lock = getattr(self, "_lock", None)
            if lock is None:
                lock = threading.RLock()
                self._lock = lock
            with lock:
                existing = records.get(dispatch_lease.target)
                if (
                    isinstance(existing, RouteReconciliationRecord)
                    and existing.lease == dispatch_lease
                    and self.live_actions.reconcile_fenced(dispatch_lease)
                ):
                    records.pop(dispatch_lease.target, None)
                    settled = True
        verification_method = "location_id" if verified_by_id else "semantic_name" if verified_by_name else None
        return (200 if settled else 502), {
            "ok": settled,
            "submitted": True,
            "delivery_status": dispatch_status.value,
            "verified": verified,
            "verification_method": verification_method,
            "client_id": resolved_client_id,
            "total_actions": self.live_actions.counters(resolved_client_id)["total_actions"],
            "before": _compact_route_snapshot(before_snapshot),
            "after": _compact_route_snapshot(after_snapshot) if isinstance(after_snapshot, dict) else None,
            "after_error": None if isinstance(after_snapshot, dict) else after_payload,
        }

    def open_exact_npc(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not self.allow_live:
            return 403, {"ok": False, "error": "live_server_not_authorized"}
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        raw_npc_id = payload.get("npcId")
        raw_instance_id = payload.get("expectedNpcInstanceId")
        endpoint_npc_id = str(raw_npc_id).strip() if not isinstance(raw_npc_id, bool) else ""
        raw_route_ref = payload.get("expectedRouteRef")
        route_ref = str(raw_route_ref).strip() if not isinstance(raw_route_ref, bool) else ""
        if not route_ref.isascii() or not route_ref.isdecimal() or int(route_ref or "0") <= 0:
            return 400, {"ok": False, "error": "npc_route_identity_invalid"}
        if raw_instance_id is None or raw_instance_id == "":
            resulting_instance_id = ""
        elif isinstance(raw_instance_id, bool):
            return 400, {"ok": False, "error": "npc_instance_identity_invalid"}
        elif isinstance(raw_instance_id, int):
            if raw_instance_id <= 0:
                return 400, {"ok": False, "error": "npc_instance_identity_invalid"}
            resulting_instance_id = str(raw_instance_id)
        elif isinstance(raw_instance_id, str):
            candidate_instance_id = raw_instance_id.strip()
            if (
                not candidate_instance_id
                or not candidate_instance_id.isascii()
                or not candidate_instance_id.isdecimal()
                or int(candidate_instance_id) <= 0
            ):
                return 400, {"ok": False, "error": "npc_instance_identity_invalid"}
            resulting_instance_id = str(int(candidate_instance_id))
        else:
            return 400, {"ok": False, "error": "npc_instance_identity_invalid"}
        contract_values = {
            "expected_snapshot_id": str(payload.get("expectedSnapshotId") or "").strip(),
            "expected_location_id": str(payload.get("expectedLocationId") or "").strip(),
            "endpoint_npc_id": endpoint_npc_id,
            "route_ref": route_ref,
            "endpoint_name": str(payload.get("expectedName") or "").strip(),
            "resulting_dialog_name": str(
                payload.get("expectedDialogName") or payload.get("expectedName") or ""
            ).strip(),
            "resulting_instance_id": resulting_instance_id,
        }
        metadata = {
            "expected_snapshot_id": contract_values["expected_snapshot_id"],
            "expected_location_id": contract_values["expected_location_id"],
            "npc_id": contract_values["endpoint_npc_id"],
            "expected_route_ref": contract_values["route_ref"],
            "expected_name": contract_values["endpoint_name"],
            "expected_dialog_name": contract_values["resulting_dialog_name"],
            "expected_npc_instance_id": contract_values["resulting_instance_id"],
        }
        retained_lease = self.live_actions.retained_fenced(
            resolved_client_id, "open_exact_npc",
        )
        records = getattr(self, "_npc_open_reconciliations", None)
        if records is None:
            records = {}
            self._npc_open_reconciliations = records
        record = records.get(retained_lease.target) if retained_lease is not None else None
        if retained_lease is not None and not (
            isinstance(record, NpcOpenReconciliationRecord) and record.lease == retained_lease
        ):
            return 502, {
                "ok": False, "submitted": False,
                "error": "npc_open_reconciliation_record_missing",
                "client_id": resolved_client_id,
            }
        if isinstance(record, NpcOpenReconciliationRecord):
            current_contract = (
                contract_values["expected_snapshot_id"], contract_values["expected_location_id"],
                contract_values["endpoint_npc_id"], contract_values["endpoint_name"],
                contract_values["route_ref"],
                contract_values["resulting_dialog_name"], contract_values["resulting_instance_id"],
            )
            stored_contract = (
                record.expected_snapshot_id, record.expected_location_id,
                record.endpoint_npc_id, record.endpoint_name,
                record.route_ref,
                record.resulting_dialog_name, record.resulting_instance_id,
            )
            if current_contract != stored_contract:
                return 409, {
                    "ok": False, "submitted": False,
                    "error": "npc_open_reconciliation_contract_mismatch",
                    "client_id": resolved_client_id,
                }

        def stage_npc_record(lease: MutationLease) -> None:
            lock = getattr(self, "_lock", None)
            if lock is None:
                lock = threading.RLock()
                self._lock = lock
            candidate = NpcOpenReconciliationRecord(lease=lease, **contract_values)
            with lock:
                existing = records.get(lease.target)
                if existing is not None and existing != candidate:
                    raise RuntimeError("NPC reconciliation CAS conflict")
                records[lease.target] = candidate

        def rollback_npc_record(lease: MutationLease) -> None:
            lock = getattr(self, "_lock", None)
            if lock is None:
                return
            with lock:
                existing = records.get(lease.target)
                if isinstance(existing, NpcOpenReconciliationRecord) and existing.lease == lease:
                    records.pop(lease.target, None)

        dispatch = None if retained_lease is not None else self.live_actions.execute_fenced(
            resolved_client_id,
            ActionRequest("open_exact_npc", dry_run=False, metadata=metadata),
            on_claimed=stage_npc_record,
            on_not_issued=rollback_npc_record,
        )
        dispatch_status = (
            ActionExecutionStatus.DELIVERY_UNKNOWN
            if dispatch is None else dispatch.execution.status
        )
        diagnostic_code = _public_action_diagnostic_code(
            ActionDiagnosticCode.RETAINED_RECONCILIATION
            if dispatch is None else dispatch.execution.diagnostic_code
        )
        dispatch_lease = retained_lease if dispatch is None else dispatch.lease
        submitted = dispatch_status in {
            ActionExecutionStatus.ISSUED, ActionExecutionStatus.DELIVERY_UNKNOWN,
        }
        if not submitted:
            return 409, {
                "ok": False,
                "error": "open_exact_npc_blocked",
                "delivery_status": dispatch_status.value,
                "diagnostic_code": diagnostic_code,
                "client_id": resolved_client_id,
            }
        dialog_status, dialog = self.npc_dialog(
            resolved_client_id,
            contract_values["resulting_dialog_name"],
            contract_values["endpoint_npc_id"],
            contract_values["resulting_instance_id"],
        )
        dialog_snapshot = dialog.get("snapshot") if isinstance(dialog, dict) else None
        expected_npc_id_text = contract_values["endpoint_npc_id"]
        observed_npc_id = (
            dialog_snapshot.get("npcId") if isinstance(dialog_snapshot, dict) else None
        )
        observed_npc_id_text = (
            str(observed_npc_id).strip()
            if not isinstance(observed_npc_id, bool) else ""
        )
        observed_instance_id = (
            dialog_snapshot.get("npcInstanceId")
            if isinstance(dialog_snapshot, dict) else None
        )
        observed_instance_id_text = (
            str(observed_instance_id).strip()
            if not isinstance(observed_instance_id, bool) else ""
        )
        expected_instance_id_text = contract_values["resulting_instance_id"]
        dialog_verified = bool(
            dialog_status == 200
            and isinstance(dialog_snapshot, dict)
            and dialog_snapshot.get("identityMatches") is True
            and expected_npc_id_text.isdecimal()
            and observed_npc_id_text.isdecimal()
            and observed_npc_id_text == expected_npc_id_text
            and (
                not expected_instance_id_text
                or expected_instance_id_text.isdecimal()
                and observed_instance_id_text.isdecimal()
                and observed_instance_id_text == expected_instance_id_text
            )
        )
        dialog_settled = dialog_verified
        if dialog_verified and dispatch_lease is not None:
            dialog_settled = False
            lock = getattr(self, "_lock", None)
            if lock is None:
                lock = threading.RLock()
                self._lock = lock
            with lock:
                existing = records.get(dispatch_lease.target)
                if (
                    isinstance(existing, NpcOpenReconciliationRecord)
                    and existing.lease == dispatch_lease
                    and self.live_actions.reconcile_fenced(dispatch_lease)
                ):
                    records.pop(dispatch_lease.target, None)
                    dialog_settled = True
        return (200 if dialog_settled else 502), {
            "ok": dialog_settled,
            "submitted": True,
            "delivery_status": dispatch_status.value,
            "diagnostic_code": diagnostic_code,
            "client_id": resolved_client_id,
            "dialog": dialog_snapshot,
        }

    def inspect_exact_npc(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Discover resulting NPC identity under an exact durable endpoint lease."""

        if not self.allow_live:
            return 403, {"ok": False, "error": "live_server_not_authorized"}
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        try:
            contract = CensusInspectContract.from_payload(payload)
        except ValueError:
            return 400, {"ok": False, "error": "npc_census_contract_invalid"}

        actor_key = self._logical_client_key(resolved_client_id)
        if not isinstance(actor_key, tuple) or len(actor_key) != 2:
            return 409, {"ok": False, "error": "npc_census_actor_invalid"}
        pending = self._npc_inspect_journal.get(actor_key)
        if pending is not None and pending.contract != contract:
            return 409, {"ok": False, "error": "npc_census_contract_mismatch"}
        if pending is not None and pending.phase == "reconciled":
            try:
                cached_dialogue = json.loads(pending.dialogue_json or "")
            except json.JSONDecodeError:
                return 502, {"ok": False, "error": "npc_census_reconciliation_corrupt"}
            return 200, {
                "ok": True, "submitted": True,
                "delivery_status": ActionExecutionStatus.DELIVERY_UNKNOWN.value,
                "diagnostic_code": "retained_reconciliation",
                "client_id": resolved_client_id, "dialog": cached_dialogue,
                "error": None,
            }
        retained = self.live_actions.retained_fenced(resolved_client_id, "inspect_exact_npc")
        lease = self._npc_inspect_leases.get(actor_key) or retained
        if pending is None and retained is not None:
            return 502, {"ok": False, "error": "npc_census_reconciliation_missing"}

        def stage(lease: MutationLease) -> None:
            candidate = PendingCensusInspection(actor_key[0], actor_key[1], contract)
            with self._lock:
                self._npc_inspect_journal.stage(candidate)
                self._npc_inspect_leases[actor_key] = lease

        def rollback(lease: MutationLease) -> None:
            with self._lock:
                candidate = self._npc_inspect_journal.get(actor_key)
                if candidate is not None and self._npc_inspect_leases.get(actor_key) == lease:
                    self._npc_inspect_journal.clear_exact(candidate)
                    self._npc_inspect_leases.pop(actor_key, None)

        dispatch = None if pending is not None else self.live_actions.execute_fenced(
            resolved_client_id,
            ActionRequest(
                "inspect_exact_npc", dry_run=False,
                metadata=contract.action_metadata(),
            ),
            on_claimed=stage,
            on_not_issued=rollback,
        )
        status = (
            ActionExecutionStatus.DELIVERY_UNKNOWN
            if dispatch is None else dispatch.execution.status
        )
        diagnostic = _public_action_diagnostic_code(
            ActionDiagnosticCode.RETAINED_RECONCILIATION
            if dispatch is None else dispatch.execution.diagnostic_code
        )
        lease = lease if dispatch is None else dispatch.lease
        if status is ActionExecutionStatus.NOT_ISSUED:
            return 409, {
                "ok": False, "submitted": False,
                "delivery_status": status.value, "diagnostic_code": diagnostic,
                "error": "npc_census_inspect_not_issued",
            }

        dialog_status, dialog = self.npc_dialog(
            resolved_client_id, None, contract.endpoint_id, None,
        )
        snapshot = dialog.get("snapshot") if isinstance(dialog, dict) else None
        verified = dialog_status == 200 and inspection_snapshot_matches(contract, snapshot)
        settled = False
        if verified:
            with self._lock:
                existing = self._npc_inspect_journal.get(actor_key)
                if existing is not None and existing.contract == contract \
                        and existing.phase == "pending" and isinstance(snapshot, dict):
                    try:
                        self._npc_inspect_journal.mark_reconciled_exact(existing, snapshot)
                    except (OSError, ValueError):
                        settled = False
                    else:
                        settled = True
        return (200 if settled else 502), {
            "ok": settled,
            "submitted": True,
            "delivery_status": status.value,
            "diagnostic_code": diagnostic,
            "client_id": resolved_client_id,
            "dialog": snapshot if isinstance(snapshot, dict) else None,
            "error": None if settled else "npc_census_reconciliation_pending",
        }

    def npc_quest_action(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not self.allow_live:
            return 403, {"ok": False, "error": "live_server_not_authorized"}
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        submitted = self.live_actions.execute(
            resolved_client_id,
            ActionRequest(
                "npc_quest_action",
                dry_run=False,
                metadata={
                    "expected_snapshot_id": payload.get("expectedSnapshotId"),
                    "npc_id": payload.get("npcId"),
                    "quest_id": payload.get("questId"),
                    "expected_title": payload.get("expectedTitle"),
                    "action": payload.get("action"),
                    "expected_ref": payload.get("expectedRef"),
                    "expected_point_id": payload.get("expectedPointId"),
                    "expected_text": payload.get("expectedText"),
                },
            )
        )
        return (200 if submitted else 409), {
            "ok": submitted,
            "submitted": submitted,
            "client_id": resolved_client_id,
            "error": None if submitted else "npc_quest_action_blocked",
        }

    def open_active_quest_page(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not self.allow_live:
            return 403, {"ok": False, "error": "live_server_not_authorized"}
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        submitted = self.live_actions.execute(
            resolved_client_id,
            ActionRequest(
                "open_active_quest_page",
                dry_run=False,
                metadata={"page": payload.get("page")},
            )
        )
        return (200 if submitted else 409), {
            "ok": submitted,
            "submitted": submitted,
            "client_id": resolved_client_id,
            "error": None if submitted else "open_active_quest_page_blocked",
        }

    def open_quest_navigator(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        target = str(payload.get("target") or "").strip()
        link_label = str(payload.get("linkLabel") or target).strip()
        return self._live_action(
            payload,
            "open_quest_navigator",
            {
                "target": target,
                "link_label": link_label,
                "quest_id": str(payload.get("questId") or "").strip(),
            },
        )

    def open_location_navigator(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._live_action(payload, "open_location_navigator", {})

    def navigator_select_target(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        navigator_client_id = str(payload.get("navigatorClientId") or "").strip()
        target = str(payload.get("target") or "").strip()
        return self._live_action(
            payload,
            "navigator_select_target",
            {
                "navigator_client_id": navigator_client_id,
                "target": target,
                "target_kind": str(payload.get("targetKind") or "location").strip(),
                "search_delay_ms": int(payload.get("searchDelayMs") or 300),
                "route_delay_ms": int(payload.get("routeDelayMs") or 500),
                "retry_delay_ms": int(payload.get("retryDelayMs") or 600),
            },
        )

    def navigator_go(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._live_action(
            payload,
            "navigator_go",
            {
                "navigator_client_id": str(payload.get("navigatorClientId") or "").strip(),
                "target": str(payload.get("target") or "").strip(),
                "route_transitions": _optional_int(payload.get("routeTransitions")),
            },
        )

    def _live_action(
        self,
        payload: dict[str, Any],
        action_type: str,
        metadata: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        if not self.allow_live:
            return 403, {"ok": False, "error": "live_server_not_authorized"}
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        submitted = self.live_actions.execute(
            resolved_client_id,
            ActionRequest(action_type, dry_run=False, metadata=metadata),
        )
        return (200 if submitted else 409), {
            "ok": submitted,
            "submitted": submitted,
            "client_id": resolved_client_id,
            "action": action_type,
            "error": None if submitted else f"{action_type}_blocked",
        }

    def open_area(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not self.allow_live:
            return 403, {"ok": False, "error": "live_server_not_authorized"}
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        actor_key = self._logical_client_key(resolved_client_id)
        pending = self._npc_inspect_journal.get(actor_key) if isinstance(actor_key, tuple) else None
        if pending is not None:
            if pending.phase != "reconciled":
                area_status, area_payload = self.area_npcs(resolved_client_id)
                area_snapshot = area_payload.get("snapshot") if area_status == 200 else None
                if not newer_same_area_authority(pending.contract, area_snapshot):
                    return 409, {"ok": False, "submitted": False, "error": "npc_census_reconciliation_pending"}
                with self._lock:
                    current = self._npc_inspect_journal.get(actor_key)
                    lease = self._npc_inspect_leases.get(actor_key) \
                        or self.live_actions.retained_fenced(resolved_client_id, "inspect_exact_npc")
                    if current != pending or lease is None:
                        return 502, {"ok": False, "submitted": False, "error": "npc_census_abort_barrier_missing"}
                    if not self._npc_inspect_journal.clear_exact(pending):
                        return 502, {"ok": False, "submitted": False, "error": "npc_census_abort_clear_conflict"}
                    if not self.live_actions.reconcile_fenced(lease):
                        return 502, {"ok": False, "submitted": False, "error": "npc_census_abort_release_failed"}
                    self._npc_inspect_leases.pop(actor_key, None)
                return 409, {
                    "ok": False, "submitted": False,
                    "error": "npc_census_pending_aborted_on_new_area_authority",
                }
            with self._lock:
                current = self._npc_inspect_journal.get(actor_key)
                lease = self._npc_inspect_leases.get(actor_key) \
                    or self.live_actions.retained_fenced(resolved_client_id, "inspect_exact_npc")
                if current != pending or lease is None:
                    return 502, {"ok": False, "submitted": False, "error": "npc_census_reconciliation_barrier_missing"}
                try:
                    cleared = self._npc_inspect_journal.clear_exact(pending)
                except (OSError, ValueError):
                    return 502, {
                        "ok": False, "submitted": False,
                        "error": "npc_census_reconciliation_clear_failed",
                    }
                if not cleared:
                    return 502, {
                        "ok": False, "submitted": False,
                        "error": "npc_census_reconciliation_clear_conflict",
                    }
                if not self.live_actions.reconcile_fenced(lease):
                    return 502, {"ok": False, "submitted": False, "error": "npc_census_reconciliation_release_failed"}
                self._npc_inspect_leases.pop(actor_key, None)
        submitted = self.live_actions.execute(
            resolved_client_id,
            ActionRequest("open_area", dry_run=False, metadata={"reason": "control_api"}),
        )
        return (200 if submitted else 409), {
            "ok": submitted,
            "submitted": submitted,
            "client_id": resolved_client_id,
            "error": None if submitted else "open_area_blocked",
        }

    def start(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if "live" in payload and not isinstance(payload.get("live"), bool):
            return 400, {
                "ok": False,
                "error": "invalid_live_flag",
                "message": "live must be the JSON boolean true or false",
            }
        if payload.get("live") is True and not self.allow_live:
            return 403, {
                "ok": False,
                "error": "live_server_not_authorized",
                "message": "Restart control-server with the explicit --live flag.",
            }
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        with self._lock:
            current = self._run_for_client_locked(resolved_client_id)
            if current is not None and current.running:
                return 409, {
                    "ok": False,
                    "error": "tab_already_running",
                    "client_id": resolved_client_id,
                    "running_client_id": current.client_id,
                    "status": self.status(resolved_client_id),
                }
            stop_event = threading.Event()
            options = self._options_from_payload(payload, browser_client_id=resolved_client_id)
            mutation_lease = None
            if payload.get("live") is True:
                logical_key = self._logical_client_key(resolved_client_id)
                if logical_key is None:
                    return 409, {
                        "ok": False,
                        "error": "mutation_identity_missing",
                        "client_id": resolved_client_id,
                    }
                mutation_target = MutationTarget(*logical_key)
                actor_generation = self.mutation_coordinator.bind(
                    mutation_target, resolved_client_id,
                )
                mutation_lease = self.mutation_coordinator.try_acquire(
                    mutation_target,
                    f"controller:{resolved_client_id}",
                    MutationLeaseMode.EXCLUSIVE_RUN,
                    actor_generation=actor_generation,
                )
                if mutation_lease is None:
                    return 409, {
                        "ok": False,
                        "error": "mutation_lease_held",
                        "client_id": resolved_client_id,
                    }
                options = replace(
                    options,
                    mutation_coordinator=self.mutation_coordinator,
                    mutation_lease=mutation_lease,
                )
            run = ControlRun(
                client_id=resolved_client_id,
                thread=threading.Thread(
                    target=self._run_thread,
                    args=(resolved_client_id, options, stop_event),
                    name=f"automation-control-run-{_short_client_id(resolved_client_id)}",
                    daemon=True,
                ),
                stop_event=stop_event,
                started_at=time.time(),
                last_options=dict(payload),
                mutation_lease=mutation_lease,
            )
            self._runs[resolved_client_id] = run
            try:
                run.thread.start()
            except Exception:
                self._runs.pop(resolved_client_id, None)
                if mutation_lease is not None:
                    self.mutation_coordinator.release(mutation_lease)
                raise
        return 200, {"ok": True, "message": "started", "client_id": resolved_client_id, "status": self.status(resolved_client_id)}

    def stop(self, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        client_id = _payload_client_id(payload or {})
        with self._lock:
            if client_id:
                run = self._run_for_client_locked(client_id)
                runs = [run] if run is not None else []
            else:
                runs = [run for run in self._runs.values() if run.running]
            if not runs:
                return 200, {"ok": True, "message": "not_running", "status": self.status(client_id)}
            for run in runs:
                run.stop_event.set()
        return 200, {"ok": True, "message": "stop_requested", "status": self.status(client_id)}

    def _run_thread(self, client_id: str, options: AutomationRunOptions, stop_event: threading.Event) -> None:
        try:
            summary = run_automation(
                options,
                stop_event=stop_event,
                status_callback=lambda status: self._update_status(client_id, status),
                print_summary=False,
            )
            with self._lock:
                run = self._runs.get(client_id)
                if run is not None:
                    run.last_summary = summary
        except Exception as exc:
            with self._lock:
                run = self._runs.get(client_id)
                if run is not None:
                    run.last_error = str(exc)
        finally:
            with self._lock:
                run = self._runs.get(client_id)
                if run is not None:
                    run.ended_at = time.time()
                    mutation_lease = run.mutation_lease
                    run.mutation_lease = None
                else:
                    mutation_lease = None
            if mutation_lease is not None:
                self.mutation_coordinator.release(mutation_lease)

    def _update_status(self, client_id: str, status: dict[str, object]) -> None:
        with self._lock:
            run = self._runs.get(client_id)
            if run is not None:
                run.last_status = dict(status)

    def _clients_locked(self) -> list[dict[str, Any]]:
        clients = self.injector.client_snapshots(within_s=30.0)
        seen = {str(client.get("client_id")) for client in clients if client.get("client_id")}
        for client_id in sorted(set(self._runs) - seen):
            clients.append(self.injector.client_snapshot(client_id))
        for client in clients:
            client_id = str(client.get("client_id") or "")
            run = self._run_for_client_locked(client_id)
            client["running"] = False if run is None else run.running
            client["started_at"] = None if run is None else run.started_at
            client["ended_at"] = None if run is None else run.ended_at
            client["last_error"] = None if run is None else run.last_error
            last_status = {} if run is None else run.last_status
            client["bot_state"] = last_status.get("state") if isinstance(last_status, dict) else None
            client["completed_cycles"] = last_status.get("completed_cycles") if isinstance(last_status, dict) else None
            client["requested_cycles"] = last_status.get("requested_cycles") if isinstance(last_status, dict) else None
        return clients

    def _single_running_client_id_locked(self) -> str | None:
        running = [client_id for client_id, run in self._runs.items() if run.running]
        return running[0] if len(running) == 1 else None

    def _run_for_client_locked(self, client_id: str | None) -> ControlRun | None:
        if not client_id:
            return None
        exact = self._runs.get(client_id)
        if exact is not None:
            return exact
        target_key = self._logical_client_key(client_id)
        if target_key is None:
            return None
        matches = [
            run
            for run in self._runs.values()
            if self._logical_client_key(run.client_id) == target_key
        ]
        running = [run for run in matches if run.running]
        if len(running) == 1:
            return running[0]
        return matches[0] if len(matches) == 1 else None

    def _logical_client_key(self, client_id: str) -> tuple[str, int] | None:
        client = self.injector.client_snapshot(client_id)
        profile_id = str(client.get("profile_id") or "")
        tab_id = _optional_int(client.get("tab_id"))
        if not profile_id or tab_id is None:
            return None
        return profile_id, tab_id

    def _resolve_client_id(self, client_id: str | None) -> tuple[str | None, dict[str, Any] | None]:
        if client_id:
            snapshot_list = (
                self.injector.client_snapshots(within_s=30.0)
                if callable(getattr(self.injector, "client_snapshots", None))
                else []
            )
            client = next(
                (
                    candidate
                    for candidate in snapshot_list
                    if candidate.get("client_id") == client_id
                ),
                self.injector.client_snapshot(client_id),
            )
            if not client.get("client_seen"):
                return None, {
                    "ok": False,
                    "error": "client_not_seen",
                    "message": "The selected Chrome tab is stale or disconnected.",
                    "client": client,
                }
            if not client.get("version_ok"):
                return None, {
                    "ok": False,
                    "error": "client_version_mismatch",
                    "message": "Reload the Antibot CV extension and the selected game tab.",
                    "client": client,
                }
            return client_id, None
        clients = [client for client in self.injector.client_snapshots(within_s=5.0) if client.get("client_seen") and client.get("version_ok")]
        if len(clients) == 1:
            return str(clients[0]["client_id"]), None
        if not clients:
            return None, {
                "ok": False,
                "error": "client_not_seen",
                "message": "No active Chrome game client is connected to the injector.",
                "clients": self.injector.client_snapshots(within_s=30.0),
            }
        return None, {
            "ok": False,
            "error": "client_required",
            "message": "Several Chrome game clients are connected. Select a client_id first.",
            "clients": clients,
        }

    def _options_from_payload(self, payload: dict[str, Any], *, browser_client_id: str) -> AutomationRunOptions:
        target_levels = _parse_levels(payload.get("targetLevels"))
        target_names = _parse_names(payload.get("targetNames"))
        runtime_overrides = {
            "targetLevels": target_levels,
            "targetNames": target_names,
            "healthMinPercent": payload.get("healthMinPercent"),
            "prowessMinPercent": payload.get("prowessMinPercent"),
            "recoverToPercent": payload.get("recoverToPercent"),
            "itemRecoveryEnabled": payload.get("itemRecoveryEnabled"),
            "recoveryThreshold": payload.get("recoveryThreshold"),
            "recoveryHealthThreshold": payload.get("recoveryHealthThreshold"),
            "recoveryProwessThreshold": payload.get("recoveryProwessThreshold"),
            "maxUsesPerResource": payload.get("maxUsesPerResource"),
            "inventoryOpenDelayMs": payload.get("inventoryOpenDelayMs"),
            "combatSlotSequence": payload.get("combatSlotSequence"),
            "combatFallbackZeroEnabled": payload.get("combatFallbackZeroEnabled"),
            "combatFallbackProwessPercent": payload.get("combatFallbackProwessPercent"),
            "combatClickIntervalMs": payload.get("combatClickIntervalMs"),
            "combatPreClickDelayMs": payload.get("combatPreClickDelayMs"),
            "combatClickHoldMs": payload.get("combatClickHoldMs"),
            "battleItemRecoveryEnabled": payload.get("battleItemRecoveryEnabled"),
            "battleHealthPotionThreshold": payload.get("battleHealthPotionThreshold"),
            "battleProwessPotionThreshold": payload.get("battleProwessPotionThreshold"),
            "battleHealthPotionSlots": payload.get("battleHealthPotionSlots"),
            "battleProwessPotionSlots": payload.get("battleProwessPotionSlots"),
            "battleHealthPotionNames": payload.get("battleHealthPotionNames"),
            "battleProwessPotionNames": payload.get("battleProwessPotionNames"),
            "battleDamageBoostEnabled": payload.get("battleDamageBoostEnabled"),
            "battleDamageBoostSlots": payload.get("battleDamageBoostSlots"),
            "battleDamageBoostNames": payload.get("battleDamageBoostNames"),
            "battleDamageBoostChancePercent": payload.get("battleDamageBoostChancePercent"),
            "battleItemCooldownMs": payload.get("battleItemCooldownMs"),
            "battleItemMaxUsesPerBattle": payload.get("battleItemMaxUsesPerBattle"),
            "goalLevel": payload.get("goalLevel"),
            "requiredCharacterName": payload.get("requiredCharacterName"),
            "maxDeathsPerSession": payload.get("maxDeathsPerSession"),
            "targetLocationName": payload.get("targetLocationName"),
            "autoNavigateQuestTargets": payload.get("autoNavigateQuestTargets"),
            "autonomousQuestDirector": payload.get("autonomousQuestDirector"),
            "acceptAvailableQuests": payload.get("acceptAvailableQuests"),
            "pinnedQuestId": payload.get("pinnedQuestId"),
            "confirmDelayMs": payload.get("confirmDelayMs"),
            "betweenItemsDelayMs": payload.get("betweenItemsDelayMs"),
        }
        runtime_overrides = {key: value for key, value in runtime_overrides.items() if value is not None}
        return AutomationRunOptions(
            config_path=str(payload.get("configPath") or self.default_config),
            live=payload.get("live") is True,
            preview=bool(payload.get("preview", False)),
            max_cycles=_optional_int(payload.get("maxCycles")),
            max_session_minutes=_optional_int(payload.get("maxSessionMinutes")),
            target_allowed_levels=tuple(target_levels) if target_levels else None,
            target_allowed_names=tuple(target_names) if target_names else None,
            start_delay=_optional_float(payload.get("startDelay")),
            activate_app=str(payload.get("activateApp") or "") or None,
            no_activate_app=bool(payload.get("noActivateApp", True)),
            hotkeys=bool(payload.get("hotkeys", False)),
            open_hunt_on_start=bool(payload.get("openHuntOnStart", True)),
            browser_client_id=browser_client_id,
            runtime_overrides=runtime_overrides,
        )


def _compact_route_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    location = snapshot.get("location")
    return {
        "href": snapshot.get("href"),
        "pageKind": snapshot.get("pageKind"),
        "location": location if isinstance(location, dict) else None,
        "currentLocationId": snapshot.get("currentLocationId"),
        "targetLocationId": snapshot.get("targetLocationId"),
        "foundPath": snapshot.get("foundPath") if isinstance(snapshot.get("foundPath"), list) else [],
        "nextTransition": snapshot.get("nextTransition") if isinstance(snapshot.get("nextTransition"), dict) else None,
        "transitionTimerSeconds": snapshot.get("transitionTimerSeconds"),
        "timerReady": snapshot.get("timerReady"),
    }


def _payload_client_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("clientId") or payload.get("client_id")
    return str(value).strip() if value else None


def _public_action_diagnostic_code(value: object) -> str:
    return (
        value.value
        if isinstance(value, ActionDiagnosticCode)
        else ActionDiagnosticCode.UNSPECIFIED.value
    )


def _short_client_id(client_id: str) -> str:
    return client_id[-8:] if len(client_id) > 8 else client_id


def _parse_levels(value: object) -> list[int]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
    elif isinstance(value, list):
        parts = value
    else:
        parts = [value]
    levels: list[int] = []
    for part in parts:
        level = _optional_int(part)
        if level and level > 0 and level not in levels:
            levels.append(level)
    return levels


def _parse_names(value: object) -> list[str]:
    if value is None:
        return []
    raw = value.replace("\n", ",").split(",") if isinstance(value, str) else value if isinstance(value, list) else [value]
    names: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    return names


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
