from __future__ import annotations

import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.antibot_cv.automation.bridge_command_registry import (
    BRIDGE_COMMAND_REGISTRY,
    PayloadPolicy,
    bridge_command_is_mutating,
)


def test_registry_covers_every_source_dispatch_command_exactly() -> None:
    source = Path(
        "browser_injector/page_bridge_modules/40_state_layout_dispatch.js"
    ).read_text(encoding="utf-8")
    dispatched = set(re.findall(r'data\.command\.type === "([^"]+)"', source))

    assert dispatched
    assert set(BRIDGE_COMMAND_REGISTRY) == dispatched
    assert all(key == descriptor.command for key, descriptor in BRIDGE_COMMAND_REGISTRY.items())


def test_registry_is_fail_closed_and_has_no_suffix_heuristic() -> None:
    assert bridge_command_is_mutating("future_unknown_snapshot", {}) is True
    assert bridge_command_is_mutating("resource_refresh", {}) is True
    assert bridge_command_is_mutating("inspect_area_object", {}) is True
    assert bridge_command_is_mutating("inspect_exact_npc", {}) is True
    assert bridge_command_is_mutating("state_snapshot", {}) is False
    assert bridge_command_is_mutating("npc_dialog_snapshot", {}) is False
    assert BRIDGE_COMMAND_REGISTRY["inventory_snapshot"].payload_policy is PayloadPolicy.INVENTORY_SNAPSHOT


def test_inventory_conditional_policy_remains_fail_closed() -> None:
    assert bridge_command_is_mutating("inventory_snapshot", {}) is True
    assert bridge_command_is_mutating("inventory_snapshot", {"open": False}) is False
    assert bridge_command_is_mutating(
        "inventory_snapshot", {"open": False, "category": "quest"},
    ) is True


def test_registry_mapping_and_descriptors_are_immutable() -> None:
    original = BRIDGE_COMMAND_REGISTRY["open_hunt"]
    with pytest.raises(TypeError):
        BRIDGE_COMMAND_REGISTRY["open_hunt"] = original  # type: ignore[index]
    with pytest.raises(TypeError):
        del BRIDGE_COMMAND_REGISTRY["open_hunt"]  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        original.payload_policy = PayloadPolicy.READ_ONLY  # type: ignore[misc]
    assert BRIDGE_COMMAND_REGISTRY["open_hunt"] is original
    assert bridge_command_is_mutating("open_hunt", {}) is True
    assert bridge_command_is_mutating(
        "inventory_snapshot", {"open": False, "category": ["quest"]},
    ) is True
