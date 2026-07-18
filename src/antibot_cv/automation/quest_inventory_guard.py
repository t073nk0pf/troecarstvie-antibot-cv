"""Pure pre-combat guard for quest items already present in the backpack."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from src.antibot_cv.automation.gathering_activity_runtime import (
    GatheringActivity,
    GatheringPlan,
    GatheringPlanStatus,
    GatheringRequirement,
    gathering_progress,
)
from src.antibot_cv.automation.quest_objective_runtime import QuestObjective
from src.antibot_cv.automation.quest_chat_progress import resource_matches_objective


@dataclass(frozen=True)
class QuestInventoryGuardResult:
    confirmed: bool
    complete: bool
    requirements: tuple[GatheringRequirement, ...]
    collected: tuple[tuple[str, int], ...]
    reason: str


@dataclass(frozen=True)
class QuestInventoryCompletionEvidence:
    quest_id: str
    quest_title: str
    fingerprint: str
    collected: tuple[tuple[str, int], ...]
    terminal_collection: bool = True


_COUNTED = re.compile(
    r"(?:получите|добудьте|соберите|найдите|принесите)\s+"
    r"(?P<count>[1-9]\d*)\s+(?P<name>[^,.;]+?)"
    r"(?=\s+и\s+(?:возвращ|вернит|отнес|переда)|[,.;]|$)",
    re.IGNORECASE,
)
_SINGULAR = re.compile(
    r"(?:получите|добудьте|найдите|снимите)\s+"
    r"(?P<name>[^,.;]+?)"
    r"(?=(?:,\s*)?(?:после чего|и)\s+(?:возвращ|вернит|отнес|переда)|[.;]|$)",
    re.IGNORECASE,
)


def quest_item_requirements(objective: QuestObjective | None) -> tuple[GatheringRequirement, ...]:
    if objective is None or not isinstance(objective.objective, str):
        return ()
    text = " ".join(objective.objective.split())
    requirements: list[GatheringRequirement] = []
    occupied: list[tuple[int, int]] = []
    for match in _COUNTED.finditer(text):
        name = _clean_name(match.group("name"))
        if name:
            requirements.append(GatheringRequirement(name, int(match.group("count"))))
            occupied.append(match.span())
    for match in _SINGULAR.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        name = _clean_name(match.group("name"))
        if name:
            requirements.append(GatheringRequirement(name, 1))
    unique = {(item.name.casefold(), item.required) for item in requirements}
    return tuple(requirements) if len(unique) == len(requirements) else ()


def evaluate_quest_inventory(
    objective: QuestObjective | None,
    snapshot: object,
) -> QuestInventoryGuardResult:
    requirements = quest_item_requirements(objective)
    if objective is None:
        return QuestInventoryGuardResult(False, False, (), (), "quest_objective_missing")
    if not isinstance(snapshot, Mapping) or snapshot.get("ok") is not True:
        return QuestInventoryGuardResult(False, False, requirements, (), "inventory_snapshot_invalid")
    if snapshot.get("category") != "quest" or snapshot.get("categoryConfirmed") is not True:
        return QuestInventoryGuardResult(False, False, requirements, (), "quest_inventory_category_unconfirmed")
    if snapshot.get("truncated") is True:
        return QuestInventoryGuardResult(False, False, requirements, (), "quest_inventory_truncated")
    raw_items = snapshot.get("items", snapshot.get("sample"))
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        return QuestInventoryGuardResult(False, False, requirements, (), "quest_inventory_items_invalid")
    items: list[dict[str, object]] = []
    semantic_items: list[tuple[str, int, str]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            return QuestInventoryGuardResult(False, False, requirements, (), "quest_inventory_item_invalid")
        name = raw.get("artAltTitle") or raw.get("name")
        description = raw.get("artAltDescription") or ""
        count = raw.get("count")
        if not isinstance(name, str) or not name.strip():
            continue
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return QuestInventoryGuardResult(False, False, requirements, (), "quest_inventory_count_invalid")
        items.append({"name": name.strip(), "count": count})
        semantic_items.append((name.strip(), count, description if isinstance(description, str) else ""))
    if len(requirements) == 1 and _COUNTED.search(" ".join(objective.objective.split())) is None:
        description_matches = [
            name
            for name, _, description in semantic_items
            if _description_binds_step(
                description,
                quest_title=objective.quest_title,
                item_name=name,
                objective_text=objective.objective,
            )
        ]
        if len(description_matches) == 1:
            requirements = (
                GatheringRequirement(description_matches[0], requirements[0].required),
            )
    # The objective uses an inflected generic noun (for example, "10
    # бивней"), while the backpack stores the concrete trophy title
    # ("Бивень кабана-секача"). Bind that requirement only when the lexical
    # match is unique; ambiguity remains fail-closed.
    requirements = _resolve_qualified_inventory_names(
        requirements, semantic_items, objective.monster.name
    )
    if not requirements:
        matched = [
            (name, count)
            for name, count, description in semantic_items
            if resource_matches_objective(name, objective.objective)
            or _description_binds_step(
                description,
                quest_title=objective.quest_title,
                item_name=name,
                objective_text=objective.objective,
            )
        ]
        if len(matched) > 1:
            return QuestInventoryGuardResult(
                False, False, (), (), "quest_inventory_item_binding_ambiguous"
            )
        if not matched:
            return QuestInventoryGuardResult(
                True, False, (), (), "matching_quest_item_absent"
            )
        requirements = (GatheringRequirement(matched[0][0], 1),)
    plan = GatheringPlan(
        GatheringPlanStatus.READY,
        objective.quest_id if objective is not None else None,
        objective.quest_title if objective is not None else None,
        GatheringActivity.GENERIC,
        requirements,
        "quest_inventory_guard",
    )
    progress = gathering_progress(plan, items)
    return QuestInventoryGuardResult(
        True,
        progress.complete,
        requirements,
        progress.collected,
        "quest_items_complete" if progress.complete else "quest_items_missing",
    )


def _resolve_qualified_inventory_names(
    requirements: Sequence[GatheringRequirement],
    items: Sequence[tuple[str, int, str]],
    monster_name: str,
) -> tuple[GatheringRequirement, ...]:
    resolved: list[GatheringRequirement] = []
    monster_tokens = _lexemes(monster_name)
    for requirement in requirements:
        # Container/measure words describe the required quantity, not the
        # trophy title stored by inventory (for example, "пузырьков крови").
        requirement_tokens = tuple(
            token
            for token in _lexemes(requirement.name)
            if not token.startswith("пузыр")
        )
        candidates = [
            name
            for name, _, _ in items
            if _qualified_name_matches(
                requirement_tokens, _lexemes(name), monster_tokens
            )
        ]
        if len(candidates) == 1:
            resolved.append(GatheringRequirement(candidates[0], requirement.required))
        else:
            resolved.append(requirement)
    return tuple(resolved)


def _qualified_name_matches(
    requirement_tokens: Sequence[str],
    item_tokens: Sequence[str],
    monster_tokens: Sequence[str],
) -> bool:
    if not requirement_tokens or not item_tokens:
        return False
    if len(requirement_tokens) > 1:
        requirement_matches = all(
            any(_lexeme_equal(token, candidate) for candidate in item_tokens)
            for token in requirement_tokens
        )
    else:
        requirement_matches = any(
            _lexeme_equal(requirement_tokens[0], candidate) for candidate in item_tokens
        )
    if not requirement_matches:
        return False
    if not any(token == "кров" for token in requirement_tokens):
        return True
    qualifiers = tuple(
        token
        for token in item_tokens
        if not any(_lexeme_equal(token, required) for required in requirement_tokens)
    )
    return len(qualifiers) == len(monster_tokens) and all(
        any(_lexeme_equal(token, monster) for monster in monster_tokens)
        for token in qualifiers
    )


def _lexeme_equal(left: str, right: str) -> bool:
    if left == right:
        return True
    # Russian declensions such as "бивней"/"бивень" and
    # "когтей"/"коготь" can drop one epenthetic vowel.  Compare a small,
    # explicit set of stem variants; never fall back to a substring match.
    return not _stem_variants(left).isdisjoint(_stem_variants(right))


def _stem_variants(value: str) -> set[str]:
    variants = {value}
    if "ен" in value:
        variants.add(value.replace("ен", "н"))
    if value.endswith("от") and len(value) >= 4:
        variants.add(value[:-2] + "т")
    return variants


def _lexemes(value: str) -> tuple[str, ...]:
    return tuple(
        _stem_word(token)
        for token in re.findall(r"[а-яa-z0-9]+", value.casefold().replace("ё", "е"))
        if len(token) >= 3
    )


def _clean_name(value: str) -> str:
    name = " ".join(value.strip(" ,.;").split())
    return name if 2 <= len(name) <= 180 else ""


def _description_binds_step(
    description: str,
    *,
    quest_title: str,
    item_name: str,
    objective_text: str,
) -> bool:
    normalized_description = " ".join(description.casefold().replace("ё", "е").split())
    normalized_title = " ".join(quest_title.casefold().replace("ё", "е").split())
    if not normalized_title or normalized_title not in normalized_description:
        return False
    item_terms = {_stem_word(value) for value in re.findall(r"[а-яa-z0-9]+", item_name.casefold().replace("ё", "е"))}
    objective_terms = {
        _stem_word(value)
        for value in re.findall(r"[а-яa-z0-9]+", objective_text.casefold().replace("ё", "е"))
    }
    shared = {value for value in item_terms & objective_terms if len(value) >= 4}
    return len(shared) == 1


def _stem_word(value: str) -> str:
    """Return a conservative common stem for Russian trophy names.

    Quest cards and inventory often use different forms of the same short
    noun (``уха`` / ``Ухо рыси``).  Keeping at least two letters is enough for
    those nouns while the caller still requires a *unique* full item match,
    so an inflection never authorizes a choice between several trophies.
    """

    normalized = value.casefold().replace("ё", "е")
    endings = (
        "иями", "ями", "ами", "иями", "его", "ого", "ему", "ому", "ией", "ией",
        "иях", "ах", "ях", "ов", "ев", "ей", "ых", "их", "ия", "ие", "ию", "иям", "ием",
        "ой", "ей", "ый", "ий", "ая", "яя", "ую", "юю", "ом", "ем", "ам", "ям",
        "ы", "и", "а", "я", "у", "ю", "е", "о", "ь", "й",
    )
    for ending in endings:
        if normalized.endswith(ending) and len(normalized) - len(ending) >= 2:
            return normalized[: -len(ending)].rstrip("ьй")
    return normalized.rstrip("ьй")
