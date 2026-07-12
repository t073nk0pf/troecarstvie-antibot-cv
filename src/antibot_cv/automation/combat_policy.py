"""Pure, fail-closed policy for choosing one next action in an active battle.

This module is deliberately an integration boundary, not an executor.  It only
evaluates an observed snapshot and returns a typed decision; callers must still
verify the returned action immediately before sending it to an action sink.

Integration notes:
* ``BattleSnapshot.items`` contains battle items only.  Recovery/backpack
  burdjuk handling outside a battle belongs to the existing recovery flow.
* A caller should take a fresh snapshot after every action.  No decision here
  performs live I/O, clicks, retries, or item batching.
* Unknown names, slots, missing readiness, and inconsistent counters fail
  closed with ``STOP_UNSAFE``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class CombatIntent(Enum):
    USE_SKILL = "USE_SKILL"
    USE_ITEM = "USE_ITEM"
    WAIT = "WAIT"
    EXIT = "EXIT"
    STOP_UNSAFE = "STOP_UNSAFE"


class BattleItemKind(Enum):
    HEALTH = "health"
    PROWESS = "prowess"
    DAMAGE_BOOST = "damage_boost"


@dataclass(frozen=True)
class BattleResources:
    hp: int | None = None
    max_hp: int | None = None
    prowess: int | None = None
    max_prowess: int | None = None


@dataclass(frozen=True)
class AvailableSkill:
    name: str
    slot: int
    damage: float = 0.0
    ready: bool | None = None
    cooldown: int | float | None = None
    allowed: bool = True


@dataclass(frozen=True)
class BattleItem:
    name: str
    slot: int
    kind: BattleItemKind | str
    count: int = 0
    ready: bool | None = None
    cooldown: int | float | None = None
    allowed: bool = True


@dataclass(frozen=True)
class BattleSnapshot:
    active: bool | None
    finished: bool | None
    turn: int | None
    resources: BattleResources
    skills: tuple[AvailableSkill, ...] = field(default_factory=tuple)
    items: tuple[BattleItem, ...] = field(default_factory=tuple)
    repeated_action_count: int = 0
    damage_boost_active: bool | None = None


@dataclass(frozen=True)
class CombatDecision:
    intent: CombatIntent
    reason: str
    skill: AvailableSkill | None = None
    item: BattleItem | None = None


@dataclass(frozen=True)
class CombatPolicy:
    skill_name_allowlist: tuple[str, ...] = field(default_factory=tuple)
    skill_slot_allowlist: tuple[int, ...] = field(default_factory=tuple)
    item_name_allowlist: Mapping[BattleItemKind | str, tuple[str, ...]] = field(default_factory=dict)
    item_slot_allowlist: Mapping[BattleItemKind | str, tuple[int, ...]] = field(default_factory=dict)
    hp_threshold: float = 0.35
    prowess_threshold: float = 0.35
    damage_boost_threshold: float = 0.0
    damage_boost_enabled: bool = False
    max_repeated_actions: int = 3
    allow_slot_zero: bool = False
    allow_zero_prowess_slot: bool = False

    def decide(self, snapshot: BattleSnapshot) -> CombatDecision:
        """Choose one action from one observation, without performing it."""
        if not self._valid_snapshot(snapshot) or not self._valid_policy():
            return self._unsafe("invalid_or_incomplete_snapshot")
        if snapshot.finished is True:
            return CombatDecision(CombatIntent.EXIT, "battle_finished")
        if snapshot.active is False:
            return CombatDecision(CombatIntent.EXIT, "battle_inactive")
        if snapshot.active is not True or snapshot.finished is not False:
            return self._unsafe("battle_state_unknown")
        if snapshot.turn == 0:
            return CombatDecision(CombatIntent.WAIT, "opponent_turn")
        if snapshot.repeated_action_count >= self.max_repeated_actions:
            return CombatDecision(CombatIntent.WAIT, "safe_repeat_limit_reached")

        allowed_items = [item for item in snapshot.items if self._item_allowed(item, snapshot.resources)]
        items = [item for item in allowed_items if self._ready(item)]
        wanted_kinds = self._wanted_item_kinds(snapshot)
        chosen = self._choose_item(items, wanted_kinds)
        if chosen is not None:
            return CombatDecision(CombatIntent.USE_ITEM, f"{self._kind(chosen).value}_threshold", item=chosen)
        item_waiting = any(self._kind(item) in wanted_kinds and not self._ready(item) for item in allowed_items)

        # Strongest known, explicitly allowlisted strike wins. Readiness is
        # explicit: a missing flag is never treated as ready.
        skills = [
            skill
            for skill in snapshot.skills
            if self._skill_allowed(skill, snapshot.resources) and self._ready(skill)
        ]
        skills.sort(key=lambda skill: (-skill.damage, skill.slot, skill.name))
        if skills:
            return CombatDecision(CombatIntent.USE_SKILL, "strongest_allowed_ready_skill", skill=skills[0])
        if item_waiting:
            return CombatDecision(CombatIntent.WAIT, "allowed_action_not_ready")
        return CombatDecision(CombatIntent.WAIT, "no_ready_allowed_action")

    def _skill_allowed(self, skill: AvailableSkill, resources: BattleResources) -> bool:
        if not self.skill_name_allowlist and not self.skill_slot_allowlist:
            return False
        if skill.slot == 0:
            zero_allowed = self.allow_slot_zero or (
                self.allow_zero_prowess_slot and resources.prowess == 0
            )
            if not zero_allowed:
                return False
        return skill.allowed and bool(skill.name) and skill.slot >= 0 and (
            (not self.skill_name_allowlist or skill.name in self.skill_name_allowlist)
            and (not self.skill_slot_allowlist or skill.slot in self.skill_slot_allowlist)
        )

    def _item_allowed(self, item: BattleItem, resources: BattleResources) -> bool:
        kind = self._kind(item)
        names = self._allowlist(self.item_name_allowlist, kind)
        slots = self._allowlist(self.item_slot_allowlist, kind)
        if not names and not slots:
            return False
        if not item.allowed or not item.name or item.slot < 0 or item.count <= 0:
            return False
        return (not names or item.name in names) and (not slots or item.slot in slots)

    def _wanted_item_kinds(self, snapshot: BattleSnapshot) -> list[BattleItemKind]:
        resources = snapshot.resources
        hp_ratio = resources.hp / resources.max_hp if resources.max_hp else 1.0
        prowess_ratio = resources.prowess / resources.max_prowess if resources.max_prowess else 1.0
        wanted: list[BattleItemKind] = []
        if hp_ratio < self.hp_threshold:
            wanted.append(BattleItemKind.HEALTH)
        if prowess_ratio < self.prowess_threshold:
            wanted.append(BattleItemKind.PROWESS)
        if (
            self.damage_boost_enabled
            and snapshot.damage_boost_active is False
            and hp_ratio >= self.hp_threshold
            and prowess_ratio >= self.prowess_threshold
        ):
            wanted.append(BattleItemKind.DAMAGE_BOOST)
        return wanted

    def _choose_item(
        self,
        items: list[BattleItem],
        wanted: list[BattleItemKind],
    ) -> BattleItem | None:
        for kind in wanted:
            candidates = [item for item in items if self._kind(item) is kind]
            if candidates:
                return min(candidates, key=lambda item: (item.slot, item.name))
        return None

    @staticmethod
    def _ready(value: AvailableSkill | BattleItem) -> bool:
        return value.ready is True and value.cooldown == 0

    @staticmethod
    def _kind(item: BattleItem) -> BattleItemKind:
        try:
            return item.kind if isinstance(item.kind, BattleItemKind) else BattleItemKind(item.kind)
        except (TypeError, ValueError):
            return BattleItemKind.DAMAGE_BOOST  # invalid kind is rejected by _valid_snapshot

    @staticmethod
    def _allowlist(mapping: Mapping[BattleItemKind | str, tuple], kind: BattleItemKind) -> tuple:
        return tuple(mapping.get(kind, mapping.get(kind.value, ())))

    @staticmethod
    def _valid_snapshot(snapshot: BattleSnapshot) -> bool:
        if not isinstance(snapshot, BattleSnapshot):
            return False
        r = snapshot.resources
        integer = lambda value: isinstance(value, int) and not isinstance(value, bool)
        valid_cooldown = lambda value: value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0)
        valid_skills = all(isinstance(skill, AvailableSkill) and isinstance(skill.name, str) and integer(skill.slot) and skill.slot >= 0 and isinstance(skill.damage, (int, float)) and not isinstance(skill.damage, bool) and skill.damage >= 0 and isinstance(skill.ready, (bool, type(None))) and valid_cooldown(skill.cooldown) for skill in snapshot.skills)
        valid_items = all(isinstance(item, BattleItem) and isinstance(item.name, str) and integer(item.slot) and item.slot >= 0 and integer(item.count) and isinstance(item.ready, (bool, type(None))) and valid_cooldown(item.cooldown) for item in snapshot.items)
        return isinstance(r, BattleResources) and isinstance(snapshot.active, (bool, type(None))) and isinstance(snapshot.finished, (bool, type(None))) and isinstance(snapshot.damage_boost_active, (bool, type(None))) and integer(snapshot.turn) and snapshot.turn >= 0 and integer(snapshot.repeated_action_count) and snapshot.repeated_action_count >= 0 and all(
            integer(v) and v >= 0 for v in (r.hp, r.max_hp, r.prowess, r.max_prowess) if v is not None
        ) and (r.max_hp is not None and r.max_hp > 0 and r.hp is not None and r.hp <= r.max_hp) and (r.max_prowess is not None and r.max_prowess > 0 and r.prowess is not None and r.prowess <= r.max_prowess) and valid_skills and valid_items and all(
            isinstance(item.kind, (BattleItemKind, str)) and (not isinstance(item.kind, str) or item.kind in {k.value for k in BattleItemKind}) for item in snapshot.items
        )

    def _valid_policy(self) -> bool:
        return 0 <= self.hp_threshold <= 1 and 0 <= self.prowess_threshold <= 1 and self.max_repeated_actions > 0 and self.damage_boost_threshold >= 0 and isinstance(self.damage_boost_enabled, bool)

    @staticmethod
    def _unsafe(reason: str) -> CombatDecision:
        return CombatDecision(CombatIntent.STOP_UNSAFE, reason)


Intent = CombatIntent
Action = CombatDecision
Skill = AvailableSkill
Item = BattleItem
CombatSnapshot = BattleSnapshot
CombatResources = BattleResources
SkillObservation = AvailableSkill
ItemObservation = BattleItem
