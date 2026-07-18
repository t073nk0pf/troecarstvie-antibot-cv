"""Explicit legacy parser boundary for the runtime-independent quest compiler."""

from __future__ import annotations

import re

from src.antibot_cv.automation.area_object_activity import (
    AreaObjectPlanStatus,
    parse_area_object_plan,
)
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_compiler import (
    CompilerAdapter,
    QuestCompileResult,
    QuestCompileStatus,
)
from src.antibot_cv.automation.quest_objective_router import (
    ObjectiveRouteKind,
    ObjectiveRouteStatus,
    classify_objective,
)
from src.antibot_cv.automation.quest_ordered_npc_handoff import (
    OrderedHandoffRequirementKind,
    OrderedHandoffStatus,
    parse_ordered_npc_handoff,
)
from src.antibot_cv.automation.quest_plan_model import (
    Acquire,
    AllOf,
    AreaObject,
    Gathering,
    InteractNpc,
    QuestGraph,
    QuestNode,
    QuestPlan,
    Sequence,
    TurnIn,
    Visit,
)


_COUNTED_REQUIREMENT = re.compile(r"^(?P<count>[1-9]\d*)\s+(?P<name>\S.+)$")


def legacy_compiler_adapter(entry: ActiveQuestEntry) -> QuestCompileResult:
    """Adapt existing pure parsers while rejecting every partial objective."""

    ordered = parse_ordered_npc_handoff(entry)
    if ordered.status is OrderedHandoffStatus.UNSAFE:
        return _result(QuestCompileStatus.UNSAFE, entry, None, f"ordered_handoff:{ordered.reason}")
    if ordered.status is OrderedHandoffStatus.READY:
        first, internal, last = ordered.requirements
        if (
            first.kind is not OrderedHandoffRequirementKind.ROUTE_TO_NPC
            or internal.kind is not OrderedHandoffRequirementKind.FULFIL_NPC_REQUESTS
            or last.kind is not OrderedHandoffRequirementKind.RETURN_TO_NPC
            or not first.location or not first.npc_mention
            or not last.location or not last.npc_mention
        ):
            return _result(QuestCompileStatus.UNSAFE, entry, None, "ordered_handoff_incomplete")
        return _result(
            QuestCompileStatus.READY,
            entry,
            QuestGraph(Sequence((
                Visit(first.location),
                InteractNpc(first.npc_mention, request=internal.text, opaque=True),
                Visit(last.location),
                TurnIn(last.npc_mention),
            ))),
            "ordered_internal_request_unknown",
        )

    overall = classify_objective(entry)
    if overall.status is ObjectiveRouteStatus.UNSAFE:
        return _result(QuestCompileStatus.UNSAFE, entry, None, f"objective_route:{overall.reason}")
    if overall.kind is ObjectiveRouteKind.COMPOSITE:
        return _result(QuestCompileStatus.UNKNOWN, entry, None, "generic_adapter_partial_composite")

    area = parse_area_object_plan(entry)
    if area.status is AreaObjectPlanStatus.UNSAFE:
        return _result(QuestCompileStatus.UNSAFE, entry, None, f"area_object:{area.reason}")
    if area.status is AreaObjectPlanStatus.READY:
        nodes = tuple(
            Acquire(item.resource_name, item.required, AreaObject(item.resource_name))
            for item in area.requirements
        )
        return _result(
            QuestCompileStatus.READY, entry, QuestGraph(_joined(nodes)), "area_object_adapter"
        )

    if overall.kind is ObjectiveRouteKind.GATHER_RESOURCE:
        parsed = tuple(_parse_counted(item.text) for item in overall.requirements)
        if not parsed or any(item is None for item in parsed):
            return _result(QuestCompileStatus.UNSAFE, entry, None, "gathering_requirement_unbound")
        nodes = tuple(Acquire(name, count, Gathering(name)) for count, name in parsed if name)
        return _result(
            QuestCompileStatus.READY, entry, QuestGraph(_joined(nodes)), "gathering_adapter"
        )

    return _result(
        QuestCompileStatus.UNKNOWN,
        entry,
        None,
        f"ordered_handoff:{ordered.reason}",
        f"area_object:{area.reason}",
        f"objective_route:{overall.reason}",
    )


def _joined(nodes: tuple[QuestNode, ...]) -> QuestNode:
    if not nodes:
        raise ValueError("adapter produced no requirements")
    return nodes[0] if len(nodes) == 1 else AllOf(nodes)


def _parse_counted(text: str) -> tuple[int, str] | None:
    match = _COUNTED_REQUIREMENT.fullmatch(text)
    return None if match is None else (int(match.group("count")), match.group("name"))


def _result(
    status: QuestCompileStatus,
    entry: ActiveQuestEntry,
    graph: QuestGraph | None,
    *diagnostics: str,
) -> QuestCompileResult:
    plan = QuestPlan(entry.id, entry.title, graph) if graph is not None else None
    return QuestCompileResult(status, plan, tuple(diagnostics))


LEGACY_COMPILER_ADAPTERS: tuple[CompilerAdapter, ...] = (legacy_compiler_adapter,)


__all__ = ["LEGACY_COMPILER_ADAPTERS", "legacy_compiler_adapter"]
