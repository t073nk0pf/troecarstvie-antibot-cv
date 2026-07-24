"""Recover a strict turn-in reference from an authoritative active objective."""

from __future__ import annotations

from collections.abc import Mapping
import re

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_dialogue_runtime import (
    QuestDialogueError,
    parse_dialogue_objective,
)
from src.antibot_cv.automation.runtime_helpers import same_location_name
from src.antibot_cv.automation.quest_return_clause import (
    DELIVERY_VERB_PATTERN,
    RETURN_VERB_PATTERN,
)


_MONSTER_TARGET = re.compile(r"^.+\s\[[1-9]\d*\]$")
_RETURN_NPC_FIRST = re.compile(
    rf"{RETURN_VERB_PATTERN}\s+"
    r"к\s+(?P<npc>.+?)\s+(?:в|во|на)\s+(?P<location>[^.;]+)[.!]?",
    re.IGNORECASE,
)
_RETURN_LOCATION_FIRST = re.compile(
    rf"{RETURN_VERB_PATTERN}\s+"
    r"(?:в|во|на)\s+(?P<location>.+?)\s+к\s+(?P<npc>[^.;]+)[.!]?",
    re.IGNORECASE,
)
_DELIVER_NPC_FIRST = re.compile(
    rf"{DELIVERY_VERB_PATTERN}\s+.+?\s+"
    r"(?P<npc>[^.;]+?)\s+(?:в|во|на)\s+(?P<location>[^.;]+)[.!]?",
    re.IGNORECASE,
)
_RETURN_ITEM_NPC_FIRST = re.compile(
    r"верните\s+.+?\s+"
    r"(?P<npc>ремесленнику\s+[^.;]+?)\s+(?:в|во|на)\s+"
    r"(?P<location>[^.;]+)[.!]?",
    re.IGNORECASE,
)


def derive_turn_in_ref(entry: ActiveQuestEntry) -> QuestRef | None:
    """Return one body-bound NPC/location reference or fail closed."""

    if not isinstance(entry, ActiveQuestEntry):
        return None
    objective = entry.data.get("objective")
    navigation = entry.data.get("navigation")
    if not isinstance(objective, str) or not objective.strip() or not isinstance(navigation, (tuple, list)):
        return None

    locations = []
    for item in navigation:
        if not isinstance(item, Mapping):
            continue
        target = str(item.get("target") or "").strip()
        if target and not _MONSTER_TARGET.fullmatch(target) and target not in locations:
            locations.append(target)
    if len(locations) != 1:
        return _dialogue_shaped_turn_in_ref(entry)

    matches = [
        *_RETURN_NPC_FIRST.finditer(objective),
        *_RETURN_LOCATION_FIRST.finditer(objective),
        *_DELIVER_NPC_FIRST.finditer(objective),
        *_RETURN_ITEM_NPC_FIRST.finditer(objective),
    ]
    if len(matches) != 1:
        return _dialogue_shaped_turn_in_ref(entry)
    npc = _canonicalize_npc_role(_clean(matches[0].group("npc")))
    stated_location = _clean(matches[0].group("location"))
    location = locations[0]
    if not npc or not stated_location or not same_location_name(stated_location, location):
        return _dialogue_shaped_turn_in_ref(entry)
    return QuestRef(entry.id, entry.title, None, location, (npc,), None)


def _dialogue_shaped_turn_in_ref(entry: ActiveQuestEntry) -> QuestRef | None:
    """Reuse strict NPC/location binding for a confirmed TURN_IN caller."""

    try:
        objective = parse_dialogue_objective(entry)
    except QuestDialogueError:
        return None
    return QuestRef(
        objective.quest_id,
        objective.quest_title,
        None,
        objective.location,
        (objective.npc_query,),
        None,
    )


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ,.!\t\r\n")


def _canonicalize_npc_role(value: str) -> str:
    return re.sub(
        r"^ремесленнику\b", "Ремесленник", value, flags=re.IGNORECASE
    )
