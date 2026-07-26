"""Fail-closed planning and durable state for ordered NPC handoff quests.

This first capability slice authorizes only the route to the first location.
NPC observations after arrival are telemetry; they never advance the cursor or
authorize an NPC, dialogue, combat, gathering, or purchase mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping, Sequence

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint


ORDERED_NPC_HANDOFF_CAPABILITY = "ordered_npc_handoff_route_v1"


class OrderedHandoffStatus(str, Enum):
    READY = "ready"
    UNSUPPORTED = "unsupported"
    UNSAFE = "unsafe"


class OrderedHandoffRequirementKind(str, Enum):
    ROUTE_TO_NPC = "route_to_npc"
    FULFIL_NPC_REQUESTS = "fulfil_npc_requests"
    RETURN_TO_NPC = "return_to_npc"


@dataclass(frozen=True)
class OrderedHandoffRequirement:
    ordinal: int
    kind: OrderedHandoffRequirementKind
    text: str
    npc_mention: str | None
    navigation_label: str | None
    location: str | None


@dataclass(frozen=True)
class OrderedHandoffPlan:
    status: OrderedHandoffStatus
    quest_id: str | None
    quest_title: str | None
    fingerprint: str | None
    requirements: tuple[OrderedHandoffRequirement, ...]
    reason: str


@dataclass(frozen=True)
class OrderedHandoffCursor:
    quest_id: str
    quest_title: str
    fingerprint: str
    requirements: tuple[OrderedHandoffRequirement, ...]
    ordinal: int
    target_location: str
    target_navigation_label: str
    lease_fingerprint: str
    active_catalog_revision: int
    active_snapshot_id: str
    active_snapshot_generated_at: float
    client_id: str
    profile_id: str
    tab_id: int
    staged_at: float
    capability_version: str = ORDERED_NPC_HANDOFF_CAPABILITY


_FIRST = re.compile(
    r"^\s*отправляйтесь\s+к\s+(?P<npc>.+?)\s+(?:в|на)\s+"
    r"(?P<location>.+?)\s+и\s+",
    re.IGNORECASE,
)
_MIDDLE_AND_RETURN = re.compile(
    r"(?P<middle>сделайте\s+все,?\s+о\s+чем\s+он\s+попросит),?\s*"
    r"(?:затем|после\s+этого)\s+"
    r"(?P<return>возвращайтесь\s+к\s+(?P<npc>.+?)\s+(?:в|на)\s+(?P<location>.+?))[.!]?\s*$",
    re.IGNORECASE,
)
_UNSAFE_ALTERNATIVE = re.compile(r"(?:\bи\s*/\s*или\b|\bили\b|\bлибо\b)", re.IGNORECASE)


def parse_ordered_npc_handoff(entry: ActiveQuestEntry) -> OrderedHandoffPlan:
    """Recognize one generic ordered route/request/return sentence."""

    if not isinstance(entry, ActiveQuestEntry):
        return OrderedHandoffPlan(OrderedHandoffStatus.UNSAFE, None, None, None, (), "entry_invalid")
    fingerprint, reason = quest_step_fingerprint(entry)
    if fingerprint is None:
        return OrderedHandoffPlan(OrderedHandoffStatus.UNSAFE, entry.id, entry.title, None, (), reason)
    objective = entry.data.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        return OrderedHandoffPlan(OrderedHandoffStatus.UNSAFE, entry.id, entry.title, fingerprint, (), "objective_invalid")
    folded = objective.casefold()
    if "сделайте все" not in folded or "возвращайтесь к" not in folded:
        return OrderedHandoffPlan(OrderedHandoffStatus.UNSUPPORTED, entry.id, entry.title, fingerprint, (), "shape_unsupported")
    if _UNSAFE_ALTERNATIVE.search(objective):
        return OrderedHandoffPlan(OrderedHandoffStatus.UNSAFE, entry.id, entry.title, fingerprint, (), "alternative_unsafe")
    first = _FIRST.match(objective)
    if first is None:
        return OrderedHandoffPlan(OrderedHandoffStatus.UNSAFE, entry.id, entry.title, fingerprint, (), "first_clause_invalid")
    remainder = objective[first.end():]
    trailing = _MIDDLE_AND_RETURN.fullmatch(remainder)
    if trailing is None:
        return OrderedHandoffPlan(OrderedHandoffStatus.UNSAFE, entry.id, entry.title, fingerprint, (), "ordered_clauses_invalid")
    first_label = first.group("location").strip(" ,.!?")
    return_label = trailing.group("location").strip(" ,.!?")
    first_target = _exact_navigation_target(entry.data.get("navigation"), first_label)
    return_target = _exact_navigation_target(entry.data.get("navigation"), return_label)
    if first_target is None or return_target is None:
        return OrderedHandoffPlan(OrderedHandoffStatus.UNSAFE, entry.id, entry.title, fingerprint, (), "navigation_binding_missing_or_ambiguous")
    requirements = (
        OrderedHandoffRequirement(0, OrderedHandoffRequirementKind.ROUTE_TO_NPC, objective[: first.end()].strip().removesuffix(" и"), first.group("npc").strip(), first_label, first_target),
        OrderedHandoffRequirement(1, OrderedHandoffRequirementKind.FULFIL_NPC_REQUESTS, trailing.group("middle").strip(), None, None, None),
        OrderedHandoffRequirement(2, OrderedHandoffRequirementKind.RETURN_TO_NPC, trailing.group("return").strip(), trailing.group("npc").strip(), return_label, return_target),
    )
    return OrderedHandoffPlan(OrderedHandoffStatus.READY, entry.id, entry.title, fingerprint, requirements, "ordered_npc_handoff_ready")


def make_ordered_handoff_cursor(
    plan: OrderedHandoffPlan,
    *,
    lease_fingerprint: str,
    active_catalog_revision: int,
    active_snapshot_id: str,
    active_snapshot_generated_at: float,
    client_id: str,
    profile_id: str,
    tab_id: int,
    staged_at: float,
) -> OrderedHandoffCursor:
    if plan.status is not OrderedHandoffStatus.READY or not plan.quest_id or not plan.quest_title or not plan.fingerprint:
        raise ValueError("ordered handoff plan is not ready")
    if lease_fingerprint != plan.fingerprint or len(plan.requirements) != 3:
        raise ValueError("ordered handoff lease does not match plan")
    first = plan.requirements[0]
    if first.kind is not OrderedHandoffRequirementKind.ROUTE_TO_NPC or not first.location or not first.navigation_label:
        raise ValueError("ordered handoff first requirement is not routable")
    if (
        not isinstance(active_catalog_revision, int) or isinstance(active_catalog_revision, bool) or active_catalog_revision <= 0
        or not active_snapshot_id or active_snapshot_generated_at <= 0
        or not client_id or not profile_id or not isinstance(tab_id, int) or isinstance(tab_id, bool) or tab_id < 0
        or staged_at <= active_snapshot_generated_at
    ):
        raise ValueError("ordered handoff causal baseline is invalid")
    return OrderedHandoffCursor(
        plan.quest_id, plan.quest_title, plan.fingerprint, plan.requirements, 0,
        first.location, first.navigation_label, lease_fingerprint,
        active_catalog_revision, active_snapshot_id, active_snapshot_generated_at,
        client_id, profile_id, tab_id, staged_at,
    )


def serialize_ordered_handoff_cursor(cursor: OrderedHandoffCursor) -> dict[str, object]:
    return {
        "schema": 1,
        "capability_version": cursor.capability_version,
        "quest_id": cursor.quest_id,
        "quest_title": cursor.quest_title,
        "fingerprint": cursor.fingerprint,
        "requirements": [
            {
                "ordinal": item.ordinal, "kind": item.kind.value, "text": item.text,
                "npc_mention": item.npc_mention, "navigation_label": item.navigation_label,
                "location": item.location,
            }
            for item in cursor.requirements
        ],
        "ordinal": cursor.ordinal,
        "target_location": cursor.target_location,
        "target_navigation_label": cursor.target_navigation_label,
        "lease_fingerprint": cursor.lease_fingerprint,
        "active_catalog_revision": cursor.active_catalog_revision,
        "active_snapshot_id": cursor.active_snapshot_id,
        "active_snapshot_generated_at": cursor.active_snapshot_generated_at,
        "client_id": cursor.client_id, "profile_id": cursor.profile_id, "tab_id": cursor.tab_id,
        "staged_at": cursor.staged_at,
    }


def restore_ordered_handoff_cursor(raw: object) -> OrderedHandoffCursor | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or raw.get("schema") != 1 or raw.get("capability_version") != ORDERED_NPC_HANDOFF_CAPABILITY:
        raise ValueError("invalid ordered handoff cursor")
    requirements_raw = raw.get("requirements")
    if not isinstance(requirements_raw, Sequence) or isinstance(requirements_raw, (str, bytes)):
        raise ValueError("invalid ordered handoff requirements")
    try:
        requirements = tuple(
            OrderedHandoffRequirement(
                int(item["ordinal"]), OrderedHandoffRequirementKind(str(item["kind"])),
                str(item["text"]), _optional_text(item.get("npc_mention")),
                _optional_text(item.get("navigation_label")), _optional_text(item.get("location")),
            )
            for item in requirements_raw if isinstance(item, Mapping)
        )
        cursor = OrderedHandoffCursor(
            str(raw["quest_id"]), str(raw["quest_title"]), str(raw["fingerprint"]), requirements,
            int(raw["ordinal"]), str(raw["target_location"]), str(raw["target_navigation_label"]),
            str(raw["lease_fingerprint"]), int(raw["active_catalog_revision"]),
            str(raw["active_snapshot_id"]), float(raw["active_snapshot_generated_at"]),
            str(raw["client_id"]), str(raw["profile_id"]), int(raw["tab_id"]), float(raw["staged_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid ordered handoff cursor") from exc
    if (
        not cursor.quest_id.isdecimal() or int(cursor.quest_id) <= 0 or not cursor.quest_title
        or not cursor.fingerprint or cursor.lease_fingerprint != cursor.fingerprint
        or cursor.ordinal != 0 or len(cursor.requirements) != 3
        or tuple(item.ordinal for item in cursor.requirements) != (0, 1, 2)
        or cursor.requirements[0].kind is not OrderedHandoffRequirementKind.ROUTE_TO_NPC
        or cursor.requirements[1].kind is not OrderedHandoffRequirementKind.FULFIL_NPC_REQUESTS
        or cursor.requirements[2].kind is not OrderedHandoffRequirementKind.RETURN_TO_NPC
        or cursor.target_location != cursor.requirements[0].location
        or cursor.target_navigation_label != cursor.requirements[0].navigation_label
        or cursor.active_catalog_revision <= 0 or not cursor.active_snapshot_id
        or cursor.active_snapshot_generated_at <= 0 or cursor.staged_at <= cursor.active_snapshot_generated_at
        or not cursor.client_id or not cursor.profile_id or cursor.tab_id < 0
    ):
        raise ValueError("invalid ordered handoff cursor")
    return cursor


def _exact_navigation_target(raw: object, label: str) -> str | None:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    wanted = " ".join(label.casefold().split())
    matches = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text")
        target = item.get("target")
        if isinstance(text, str) and isinstance(target, str) and " ".join(text.casefold().split()) == wanted and target.strip():
            matches.append(target.strip())
    return matches[0] if len(matches) == 1 else None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid optional text")
    return value
