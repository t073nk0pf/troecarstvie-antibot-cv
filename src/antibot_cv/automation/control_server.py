from __future__ import annotations

from typing import Any

from src.antibot_cv.automation.control_service import (
    AutomationControlService,
    ControlRun,
)

__all__ = ["AutomationControlApi", "ControlRun"]


class AutomationControlApi(AutomationControlService):
    """Transport facade that maps control-server routes to application operations."""

    def handle(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        payload: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        if path == "/api/status" and method == "GET":
            return 200, self.status(_query_client_id(query))
        if path == "/api/clients" and method == "GET":
            return 200, {"ok": True, "clients": self.clients()}
        if path == "/api/config" and method == "GET":
            return 200, self.config_snapshot()
        if path == "/api/battle-skills" and method == "GET":
            return self.battle_skills(_query_client_id(query))
        if path == "/api/battle-debug" and method == "GET":
            return self.battle_debug(_query_client_id(query))
        if path == "/api/state-snapshot" and method == "GET":
            return self.state_snapshot(_query_client_id(query), _query_include(query))
        if path == "/api/area-npcs" and method == "GET":
            return self.area_npcs(_query_client_id(query), _query_text(query, "expectedName"))
        if path == "/api/npc-dialog" and method == "GET":
            return self.npc_dialog(_query_client_id(query), _query_text(query, "expectedName"))
        if path == "/api/location-route" and method == "GET":
            return self.location_route(_query_client_id(query))
        if path == "/api/instance-entrance" and method == "GET":
            return self.instance_entrance(
                _query_client_id(query),
                _query_text(query, "expectedName"),
            )
        if path == "/api/enter-instance" and method == "POST":
            return self.enter_instance(payload or {})
        if path == "/api/location-route-step" and method == "POST":
            return self.location_route_step(payload or {})
        if path == "/api/open-exact-npc" and method == "POST":
            return self.open_exact_npc(payload or {})
        if path == "/api/inspect-exact-npc" and method == "POST":
            return self.inspect_exact_npc(payload or {})
        if path == "/api/npc-quest-action" and method == "POST":
            return self.npc_quest_action(payload or {})
        if path == "/api/open-active-quest-page" and method == "POST":
            return self.open_active_quest_page(payload or {})
        if path == "/api/open-quest-navigator" and method == "POST":
            return self.open_quest_navigator(payload or {})
        if path == "/api/open-location-navigator" and method == "POST":
            return self.open_location_navigator(payload or {})
        if path == "/api/navigator-select-target" and method == "POST":
            return self.navigator_select_target(payload or {})
        if path == "/api/navigator-go" and method == "POST":
            return self.navigator_go(payload or {})
        if path == "/api/open-area" and method == "POST":
            return self.open_area(payload or {})
        if path == "/api/start" and method == "POST":
            return self.start(payload or {})
        if path == "/api/stop" and method == "POST":
            return self.stop(payload or {})
        return 404, {"ok": False, "error": "not_found"}


def _query_client_id(query: dict[str, list[str]]) -> str | None:
    value = query.get("clientId", [None])[0] or query.get("client_id", [None])[0]
    return str(value).strip() if value else None


def _query_text(query: dict[str, list[str]], key: str) -> str | None:
    value = query.get(key, [None])[0]
    return str(value).strip() if value else None


def _query_include(query: dict[str, list[str]]) -> list[str] | None:
    raw_values = query.get("include", [])
    values: list[str] = []
    for raw in raw_values:
        for part in str(raw).replace(",", " ").split():
            name = part.strip()
            if name and name not in values:
                values.append(name)
    return values or None
