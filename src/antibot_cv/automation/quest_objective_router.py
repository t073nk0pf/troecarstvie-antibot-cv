"""Pure, fail-closed classification of authoritative active quest steps.

The router is deliberately action-free.  It translates one validated active
catalogue entry into planning evidence; executors remain responsible for all
guarded mutations and for obtaining fresher authoritative observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping

from src.antibot_cv.automation.gathering_activity_runtime import (
    GatheringPlanStatus,
    parse_gathering_plan,
)
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint
from src.antibot_cv.automation.quest_ordered_npc_handoff import (
    OrderedHandoffStatus,
    parse_ordered_npc_handoff,
)


class ObjectiveRouteKind(str, Enum):
    COMBAT_DROP = "combat_drop"
    PURE_KILL = "pure_kill"
    NPC_DIALOGUE_OR_HANDOFF = "npc_dialogue_or_handoff"
    LOCATION_VISIT = "location_visit"
    GATHER_RESOURCE = "gather_resource"
    NPC_PURCHASE = "npc_purchase"
    AUCTION_ACQUISITION = "auction_acquisition"
    COMPOSITE = "composite"
    TURN_IN = "turn_in"
    UNSUPPORTED = "unsupported"


class ObjectiveRouteStatus(str, Enum):
    READY = "ready"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class ObjectiveRequirement:
    """One conservatively recognized requirement in authoritative text order."""

    kind: ObjectiveRouteKind
    text: str
    ordinal: int
    complete: bool


@dataclass(frozen=True)
class ObjectiveRoutePlan:
    status: ObjectiveRouteStatus
    kind: ObjectiveRouteKind
    quest_id: str | None
    quest_title: str | None
    fingerprint: str | None
    requirements: tuple[ObjectiveRequirement, ...]
    unmet_requirements: tuple[ObjectiveRequirement, ...]
    reason: str


_MONSTER_TARGET = re.compile(r"^.+\s\[[1-9]\d*\]$")
_CLAUSE_START = (
    r"(?:убейте|убить|победите|победить|уничтожьте|уничтожить|"
    r"добудьте|добыть|найдите|найти|соберите|собрать|нарубите|нарубить|поймайте|поймать|"
    r"купите|купить|приобретите|приобрести|возьмите|взять|получите|получить|поговорите|поговорить|"
    r"отправляйтесь|отправиться|посетите|посетить|вернитесь|вернуться|отнесите|отнести)"
)
_COMMA_CLAUSE_START = (
    r"(?:убейте|убить|победите|победить|уничтожьте|уничтожить|"
    r"добудьте|добыть|найдите|найти|соберите|собрать|нарубите|нарубить|поймайте|поймать|"
    r"купите|купить|приобретите|приобрести|возьмите|взять|поговорите|поговорить|"
    r"отправляйтесь|отправиться|посетите|посетить|вернитесь|вернуться|отнесите|отнести)"
)
_SPLIT = re.compile(
    rf"\s*(?:;|,(?=\s*{_COMMA_CLAUSE_START})|\bзатем\b|\bпосле\s+(?:этого|чего)\b|\bи\b(?=\s*{_CLAUSE_START}))\s*",
    re.IGNORECASE,
)
_ALTERNATIVE = re.compile(r"(?:\bи\s*/\s*или\b|\bили\b|\bлибо\b)", re.IGNORECASE)
_COUNTED_RESOURCE = re.compile(
    r"^(?:добудьте|добыть|найдите|найти|соберите|собрать|нарубите|нарубить|поймайте|поймать)\s+[1-9]\d*\s+\S.+?[.!]?$",
    re.IGNORECASE,
)
_PURE_KILL = re.compile(
    r"^(?:убейте|убить|победите|победить|уничтожьте|уничтожить)\s+.+?[.!]?$",
    re.IGNORECASE,
)
_COMBAT_DROP = re.compile(
    r"^(?:(?:убивая|побеждая)\s+.+?\s*,?\s*(?:добудьте|добыть|найдите|найти|получите|получить|соберите|собрать)|(?:добудьте|добыть|найдите|найти|получите|получить|соберите|собрать)\s+.+?\s+(?:с|у)\s+).+?[.!]?$",
    re.IGNORECASE,
)
_PURCHASE = re.compile(r"^(?:купите|купить)\s+.+?\s+у\s+\S.+?[.!]?$", re.IGNORECASE)
_AUCTION = re.compile(
    r"^(?:купите|купить|приобретите|приобрести)\s+.+?\s+(?:на|через)\s+аукционе?[.!]?$",
    re.IGNORECASE,
)
_NPC_HANDOFF = re.compile(
    r"^(?:(?:поговорите|поговорить|обратитесь|расспросите)\s+(?:с\s+|к\s+)?\S.+|(?:возьмите|взять|получите|получить)\s+.+?\s+у\s+\S.+)[.!]?$",
    re.IGNORECASE,
)
_TRAVEL_TO_NPC = re.compile(
    r"^(?:отправляйтесь|отправиться)\s+(?:к\s+)?\S.+?\s+(?:в|на)\s+\S.+?[.!]?$",
    re.IGNORECASE,
)
_TURN_IN = re.compile(
    r"^(?:вернитесь|вернуться)\s+к\s+\S.+|^(?:отнесите|отнести|передайте)\s+.+?\s+\S.+?[.!]?$",
    re.IGNORECASE,
)
_LOCATION = re.compile(
    r"^(?:отправляйтесь|отправиться|доберитесь|добраться|посетите|посетить|идите)\s+(?:в|на|к)\s+\S.+?[.!]?$",
    re.IGNORECASE,
)


def classify_objective(entry: ActiveQuestEntry) -> ObjectiveRoutePlan:
    """Classify one authoritative step without authorizing an action."""

    if not isinstance(entry, ActiveQuestEntry):
        return _unsafe(None, "objective_entry_invalid")
    fingerprint, reason = quest_step_fingerprint(entry)
    if fingerprint is None:
        return _unsafe(entry, reason)
    objective = entry.data.get("objective")
    assert isinstance(objective, str)
    kind_hint = entry.data.get("objectiveKind")
    if kind_hint is not None and (
        not isinstance(kind_hint, str)
        or kind_hint.strip().casefold() not in {"combat", "collect", "dialogue", "travel", "unknown"}
    ):
        return _unsafe(entry, "objective_kind_invalid", fingerprint)
    hint = kind_hint.strip().casefold() if isinstance(kind_hint, str) else ""
    progress_complete, progress_reason = _progress_state(entry.data.get("progress"))
    if progress_reason is not None:
        return _unsafe(entry, progress_reason, fingerprint)
    if progress_complete:
        requirement = ObjectiveRequirement(ObjectiveRouteKind.TURN_IN, objective, 0, False)
        return _ready(entry, fingerprint, ObjectiveRouteKind.TURN_IN, (requirement,), "objective_progress_complete")

    navigation = entry.data.get("navigation")
    assert isinstance(navigation, (tuple, list))
    monster_count = sum(
        1
        for item in navigation
        if isinstance(item, Mapping) and _MONSTER_TARGET.fullmatch(str(item.get("target") or ""))
    )
    if monster_count > 1:
        return _unsafe(entry, "objective_monster_ambiguous", fingerprint)
    if _ALTERNATIVE.search(objective):
        return _unsafe(entry, "objective_alternative_unsafe", fingerprint)
    ordered_handoff = parse_ordered_npc_handoff(entry)
    if ordered_handoff.status is OrderedHandoffStatus.UNSAFE:
        return _unsafe(entry, f"objective_ordered_handoff:{ordered_handoff.reason}", fingerprint)
    if ordered_handoff.status is OrderedHandoffStatus.READY:
        requirements = tuple(
            ObjectiveRequirement(
                ObjectiveRouteKind.NPC_DIALOGUE_OR_HANDOFF,
                requirement.text,
                requirement.ordinal,
                False,
            )
            for requirement in ordered_handoff.requirements
        )
        return _ready(
            entry, fingerprint, ObjectiveRouteKind.COMPOSITE, requirements,
            "objective_ordered_npc_handoff_ready",
        )
    if _TRAVEL_TO_NPC.fullmatch(objective):
        if hint == "travel" or not _travel_npc_is_bound(objective, navigation):
            return _unsafe(entry, "objective_travel_npc_ambiguous", fingerprint)
        hint = "dialogue"

    clauses = tuple(part.strip(" ,") for part in _SPLIT.split(objective) if part.strip(" ,"))
    classified = tuple(_classify_clause(clause, hint=hint, monster_count=monster_count) for clause in clauses)
    recognized = tuple(kind for kind in classified if kind is not ObjectiveRouteKind.UNSUPPORTED)
    if len(clauses) > 1:
        if len(recognized) != len(clauses):
            return _unsafe(entry, "objective_composite_part_unsupported", fingerprint)
        requirements = tuple(
            ObjectiveRequirement(kind, clause, index, False)
            for index, (clause, kind) in enumerate(zip(clauses, classified))
        )
        if len(set(classified)) > 1:
            return _ready(entry, fingerprint, ObjectiveRouteKind.COMPOSITE, requirements, "objective_composite_ready")
        # Multiple same-kind resource requirements are one gather plan, while
        # repeated NPC/route clauses remain composite because order matters.
        if classified[0] is ObjectiveRouteKind.GATHER_RESOURCE:
            return _ready(entry, fingerprint, classified[0], requirements, "objective_gather_ready")
        return _ready(entry, fingerprint, ObjectiveRouteKind.COMPOSITE, requirements, "objective_composite_ready")

    direct = classified[0] if classified else ObjectiveRouteKind.UNSUPPORTED
    if direct is ObjectiveRouteKind.GATHER_RESOURCE:
        gathering = parse_gathering_plan(entry)
        if gathering.status is GatheringPlanStatus.UNSAFE:
            return _unsafe(entry, f"objective_gather:{gathering.reason}", fingerprint)
        if gathering.status is GatheringPlanStatus.READY:
            requirements = tuple(
                ObjectiveRequirement(
                    ObjectiveRouteKind.GATHER_RESOURCE,
                    f"{item.required} {item.name}",
                    index,
                    False,
                )
                for index, item in enumerate(gathering.requirements)
            )
            return _ready(entry, fingerprint, direct, requirements, "objective_gather_ready")
    if direct is ObjectiveRouteKind.UNSUPPORTED and hint == "collect" and monster_count == 0:
        gathering = parse_gathering_plan(entry)
        if gathering.status is GatheringPlanStatus.UNSAFE:
            return _unsafe(entry, f"objective_gather:{gathering.reason}", fingerprint)
        if gathering.status is GatheringPlanStatus.READY:
            requirements = tuple(
                ObjectiveRequirement(
                    ObjectiveRouteKind.GATHER_RESOURCE,
                    f"{item.required} {item.name}",
                    index,
                    False,
                )
                for index, item in enumerate(gathering.requirements)
            )
            return _ready(entry, fingerprint, ObjectiveRouteKind.GATHER_RESOURCE, requirements, "objective_gather_ready")
    requirement = ObjectiveRequirement(direct, objective, 0, False)
    return _ready(
        entry,
        fingerprint,
        direct,
        (requirement,),
        "objective_unsupported" if direct is ObjectiveRouteKind.UNSUPPORTED else f"objective_{direct.value}_ready",
    )


def _classify_clause(clause: str, *, hint: str, monster_count: int) -> ObjectiveRouteKind:
    matches: list[ObjectiveRouteKind] = []
    if _PURCHASE.fullmatch(clause):
        matches.append(ObjectiveRouteKind.NPC_PURCHASE)
    if _AUCTION.fullmatch(clause):
        matches.append(ObjectiveRouteKind.AUCTION_ACQUISITION)
    if _TURN_IN.fullmatch(clause):
        matches.append(ObjectiveRouteKind.TURN_IN)
    if monster_count == 0 and (_NPC_HANDOFF.fullmatch(clause) or (hint == "dialogue" and _TRAVEL_TO_NPC.fullmatch(clause))):
        matches.append(ObjectiveRouteKind.NPC_DIALOGUE_OR_HANDOFF)
    if hint != "dialogue" and _LOCATION.fullmatch(clause):
        matches.append(ObjectiveRouteKind.LOCATION_VISIT)
    if monster_count == 1 and _COMBAT_DROP.fullmatch(clause):
        matches.append(ObjectiveRouteKind.COMBAT_DROP)
    if monster_count == 1 and _PURE_KILL.fullmatch(clause):
        matches.append(ObjectiveRouteKind.PURE_KILL)
    if monster_count == 0 and _COUNTED_RESOURCE.fullmatch(clause):
        matches.append(ObjectiveRouteKind.GATHER_RESOURCE)
    return matches[0] if len(set(matches)) == 1 else ObjectiveRouteKind.UNSUPPORTED


def first_unmet_combat_requirement(plan: ObjectiveRoutePlan) -> ObjectiveRequirement | None:
    """Return the executable opening combat phase for a route plan, if any."""

    if not isinstance(plan, ObjectiveRoutePlan) or plan.status is not ObjectiveRouteStatus.READY:
        return None
    if plan.kind in {ObjectiveRouteKind.COMBAT_DROP, ObjectiveRouteKind.PURE_KILL}:
        return plan.unmet_requirements[0] if plan.unmet_requirements else None
    if plan.kind is not ObjectiveRouteKind.COMPOSITE or not plan.unmet_requirements:
        return None
    first = plan.unmet_requirements[0]
    if first.kind in {ObjectiveRouteKind.COMBAT_DROP, ObjectiveRouteKind.PURE_KILL}:
        return first
    return None


def _progress_state(raw: object) -> tuple[bool, str | None]:
    if not isinstance(raw, Mapping):
        return False, None
    complete = raw.get("complete") is True
    current = raw.get("current")
    required = raw.get("required")
    if (
        isinstance(current, int)
        and not isinstance(current, bool)
        and isinstance(required, int)
        and not isinstance(required, bool)
    ):
        if complete and current < required:
            return False, "objective_progress_complete_contradicts_ratio"
        if not complete and current >= required:
            return False, "objective_progress_incomplete_contradicts_ratio"
    return complete, None


def _travel_npc_is_bound(objective: str, navigation: object) -> bool:
    if not isinstance(navigation, (tuple, list)) or len(navigation) != 1:
        return False
    item = navigation[0]
    if not isinstance(item, Mapping):
        return False
    label = item.get("text")
    target = item.get("target")
    if not isinstance(label, str) or not isinstance(target, str):
        return False
    if " ".join(label.casefold().split()) != " ".join(target.casefold().split()):
        return False
    return re.search(
        rf"\s(?:к\s+)?\S.+?\s(?:в|на)\s+{re.escape(label)}(?=\s|[.,!?;:]|$)",
        objective,
        flags=re.IGNORECASE,
    ) is not None


def _ready(
    entry: ActiveQuestEntry,
    fingerprint: str,
    kind: ObjectiveRouteKind,
    requirements: tuple[ObjectiveRequirement, ...],
    reason: str,
) -> ObjectiveRoutePlan:
    unmet = tuple(item for item in requirements if not item.complete)
    return ObjectiveRoutePlan(
        ObjectiveRouteStatus.READY,
        kind,
        entry.id,
        entry.title,
        fingerprint,
        requirements,
        unmet,
        reason,
    )


def _unsafe(
    entry: ActiveQuestEntry | None,
    reason: str,
    fingerprint: str | None = None,
) -> ObjectiveRoutePlan:
    return ObjectiveRoutePlan(
        ObjectiveRouteStatus.UNSAFE,
        ObjectiveRouteKind.UNSUPPORTED,
        entry.id if entry is not None else None,
        entry.title if entry is not None else None,
        fingerprint,
        (),
        (),
        reason,
    )
