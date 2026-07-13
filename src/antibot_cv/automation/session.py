from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


MAX_ATTACK_CLICKS_PER_BATTLE = 3
MAX_HUNT_CLICKS_PER_CYCLE = 3


@dataclass
class SessionState:
    requested_cycles: int = 3
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_wall: float = field(default_factory=time.time)
    started_monotonic: float = field(default_factory=time.monotonic)
    cycle_id: int = 0
    battle_id: int | None = None
    next_battle_id: int = 1
    completed_cycles: int = 0
    total_actions: int = 0
    viewport_moves: int = 0
    targets_detected: int = 0
    battles_detected: int = 0
    attack_actions: int = 0
    ability4_actions: int = 0
    combat_actions: int = 0
    exit_actions: int = 0
    hunt_actions: int = 0
    incomplete_cycles: int = 0
    resource_waits: int = 0
    recoveries: int = 0
    errors: int = 0
    emergency_stop: bool = False
    cycle_had_battle: bool = False
    cycle_had_combat_action: bool = False
    cycle_battle_outcome: str | None = None
    ability_used_battle_ids: set[int] = field(default_factory=set)
    exit_clicked_battle_ids: set[int] = field(default_factory=set)
    hunt_click_counts_by_cycle_id: dict[int, int] = field(default_factory=dict)
    attack_click_counts_by_battle_id: dict[int, int] = field(default_factory=dict)

    def new_battle(self) -> int:
        self.battle_id = self.next_battle_id
        self.next_battle_id += 1
        return self.battle_id

    def can_use_ability4(self) -> bool:
        return self.battle_id is not None and self.battle_id not in self.ability_used_battle_ids

    def can_click_attack(self) -> bool:
        return self.battle_id is not None and self.attack_click_count() < MAX_ATTACK_CLICKS_PER_BATTLE

    def attack_click_count(self) -> int:
        if self.battle_id is None:
            return 0
        return self.attack_click_counts_by_battle_id.get(self.battle_id, 0)

    def mark_attack(self) -> None:
        if self.battle_id is None:
            return
        self.attack_click_counts_by_battle_id[self.battle_id] = self.attack_click_count() + 1
        self.attack_actions += 1

    def mark_battle_detected(self) -> None:
        self.battles_detected += 1
        self.cycle_had_battle = True

    def mark_battle_outcome(self, outcome: str | None) -> None:
        normalized = str(outcome or "").strip().lower()
        self.cycle_battle_outcome = normalized if normalized in {"victory", "defeat", "unknown"} else None

    def mark_ability4(self) -> None:
        if self.battle_id is None:
            return
        self.ability_used_battle_ids.add(self.battle_id)
        self.ability4_actions += 1
        self.cycle_had_combat_action = True

    def mark_combat(self) -> None:
        self.combat_actions += 1
        self.cycle_had_combat_action = True

    def can_click_exit(self) -> bool:
        return self.battle_id is not None and self.battle_id not in self.exit_clicked_battle_ids

    def mark_exit(self) -> None:
        if self.battle_id is None:
            return
        self.exit_clicked_battle_ids.add(self.battle_id)
        self.exit_actions += 1

    def can_click_hunt(self) -> bool:
        return self.hunt_click_count() < MAX_HUNT_CLICKS_PER_CYCLE

    def hunt_click_count(self) -> int:
        return self.hunt_click_counts_by_cycle_id.get(self.cycle_id, 0)

    def mark_hunt(self) -> None:
        self.hunt_click_counts_by_cycle_id[self.cycle_id] = self.hunt_click_count() + 1
        self.hunt_actions += 1

    def mark_resource_wait(self) -> None:
        self.resource_waits += 1

    def mark_recovery(self) -> None:
        self.recoveries += 1

    def can_complete_cycle(self, *, require_victory: bool = False) -> bool:
        base_complete = self.cycle_had_battle and self.cycle_had_combat_action
        return base_complete and (not require_victory or self.cycle_battle_outcome == "victory")

    def reset_cycle_attempt(self) -> None:
        self.battle_id = None
        self.cycle_had_battle = False
        self.cycle_had_combat_action = False
        self.cycle_battle_outcome = None
        self.hunt_click_counts_by_cycle_id.pop(self.cycle_id, None)

    def mark_incomplete_cycle(self) -> None:
        self.incomplete_cycles += 1
        self.reset_cycle_attempt()

    def complete_cycle(self) -> None:
        self.completed_cycles += 1
        self.cycle_id += 1
        self.battle_id = None
        self.cycle_had_battle = False
        self.cycle_had_combat_action = False
        self.cycle_battle_outcome = None

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_monotonic
