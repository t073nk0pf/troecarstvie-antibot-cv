"""Pure, fail-closed selection of supported active-quest objectives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Mapping, Sequence

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry


class ObjectiveKind(str, Enum):
    MONSTER_HUNT = "monster_hunt"


class ObjectiveSelectionStatus(str, Enum):
    SELECTED = "selected"
    NONE_SUPPORTED = "none_supported"
    UNSAFE = "unsafe"


class ObjectiveRefreshState(str, Enum):
    SAME_STEP = "same_step"
    ADVANCED = "advanced"
    QUEST_REMOVED = "quest_removed"
    REGRESSED_UNSAFE = "regressed_unsafe"


@dataclass(frozen=True)
class MonsterTarget:
    """Exact navigator identity plus the parsed display name and level."""

    target: str
    name: str
    level: int


@dataclass(frozen=True)
class QuestObjective:
    """One immutable, snapshot-bound monster-hunt step."""

    kind: ObjectiveKind
    quest_id: str
    quest_title: str
    objective: str
    fingerprint: str
    monster: MonsterTarget
    navigator_label: str
    progress: int | None
    required: int | None
    complete: bool
    source_order: int


@dataclass(frozen=True)
class ObjectiveSelection:
    status: ObjectiveSelectionStatus
    objective: QuestObjective | None
    reason: str


@dataclass(frozen=True)
class ObjectiveRefreshComparison:
    state: ObjectiveRefreshState
    refreshed: QuestObjective | None
    reason: str


_MONSTER_TARGET = re.compile(r"^(.+\S) \[([1-9]\d*)\]$")
_PROGRESS_RATIO = re.compile(r"(?<!\d)\d+\s*/\s*[1-9]\d*(?!\d)")


def select_monster_hunt_objective(
    entries: Sequence[ActiveQuestEntry], *, current_level_cap: int
) -> ObjectiveSelection:
    """Select the first supported quest from a complete active catalogue.

    ``entries`` must be the terminal ``ActiveQuestCatalogAccumulator.result``.
    The accumulator's stable flattened order is retained as ``source_order``.
    """

    if not _positive_int(current_level_cap):
        return ObjectiveSelection(ObjectiveSelectionStatus.UNSAFE, None, "invalid_current_level_cap")
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        return ObjectiveSelection(ObjectiveSelectionStatus.UNSAFE, None, "active_catalog_invalid")

    seen: set[str] = set()
    supported: list[QuestObjective] = []
    unsafe_reasons: list[str] = []
    for order, entry in enumerate(entries):
        if not isinstance(entry, ActiveQuestEntry):
            return ObjectiveSelection(ObjectiveSelectionStatus.UNSAFE, None, "active_catalog_invalid")
        if entry.id in seen:
            return ObjectiveSelection(ObjectiveSelectionStatus.UNSAFE, None, "duplicate_quest_id")
        seen.add(entry.id)
        parsed, reason = _parse_monster_objective(entry, source_order=order)
        if parsed is None:
            if reason.startswith("unsupported_"):
                continue
            unsafe_reasons.append(f"quest_{entry.id}:{reason}")
            continue
        if parsed.monster.level > current_level_cap:
            unsafe_reasons.append(f"quest_{entry.id}:monster_above_level_cap")
            continue
        supported.append(parsed)

    if supported:
        selected = min(supported, key=lambda item: (item.source_order, int(item.quest_id)))
        return ObjectiveSelection(ObjectiveSelectionStatus.SELECTED, selected, "monster_hunt_selected")
    if unsafe_reasons:
        return ObjectiveSelection(ObjectiveSelectionStatus.UNSAFE, None, unsafe_reasons[0])
    return ObjectiveSelection(ObjectiveSelectionStatus.NONE_SUPPORTED, None, "no_supported_monster_hunt")


def compare_refreshed_objective(
    previous: QuestObjective,
    refreshed_entries: Sequence[ActiveQuestEntry],
    *,
    current_level_cap: int,
) -> ObjectiveRefreshComparison:
    """Compare a selected step with the same quest in a complete refresh."""

    if not isinstance(previous, QuestObjective) or not _positive_int(current_level_cap):
        return ObjectiveRefreshComparison(ObjectiveRefreshState.REGRESSED_UNSAFE, None, "invalid_comparison_input")
    if isinstance(refreshed_entries, (str, bytes)) or not isinstance(refreshed_entries, Sequence):
        return ObjectiveRefreshComparison(ObjectiveRefreshState.REGRESSED_UNSAFE, None, "active_catalog_invalid")
    matches: list[tuple[int, ActiveQuestEntry]] = []
    seen: set[str] = set()
    for order, entry in enumerate(refreshed_entries):
        if not isinstance(entry, ActiveQuestEntry) or entry.id in seen:
            return ObjectiveRefreshComparison(ObjectiveRefreshState.REGRESSED_UNSAFE, None, "active_catalog_invalid_or_duplicate")
        seen.add(entry.id)
        if entry.id == previous.quest_id:
            matches.append((order, entry))
    if not matches:
        return ObjectiveRefreshComparison(ObjectiveRefreshState.QUEST_REMOVED, None, "quest_removed_from_active_catalog")

    order, entry = matches[0]
    if entry.title != previous.quest_title:
        return ObjectiveRefreshComparison(ObjectiveRefreshState.REGRESSED_UNSAFE, None, "quest_identity_changed")
    refreshed, reason = _parse_monster_objective(entry, source_order=order)
    if refreshed is None or refreshed.monster.level > current_level_cap:
        return ObjectiveRefreshComparison(ObjectiveRefreshState.REGRESSED_UNSAFE, None, reason)
    if refreshed.required != previous.required and None not in (refreshed.required, previous.required):
        return ObjectiveRefreshComparison(ObjectiveRefreshState.REGRESSED_UNSAFE, refreshed, "required_count_changed")
    if refreshed.fingerprint != previous.fingerprint:
        return ObjectiveRefreshComparison(ObjectiveRefreshState.ADVANCED, refreshed, "quest_step_fingerprint_changed")
    if previous.complete and not refreshed.complete:
        return ObjectiveRefreshComparison(ObjectiveRefreshState.REGRESSED_UNSAFE, refreshed, "quest_completion_regressed")
    if previous.progress is not None and refreshed.progress is not None:
        if refreshed.progress < previous.progress:
            return ObjectiveRefreshComparison(ObjectiveRefreshState.REGRESSED_UNSAFE, refreshed, "quest_progress_regressed")
    if refreshed.complete or (
        refreshed.progress is not None
        and refreshed.required is not None
        and refreshed.progress >= refreshed.required
    ):
        return ObjectiveRefreshComparison(ObjectiveRefreshState.ADVANCED, refreshed, "quest_step_completed")
    return ObjectiveRefreshComparison(ObjectiveRefreshState.SAME_STEP, refreshed, "quest_step_unchanged")


def _parse_monster_objective(
    entry: ActiveQuestEntry, *, source_order: int
) -> tuple[QuestObjective | None, str]:
    data = entry.data
    if not isinstance(data, Mapping) or data.get("status") != "active":
        return None, "unsafe_active_identity"
    if data.get("id") != entry.id or data.get("title") != entry.title:
        return None, "unsafe_active_identity"
    objective = data.get("objective")
    if not isinstance(objective, str) or objective != objective.strip() or not objective:
        return None, "unsafe_missing_objective"
    navigation = data.get("navigation")
    if not isinstance(navigation, (tuple, list)) or not navigation:
        return None, "unsupported_navigation_missing"

    candidates: list[tuple[int, MonsterTarget, str]] = []
    fingerprint_navigation: list[tuple[str, str]] = []
    for index, raw in enumerate(navigation):
        if not isinstance(raw, Mapping):
            return None, "unsafe_navigation_mapping"
        target = raw.get("target")
        label = raw.get("text")
        if target is None:
            continue
        if not isinstance(target, str) or target != target.strip() or not target:
            return None, "unsafe_navigation_target"
        if not isinstance(label, str) or label != label.strip() or not label:
            return None, "unsafe_navigation_label"
        fingerprint_navigation.append((target, label))
        match = _MONSTER_TARGET.fullmatch(target)
        if match:
            candidates.append(
                (index, MonsterTarget(target, match.group(1), int(match.group(2))), label)
            )

    if not candidates:
        return None, "unsupported_monster_target_missing"
    if candidates[0][0] != 0:
        return None, "unsupported_monster_target_not_current_step"
    identities = {(candidate.target, label) for _, candidate, label in candidates}
    if len(candidates) != 1 or len(identities) != 1:
        return None, "unsafe_ambiguous_monster_target"
    _, monster, navigator_label = candidates[0]
    progress, required, complete, progress_reason = _parse_progress(data.get("progress"))
    if progress_reason:
        return None, progress_reason

    canonical_objective = _PROGRESS_RATIO.sub("#/#", objective)
    encoded = json.dumps(
        {
            "quest_id": entry.id,
            "quest_title": entry.title,
            "objective": canonical_objective,
            "monster_target": monster.target,
            "navigator_label": navigator_label,
            "navigation": fingerprint_navigation,
            "required": required,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    return (
        QuestObjective(
            ObjectiveKind.MONSTER_HUNT,
            entry.id,
            entry.title,
            objective,
            fingerprint,
            monster,
            navigator_label,
            progress,
            required,
            complete,
            source_order,
        ),
        "monster_hunt_supported",
    )


def _parse_progress(raw: object) -> tuple[int | None, int | None, bool, str | None]:
    if raw is None:
        return None, None, False, None
    if not isinstance(raw, Mapping):
        return None, None, False, "unsafe_progress_mapping"
    complete = raw.get("complete", False)
    if not isinstance(complete, bool):
        return None, None, False, "unsafe_progress_complete"
    current = raw.get("current")
    required = raw.get("required")
    if current is None and required is None:
        return None, None, complete, None
    if not _non_negative_int(current) or not _positive_int(required):
        return None, None, False, "unsafe_progress_values"
    if current > required:
        return None, None, False, "unsafe_progress_values"
    return current, required, complete, None


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
