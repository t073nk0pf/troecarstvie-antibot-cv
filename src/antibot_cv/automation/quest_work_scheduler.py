"""Pure scheduling of quest work that is already in the current location.

The scheduler deliberately does not plan routes.  It receives exact location
identities from an orchestrator and only returns targets whose
``current_location_id`` equals the observed location.  This makes side-quest
work opportunistic: it can save fights while the character is already there,
but can never pull the character away from the active quest chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class LocalQuestTarget:
    """One exact monster target contributing to one active quest."""

    quest_id: str
    quest_title: str
    current_location_id: str
    target_id: str
    target_name: str
    primary_chain: bool = False
    source_order: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "quest_id",
            "quest_title",
            "current_location_id",
            "target_id",
            "target_name",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{field_name} must be a non-empty stripped string")
        if not self.quest_id.isdecimal() or int(self.quest_id) <= 0:
            raise ValueError("quest_id must be a positive decimal identity")
        if not self.current_location_id.isdecimal() or int(self.current_location_id) < 0:
            raise ValueError("current_location_id must be an exact decimal identity")
        if not self.target_id.isdecimal() or int(self.target_id) <= 0:
            raise ValueError("target_id must be a positive decimal identity")
        if isinstance(self.source_order, bool) or not isinstance(self.source_order, int):
            raise ValueError("source_order must be an integer")
        if self.source_order < 0:
            raise ValueError("source_order must not be negative")

    @property
    def target_key(self) -> tuple[str, str]:
        """Exact location-scoped identity used to merge quest requirements."""

        return self.current_location_id, self.target_id


@dataclass(frozen=True)
class LocationWorkPlan:
    """Deterministic, route-free work available at one exact location."""

    current_location_id: str
    primary_targets: tuple[LocalQuestTarget, ...]
    opportunistic_targets: tuple[LocalQuestTarget, ...]
    selected_target: LocalQuestTarget | None
    selected_quest_ids: tuple[str, ...]
    reason: str
    may_initiate_route: bool = False

    @property
    def side_targets(self) -> tuple[LocalQuestTarget, ...]:
        """Compatibility/readability alias for opportunistic targets."""

        return self.opportunistic_targets

    @property
    def multi_quest(self) -> bool:
        return len(self.selected_quest_ids) > 1


class QuestWorkScheduler:
    """Choose local primary and opportunistic fights under bounded budgets."""

    def __init__(
        self,
        *,
        max_consecutive_side_victories: int = 2,
        max_side_victories_per_location_visit: int = 8,
        max_no_progress_victories: int = 3,
    ) -> None:
        for name, value in (
            ("max_consecutive_side_victories", max_consecutive_side_victories),
            ("max_side_victories_per_location_visit", max_side_victories_per_location_visit),
            ("max_no_progress_victories", max_no_progress_victories),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self.max_consecutive_side_victories = max_consecutive_side_victories
        self.max_side_victories_per_location_visit = max_side_victories_per_location_visit
        self.max_no_progress_victories = max_no_progress_victories
        self._location_id: str | None = None
        self._side_victories_this_visit = 0
        self._consecutive_side_victories = 0
        self._no_progress_by_target: dict[tuple[str, str], int] = {}
        self._selected_key: tuple[str, str] | None = None
        self._selected_is_side = False

    @property
    def consecutive_side_victories(self) -> int:
        return self._consecutive_side_victories

    @property
    def side_victories_this_visit(self) -> int:
        return self._side_victories_this_visit

    def begin_location_visit(self, current_location_id: str) -> None:
        """Start a visit explicitly, resetting only the per-visit side budget."""

        location_id = _location_identity(current_location_id)
        self._location_id = location_id
        self._side_victories_this_visit = 0
        self._selected_key = None
        self._selected_is_side = False

    def build_plan(
        self,
        current_location_id: str,
        targets: Iterable[LocalQuestTarget],
    ) -> LocationWorkPlan:
        """Build a plan from exact local targets without proposing travel."""

        location_id = _location_identity(current_location_id)
        if self._location_id != location_id:
            self.begin_location_visit(location_id)
        if isinstance(targets, (str, bytes)):
            raise ValueError("targets must contain LocalQuestTarget values")
        values = tuple(targets)
        if any(not isinstance(target, LocalQuestTarget) for target in values):
            raise ValueError("targets must contain LocalQuestTarget values")

        local = tuple(
            sorted(
                (target for target in values if target.current_location_id == location_id),
                key=_target_order,
            )
        )
        primary = tuple(target for target in local if target.primary_chain)
        side = tuple(target for target in local if not target.primary_chain)
        groups = _groups(local)
        eligible = [
            group
            for group in groups
            if self._no_progress_by_target.get(group[0].target_key, 0)
            < self.max_no_progress_victories
        ]

        side_budget_open = (
            self._consecutive_side_victories < self.max_consecutive_side_victories
            and self._side_victories_this_visit < self.max_side_victories_per_location_visit
        )
        if not side_budget_open:
            eligible = [group for group in eligible if any(item.primary_chain for item in group)]

        if not eligible:
            self._selected_key = None
            self._selected_is_side = False
            if not local:
                reason = "no_exact_local_targets"
            elif groups and all(
                self._no_progress_by_target.get(group[0].target_key, 0)
                >= self.max_no_progress_victories
                for group in groups
            ):
                reason = "local_targets_exhausted_no_progress_budget"
            else:
                reason = "side_victory_budget_exhausted"
            return LocationWorkPlan(location_id, primary, side, None, (), reason)

        # A shared monster advances the greatest number of quests.  Ties prefer
        # the active chain, then stable catalogue order and numeric identities.
        selected_group = min(eligible, key=_group_order)
        selected = min(
            selected_group,
            key=lambda item: (not item.primary_chain, *_target_order(item)),
        )
        quest_ids = tuple(
            target.quest_id
            for target in sorted(
                selected_group,
                key=lambda item: (item.source_order, int(item.quest_id), item.quest_title),
            )
        )
        self._selected_key = selected.target_key
        self._selected_is_side = not any(item.primary_chain for item in selected_group)
        if len(set(quest_ids)) > 1:
            reason = "multi_quest_local_target"
        elif selected.primary_chain:
            reason = "primary_chain_local_target"
        else:
            reason = "opportunistic_local_target"
        return LocationWorkPlan(
            location_id,
            primary,
            side,
            selected,
            tuple(dict.fromkeys(quest_ids)),
            reason,
        )

    def acknowledge_victory(self, *, progressed: bool) -> None:
        """Account for the last selection and its subsequent quest refresh."""

        if self._selected_key is None:
            raise RuntimeError("there is no selected local target to acknowledge")
        if not isinstance(progressed, bool):
            raise ValueError("progressed must be a boolean")
        if self._selected_is_side:
            self._consecutive_side_victories += 1
            self._side_victories_this_visit += 1
        else:
            self._consecutive_side_victories = 0
        if progressed:
            self._no_progress_by_target.pop(self._selected_key, None)
        else:
            self._no_progress_by_target[self._selected_key] = (
                self._no_progress_by_target.get(self._selected_key, 0) + 1
            )
        self._selected_key = None
        self._selected_is_side = False

    def acknowledge_progress(self, target: LocalQuestTarget) -> None:
        """Reset stale no-progress evidence after an independent quest refresh."""

        if not isinstance(target, LocalQuestTarget):
            raise ValueError("target must be a LocalQuestTarget")
        self._no_progress_by_target.pop(target.target_key, None)


def _location_identity(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("current_location_id must be an exact decimal identity")
    if not value.isdecimal() or int(value) < 0:
        raise ValueError("current_location_id must be an exact decimal identity")
    return value


def _target_order(target: LocalQuestTarget) -> tuple[int, int, int, str]:
    return target.source_order, int(target.quest_id), int(target.target_id), target.target_name


def _groups(
    targets: tuple[LocalQuestTarget, ...],
) -> tuple[tuple[LocalQuestTarget, ...], ...]:
    grouped: dict[tuple[str, str], list[LocalQuestTarget]] = {}
    for target in targets:
        grouped.setdefault(target.target_key, []).append(target)
    return tuple(tuple(items) for items in grouped.values())


def _group_order(group: tuple[LocalQuestTarget, ...]) -> tuple[object, ...]:
    distinct_quests = len({target.quest_id for target in group})
    has_primary = any(target.primary_chain for target in group)
    first = min(group, key=_target_order)
    return (
        -distinct_quests,
        not has_primary,
        first.source_order,
        int(first.quest_id),
        int(first.target_id),
        first.target_name,
    )
