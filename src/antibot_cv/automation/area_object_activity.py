"""Pure planning for quest resources obtained from illustrated area objects.

The game presents these as unlabeled visual hotspots rather than hunt-map
nodes.  This module only extracts the exact resource/location requirements
from a trusted active quest card; browser discovery and clicks stay elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.runtime_helpers import clean_quest_route_label


class AreaObjectPlanStatus(str, Enum):
    READY = "ready"
    UNSUPPORTED = "unsupported"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class AreaObjectRequirement:
    resource_name: str
    required: int
    location: str


@dataclass(frozen=True)
class AreaObjectPlan:
    status: AreaObjectPlanStatus
    quest_id: str | None
    quest_title: str | None
    requirements: tuple[AreaObjectRequirement, ...]
    reason: str


@dataclass(frozen=True)
class AreaObjectProgress:
    """Inventory-backed progress for a multi-location illustrated-object step."""

    complete: bool
    collected: tuple[tuple[str, int], ...]
    next_requirement: AreaObjectRequirement | None


_FIND_START = re.compile(r"\b(?:найдите|найти|исследуйте|исследовать)\b", re.IGNORECASE)
_COUNT_PREFIX = re.compile(r"^(?P<count>[1-9]\d*)\s+(?P<name>.+)$")


def parse_area_object_plan(entry: ActiveQuestEntry) -> AreaObjectPlan:
    """Extract area-object requirements after an explicit find/explore verb.

    A location is accepted only when it is backed by an exact quest navigation
    target.  The parser deliberately does not infer locations from free text.
    """

    if not isinstance(entry, ActiveQuestEntry):
        return AreaObjectPlan(AreaObjectPlanStatus.UNSAFE, None, None, (), "entry_invalid")
    objective = entry.data.get("objective")
    navigation = entry.data.get("navigation")
    if not isinstance(objective, str) or objective != objective.strip() or not objective:
        return AreaObjectPlan(AreaObjectPlanStatus.UNSAFE, entry.id, entry.title, (), "objective_invalid")
    if not isinstance(navigation, (tuple, list)):
        return AreaObjectPlan(AreaObjectPlanStatus.UNSAFE, entry.id, entry.title, (), "navigation_invalid")
    start = _FIND_START.search(objective)
    if start is None:
        return AreaObjectPlan(AreaObjectPlanStatus.UNSUPPORTED, entry.id, entry.title, (), "find_verb_missing")
    locations = _locations(navigation)
    if not locations:
        return AreaObjectPlan(AreaObjectPlanStatus.UNSAFE, entry.id, entry.title, (), "area_location_unbound")
    clause = objective[start.start():]
    requirements: list[AreaObjectRequirement] = []
    for location, aliases in locations:
        requirement = _requirement_for_location(clause, location, aliases)
        if requirement is not None:
            requirements.append(requirement)
    if not requirements:
        return AreaObjectPlan(AreaObjectPlanStatus.UNSUPPORTED, entry.id, entry.title, (), "area_requirements_unparsed")
    keys = {(item.resource_name.casefold(), item.location.casefold()) for item in requirements}
    if len(keys) != len(requirements):
        return AreaObjectPlan(AreaObjectPlanStatus.UNSAFE, entry.id, entry.title, (), "duplicate_area_requirement")
    return AreaObjectPlan(
        AreaObjectPlanStatus.READY,
        entry.id,
        entry.title,
        tuple(requirements),
        "area_object_plan_ready",
    )


def _locations(navigation: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result: list[tuple[str, tuple[str, ...]]] = []
    assert isinstance(navigation, (tuple, list))
    for item in navigation:
        if not isinstance(item, Mapping):
            continue
        target = clean_quest_route_label(item.get("target") or item.get("text"))
        if not target or "[" in target or any(target == value[0] for value in result):
            continue
        aliases = tuple(
            value
            for value in (
                target,
                clean_quest_route_label(item.get("text")),
            )
            if value
        )
        result.append((target, tuple(dict.fromkeys(aliases))))
    return tuple(result)


def _requirement_for_location(
    text: str, location: str, aliases: tuple[str, ...]
) -> AreaObjectRequirement | None:
    escaped = "|".join(re.escape(value) for value in aliases)
    # ``найдите в Локации Ресурс`` and subsequent ``в Локации Ресурс`` items.
    after_location = re.search(
        rf"(?:^|[,;]\s*|\bи\s+|\b(?:найдите|найти|исследуйте|исследовать)\s+)(?:в|на)\s+(?:{escaped})\s+(?P<resource>[А-ЯЁA-Z][^,.;]+?)(?=(?:,|;|\.|$|\s+и\s+(?:[1-9]\d*\s+)?[А-ЯЁA-Z]))",
        text,
        re.IGNORECASE,
    )
    if after_location is not None:
        return _make_requirement(after_location.group("resource"), location)
    # ``5 Ресурса на Локации``.  It must be introduced by the find clause;
    # otherwise a delivery location at the end of a card could be mistaken for
    # an object-search location.
    for fragment in re.split(r"[,;.]|\bи\s+", text, flags=re.IGNORECASE):
        fragment = fragment.strip(" .;")
        before_location = re.fullmatch(
            rf"\s*(?P<resource>(?:[1-9]\d*\s+)?[А-ЯЁA-Z][^,.;]+?)\s+(?:в|на)\s+(?:{escaped})\s*",
            fragment,
            re.IGNORECASE,
        )
        if before_location is not None:
            return _make_requirement(before_location.group("resource"), location)
    return None


def _make_requirement(raw_resource: str, location: str) -> AreaObjectRequirement | None:
    value = " ".join(raw_resource.strip(" ,.;").split())
    if not value or len(value) > 180:
        return None
    count = 1
    match = _COUNT_PREFIX.fullmatch(value)
    if match is not None:
        count = int(match.group("count"))
        value = " ".join(match.group("name").split())
    if not value or count <= 0:
        return None
    return AreaObjectRequirement(value, count, location)


def area_object_progress(
    plan: AreaObjectPlan,
    items: object,
) -> AreaObjectProgress:
    """Select the first unmet map-object requirement from a quest inventory.

    The caller must pass a confirmed quest-category inventory snapshot; this
    pure helper intentionally treats malformed entries as absent rather than
    guessing from a similarly named ordinary item.
    """

    counts: dict[str, int] = {}
    if isinstance(items, (tuple, list)):
        for item in items:
            if not isinstance(item, Mapping):
                continue
            name = item.get("artAltTitle") or item.get("name")
            count = item.get("count")
            if not isinstance(name, str) or not name.strip():
                continue
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                continue
            key = _resource_key(name)
            if key:
                counts[key] = counts.get(key, 0) + count
    collected: list[tuple[str, int]] = []
    next_requirement = None
    for requirement in plan.requirements:
        count = counts.get(_resource_key(requirement.resource_name), 0)
        collected.append((requirement.resource_name, count))
        if next_requirement is None and count < requirement.required:
            next_requirement = requirement
    return AreaObjectProgress(next_requirement is None, tuple(collected), next_requirement)


def _resource_key(value: object) -> str:
    words = re.findall(r"[а-яёa-z0-9]+", str(value or "").casefold().replace("ё", "е"))
    result: list[str] = []
    for word in words:
        for ending in (
            "иями", "ями", "ами", "ого", "ему", "ому", "его", "иях", "ах", "ях",
            "ов", "ев", "ей", "ый", "ий", "ая", "яя", "ую", "юю", "ом", "ем",
            "а", "я", "у", "ю", "ы", "и", "е", "о", "ь", "й",
        ):
            if word.endswith(ending) and len(word) - len(ending) >= 3:
                word = word[: -len(ending)]
                break
        result.append(word)
    return " ".join(result)
