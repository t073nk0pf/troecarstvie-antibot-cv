"""Closed mutability registry for page-bridge transport commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any


class PayloadPolicy(str, Enum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    INVENTORY_SNAPSHOT = "inventory_snapshot"


@dataclass(frozen=True, slots=True)
class BridgeCommandDescriptor:
    command: str
    payload_policy: PayloadPolicy

    def is_mutating(self, payload: dict[str, Any]) -> bool:
        if self.payload_policy is PayloadPolicy.READ_ONLY:
            return False
        if self.payload_policy is PayloadPolicy.MUTATING:
            return True
        return _inventory_snapshot_is_mutating(payload)


_READ_ONLY_COMMANDS = frozenset({
    "area_npc_snapshot", "area_object_snapshot", "battle_debug",
    "battle_snapshot", "gathering_node_snapshot", "hunt_bot_info",
    "hunt_candidates", "hunt_debug", "hunt_snapshot", "inspect_functions",
    "instance_entrance_snapshot", "layout_snapshot",
    "location_route_debug_snapshot", "location_route_snapshot",
    "navigator_snapshot", "npc_dialog_snapshot",
    "procurement_observation_snapshot", "probe_page",
    "resource_model_capabilities", "resource_snapshot", "state_snapshot",
    "visible_hunt_targets",
})

_MUTATING_COMMANDS = frozenset({
    "attack_bot", "attack_visible_bot", "close_resurrection_notice",
    "confirm_action_form", "enter_instance", "hunt_move_direction",
    "inspect_area_object", "inspect_exact_npc", "layout", "location_route_step", "navigator_go",
    "navigator_select_target", "npc_quest_action", "open_active_quest_page",
    "open_area", "open_exact_npc", "open_hunt", "open_location_navigator",
    "open_quest_catalog", "open_quest_navigator", "open_quests",
    "open_recovery_item", "resource_refresh", "revive_free",
    "use_battle_item", "use_quest_item", "use_recovery_items", "use_skill_slot",
})

_BRIDGE_COMMAND_REGISTRY = {
    **{
        command: BridgeCommandDescriptor(command, PayloadPolicy.READ_ONLY)
        for command in _READ_ONLY_COMMANDS
    },
    **{
        command: BridgeCommandDescriptor(command, PayloadPolicy.MUTATING)
        for command in _MUTATING_COMMANDS
    },
    "inventory_snapshot": BridgeCommandDescriptor(
        "inventory_snapshot", PayloadPolicy.INVENTORY_SNAPSHOT,
    ),
}
BRIDGE_COMMAND_REGISTRY = MappingProxyType(_BRIDGE_COMMAND_REGISTRY)
del _BRIDGE_COMMAND_REGISTRY


def bridge_command_is_mutating(command: str, payload: dict[str, Any]) -> bool:
    """Fail closed for unknown commands; known commands use typed policy."""

    descriptor = BRIDGE_COMMAND_REGISTRY.get(str(command or ""))
    return True if descriptor is None else descriptor.is_mutating(payload)


def _inventory_snapshot_is_mutating(payload: dict[str, Any]) -> bool:
    raw_open = payload.get("open")
    if raw_open is None:
        opens_backpack = True
    elif isinstance(raw_open, bool):
        opens_backpack = raw_open
    elif isinstance(raw_open, (int, float)):
        opens_backpack = raw_open != 0
    elif isinstance(raw_open, str):
        opens_backpack = raw_open != ""
    else:
        opens_backpack = True
    raw_category = payload.get("category")
    if raw_category is not None and not isinstance(raw_category, str):
        return True
    category = (raw_category or "").strip().lower()
    return opens_backpack or category == "quest"


__all__ = [
    "BRIDGE_COMMAND_REGISTRY", "BridgeCommandDescriptor", "PayloadPolicy",
    "bridge_command_is_mutating",
]
