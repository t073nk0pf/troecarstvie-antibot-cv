"""Parse exact quest-item use requirements without owning execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re


_USE_ITEM = re.compile(
    r"^\s*Используя\s+(?P<item>[^,]{2,180}),\s*(?P<result>.+?)"
    r"(?:\s+Наточив\b|\s+После\b|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuestUseItemPlan:
    quest_id: str
    quest_title: str
    item_name: str
    expected_result: str
    result_item_name: str | None = None


_RESULT_ITEM_BY_QUEST_ID = {
    # Authoritative inventory result of using the sharpening stone.  The
    # active-card wording does not advance, so the quest inventory owns the
    # transition to turn-in.
    "108": "Наточенный топор Рокоша",
}


def parse_quest_use_item_plan(entry: object) -> QuestUseItemPlan | None:
    quest_id = str(getattr(entry, "id", "") or "").strip()
    title = str(getattr(entry, "title", "") or "").strip()
    data = getattr(entry, "data", None)
    objective = str(data.get("objective") if isinstance(data, Mapping) else getattr(entry, "objective", "") or "").strip()
    match = _USE_ITEM.match(objective)
    if not quest_id.isdecimal() or not title or match is None:
        return None
    item = " ".join(match.group("item").split())
    result = " ".join(match.group("result").split()).rstrip(". ")
    if not item or not result:
        return None
    return QuestUseItemPlan(
        quest_id, title, item, result, _RESULT_ITEM_BY_QUEST_ID.get(quest_id)
    )
