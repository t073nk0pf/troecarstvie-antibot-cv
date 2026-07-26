"""Deterministic, fail-closed decisions for the leveling MVP.

This module only turns an observed snapshot into an intent.  It deliberately
does not know how an intent is executed, and therefore cannot make live calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class LevelingIntent(Enum):
    STOP_GOAL = "STOP_GOAL"
    STOP_UNSAFE = "STOP_UNSAFE"
    REVIVE_FREE = "REVIVE_FREE"
    REST = "REST"
    RECOVER_HP_ITEM = "RECOVER_HP_ITEM"
    RECOVER_PROWESS_ITEM = "RECOVER_PROWESS_ITEM"
    OPEN_HUNT = "OPEN_HUNT"
    FARM = "FARM"
    WAIT = "WAIT"


@dataclass(frozen=True)
class ObservedPlayer:
    character: str | None = None
    level: int | None = None
    hp: int | None = None
    max_hp: int | None = None
    prowess: int | None = None
    max_prowess: int | None = None
    alive: bool | None = None
    stale: bool = False


@dataclass(frozen=True)
class ObservedLocation:
    name: str | None = None
    safe: bool | None = None
    in_hunt: bool | None = None
    hunt_open: bool | None = None
    ready: bool | None = True
    stale: bool = False


@dataclass(frozen=True)
class ObservedDeath:
    deaths: int | None = None
    can_free_revive: bool | None = None
    stale: bool = False


@dataclass(frozen=True)
class ObservedQuests:
    complete: bool | None = None
    stale: bool = False


@dataclass(frozen=True)
class ObservedInventory:
    items: Mapping[str, int] | None = None
    stale: bool = False


@dataclass(frozen=True)
class LevelingDecision:
    intent: LevelingIntent
    reason: str
    item: str | None = None


@dataclass(frozen=True)
class LevelingPolicy:
    required_character: str
    target_level: int
    max_deaths: int
    hp_threshold: float = 0.35
    prowess_threshold: float = 0.35
    hp_item_allowlist: tuple[str, ...] = field(default_factory=tuple)
    prowess_item_allowlist: tuple[str, ...] = field(default_factory=tuple)

    def decide(
        self,
        player: ObservedPlayer,
        location: ObservedLocation,
        death: ObservedDeath,
        quests: ObservedQuests,
        inventory: ObservedInventory,
    ) -> LevelingDecision:
        """Return one deterministic intent for a single observed snapshot."""
        if any(observation.stale for observation in (player, location, death, quests, inventory)):
            return self._unsafe("stale_observation")
        if not self._configuration_is_safe():
            return self._unsafe("invalid_policy_configuration")
        if not self._identity_observations_are_complete(player, location, death, quests, inventory):
            return self._unsafe("unknown_observation")
        assert player.character is not None
        assert player.level is not None
        assert death.deaths is not None
        assert player.alive is not None

        if player.character != self.required_character:
            return self._unsafe("wrong_character")
        if player.level >= self.target_level:
            return LevelingDecision(LevelingIntent.STOP_GOAL, "goal_reached")
        if death.deaths >= self.max_deaths:
            return self._unsafe("max_deaths_reached")
        if not player.alive:
            if death.can_free_revive is True:
                return LevelingDecision(LevelingIntent.REVIVE_FREE, "free_revive_available")
            return self._unsafe("paid_or_unknown_revive")
        if not self._active_observations_are_complete(player, location):
            return self._unsafe("unknown_observation")
        assert player.hp is not None and player.max_hp is not None
        assert player.prowess is not None and player.max_prowess is not None
        if not location.safe:
            return self._unsafe("unsafe_location")
        assert location.in_hunt is not None and location.hunt_open is not None
        assert location.ready is not None

        hp_ratio = player.hp / player.max_hp
        if hp_ratio < self.hp_threshold:
            item = self._available_item(inventory.items or {}, self.hp_item_allowlist)
            if item is not None:
                return LevelingDecision(LevelingIntent.RECOVER_HP_ITEM, "hp_below_threshold", item)
            return LevelingDecision(LevelingIntent.REST, "hp_below_threshold_no_allowed_item")

        prowess_ratio = player.prowess / player.max_prowess
        if prowess_ratio < self.prowess_threshold:
            item = self._available_item(inventory.items or {}, self.prowess_item_allowlist)
            if item is not None:
                return LevelingDecision(LevelingIntent.RECOVER_PROWESS_ITEM, "prowess_below_threshold", item)
            return LevelingDecision(LevelingIntent.REST, "prowess_below_threshold_no_allowed_item")
        if not location.ready:
            return LevelingDecision(LevelingIntent.WAIT, "location_not_ready")
        if not location.in_hunt:
            return LevelingDecision(LevelingIntent.OPEN_HUNT, "hunt_not_open")
        if not location.hunt_open:
            return LevelingDecision(LevelingIntent.OPEN_HUNT, "hunt_not_open")
        return LevelingDecision(LevelingIntent.FARM, "ready_to_farm")

    def _configuration_is_safe(self) -> bool:
        return (
            bool(self.required_character)
            and self.target_level > 0
            and self.max_deaths >= 0
            and 0 <= self.hp_threshold <= 1
            and 0 <= self.prowess_threshold <= 1
        )

    @staticmethod
    def _identity_observations_are_complete(*observations: object) -> bool:
        player, location, death, quests, inventory = observations
        return (
            isinstance(player, ObservedPlayer)
            and player.character is not None
            and player.level is not None
            and player.alive is not None
            and isinstance(location, ObservedLocation)
            and isinstance(death, ObservedDeath)
            and death.deaths is not None
            and death.deaths >= 0
            and isinstance(quests, ObservedQuests)
            and isinstance(inventory, ObservedInventory)
        )

    @staticmethod
    def _active_observations_are_complete(player: ObservedPlayer, location: ObservedLocation) -> bool:
        return (
            player.hp is not None
            and player.max_hp is not None
            and player.prowess is not None
            and player.max_prowess is not None
            and player.max_hp > 0
            and player.max_prowess > 0
            and location.safe is not None
            and location.in_hunt is not None
            and location.hunt_open is not None
            and location.ready is not None
        )

    @staticmethod
    def _available_item(items: Mapping[str, int], allowlist: tuple[str, ...]) -> str | None:
        for name in allowlist:
            quantity = items.get(name)
            if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
                return name
        return None

    @staticmethod
    def _unsafe(reason: str) -> LevelingDecision:
        return LevelingDecision(LevelingIntent.STOP_UNSAFE, reason)


# Short aliases make the observation contract pleasant for callers while the
# explicit names above remain the canonical public API.
Player = ObservedPlayer
Location = ObservedLocation
Death = ObservedDeath
Quests = ObservedQuests
Inventory = ObservedInventory
Intent = LevelingIntent
LevelingPlanner = LevelingPolicy
