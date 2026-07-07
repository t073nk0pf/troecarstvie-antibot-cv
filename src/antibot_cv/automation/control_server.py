from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.antibot_cv.automation.browser_injector import CURRENT_BRIDGE_VERSION, BrowserInjectorServer
from src.antibot_cv.automation.config import to_plain_dict
from src.antibot_cv.automation.controller import AutomationRunOptions, load_config, run_automation


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

    @property
    def running(self) -> bool:
        return self.thread.is_alive()


class AutomationControlApi:
    def __init__(self, injector: BrowserInjectorServer, *, default_config: str | Path | None = None) -> None:
        self.injector = injector
        self.default_config = str(default_config) if default_config is not None else "config/automation.local.json"
        self._lock = threading.RLock()
        self._runs: dict[str, ControlRun] = {}

    def handle(self, method: str, path: str, query: dict[str, list[str]], payload: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if path == "/api/status" and method == "GET":
            return 200, self.status(_query_client_id(query))
        if path == "/api/clients" and method == "GET":
            return 200, {"ok": True, "clients": self.clients()}
        if path == "/api/config" and method == "GET":
            return 200, self.config_snapshot()
        if path == "/api/battle-skills" and method == "GET":
            return self.battle_skills(_query_client_id(query))
        if path == "/api/start" and method == "POST":
            return self.start(payload or {})
        if path == "/api/stop" and method == "POST":
            return self.stop(payload or {})
        return 404, {"ok": False, "error": "not_found"}

    def clients(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._clients_locked()

    def status(self, client_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            selected_client_id = client_id or self._single_running_client_id_locked()
            selected_run = self._runs.get(selected_client_id or "") if selected_client_id else None
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
            "defaults": {
                "max_cycles": config.max_cycles,
                "max_session_minutes": config.max_session_minutes,
                "target_levels": list(config.target.allowed_levels),
                "health_min_percent": config.resources.health_min_percent,
                "prowess_min_percent": config.resources.prowess_min_percent,
                "recover_to_percent": config.resources.recover_to_percent,
                "item_recovery": to_plain_dict(config.item_recovery),
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

    def start(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        resolved_client_id, error = self._resolve_client_id(_payload_client_id(payload))
        if error is not None:
            return 409, error
        assert resolved_client_id is not None
        with self._lock:
            current = self._runs.get(resolved_client_id)
            if current is not None and current.running:
                return 409, {"ok": False, "error": "already_running", "client_id": resolved_client_id, "status": self.status(resolved_client_id)}
            stop_event = threading.Event()
            options = self._options_from_payload(payload, browser_client_id=resolved_client_id)
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
            )
            self._runs[resolved_client_id] = run
            run.thread.start()
        return 200, {"ok": True, "message": "started", "client_id": resolved_client_id, "status": self.status(resolved_client_id)}

    def stop(self, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        client_id = _payload_client_id(payload or {})
        with self._lock:
            if client_id:
                runs = [self._runs[client_id]] if client_id in self._runs else []
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
            run = self._runs.get(client_id)
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

    def _resolve_client_id(self, client_id: str | None) -> tuple[str | None, dict[str, Any] | None]:
        if client_id:
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
        runtime_overrides = {
            "targetLevels": target_levels,
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
            "confirmDelayMs": payload.get("confirmDelayMs"),
            "betweenItemsDelayMs": payload.get("betweenItemsDelayMs"),
        }
        runtime_overrides = {key: value for key, value in runtime_overrides.items() if value is not None}
        return AutomationRunOptions(
            config_path=str(payload.get("configPath") or self.default_config),
            live=bool(payload.get("live", True)),
            preview=bool(payload.get("preview", False)),
            max_cycles=_optional_int(payload.get("maxCycles")),
            max_session_minutes=_optional_int(payload.get("maxSessionMinutes")),
            target_allowed_levels=tuple(target_levels) if target_levels else None,
            start_delay=_optional_float(payload.get("startDelay")),
            activate_app=str(payload.get("activateApp") or "") or None,
            no_activate_app=bool(payload.get("noActivateApp", True)),
            hotkeys=bool(payload.get("hotkeys", False)),
            open_hunt_on_start=bool(payload.get("openHuntOnStart", True)),
            browser_client_id=browser_client_id,
            runtime_overrides=runtime_overrides,
        )


def _query_client_id(query: dict[str, list[str]]) -> str | None:
    value = query.get("clientId", [None])[0] or query.get("client_id", [None])[0]
    return str(value).strip() if value else None


def _payload_client_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("clientId") or payload.get("client_id")
    return str(value).strip() if value else None


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
