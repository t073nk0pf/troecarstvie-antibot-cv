"""Pure planning for non-combat gathering activities on the hunt map.

The page bridge owns discovery and interaction with map nodes. This module
only turns a trusted quest objective and inventory observation into an exact,
auditable activity plan; it never guesses a node or a profession.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping, Sequence

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry


class GatheringActivity(str, Enum):
    HERBALISM = "herbalism"
    FISHING = "fishing"
    SKINNING = "skinning"
    MINING = "mining"
    GENERIC = "generic"


class GatheringPlanStatus(str, Enum):
    READY = "ready"
    UNSUPPORTED = "unsupported"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class GatheringRequirement:
    name: str
    required: int


@dataclass(frozen=True)
class GatheringPlan:
    status: GatheringPlanStatus
    quest_id: str | None
    quest_title: str | None
    activity: GatheringActivity | None
    requirements: tuple[GatheringRequirement, ...]
    reason: str


@dataclass(frozen=True)
class GatheringProgress:
    complete: bool
    collected: tuple[tuple[str, int], ...]
    missing: tuple[GatheringRequirement, ...]


_COUNTED_ITEM = re.compile(
    r"(?:(?:добудьте|соберите|поймайте|снимите|нарубите)\s+)?"
    r"(?P<count>[1-9]\d*)\s+(?P<name>[А-ЯЁA-Z][^,.;]+?)"
    r"(?=(?:\s+и\s+\d+\s+)|(?:,|;|\.)|$)",
    re.IGNORECASE,
)


def parse_gathering_plan(entry: ActiveQuestEntry) -> GatheringPlan:
    """Parse an explicitly marked collection objective without fuzzy matches."""

    if not isinstance(entry, ActiveQuestEntry):
        return GatheringPlan(GatheringPlanStatus.UNSAFE, None, None, None, (), "entry_invalid")
    data = entry.data
    kind = str(data.get("objectiveKind") or "").strip().casefold()
    objective = data.get("objective")
    if kind != "collect":
        return GatheringPlan(GatheringPlanStatus.UNSUPPORTED, entry.id, entry.title, None, (), "objective_not_marked_collect")
    if not isinstance(objective, str) or objective != objective.strip() or not objective:
        return GatheringPlan(GatheringPlanStatus.UNSAFE, entry.id, entry.title, None, (), "objective_invalid")
    requirements: list[GatheringRequirement] = []
    for match in _COUNTED_ITEM.finditer(objective):
        name = " ".join(match.group("name").split())
        count = int(match.group("count"))
        if not name or count <= 0 or len(name) > 180:
            return GatheringPlan(GatheringPlanStatus.UNSAFE, entry.id, entry.title, None, (), "requirement_invalid")
        requirements.append(GatheringRequirement(name, count))
    if not requirements:
        return GatheringPlan(GatheringPlanStatus.UNSUPPORTED, entry.id, entry.title, None, (), "collection_requirements_unparsed")
    normalized = [_normalized(requirement.name) for requirement in requirements]
    if len(normalized) != len(set(normalized)):
        return GatheringPlan(GatheringPlanStatus.UNSAFE, entry.id, entry.title, None, (), "duplicate_requirement")
    return GatheringPlan(GatheringPlanStatus.READY, entry.id, entry.title, _activity_from_text(objective), tuple(requirements), "gathering_plan_ready")


def gathering_progress(plan: GatheringPlan, inventory_items: Sequence[Mapping[str, object]]) -> GatheringProgress:
    """Aggregate exact inventory names; incomplete or malformed snapshots never pass."""

    if plan.status is not GatheringPlanStatus.READY:
        return GatheringProgress(False, (), plan.requirements)
    totals: dict[str, int] = {}
    for item in inventory_items:
        if not isinstance(item, Mapping):
            return GatheringProgress(False, (), plan.requirements)
        raw_name, raw_count = item.get("name"), item.get("count")
        if not isinstance(raw_name, str) or not raw_name.strip() or isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
            return GatheringProgress(False, (), plan.requirements)
        key = _normalized(raw_name)
        totals[key] = totals.get(key, 0) + raw_count
    resolved_totals: dict[str, int] = {}
    for requirement in plan.requirements:
        requirement_key = _normalized(requirement.name)
        exact = totals.get(requirement_key)
        if exact is not None:
            resolved_totals[requirement_key] = exact
            continue
        candidates = [
            count
            for inventory_name, count in totals.items()
            if _same_resource_lexeme(requirement_key, inventory_name)
        ]
        resolved_totals[requirement_key] = candidates[0] if len(candidates) == 1 else 0
    collected = tuple((requirement.name, resolved_totals[_normalized(requirement.name)]) for requirement in plan.requirements)
    missing = tuple(
        GatheringRequirement(requirement.name, requirement.required - resolved_totals[_normalized(requirement.name)])
        for requirement in plan.requirements
        if resolved_totals[_normalized(requirement.name)] < requirement.required
    )
    return GatheringProgress(not missing, collected, missing)


def _activity_from_text(value: str) -> GatheringActivity:
    lowered = value.casefold()
    if any(token in lowered for token in ("рыб", "удочк", "рыбал")):
        return GatheringActivity.FISHING
    if any(token in lowered for token in ("шкур", "освеж", "кож")):
        return GatheringActivity.SKINNING
    if any(token in lowered for token in ("руд", "минерал", "жил")):
        return GatheringActivity.MINING
    if any(token in lowered for token in ("трав", "цвет", "корен", "растен")):
        return GatheringActivity.HERBALISM
    return GatheringActivity.GENERIC


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _same_resource_lexeme(requirement: str, inventory_name: str) -> bool:
    """Accept a unique Russian case inflection, never a broad substring match."""

    requirement_words = requirement.split()
    inventory_words = inventory_name.split()
    if len(requirement_words) != len(inventory_words) or not requirement_words:
        return False
    return all(
        _same_word_lexeme(expected, observed)
        for expected, observed in zip(requirement_words, inventory_words)
    )


def _same_word_lexeme(expected: str, observed: str) -> bool:
    if expected == observed:
        return True
    shared = 0
    for left, right in zip(expected, observed):
        if left != right:
            break
        shared += 1
    return shared >= 4 and shared >= min(len(expected), len(observed)) - 2
